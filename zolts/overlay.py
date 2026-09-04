"""Overlay resolution: Blueprint -> Industry Pack -> Tenant -> Program.

Claim under test (docs/05): adaptability to any company type comes from
inheritance, not from per-customer forks, and no layer can loosen the policy
inherited from above. If a program could relax tenant policy, the whole
compliance argument collapses — so loosening is a hard error, not a warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Policy keys where a lower value is stricter, so an override may only lower it.
_LOWER_IS_STRICTER = {"max_touches_per_person_per_week", "max_cost_per_account", "holdout_waiver"}
# Policy keys where a larger set is stricter (more lists checked, more blocks).
_SUPERSET_IS_STRICTER = {"lists_check", "blocked_channels"}
# Basis strength ordering: a stricter override must require at least as much.
_BASIS_STRENGTH = {"legitimate_interest": 0, "contract": 1, "consent": 2}


class PolicyLoosened(ValueError):
    """Raised when a lower overlay layer tries to weaken an inherited policy."""


@dataclass(frozen=True)
class Layer:
    name: str
    data: dict[str, Any]


def resolve(layers: list[Layer]) -> dict[str, Any]:
    """Merge layers in order, enforcing restrict-only semantics on `policy`.

    Non-policy keys merge normally (later layers win), which is what makes a
    tenant able to add its own signals, segments and plays without a fork.
    """
    resolved: dict[str, Any] = {}
    for layer in layers:
        resolved = _merge(resolved, layer.data, layer.name, path="")
    return resolved


def _merge(base: dict[str, Any], overlay: dict[str, Any], layer_name: str, path: str) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        key_path = f"{path}.{key}" if path else key
        if key not in out:
            out[key] = value
            continue
        current = out[key]
        # Policy keys are checked before the generic dict merge: without this,
        # `channels_require_basis` recurses as a plain mapping and a weakened
        # basis slips through unnoticed.
        if _is_policy_path(key_path) and _needs_policy_merge(key, key_path):
            out[key] = _merge_policy_value(key, current, value, layer_name, key_path)
        elif isinstance(current, dict) and isinstance(value, dict):
            out[key] = _merge(current, value, layer_name, key_path)
        else:
            out[key] = value
    return out


def _is_policy_path(key_path: str) -> bool:
    return key_path.split(".")[0] == "policy"


def _needs_policy_merge(key: str, key_path: str) -> bool:
    return (
        key in _LOWER_IS_STRICTER
        or key in _SUPERSET_IS_STRICTER
        or key_path.endswith("channels_require_basis")
    )


def _merge_policy_value(key: str, current: Any, override: Any, layer_name: str, key_path: str) -> Any:
    if key in _LOWER_IS_STRICTER:
        if override > current:
            raise PolicyLoosened(
                f"layer '{layer_name}' raises {key_path} from {current} to {override}; "
                "overlays may only tighten policy"
            )
        return override

    if key in _SUPERSET_IS_STRICTER:
        current_set, override_set = set(current or []), set(override or [])
        if not current_set.issubset(override_set):
            missing = sorted(current_set - override_set)
            raise PolicyLoosened(
                f"layer '{layer_name}' drops {missing} from {key_path}; "
                "overlays may only tighten policy"
            )
        return sorted(override_set)

    if key_path.endswith("channels_require_basis"):
        return _merge_basis_map(current, override, layer_name, key_path)

    return override


def _merge_basis_map(current: dict, override: dict, layer_name: str, key_path: str) -> dict:
    """A channel's required basis may be strengthened, never weakened.

    Dropping a channel from the map is also a loosening: an unlisted channel
    falls back to the jurisdiction default, which may be weaker.
    """
    out = dict(current)
    for channel, required in current.items():
        if channel not in override:
            continue
        new_required = override[channel]
        if _BASIS_STRENGTH[new_required] < _BASIS_STRENGTH[required]:
            raise PolicyLoosened(
                f"layer '{layer_name}' weakens {key_path}.{channel} from "
                f"{required} to {new_required}; overlays may only tighten policy"
            )
    out.update(override)
    return out
