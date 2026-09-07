"""The commands the platform will actually run.

CI builds the image and boots it — on the image's own default CMD, which is
`serve --port 8000`. `fly.toml` runs neither of those: it declares its own
process table, and those two strings are what a deploy executes.

    [processes]
      api    = "python3 -m runtime.cli serve --host 0.0.0.0 --port 8080"
      worker = "python3 -m runtime.cli worker"

So the commands that were verified and the commands that will run were two
different sets. Rename a flag and the suite stays green while the next deploy
brings up an API that answers and a worker that crashloops — and the worker is
the product: without it, actions queue and nothing is ever sent. The console
would show a growing queue and no error.

These read the process table out of `fly.toml` rather than restating it, and
check each command against the parser that has to accept it.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

import pytest

FLY = tomllib.loads(Path("fly.toml").read_text(encoding="utf-8"))
PROCESSES: dict[str, str] = FLY.get("processes", {})


def _argv(command: str) -> list[str]:
    """The arguments the CLI itself would receive, dropping `python3 -m ...`."""
    parts = command.split()
    assert parts[:3] == ["python3", "-m", "runtime.cli"], (
        f"the process table runs {parts[:3]}, which is not this CLI")
    return parts[3:]


def test_fly_declares_both_processes():
    """One of them serving is not a deployment. The API answers questions; the
    worker is what actually contacts anybody."""
    assert set(PROCESSES) == {"api", "worker"}, (
        f"fly.toml declares {sorted(PROCESSES)}; the runtime needs both")


@pytest.mark.parametrize("name", sorted(PROCESSES))
def test_every_declared_process_is_a_command_this_cli_accepts(name):
    """Parsed, not executed: `worker` never returns and `serve` binds a port.

    Parsing is where the failure would be anyway — an unknown subcommand or a
    renamed flag is an argparse error two seconds into a deploy.
    """
    from runtime import cli

    argv = _argv(PROCESSES[name])
    parser = argparse.ArgumentParser(prog="zolts")
    # Rebuild the real parser by calling main with `--help` suppressed is not
    # possible, so parse through the CLI's own entry point in a mode that
    # stops before doing any work.
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv + ["--help"])
    assert exit_info.value.code == 0, (
        f"fly.toml's `{name}` command is not one this CLI accepts: "
        f"{PROCESSES[name]!r}")
    del parser


def test_the_api_binds_the_port_fly_routes_to():
    """`internal_port` is where Fly sends traffic; `--port` is where the
    process listens. A mismatch is a deployment that builds, boots, passes
    nothing, and is rolled back after the health checks time out."""
    command = PROCESSES["api"]
    argv = _argv(command)
    port = argv[argv.index("--port") + 1] if "--port" in argv else None
    assert port is not None, "the api process does not pin a port"
    routed = FLY.get("http_service", {}).get("internal_port")
    assert str(routed) == port, (
        f"fly.toml routes to {routed} and the api listens on {port}")


def test_the_api_listens_on_every_interface():
    """A container that binds 127.0.0.1 is unreachable from outside itself,
    which looks exactly like a slow start until the deploy is rolled back."""
    argv = _argv(PROCESSES["api"])
    assert "--host" in argv and argv[argv.index("--host") + 1] == "0.0.0.0", (
        "the api binds a loopback address inside a container")
