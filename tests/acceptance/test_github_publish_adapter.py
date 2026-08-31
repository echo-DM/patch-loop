from __future__ import annotations

import base64
import json
import urllib.request
from collections.abc import Mapping
from typing import cast

import pytest

from patchloop.github_api import GitHubApiClient
from patchloop.github_publish import (
    BaseFile,
    BaseRevision,
    CommitFile,
    DraftPullRequest,
)


class FixtureResponse:
    def __init__(self, document: object) -> None:
        self._content = json.dumps(document).encode()

    def __enter__(self) -> FixtureResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._content


def test_github_publish_adapter_uses_git_database_and_draft_pr_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, str, object | None]] = []
    responses: list[object] = [
        {"object": {"sha": "base-sha"}},
        {"tree": {"sha": "base-tree"}},
        {
            "truncated": False,
            "tree": [
                {
                    "mode": "100755",
                    "path": "docs/greeting.md",
                    "sha": "old-blob",
                    "type": "blob",
                }
            ],
        },
        {
            "encoding": "base64",
            "content": base64.b64encode(b"Hello, world!\n").decode(),
        },
        {"object": {"sha": "existing-sha"}},
        {"sha": "blob-sha"},
        {"sha": "new-tree"},
        {"sha": "commit-sha"},
        {},
        {"number": 7, "html_url": "https://github.test/pull/7", "draft": True},
        {},
    ]

    def urlopen(request: urllib.request.Request, timeout: int) -> FixtureResponse:
        assert timeout == 30
        payload = (
            json.loads(cast(bytes, request.data))
            if request.data is not None
            else None
        )
        requests.append((request.get_method(), request.full_url, payload))
        return FixtureResponse(responses.pop(0))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    client = GitHubApiClient(api_url="https://api.github.test", token="token")

    base = client.base_revision("octo-org/example", "main")
    files = client.base_files(
        "octo-org/example", base, ("docs/greeting.md", "docs/new.md")
    )
    existing = client.branch_head("octo-org/example", "patchloop/issue-42")
    commit = client.create_commit(
        "octo-org/example",
        base,
        (CommitFile("docs/greeting.md", b"Hello from PatchLoop!\n", "100755"),),
        "patchloop: address issue #42",
    )
    client.create_branch("octo-org/example", "patchloop/issue-42", commit)
    pull_request = client.create_draft_pull_request(
        "octo-org/example",
        base_branch="main",
        head_branch="patchloop/issue-42",
        title="PatchLoop: address #42",
        body="Draft body",
    )
    client.delete_branch("octo-org/example", "patchloop/issue-42")

    assert base == BaseRevision("base-sha", "base-tree")
    assert files == (
        BaseFile("docs/greeting.md", b"Hello, world!\n", "100755"),
        BaseFile("docs/new.md", None, "100644"),
    )
    assert existing == "existing-sha"
    assert commit == "commit-sha"
    assert pull_request == DraftPullRequest(7, "https://github.test/pull/7")
    assert responses == []
    assert requests == [
        (
            "GET",
            "https://api.github.test/repos/octo-org/example/git/ref/heads/main",
            None,
        ),
        (
            "GET",
            "https://api.github.test/repos/octo-org/example/git/commits/base-sha",
            None,
        ),
        (
            "GET",
            "https://api.github.test/repos/octo-org/example/git/trees/base-tree?recursive=1",
            None,
        ),
        (
            "GET",
            "https://api.github.test/repos/octo-org/example/git/blobs/old-blob",
            None,
        ),
        (
            "GET",
            "https://api.github.test/repos/octo-org/example/git/ref/heads/patchloop%2Fissue-42",
            None,
        ),
        (
            "POST",
            "https://api.github.test/repos/octo-org/example/git/blobs",
            {
                "content": base64.b64encode(b"Hello from PatchLoop!\n").decode(),
                "encoding": "base64",
            },
        ),
        (
            "POST",
            "https://api.github.test/repos/octo-org/example/git/trees",
            {
                "base_tree": "base-tree",
                "tree": [
                    {
                        "mode": "100755",
                        "path": "docs/greeting.md",
                        "sha": "blob-sha",
                        "type": "blob",
                    }
                ],
            },
        ),
        (
            "POST",
            "https://api.github.test/repos/octo-org/example/git/commits",
            {
                "message": "patchloop: address issue #42",
                "parents": ["base-sha"],
                "tree": "new-tree",
            },
        ),
        (
            "POST",
            "https://api.github.test/repos/octo-org/example/git/refs",
            {"ref": "refs/heads/patchloop/issue-42", "sha": "commit-sha"},
        ),
        (
            "POST",
            "https://api.github.test/repos/octo-org/example/pulls",
            {
                "base": "main",
                "body": "Draft body",
                "draft": True,
                "head": "patchloop/issue-42",
                "title": "PatchLoop: address #42",
            },
        ),
        (
            "DELETE",
            "https://api.github.test/repos/octo-org/example/git/refs/heads/patchloop%2Fissue-42",
            None,
        ),
    ]


def test_github_request_headers_never_expose_token_in_payload_or_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def urlopen(request: urllib.request.Request, timeout: int) -> FixtureResponse:
        _ = timeout
        observed.update(
            {
                "url": request.full_url,
                "data": request.data,
                "headers": cast(Mapping[str, str], request.headers),
            }
        )
        return FixtureResponse({"object": {"sha": "base-sha"}})

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    client = GitHubApiClient(api_url="https://api.github.test", token="secret-token")

    with pytest.raises(Exception):
        client.base_revision("octo-org/example", "main")

    assert "secret-token" not in str(observed["url"])
    assert "secret-token" not in str(observed["data"])
    assert cast(Mapping[str, str], observed["headers"])["Authorization"] == (
        "Bearer secret-token"
    )
