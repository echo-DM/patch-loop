from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Literal, cast

from patchloop.adapters import RepositoryPermission
from patchloop.errors import InfrastructureError
from patchloop.github_publish import (
    BaseRevision,
    BaseFile,
    CommitFile,
    DraftPullRequest,
)


class GitHubApiClient:
    """Perform the bounded GitHub API operations used by Gate and Publish."""

    def __init__(self, *, api_url: str, token: str) -> None:
        if not token.strip():
            raise InfrastructureError(
                "github_token_missing", "GitHub operations require the caller GITHUB_TOKEN."
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

    def base_revision(self, repository: str, branch: str) -> BaseRevision:
        commit_sha = self.branch_head(repository, branch)
        if commit_sha is None:
            raise ValueError("GitHub base branch does not exist.")
        commit = self._object(
            self._get(f"{self._repository_path(repository)}/git/commits/{commit_sha}"),
            "GitHub commit response",
        )
        tree = self._object(commit.get("tree"), "GitHub commit tree")
        return BaseRevision(
            commit_sha=commit_sha,
            tree_sha=self._string(tree.get("sha"), "GitHub base tree"),
        )

    def base_files(
        self,
        repository: str,
        base: BaseRevision,
        paths: tuple[str, ...],
    ) -> tuple[BaseFile, ...]:
        repository_path = self._repository_path(repository)
        tree = self._object(
            self._get(f"{repository_path}/git/trees/{base.tree_sha}?recursive=1"),
            "GitHub recursive tree response",
        )
        if tree.get("truncated") is True:
            raise ValueError("GitHub base tree is too large to validate safely.")
        raw_entries = tree.get("tree")
        if not isinstance(raw_entries, list):
            raise ValueError("GitHub recursive tree entries must be a list.")
        entries: dict[str, dict[str, object]] = {}
        for raw_entry in raw_entries:
            entry = self._object(raw_entry, "GitHub tree entry")
            path = self._string(entry.get("path"), "GitHub tree entry path")
            entries[path] = entry
        files: list[BaseFile] = []
        for path in paths:
            selected_entry = entries.get(path)
            if selected_entry is None:
                files.append(BaseFile(path, None, "100644"))
                continue
            mode = selected_entry.get("mode")
            if selected_entry.get("type") != "blob" or mode not in {"100644", "100755"}:
                raise ValueError("GitHub patch path is not a regular text file.")
            blob_sha = self._string(selected_entry.get("sha"), "GitHub blob")
            blob = self._object(
                self._get(f"{repository_path}/git/blobs/{blob_sha}"),
                "GitHub blob response",
            )
            files.append(
                BaseFile(
                    path,
                    self._base64_content(blob, "GitHub blob response"),
                    cast(Literal["100644", "100755"], mode),
                )
            )
        return tuple(files)

    def branch_head(self, repository: str, branch: str) -> str | None:
        encoded_branch = urllib.parse.quote(branch, safe="")
        try:
            document = self._get(
                f"{self._repository_path(repository)}/git/ref/heads/{encoded_branch}"
            )
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise
        reference = self._object(document, "GitHub branch response")
        reference_object = self._object(
            reference.get("object"), "GitHub branch object"
        )
        return self._string(reference_object.get("sha"), "GitHub branch commit")

    def create_commit(
        self,
        repository: str,
        base: BaseRevision,
        files: tuple[CommitFile, ...],
        message: str,
    ) -> str:
        if not files:
            raise ValueError("A GitHub commit requires at least one file.")
        repository_path = self._repository_path(repository)
        tree_entries: list[dict[str, str]] = []
        for file in files:
            blob = self._object(
                self._post(
                    f"{repository_path}/git/blobs",
                    {
                        "content": base64.b64encode(file.content).decode(),
                        "encoding": "base64",
                    },
                ),
                "GitHub blob response",
            )
            tree_entries.append(
                {
                    "mode": file.mode,
                    "path": file.path,
                    "sha": self._string(blob.get("sha"), "GitHub blob"),
                    "type": "blob",
                }
            )
        tree = self._object(
            self._post(
                f"{repository_path}/git/trees",
                {"base_tree": base.tree_sha, "tree": tree_entries},
            ),
            "GitHub tree response",
        )
        commit = self._object(
            self._post(
                f"{repository_path}/git/commits",
                {
                    "message": message,
                    "parents": [base.commit_sha],
                    "tree": self._string(tree.get("sha"), "GitHub tree"),
                },
            ),
            "GitHub commit response",
        )
        return self._string(commit.get("sha"), "GitHub commit")

    def create_branch(self, repository: str, branch: str, commit_sha: str) -> None:
        self._post(
            f"{self._repository_path(repository)}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": commit_sha},
        )

    def create_draft_pull_request(
        self,
        repository: str,
        *,
        base_branch: str,
        head_branch: str,
        title: str,
        body: str,
    ) -> DraftPullRequest:
        pull_request = self._object(
            self._post(
                f"{self._repository_path(repository)}/pulls",
                {
                    "base": base_branch,
                    "body": body,
                    "draft": True,
                    "head": head_branch,
                    "title": title,
                },
            ),
            "GitHub pull request response",
        )
        if pull_request.get("draft") is not True:
            raise ValueError("GitHub did not create a Draft pull request.")
        number = pull_request.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise ValueError("GitHub pull request number is invalid.")
        return DraftPullRequest(
            number=number,
            url=self._string(pull_request.get("html_url"), "GitHub pull request URL"),
        )

    def delete_branch(self, repository: str, branch: str) -> None:
        encoded_branch = urllib.parse.quote(branch, safe="")
        self._request(
            "DELETE",
            f"{self._repository_path(repository)}/git/refs/heads/{encoded_branch}",
        )

    def _get(self, path: str) -> object:
        return self._request("GET", path)

    def _post(self, path: str, document: object) -> object:
        return self._request("POST", path, document)

    def _request(
        self, method: str, path: str, document: object | None = None
    ) -> object:
        data = None
        if document is not None:
            data = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        request = urllib.request.Request(
            self._api_url + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
        return json.loads(content) if content else {}

    @staticmethod
    def _repository_path(repository: str) -> str:
        return f"/repos/{urllib.parse.quote(repository, safe='/')}"

    @staticmethod
    def _object(value: object, label: str) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object.")
        return cast(dict[str, object], value)

    @staticmethod
    def _string(value: object, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} must be a non-empty string.")
        return value

    @classmethod
    def _base64_content(cls, document: dict[str, object], label: str) -> bytes:
        if document.get("encoding") != "base64":
            raise ValueError(f"{label} has an unsupported encoding.")
        encoded = cls._string(document.get("content"), f"{label} content")
        try:
            return base64.b64decode("".join(encoded.split()), validate=True)
        except ValueError as error:
            raise ValueError(f"{label} has invalid base64 content.") from error
