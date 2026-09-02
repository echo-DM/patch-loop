from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import cast

import pytest
import yaml

import patchloop.workflow as workflow_module
from patchloop.adapters import (
    DeterministicPatchModelAdapter,
    DeterministicVerifierAdapter,
    EvaluationDecision,
    ModelTurn,
    RepositoryPermission,
    ToolCall,
    VerificationResult,
)
from patchloop.workflow import (
    ArtifactIntegrityError,
    main as workflow_main,
    run_agent,
    run_gate,
    run_prepare,
    verify_artifact,
)
from patchloop.github_publish import PullRequestState


ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "patchloop-reusable.yml"
CALLER = ROOT / "examples" / "patchloop-caller.yml"


class FixtureGitHubClient:
    def __init__(self, permission: RepositoryPermission) -> None:
        self.permission = permission

    def permission_for(
        self, repository: str, username: str
    ) -> RepositoryPermission | None:
        assert repository == "octo-org/example"
        assert username == "maintainer"
        return self.permission

    def issue_comments(
        self, repository: str, issue_number: int
    ) -> list[dict[str, object]]:
        assert repository == "octo-org/example"
        assert issue_number == 42
        return []


class FixturePublicationClient:
    def __init__(self, pull_request: PullRequestState | None, head: str | None) -> None:
        self.pull_request = pull_request
        self.head = head
        self.comments: list[str] = []

    def pull_request_for_branch(
        self, repository: str, branch: str
    ) -> PullRequestState | None:
        assert repository == "octo-org/example"
        assert branch == "patchloop/issue-42"
        return self.pull_request

    def branch_head(self, repository: str, branch: str) -> str | None:
        assert repository == "octo-org/example"
        assert branch == "patchloop/issue-42"
        return self.head

    def create_issue_comment(
        self, repository: str, issue_number: int, body: str
    ) -> None:
        assert repository == "octo-org/example"
        assert issue_number == 42
        self.comments.append(body)


def load_workflow(path: Path) -> dict[str, object]:
    return cast(dict[str, object], yaml.load(path.read_text(), Loader=yaml.BaseLoader))


def test_reusable_workflow_declares_explicit_inputs_secret_and_job_boundaries() -> None:
    workflow = load_workflow(WORKFLOW)
    workflow_call = cast(
        dict[str, object], cast(dict[str, object], workflow["on"])["workflow_call"]
    )
    inputs = cast(dict[str, object], workflow_call["inputs"])
    secrets = cast(dict[str, object], workflow_call["secrets"])

    assert set(inputs) == {"config_path"}
    assert cast(dict[str, str], inputs["config_path"])["default"] == ".patchloop.yml"
    assert set(secrets) == {"gemini_api_key"}
    assert cast(dict[str, str], secrets["gemini_api_key"])["required"] == "true"

    assert workflow["permissions"] == {"contents": "read"}
    jobs = cast(dict[str, object], workflow["jobs"])
    assert workflow["concurrency"] == {
        "group": "patchloop-${{ github.repository }}-${{ github.event.issue.number }}",
        "cancel-in-progress": "false",
    }
    assert set(jobs) == {"gate", "prepare", "agent", "publish"}
    assert cast(dict[str, object], jobs["gate"])["permissions"] == {
        "contents": "read",
        "issues": "read",
    }
    gate_steps = cast(
        list[dict[str, object]], cast(dict[str, object], jobs["gate"])["steps"]
    )
    gate_step = next(
        step for step in gate_steps if step.get("name") == "Authorize and freeze Issue"
    )
    assert gate_step["env"] == {"GITHUB_TOKEN": "${{ github.token }}"}
    assert '--event "$GITHUB_EVENT_PATH"' in str(gate_step["run"])
    assert cast(dict[str, object], jobs["agent"])["permissions"] == {
        "contents": "read"
    }
    assert cast(dict[str, object], jobs["prepare"])["permissions"] == {
        "contents": "read",
        "issues": "write",
        "pull-requests": "read",
    }
    assert cast(dict[str, object], jobs["publish"])["permissions"] == {
        "contents": "write",
        "issues": "write",
        "pull-requests": "write",
    }
    assert cast(dict[str, object], jobs["agent"])["if"] == (
        "needs.prepare.outputs.proceed == 'true'"
    )
    assert cast(dict[str, object], jobs["agent"])["needs"] == ["gate", "prepare"]
    assert cast(dict[str, object], jobs["publish"])["if"] == (
        "needs.agent.result == 'success'"
    )


def test_secret_and_artifacts_follow_only_the_declared_job_path() -> None:
    raw_workflow = WORKFLOW.read_text()
    workflow = load_workflow(WORKFLOW)
    jobs = cast(dict[str, object], workflow["jobs"])
    gate = cast(dict[str, object], jobs["gate"])
    prepare = cast(dict[str, object], jobs["prepare"])
    agent = cast(dict[str, object], jobs["agent"])
    publish = cast(dict[str, object], jobs["publish"])

    assert "secrets: inherit" not in raw_workflow
    assert raw_workflow.count("${{ secrets.gemini_api_key }}") == 1
    assert "checkpoint" not in raw_workflow.lower()
    assert raw_workflow.count("${{ job.workflow_repository }}") == 4
    assert raw_workflow.count("${{ job.workflow_sha }}") == 4
    assert "github.workflow_ref" not in raw_workflow
    assert raw_workflow.count("pull-requests: write") == 1
    assert raw_workflow.count("issues: write") == 2
    assert raw_workflow.count("contents: write") == 1

    gate_steps = cast(list[dict[str, object]], gate["steps"])
    prepare_steps = cast(list[dict[str, object]], prepare["steps"])
    agent_steps = cast(list[dict[str, object]], agent["steps"])
    publish_steps = cast(list[dict[str, object]], publish["steps"])
    assert any(
        step.get("uses") == "actions/upload-artifact@v4" for step in gate_steps
    )
    prepare_text = json.dumps(prepare, sort_keys=True)
    assert "patchloop.workflow prepare" in prepare_text
    assert "PATCHLOOP_GEMINI_API_KEY" not in prepare_text
    assert "github.token" in prepare_text
    assert any(
        step.get("uses") == "actions/download-artifact@v4" for step in agent_steps
    )
    assert any(
        step.get("uses") == "actions/upload-artifact@v4" for step in agent_steps
    )
    assert any(
        step.get("uses") == "actions/download-artifact@v4" for step in publish_steps
    )

    publish_text = json.dumps(publish, sort_keys=True)
    assert "Check out target repository" not in publish_text
    assert "PATCHLOOP_GEMINI_API_KEY" not in publish_text
    assert "Docker" not in publish_text
    assert ".patchloop.yml" not in publish_text
    assert "patchloop.workflow publish" in publish_text
    assert "github.token" in publish_text
    assert "github.repository" in publish_text
    assert "github.event.issue.number" in publish_text
    assert "github.event.repository.default_branch" in publish_text
    assert "git push" not in publish_text
    assert "run_checks" not in publish_text


def test_prepare_selects_the_active_pr_branch_before_the_agent(tmp_path: Path) -> None:
    client = FixturePublicationClient(
        PullRequestState(
            number=7,
            url="https://example.test/pull/7",
            state="open",
            merged=False,
            draft=True,
            base_branch="main",
            head_branch="patchloop/issue-42",
            head_sha="active-sha",
            mergeable=True,
        ),
        "active-sha",
    )
    github_output = tmp_path / "github-output"

    plan = run_prepare(
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=client,
        github_output=github_output,
    )

    assert plan.action == "update"
    assert github_output.read_text() == (
        "proceed=true\ncheckout_ref=patchloop/issue-42\n"
    )
    assert client.comments == []


def test_prepare_stops_closed_pr_before_the_agent_and_reports_it(
    tmp_path: Path,
) -> None:
    client = FixturePublicationClient(
        PullRequestState(
            number=7,
            url="https://example.test/pull/7",
            state="closed",
            merged=False,
            draft=True,
            base_branch="main",
            head_branch="patchloop/issue-42",
            head_sha="active-sha",
            mergeable=False,
        ),
        None,
    )
    github_output = tmp_path / "github-output"

    plan = run_prepare(
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=client,
        github_output=github_output,
    )

    assert plan.action == "blocked"
    assert github_output.read_text() == "proceed=false\ncheckout_ref=\n"
    assert "explicit restart" in client.comments[0]


def test_minimal_caller_filters_the_label_and_serializes_runs_per_issue() -> None:
    caller = load_workflow(CALLER)
    trigger = cast(dict[str, object], cast(dict[str, object], caller["on"])["issues"])
    assert trigger == {"types": ["labeled"]}
    assert caller["concurrency"] == {
        "group": "patchloop-${{ github.repository }}-${{ github.event.issue.number }}",
        "cancel-in-progress": "false",
    }
    assert caller["permissions"] == {
        "contents": "write",
        "issues": "write",
        "pull-requests": "write",
    }

    jobs = cast(dict[str, object], caller["jobs"])
    call = cast(dict[str, object], jobs["patchloop"])
    assert call["if"] == "github.event.label.name == 'patchloop'"
    assert call["uses"] == (
        "echo-DM/patch-loop/.github/workflows/patchloop-reusable.yml@v0.1.0"
    )
    assert call["with"] == {"config_path": ".patchloop.yml"}
    assert call["secrets"] == {
        "gemini_api_key": "${{ secrets.PATCHLOOP_GEMINI_API_KEY }}"
    }


def test_gate_freezes_only_an_authorized_issue_for_the_agent(tmp_path: Path) -> None:
    event = cast(
        dict[str, object],
        json.loads(ROOT.joinpath("tests/fixtures/github/labeled_issue.json").read_text()),
    )
    output = tmp_path / "gate"
    github_output = tmp_path / "github-output"

    authorized = run_gate(
        event=event,
        client=FixtureGitHubClient("write"),
        output=output,
        github_output=github_output,
    )

    assert authorized is True
    assert github_output.read_text() == "authorized=true\n"
    task = cast(dict[str, object], json.loads(output.joinpath("task.json").read_text()))
    assert task["task_version"] == "1"
    assert task["id"] == "github:octo-org/example#42"
    assert task["authorized_by"] == "maintainer"
    assert "sender" not in task
    context = cast(
        dict[str, object],
        json.loads(output.joinpath("publication-context.json").read_text()),
    )
    assert context == {
        "base_branch": "main",
        "context_version": "1",
        "issue_number": 42,
        "repository": "octo-org/example",
        "task_id": "github:octo-org/example#42",
    }
    assert output.joinpath("manifest.json").is_file()


def test_gate_cli_reads_the_runner_event_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_path = ROOT / "tests" / "fixtures" / "github" / "labeled_issue.json"
    output = tmp_path / "gate"
    github_output = tmp_path / "github-output"
    monkeypatch.setattr(
        workflow_module,
        "GitHubApiClient",
        lambda **_kwargs: FixtureGitHubClient("write"),
    )

    exit_code = workflow_main(
        [
            "gate",
            "--event",
            str(event_path),
            "--output",
            str(output),
            "--github-output",
            str(github_output),
        ]
    )

    assert exit_code == 0
    assert github_output.read_text() == "authorized=true\n"
    task = json.loads(output.joinpath("task.json").read_text())
    assert task["id"] == "github:octo-org/example#42"


def test_gate_cli_rejects_an_unreadable_runner_event_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_event = tmp_path / "missing-event.json"

    exit_code = workflow_main(
        [
            "gate",
            "--event",
            str(missing_event),
            "--output",
            str(tmp_path / "gate"),
            "--github-output",
            str(tmp_path / "github-output"),
        ]
    )

    assert exit_code == 1
    assert capsys.readouterr().err == "The GitHub event file is not readable.\n"


def test_gate_redacts_credential_shaped_issue_content_before_artifact_transfer(
    tmp_path: Path,
) -> None:
    event = cast(
        dict[str, object],
        json.loads(ROOT.joinpath("tests/fixtures/github/labeled_issue.json").read_text()),
    )
    cast(dict[str, object], event["issue"])["body"] = (
        "Use AIzaSyCredentialShapedIssueText123456789."
    )

    run_gate(
        event=event,
        client=FixtureGitHubClient("write"),
        output=tmp_path / "gate",
        github_output=tmp_path / "github-output",
    )

    assert "AIzaSyCredential" not in (tmp_path / "gate" / "task.json").read_text()


def test_gate_rejection_cannot_start_the_agent_and_has_a_safe_summary(
    tmp_path: Path,
) -> None:
    event = cast(
        dict[str, object],
        json.loads(ROOT.joinpath("tests/fixtures/github/labeled_issue.json").read_text()),
    )
    output = tmp_path / "gate"
    github_output = tmp_path / "github-output"
    summary = tmp_path / "summary.md"

    authorized = run_gate(
        event=event,
        client=FixtureGitHubClient("read"),
        output=output,
        github_output=github_output,
        summary=summary,
    )

    assert authorized is False
    assert github_output.read_text() == "authorized=false\n"
    assert not output.joinpath("task.json").exists()
    assert "insufficient_permission" in output.joinpath("gate-report.json").read_text()
    assert "failed" in summary.read_text()
    assert "labeled_issue" not in summary.read_text()


def test_agent_emits_an_integrity_protected_patch_report_and_intent(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    event = cast(
        dict[str, object],
        json.loads(ROOT.joinpath("tests/fixtures/github/labeled_issue.json").read_text()),
    )
    gate = tmp_path / "gate"
    run_gate(
        event=event,
        client=FixtureGitHubClient("write"),
        output=gate,
        github_output=tmp_path / "github-output",
    )
    task = gate / "task.json"
    output = tmp_path / "result"
    model = DeterministicPatchModelAdapter(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "edit",
                        "apply_patch",
                        {"path": "README.md", "content": "Hello from workflow.\n"},
                    ),
                    ToolCall("checks", "run_checks", {}),
                )
            )
        ]
    )

    run_agent(
        task_path=task,
        repository=repository,
        config_path=".patchloop.yml",
        output=output,
        model=model,
        verifier=DeterministicVerifierAdapter(
            VerificationResult.passed(("check greeting",))
        ),
    )

    verified = verify_artifact(output)
    assert set(verified) == {
        "patch.diff",
        "publication-context.json",
        "publication-intent.json",
        "run-report.json",
    }
    report = cast(dict[str, object], verified["run-report.json"])
    assert report["task_id"] == "github:octo-org/example#42"
    assert report["terminal_outcome"] == "pr_created"
    assert cast(dict[str, object], report["verification"])["status"] == (
        "checks_passed"
    )
    assert cast(dict[str, object], verified["publication-intent.json"])["intent"] == (
        "draft_pr"
    )
    assert cast(str, verified["patch.diff"]).startswith("--- a/README.md")

    summary = tmp_path / "summary.md"
    verify_artifact(output, summary=summary)
    summary_text = summary.read_text()
    assert "checks_passed" in summary_text
    assert "Tool calls" in summary_text
    assert "Change the greeting" not in summary_text


def test_artifact_verification_rejects_tampering_and_undeclared_files(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    task = tmp_path / "task.json"
    task.write_text(
        """{
  "task_version": "1",
  "id": "issue-42",
  "title": "No change",
  "body": "No patch is needed.",
  "authorized_by": "maintainer",
  "supplemental_requirements": [],
  "reference_material": []
}
"""
    )
    output = tmp_path / "result"
    model = DeterministicPatchModelAdapter(
        [
            ModelTurn.decide(
                EvaluationDecision(
                    terminal_outcome="no_change",
                    summary="No change is required.",
                    actionable_message="Do not publish a pull request.",
                )
            )
        ]
    )
    run_agent(
        task_path=task,
        repository=repository,
        config_path=".patchloop.yml",
        output=output,
        model=model,
        verifier=DeterministicVerifierAdapter(
            VerificationResult.passed(("check greeting",))
        ),
    )
    (output / "run-report.json").write_text("{}\n")

    with pytest.raises(ArtifactIntegrityError):
        verify_artifact(output)


def test_artifact_verification_rejects_conflicting_publication_intents(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    task = tmp_path / "task.json"
    task.write_text(
        """{
  "task_version": "1",
  "id": "issue-42",
  "title": "No change",
  "body": "No patch is needed.",
  "authorized_by": "maintainer",
  "supplemental_requirements": [],
  "reference_material": []
}
"""
    )
    output = tmp_path / "result"
    run_agent(
        task_path=task,
        repository=repository,
        config_path=".patchloop.yml",
        output=output,
        model=DeterministicPatchModelAdapter(
            [
                ModelTurn.decide(
                    EvaluationDecision(
                        terminal_outcome="no_change",
                        summary="No change is required.",
                        actionable_message="Do not publish a pull request.",
                    )
                )
            ]
        ),
        verifier=DeterministicVerifierAdapter(
            VerificationResult.passed(("check greeting",))
        ),
    )
    intent_path = output / "publication-intent.json"
    intent_path.write_text('{"intent":"none","reason":"failed"}\n')
    manifest_path = output / "manifest.json"
    manifest = cast(dict[str, object], json.loads(manifest_path.read_text()))
    files = cast(dict[str, object], manifest["files"])
    intent_bytes = intent_path.read_bytes()
    files["publication-intent.json"] = {
        "byte_length": len(intent_bytes),
        "sha256": hashlib.sha256(intent_bytes).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    with pytest.raises(ArtifactIntegrityError):
        verify_artifact(output)


def test_agent_setup_failure_still_emits_a_protected_terminal_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = configured_repository(tmp_path)
    repository.joinpath(".patchloop.yml").write_text(
        repository.joinpath(".patchloop.yml")
        .read_text()
        .replace("max_tool_calls: 60", "max_tool_calls: 7")
    )
    task = tmp_path / "task.json"
    task.write_text(
        """{
  "task_version": "1",
  "id": "issue-42",
  "title": "Update greeting",
  "body": "Change the greeting.",
  "authorized_by": "maintainer",
  "supplemental_requirements": [],
  "reference_material": []
}
"""
    )
    output = tmp_path / "result"
    monkeypatch.delenv("PATCHLOOP_GEMINI_API_KEY", raising=False)

    exit_code = workflow_main(
        [
            "agent",
            "--task",
            str(task),
            "--repository",
            str(repository),
            "--config",
            ".patchloop.yml",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    verified = verify_artifact(output, summary=tmp_path / "summary.md")
    report = cast(dict[str, object], verified["run-report.json"])
    assert report["terminal_outcome"] == "failed"
    assert report["task_id"] == "issue-42"
    assert cast(dict[str, object], report["model"])["name"] == "fixture-model"
    assert cast(dict[str, object], report["verification"])["status"] == "not_run"
    budgets = cast(dict[str, object], report["budgets"])
    assert cast(dict[str, object], budgets["limits"])["max_tool_calls"] == 7
    assert "gemini_api_key_missing" in output.joinpath("run-report.json").read_text()
    summary = tmp_path.joinpath("summary.md").read_text()
    assert "failed" in summary
    assert "not_run" in summary
    assert "Tool calls" in summary

    output.joinpath("run-report.json").unlink()
    output.joinpath("undeclared.txt").write_text("unexpected")
    with pytest.raises(ArtifactIntegrityError):
        verify_artifact(output)


def test_authorized_config_failure_preserves_publication_context(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    repository.joinpath(".patchloop.yml").write_text("version: 99\n")
    event = cast(
        dict[str, object],
        json.loads(ROOT.joinpath("tests/fixtures/github/labeled_issue.json").read_text()),
    )
    gate = tmp_path / "gate"
    run_gate(
        event=event,
        client=FixtureGitHubClient("write"),
        output=gate,
        github_output=tmp_path / "github-output",
    )
    output = tmp_path / "result"

    exit_code = workflow_main(
        [
            "agent",
            "--task",
            str(gate / "task.json"),
            "--repository",
            str(repository),
            "--config",
            ".patchloop.yml",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    verified = verify_artifact(output)
    assert cast(dict[str, object], verified["publication-context.json"])[
        "task_id"
    ] == "github:octo-org/example#42"
    report = cast(dict[str, object], verified["run-report.json"])
    assert cast(list[dict[str, object]], report["errors"])[0]["code"] == (
        "unsupported_config_version"
    )


def configured_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    repository.joinpath("README.md").write_text("Hello, world!\n")
    repository.joinpath(".patchloop.yml").write_text(
        """\
version: 1
model: fixture-model
verifier:
  image: fixture
  setup:
    - prepare fixture
  checks:
    - check greeting
  limits:
    timeout_seconds: 300
    memory_mb: 512
    pids: 64
    output_bytes: 65536
budgets:
  max_iterations: 3
  max_tool_calls: 60
  max_changed_files: 20
  max_diff_lines: 2000
  max_wall_time_minutes: 30
"""
    )
    return repository
