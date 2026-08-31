from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
import yaml

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
    run_agent,
    run_gate,
    verify_artifact,
)


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


def load_workflow(path: Path) -> dict[str, object]:
    return cast(dict[str, object], yaml.load(path.read_text(), Loader=yaml.BaseLoader))


def test_reusable_workflow_declares_explicit_inputs_secret_and_job_boundaries() -> None:
    workflow = load_workflow(WORKFLOW)
    workflow_call = cast(
        dict[str, object], cast(dict[str, object], workflow["on"])["workflow_call"]
    )
    inputs = cast(dict[str, object], workflow_call["inputs"])
    secrets = cast(dict[str, object], workflow_call["secrets"])

    assert set(inputs) == {"event_json", "config_path"}
    assert cast(dict[str, str], inputs["event_json"])["required"] == "true"
    assert cast(dict[str, str], inputs["config_path"])["default"] == ".patchloop.yml"
    assert set(secrets) == {"gemini_api_key"}
    assert cast(dict[str, str], secrets["gemini_api_key"])["required"] == "true"

    assert workflow["permissions"] == {"contents": "read"}
    jobs = cast(dict[str, object], workflow["jobs"])
    assert set(jobs) == {"gate", "agent", "publish"}
    assert cast(dict[str, object], jobs["gate"])["permissions"] == {
        "contents": "read",
        "issues": "read",
    }
    assert cast(dict[str, object], jobs["agent"])["permissions"] == {
        "contents": "read"
    }
    assert cast(dict[str, object], jobs["publish"])["permissions"] == {
        "contents": "read"
    }
    assert cast(dict[str, object], jobs["agent"])["if"] == (
        "needs.gate.outputs.authorized == 'true'"
    )
    assert cast(dict[str, object], jobs["publish"])["if"] == (
        "needs.agent.result == 'success'"
    )


def test_secret_and_artifacts_follow_only_the_declared_job_path() -> None:
    raw_workflow = WORKFLOW.read_text()
    workflow = load_workflow(WORKFLOW)
    jobs = cast(dict[str, object], workflow["jobs"])
    gate = cast(dict[str, object], jobs["gate"])
    agent = cast(dict[str, object], jobs["agent"])
    publish = cast(dict[str, object], jobs["publish"])

    assert "secrets: inherit" not in raw_workflow
    assert raw_workflow.count("${{ secrets.gemini_api_key }}") == 1
    assert "checkpoint" not in raw_workflow.lower()
    assert raw_workflow.count("${{ job.workflow_repository }}") == 3
    assert raw_workflow.count("${{ job.workflow_sha }}") == 3
    assert "github.workflow_ref" not in raw_workflow
    assert "pull-requests: write" not in raw_workflow
    assert "issues: write" not in raw_workflow
    assert "contents: write" not in raw_workflow

    gate_steps = cast(list[dict[str, object]], gate["steps"])
    agent_steps = cast(list[dict[str, object]], agent["steps"])
    publish_steps = cast(list[dict[str, object]], publish["steps"])
    assert any(
        step.get("uses") == "actions/upload-artifact@v4" for step in gate_steps
    )
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


def test_minimal_caller_filters_the_label_and_serializes_runs_per_issue() -> None:
    caller = load_workflow(CALLER)
    trigger = cast(dict[str, object], cast(dict[str, object], caller["on"])["issues"])
    assert trigger == {"types": ["labeled"]}
    assert caller["concurrency"] == {
        "group": "patchloop-${{ github.repository }}-${{ github.event.issue.number }}",
        "cancel-in-progress": "false",
    }

    jobs = cast(dict[str, object], caller["jobs"])
    call = cast(dict[str, object], jobs["patchloop"])
    assert call["if"] == "github.event.label.name == 'patchloop'"
    assert call["uses"] == "OWNER/patchloop/.github/workflows/patchloop-reusable.yml@v1"
    assert call["with"] == {
        "event_json": "${{ toJson(github.event) }}",
        "config_path": ".patchloop.yml",
    }
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
    assert output.joinpath("manifest.json").is_file()


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
    task = tmp_path / "task.json"
    task.write_text(
        """{
  "task_version": "1",
  "id": "github:octo-org/example#42",
  "title": "Update greeting",
  "body": "Change the greeting.",
  "authorized_by": "maintainer",
  "supplemental_requirements": [],
  "reference_material": []
}
"""
    )
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

    output.joinpath("run-report.json").unlink()
    output.joinpath("undeclared.txt").write_text("unexpected")
    with pytest.raises(ArtifactIntegrityError):
        verify_artifact(output)


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
