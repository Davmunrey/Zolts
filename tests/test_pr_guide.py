"""The pr-guide decision: what blocks a pull request, and who moves next."""
from dataclasses import replace

from scripts.pr_guide import MARKER, State, linked_issues, next_step, pull_numbers, render

READY = State(author="agent-bot", owner="owner", draft=False, linked=["#97"], passed=4)


def test_a_body_names_its_issues_in_order_without_repeats():
    body = "Fixes #12. Also resolves: other/repo#3, and closes #12 again.\nRefs #9."
    assert linked_issues(body) == ["#12", "other/repo#3"]


def test_a_body_without_a_closing_keyword_links_nothing():
    assert linked_issues("See #12 and issue 14") == []
    assert linked_issues(None) == []


def test_a_missing_issue_comes_first_and_belongs_to_the_author():
    state = replace(READY, linked=[], failing=["test"], conflicts=True, draft=True)
    step, who = next_step(state)
    assert "Fixes #n" in step and who == "agent-bot"


def test_conflicts_come_before_ci():
    step, who = next_step(replace(READY, conflicts=True, failing=["test"]))
    assert "conflicts" in step and who == "agent-bot"


def test_a_failing_check_is_named():
    step, who = next_step(replace(READY, failing=["image", "test"]))
    assert "`image`, `test`" in step and who == "agent-bot"


def test_running_ci_waits_on_ci_not_on_a_person():
    assert next_step(replace(READY, pending=["test"])) == ("Wait for CI to finish.", "CI")


def test_a_green_draft_asks_the_author_to_mark_it_ready():
    step, who = next_step(replace(READY, draft=True))
    assert "ready for review" in step and who == "agent-bot"


def test_requested_changes_go_back_to_the_author():
    step, who = next_step(replace(READY, changes_requested_by=["owner"]))
    assert "@owner" in step and who == "agent-bot"


def test_a_clean_pull_request_waits_on_the_owner_s_review():
    step, who = next_step(READY)
    assert step.startswith("Review and approve") and who == "owner"


def test_an_approved_clean_pull_request_is_ready_to_merge():
    step, who = next_step(replace(READY, approved_by=["owner"]))
    assert step.startswith("Merge") and who == "owner"


def test_the_owner_s_own_pull_request_is_not_sent_for_an_approval_github_refuses():
    step, who = next_step(replace(READY, author="owner"))
    assert "cannot approve their own" in step and who == "owner"


def test_an_unknown_merge_state_is_not_reported_as_a_conflict():
    step, _ = next_step(replace(READY, conflicts=None))
    assert "conflicts" not in step
    assert "still checking" in render(replace(READY, conflicts=None))


def test_the_comment_carries_the_marker_and_every_row():
    body = render(replace(READY, failing=["test"]))
    assert body.startswith(MARKER)
    for row in ("Linked issue", "Draft", "CI", "Merge conflicts", "Review"):
        assert f"| {row} |" in body
    assert "❌ failing: `test`" in body and "@agent-bot" in body


def test_pull_request_and_workflow_run_events_both_name_their_pull_requests():
    assert pull_numbers({"pull_request": {"number": 7}}) == [7]
    assert pull_numbers({"workflow_run": {"pull_requests": [{"number": 3}, {"number": 5}]}}) == [3, 5]
    assert pull_numbers({"workflow_run": {"pull_requests": []}}) == []
