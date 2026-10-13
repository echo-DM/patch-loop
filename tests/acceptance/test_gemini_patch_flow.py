from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from patchloop.adapters import (
    DeterministicVerifierAdapter,
    EvaluationTask,
    ToolDefinition,
    VerificationResult,
)
from patchloop.config import load_repository_config
from patchloop.core import RunAdapters, RunRequest, TaskSnapshot, run
from patchloop.errors import ConfigError
from patchloop.gemini import GeminiPatchModelAdapter


@dataclass
class FakeResponse:
    content: object = ""
    tool_calls: list[dict[str, object]] | None = None
    invalid_tool_calls: list[dict[str, object]] | None = None


class FakeGeminiClient:
    def __init__(self, responses: list[FakeResponse | BaseException]) -> None:
        self._responses = iter(responses)
        self.bound_tools: list[dict[str, object]] = []
        self.requests: list[list[object]] = []

    def bind_tools(self, tools: list[dict[str, object]]) -> FakeGeminiClient:
        self.bound_tools = tools
        return self

    def invoke(self, messages: list[object]) -> FakeResponse:
        self.requests.append(messages)
        response = next(self._responses)
        if isinstance(response, BaseException):
            raise response
        return response


class RateLimitError(Exception):
    status_code = 429


def test_gemini_tool_calls_drive_only_the_controlled_patch_flow(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient(
        [
            FakeResponse(
                tool_calls=[
                    {"id": "list", "name": "list_files", "args": {}},
                ]
            ),
            FakeResponse(
                tool_calls=[
                    {
                        "id": "read",
                        "name": "read_file",
                        "args": {"path": "README.md"},
                    },
                ]
            ),
            FakeResponse(
                tool_calls=[
                    {
                        "id": "edit",
                        "name": "apply_patch",
                        "args": {
                            "path": "README.md",
                            "content": "Hello from Gemini.\n",
                        },
                    },
                    {"id": "checks", "name": "run_checks", "args": {}},
                ]
            ),
        ]
    )

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "pr_created"
    assert repository.joinpath("README.md").read_text() == "Hello from Gemini.\n"
    assert result.report["model"] == {
        "provider": "google_gemini_developer_api",
        "name": "gemini-3.5-flash-lite",
    }
    assert [tool["function"]["name"] for tool in client.bound_tools] == [
        "list_files",
        "search_code",
        "read_file",
        "apply_patch",
        "inspect_diff",
        "run_checks",
    ]
    assert all(tool["type"] == "function" for tool in client.bound_tools)
    declarations = {
        tool["function"]["name"]: tool["function"] for tool in client.bound_tools
    }
    for declaration in declarations.values():
        assert "Example arguments:" in declaration["description"]
        assert declaration["parameters"]["additionalProperties"] is False
        for name, parameter in declaration["parameters"]["properties"].items():
            assert parameter["type"] == "string"
            assert parameter["description"]
            if name == "path":
                assert "Repository-relative" in parameter["description"]
                assert "POSIX" in parameter["description"]
    assert "case-sensitive literal substring" in declarations["search_code"]["description"]
    assert "1-based" in declarations["search_code"]["description"]
    for name in ("list_files", "search_code"):
        path_description = declarations[name]["parameters"]["properties"]["path"]["description"]
        assert "Defaults to '.'" in path_description
    patch_parameters = declarations["apply_patch"]["parameters"]
    assert patch_parameters["required"] == ["path", "content"]
    assert "complete final" in declarations["apply_patch"]["description"]
    assert "written verbatim" in patch_parameters["properties"]["content"]["description"]
    assert "not interpreted" in patch_parameters["properties"]["content"]["description"]
    for name in ("inspect_diff", "run_checks"):
        assert declarations[name]["parameters"]["properties"] == {}
        assert declarations[name]["parameters"]["required"] == []
    assert not any(
        forbidden in str(client.bound_tools).lower()
        for forbidden in (
            "code_execution",
            "google_search",
            "url_context",
            "computer_use",
            "remote_mcp",
        )
    )
    assert "AIzaSyDedicatedPatchLoopKey123456789" not in str(result.report)
    assert "AIzaSyDedicatedPatchLoopKey123456789" not in str(client.requests)


def test_legacy_tool_definition_can_still_be_bound_without_parameter_descriptions() -> None:
    definition = ToolDefinition("read_file", "Read a UTF-8 file.", ("path",), ())
    client = FakeGeminiClient([FakeResponse(content="Done.")])
    adapter = GeminiPatchModelAdapter(
        model_name="gemini-3.5-flash-lite",
        api_key="fixture-key",
        client=client,
    )

    adapter.next_turn(
        EvaluationTask("legacy-tool", "Read a file", "Read README.md."),
        (),
        (definition,),
    )

    assert definition.argument_descriptions == {}
    assert hash(definition) == hash(
        ToolDefinition("read_file", "Read a UTF-8 file.", ("path",), ())
    )
    assert client.bound_tools[0]["function"]["parameters"] == {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }


def test_invalid_and_unknown_gemini_calls_are_validated_by_patchloop(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient(
        [
            FakeResponse(
                tool_calls=[
                    {"id": "bad", "name": "read_file", "args": "README.md"},
                    {"id": "unknown", "name": "shell", "args": {"command": "id"}},
                    {"id": "fields", "name": "read_file", "args": {"offset": 10}},
                ]
            ),
            FakeResponse(
                tool_calls=[
                    {
                        "id": "corrected-read",
                        "name": "read_file",
                        "args": {"path": "README.md"},
                    },
                    {
                        "id": "edit",
                        "name": "apply_patch",
                        "args": {"path": "README.md", "content": "Safe content.\n"},
                    },
                    {"id": "checks", "name": "run_checks", "args": {}},
                ]
            ),
        ]
    )

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "pr_created"
    assert repository.joinpath("README.md").read_text() == "Safe content.\n"
    assert len(client.requests) == 2
    assert "invalid_arguments" in str(client.requests[1])
    assert "unknown_tool" in str(client.requests[1])
    feedback: dict[str, Any] = {}
    for message in client.requests[1]:
        if isinstance(message, ToolMessage):
            assert isinstance(message.content, str)
            feedback[message.tool_call_id] = json.loads(message.content)
    message = feedback["fields"]["error"]["message"]
    assert feedback["fields"]["error"]["code"] == "invalid_arguments"
    assert 'Missing required fields: ["path"]' in message
    assert 'Unexpected fields: ["offset"]' in message
    assert "Supply missing required fields and remove unexpected fields" in message
    assert result.report["budgets"]["usage"]["tool_calls"] == 6


@pytest.mark.parametrize(
    ("provider_error", "expected_code"),
    [
        (TimeoutError("AIzaSySecretTimeout123456789"), "provider_timeout"),
        (RateLimitError("AIzaSySecretRateLimit123456789"), "provider_rate_limited"),
        (RuntimeError("AIzaSySecretProvider123456789"), "provider_error"),
    ],
)
def test_provider_failures_are_stable_and_redacted(
    tmp_path: Path,
    provider_error: BaseException,
    expected_code: str,
) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient([provider_error, provider_error])

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "failed"
    assert result.report["errors"] == [
        {
            "category": "model",
            "code": expected_code,
            "message": {
                "provider_timeout": "The Gemini request timed out.",
                "provider_rate_limited": "The Gemini request was rate limited.",
                "provider_error": "The Gemini provider could not complete the request.",
            }[expected_code],
        }
    ]
    assert len(client.requests) == 2
    assert "AIzaSySecret" not in str(result.report)


def test_initial_provider_failure_retries_the_pending_turn(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient(
        [
            RuntimeError("AIzaSySecretInitialProvider123456789"),
            FakeResponse(
                tool_calls=[
                    {
                        "id": "edit",
                        "name": "apply_patch",
                        "args": {
                            "path": "README.md",
                            "content": "Recovered initial request.\n",
                        },
                    },
                    {"id": "checks", "name": "run_checks", "args": {}},
                ]
            ),
        ]
    )

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "pr_created"
    assert repository.joinpath("README.md").read_text() == (
        "Recovered initial request.\n"
    )
    assert result.report["verification"]["status"] == "checks_passed"
    assert len(client.requests) == 2
    assert len(client.requests[0]) == len(client.requests[1])
    assert "AIzaSySecret" not in str(result.report)
    assert "AIzaSySecret" not in str(client.requests)


def test_provider_failure_after_edit_retries_the_pending_turn(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient(
        [
            FakeResponse(
                tool_calls=[
                    {
                        "id": "edit",
                        "name": "apply_patch",
                        "args": {
                            "path": "README.md",
                            "content": "Recovered after provider failure.\n",
                        },
                    },
                ]
            ),
            RuntimeError("AIzaSySecretTransientProvider123456789"),
            FakeResponse(
                tool_calls=[
                    {"id": "checks", "name": "run_checks", "args": {}},
                ]
            ),
        ]
    )

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "pr_created"
    assert repository.joinpath("README.md").read_text() == (
        "Recovered after provider failure.\n"
    )
    assert result.report["verification"]["status"] == "checks_passed"
    assert result.report["errors"] == []
    assert len(client.requests) == 3
    assert len(client.requests[1]) == len(client.requests[2])
    assert "AIzaSySecret" not in str(result.report)
    assert "AIzaSySecret" not in str(client.requests)


def test_repeated_provider_failure_after_edit_preserves_the_provider_error(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient(
        [
            FakeResponse(
                tool_calls=[
                    {
                        "id": "edit",
                        "name": "apply_patch",
                        "args": {
                            "path": "README.md",
                            "content": "Unverified provider recovery.\n",
                        },
                    },
                ]
            ),
            RuntimeError("AIzaSySecretFirstProvider123456789"),
            RuntimeError("AIzaSySecretSecondProvider123456789"),
        ]
    )

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.report["errors"] == [
        {
            "category": "model",
            "code": "provider_error",
            "message": "The Gemini provider could not complete the request.",
        }
    ]
    assert repository.joinpath("README.md").read_text() == (
        "Unverified provider recovery.\n"
    )
    assert len(client.requests) == 3
    assert len(client.requests[1]) == len(client.requests[2])
    assert "decision_after_edit" not in str(result.report)
    assert "AIzaSySecret" not in str(result.report)
    assert "AIzaSySecret" not in str(client.requests)


def test_plain_text_is_a_stable_completion_without_persisting_raw_context(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    raw_text = "Done. " + ("x" * 2_000)
    client = FakeGeminiClient([FakeResponse(content=raw_text)])

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "failed"
    assert result.report["errors"][0]["code"] == "empty_patch"
    assert raw_text not in str(result.report)


def test_dedicated_environment_secret_explicitly_selects_developer_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dedicated_key = "AIzaSyDedicatedPatchLoopKey123456789"
    monkeypatch.setenv("PATCHLOOP_GEMINI_API_KEY", dedicated_key)
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSyAmbientGoogleKey123456789")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyAmbientGeminiKey123456789")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    captured: dict[str, Any] = {}

    def client_factory(**kwargs: object) -> FakeGeminiClient:
        captured.update(kwargs)
        return FakeGeminiClient([])

    adapter = GeminiPatchModelAdapter.from_environment(
        "gemini-3.5-flash-lite", client_factory=client_factory
    )

    assert adapter.model_name == "gemini-3.5-flash-lite"
    assert captured == {
        "model": "gemini-3.5-flash-lite",
        "api_key": dedicated_key,
        "vertexai": False,
        "retries": 0,
        "request_timeout": 60.0,
    }


def test_ambient_google_credentials_do_not_replace_the_dedicated_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PATCHLOOP_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSyAmbientGoogleKey123456789")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyAmbientGeminiKey123456789")

    with pytest.raises(ConfigError) as error:
        GeminiPatchModelAdapter.from_environment("gemini-3.5-flash-lite")

    assert error.value.code == "gemini_api_key_missing"
    assert "AIzaSyAmbient" not in error.value.message


def test_configured_model_must_match_the_model_that_will_execute(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient([FakeResponse(content="This must not execute.")])

    with pytest.raises(ConfigError) as error:
        run_gemini(repository, client, model_name="different-model")

    assert error.value.code == "model_config_mismatch"
    assert client.requests == []


def run_gemini(
    repository: Path,
    client: FakeGeminiClient,
    *,
    model_name: str = "gemini-3.5-flash-lite",
):
    return run(
        RunRequest(
            task=TaskSnapshot("issue-108", "Update greeting", "Change the README."),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(
                model=GeminiPatchModelAdapter(
                    model_name=model_name,
                    api_key="AIzaSyDedicatedPatchLoopKey123456789",
                    client=client,
                ),
                verifier=DeterministicVerifierAdapter(
                    VerificationResult.passed(("check greeting",))
                ),
            ),
        )
    )


def configured_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "README.md").write_text("Hello, world!\n")
    (repository / ".patchloop.yml").write_text(
        """\
version: 1
model: gemini-3.5-flash-lite
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
