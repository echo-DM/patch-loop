from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import cast

from patchloop.adapters import RepositoryPermission
from patchloop.errors import InfrastructureError


class GitHubApiClient:
    """Read only the permission and comment data required by the workflow Gate."""

    def __init__(self, *, api_url: str, token: str) -> None:
        if not token.strip():
            raise InfrastructureError(
                "github_token_missing", "The Gate requires the caller GITHUB_TOKEN."
            )
        self._api_url = api_url.rstrip("/")
        self._token = token

    def permission_for(
        self, repository: str, username: str
    ) -> RepositoryPermission | None:
        path = (
            f"/repos/{urllib.parse.quote(repository, safe='/')}/collaborators/"
            f"{urllib.parse.quote(username, safe='')}/permission"
        )
        try:
            document = self._get(path)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise
        if not isinstance(document, dict):
            raise ValueError("GitHub permission response must be an object.")
        permission = cast(dict[str, object], document).get("permission")
        if permission not in {"admin", "write", "read", "none"}:
            raise ValueError("GitHub permission response is invalid.")
        return cast(RepositoryPermission, permission)

    def issue_comments(
        self, repository: str, issue_number: int
    ) -> list[dict[str, object]]:
        comments: list[dict[str, object]] = []
        page = 1
        while True:
            path = (
                f"/repos/{urllib.parse.quote(repository, safe='/')}/issues/"
                f"{issue_number}/comments?per_page=100&page={page}"
            )
            document = self._get(path)
            if not isinstance(document, list) or not all(
                isinstance(item, dict) for item in document
            ):
                raise ValueError("GitHub comments response must be a list of objects.")
            page_comments = cast(list[dict[str, object]], document)
            comments.extend(page_comments)
            if len(page_comments) < 100:
                return comments
            page += 1

    def _get(self, path: str) -> object:
        request = urllib.request.Request(
            self._api_url + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
