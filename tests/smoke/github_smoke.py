from __future__ import annotations

import os

import pytest

from patchloop.github_api import GitHubApiClient


@pytest.mark.smoke
def test_smoke_repository_contains_the_live_draft_pr() -> None:
    if os.environ.get("PATCHLOOP_RUN_GITHUB_SMOKE") != "1":
        pytest.skip("Set PATCHLOOP_RUN_GITHUB_SMOKE=1 to inspect live GitHub state.")

    token = os.environ.get("GITHUB_TOKEN", "")
    repository = os.environ.get("PATCHLOOP_SMOKE_REPOSITORY", "")
    issue_text = os.environ.get("PATCHLOOP_SMOKE_ISSUE", "")
    if not token or not repository or not issue_text:
        pytest.skip(
            "Live GitHub smoke requires GITHUB_TOKEN, PATCHLOOP_SMOKE_REPOSITORY, "
            "and PATCHLOOP_SMOKE_ISSUE."
        )
    if repository == "echo-DM/patch-loop":
        pytest.fail("Live smoke must use a Smoke repository.")
    try:
        issue_number = int(issue_text)
    except ValueError:
        pytest.fail("PATCHLOOP_SMOKE_ISSUE must be an integer.")
    if issue_number <= 0:
        pytest.fail("PATCHLOOP_SMOKE_ISSUE must be positive.")

    client = GitHubApiClient(
        api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        token=token,
    )
    pull_request = client.pull_request_for_branch(
        repository, f"patchloop/issue-{issue_number}"
    )

    assert pull_request is not None
    assert pull_request.state == "open"
    assert pull_request.draft is True
    assert pull_request.head_branch == f"patchloop/issue-{issue_number}"
