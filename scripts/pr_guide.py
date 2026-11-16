"""Keep one comment on a pull request that says what blocks it and who moves next.

Run by .github/workflows/pr-guide.yml. Reads the event from GITHUB_EVENT_PATH,
gathers the pull request's state over the REST API with GITHUB_TOKEN, and
creates or edits the single comment that carries MARKER. Standard library only.

The decision is `next_step(state)`, a pure function over a `State`; the rest is
I/O. tests/test_pr_guide.py covers the decision and the rendering.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field

MARKER = "<!-- pr-guide -->"
LINK = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+((?:[\w.-]+/[\w.-]+)?#\d+)", re.I)
FAILED = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "error"}


@dataclass
class State:
    author: str
    owner: str
    draft: bool
    linked: list[str]
    failing: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    passed: int = 0
    conflicts: bool | None = False      # None: GitHub has not computed it yet
    approved_by: list[str] = field(default_factory=list)
    changes_requested_by: list[str] = field(default_factory=list)


def linked_issues(body: str | None) -> list[str]:
    """Issue references a merge would close, in body order, without repeats."""
    seen: list[str] = []
    for ref in LINK.findall(body or ""):
        if ref not in seen:
            seen.append(ref)
    return seen


def next_step(s: State) -> tuple[str, str]:
    """The one thing that moves this pull request next, and who does it."""
    if not s.linked:
        return "Link the issue this resolves: add `Fixes #n` to the description, or open the issue first.", s.author
    if s.conflicts:
        return "Resolve the conflicts with the base branch: merge the base into this branch.", s.author
    if s.failing:
        return f"Fix the failing check{'s' if len(s.failing) > 1 else ''}: {', '.join(f'`{n}`' for n in s.failing)}.", s.author
    if s.pending:
        return "Wait for CI to finish.", "CI"
    if s.draft:
        return "Mark the pull request ready for review: CI is green.", s.author
    if s.changes_requested_by:
        return f"Address the changes requested by {', '.join('@' + u for u in s.changes_requested_by)}.", s.author
    if s.author == s.owner:
        # GitHub refuses an author's approval of their own pull request.
        return "Read the diff, then merge: green and conflict-free (an author cannot approve their own PR).", s.owner
    if s.owner not in s.approved_by:
        return "Review and approve, or request changes.", s.owner
    return "Merge: approved, green and conflict-free.", s.owner


def render(s: State) -> str:
    step, who = next_step(s)
    who_text = who if who == "CI" else f"@{who}"
    if s.failing:
        ci = "❌ failing: " + ", ".join(f"`{n}`" for n in s.failing)
    elif s.pending:
        ci = f"⏳ {len(s.pending)} running"
    else:
        ci = f"✅ {s.passed} passed"
    if s.conflicts is None:
        merge = "⏳ GitHub is still checking"
    else:
        merge = "❌ conflicts with the base branch" if s.conflicts else "✅ none"
    if s.changes_requested_by:
        review = "❌ changes requested by " + ", ".join("@" + u for u in s.changes_requested_by)
    elif s.approved_by:
        review = "✅ approved by " + ", ".join("@" + u for u in s.approved_by)
    else:
        review = f"⏳ waiting on @{s.owner}"
    rows = [
        ("Linked issue", "✅ " + ", ".join(s.linked) if s.linked else "❌ none"),
        ("Draft", "📝 draft" if s.draft else "✅ ready for review"),
        ("CI", ci),
        ("Merge conflicts", merge),
        ("Review", review),
    ]
    table = "\n".join(f"| {k} | {v} |" for k, v in rows)
    return (f"{MARKER}\n**Next step:** {step} · {who_text}\n\n| Check | State |\n|---|---|\n{table}\n\n"
            "<sub>Kept current by the pr-guide workflow on every push, edit, review and CI run.</sub>\n")


# --- GitHub I/O ---------------------------------------------------------------

def _api(method: str, path: str, payload: dict | None = None):
    req = urllib.request.Request(
        f"{os.environ.get('GITHUB_API_URL', 'https://api.github.com')}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"null")


def _pages(path: str, key: str | None = None) -> list:
    out, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        data = _api("GET", f"{path}{sep}per_page=100&page={page}")
        items = data[key] if key else data
        out.extend(items)
        if len(items) < 100:
            return out
        page += 1


def gather(repo: str, number: int, owner: str, self_check: str) -> State:
    pr = _api("GET", f"/repos/{repo}/pulls/{number}")
    if pr.get("mergeable") is None:          # computed lazily; ask once more
        time.sleep(3)
        pr = _api("GET", f"/repos/{repo}/pulls/{number}")
    sha = pr["head"]["sha"]

    failing, pending, passed = [], [], 0
    for run in _pages(f"/repos/{repo}/commits/{sha}/check-runs", "check_runs"):
        if run["name"] == self_check:
            continue
        if run["status"] != "completed":
            pending.append(run["name"])
        elif run["conclusion"] in FAILED:
            failing.append(run["name"])
        else:
            passed += 1
    for status in _api("GET", f"/repos/{repo}/commits/{sha}/status")["statuses"]:
        if status["state"] == "pending":
            pending.append(status["context"])
        elif status["state"] in FAILED:
            failing.append(status["context"])
        else:
            passed += 1

    latest: dict[str, str] = {}
    for review in _pages(f"/repos/{repo}/pulls/{number}/reviews"):
        if review["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[review["user"]["login"]] = review["state"]
    return State(
        author=pr["user"]["login"], owner=owner, draft=pr["draft"], linked=linked_issues(pr["body"]),
        failing=sorted(set(failing)), pending=sorted(set(pending)), passed=passed,
        conflicts=None if pr.get("mergeable") is None else pr.get("mergeable_state") == "dirty",
        approved_by=sorted(u for u, st in latest.items() if st == "APPROVED"),
        changes_requested_by=sorted(u for u, st in latest.items() if st == "CHANGES_REQUESTED"))


def publish(repo: str, number: int, body: str) -> None:
    for comment in _pages(f"/repos/{repo}/issues/{number}/comments"):
        if MARKER in (comment.get("body") or ""):
            if comment["body"] != body:
                _api("PATCH", f"/repos/{repo}/issues/comments/{comment['id']}", {"body": body})
            return
    _api("POST", f"/repos/{repo}/issues/{number}/comments", {"body": body})


def pull_numbers(event: dict) -> list[int]:
    if "pull_request" in event:
        return [event["pull_request"]["number"]]
    return [pr["number"] for pr in (event.get("workflow_run") or {}).get("pull_requests", [])]


def main() -> int:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
        event = json.load(fh)
    repo = os.environ["GITHUB_REPOSITORY"]
    owner = os.environ.get("PR_GUIDE_OWNER") or repo.split("/")[0]
    self_check = os.environ.get("PR_GUIDE_SELF", "guide")
    for number in pull_numbers(event):
        publish(repo, number, render(gather(repo, number, owner, self_check)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
