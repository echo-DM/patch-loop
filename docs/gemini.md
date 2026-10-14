# Gemini adapter

PatchLoop v0 uses `ChatGoogleGenerativeAI` with the Gemini Developer API. The
adapter explicitly sets `vertexai=False`; Vertex AI, Application Default
Credentials, Google Search, URL Context, Code Execution, Computer Use, and
Remote MCP are not enabled.

## Configuration

The repository may choose a model in `.patchloop.yml`:

```yaml
version: 1
model: gemini-3.5-flash-lite
```

The example model above completed real GitHub Actions/Gemini end-to-end runs in
`patchloop-sorting-lab`. Configure a model explicitly because provider
availability can change. If `model` is omitted, PatchLoop uses
`gemini-3.5-flash-lite`. Model identifiers are validated before
execution and the selected non-sensitive identifier is stored in the Run Report.

The adapter reads only the dedicated environment variable below:

```bash
export PATCHLOOP_GEMINI_API_KEY='your Gemini Developer API key'
```

`GOOGLE_API_KEY`, `GEMINI_API_KEY`, Google Cloud credentials, and repository
`.env` files are not credential sources. Callers construct the adapter from the
validated configuration and inject it at the public execution seam:

```python
from patchloop import GeminiPatchModelAdapter, RunAdapters

model = GeminiPatchModelAdapter.from_environment(config.model)
adapters = RunAdapters(model=model, verifier=verifier)
```

Gemini receives only the six structured PatchLoop declarations: `list_files`,
`search_code`, `read_file`, `apply_patch`, `inspect_diff`, and `run_checks`.
Function calls are proposals; PatchLoop validates names, arguments, paths,
budgets, and patch policy before executing them. API keys and complete provider
message history are not written to LangGraph state, logs, artifacts, or reports.
Provider calls use a 60-second request timeout and do not perform hidden SDK
retries. The PatchLoop harness currently allows one immediate additional request
across the entire run for a recognized transient provider failure, within the
existing tool and wall-time budgets. It retries the pending turn, including after
edits, without replaying completed tools or duplicating pending observations.
Multiple retries, backoff, and a timeout based on remaining run time are later
tickets in the [provider retry specification](../.scratch/provider-retry-policy/spec.md).

## Provider failures

The adapter normalizes provider failures to fixed codes and messages; raw
exception text, response bodies, headers, URLs, and quota identifiers are not
added to model context, reports, or logs. It inspects at most eight linked
exceptions, respecting explicit causes and suppressed contexts. Recognized
HTTP status codes take precedence over transport exception types; names or
message substrings alone do not establish retry eligibility.

| Failure | Stable code | Retry eligibility |
| --- | --- | --- |
| Recognized timeout or HTTP 408 | `provider_timeout` | Yes |
| Recognized connection/network failure | `provider_connection_failed` | Yes |
| HTTP 429 or a recognized SDK rate-limit exception without confirmed daily exhaustion | `provider_rate_limited` | Yes |
| HTTP 500/502/503/504 | `provider_unavailable` | Yes |
| HTTP 400 or recognized invalid-request exception | `provider_invalid_request` | No |
| HTTP 401 or recognized authentication exception | `provider_authentication_failed` | No |
| HTTP 403 or recognized permission exception | `provider_permission_denied` | No |
| HTTP 404 or recognized model-not-found exception | `provider_model_not_found` | No |
| Confirmed daily quota exhaustion | `provider_daily_quota_exhausted` | No |
| Other HTTP errors or unclassified exceptions | `provider_error` | No |

Daily quota exhaustion requires a structured
`type.googleapis.com/google.rpc.QuotaFailure` violation in a rate-limit failure.
The adapter recognizes the exact `GenerateRequestsPerDayPerProjectPerModel` and
`GenerateContentInputTokensPerModelPerDay` quota IDs, each with or without the
`-FreeTier` suffix, or the metric
`generativelanguage.googleapis.com/generate_requests_per_model_per_day`.
It examines at most 32 detail entries and 32 violations per entry, with quota
identifiers bounded to 256 characters. Malformed, unknown, or out-of-bound
details do not confirm daily exhaustion. A 429, `RESOURCE_EXHAUSTED`, or a message
about a daily limit alone is insufficient. The daily-exhaustion message directs
the user to check the project's quota in AI Studio and rerun after it resets.

Failures that cannot be retried stop immediately. If the additional request
also fails, the report records the last normalized failure. Provider failures
retain category `model` and end with `failed`, no publication intent, and no
returned patch, even after local edits. They do not enter the budget-exhaustion
branch that can preserve a partial patch for publication.

## Tool semantics and argument recovery

The model-visible tool declarations include usage examples, return-value
descriptions, and descriptions for every parameter. Paths use repository-relative
POSIX separators, such as `src/main.py`; absolute paths and `..` components are
rejected. The optional `path` on `list_files` and `search_code` defaults to `.`.
`search_code` performs case-sensitive literal substring matching, not regex
matching, and returns file paths, 1-based line numbers, and matching text.

`apply_patch` keeps its existing name and `path`/`content` parameters. It creates
or replaces one UTF-8 file with the **complete final file content**, including
unchanged content that should remain. The supplied string is written verbatim;
unified diff hunks are not interpreted. Read an existing file with `read_file`
first, submit its complete modified content, inspect the resulting diff with
`inspect_diff`, and request verification with `run_checks`.

For example, these are valid `apply_patch` arguments for a new `greeting.py` file:

```json
{"path": "greeting.py", "content": "def greet():\n    return \"Hello\"\n"}
```

`inspect_diff` and `run_checks` take an empty argument object (`{}`). Check
commands come from the repository configuration; model calls cannot supply
custom commands. Passing checks for a non-empty current patch finish the
controlled patch loop; ordinary check failures can guide repair within the
remaining budget.

Argument validation retains the `invalid_arguments` error code. When fields
are missing or unexpected, its message names the affected fields, lists required
and optional fields, and explains how to correct the call. For example,
`read_file` called with `{"offset": 10}` reports missing `path` and unexpected
`offset`. Retry with `{"path": "src/main.py"}` and a fresh call ID. Parameter
values are excluded from argument-validation errors, credential-shaped field
names are redacted, and messages exceeding 1,000 characters end with
`[message truncated]`.

Parameter descriptions are optional metadata on `ToolDefinition`; existing
definitions without that metadata remain valid. See the
[controlled tools implementation](../src/patchloop/controlled_tools.py) for the
authoritative tool behavior.

## Tests

Default tests use simulated provider responses and never call Gemini:

```bash
uv run pytest
```

The live smoke test is outside pytest's default filename pattern. It is invoked
explicitly and may consume Gemini quota:

```bash
PATCHLOOP_RUN_GEMINI_SMOKE=1 \
  uv run pytest tests/smoke/gemini_smoke.py
```

The smoke test also requires `PATCHLOOP_GEMINI_API_KEY`. Do not put the key in
`.patchloop.yml`, `.env`, test fixtures, or command-line arguments.

If the enable flag or dedicated key is absent, the smoke test skips without
calling the provider. The complete GitHub smoke uses a private Smoke repository:
copy `examples/patchloop-smoke-caller.yml`, copy
`examples/patchloop-smoke.yml` as `.patchloop.yml`, replace both candidate
placeholders with the same reviewed commit SHA, set
`PATCHLOOP_RUN_LIVE_SMOKE=1` as a repository variable, and configure
`PATCHLOOP_GEMINI_API_KEY` as an Actions secret. The versioned fixture quotes
its full `grep` command so the colon in the expected README line remains a YAML
string. Without both readiness signals, preflight skips the credentialed jobs.
The inspection Job uses only the automatic `GITHUB_TOKEN` and never receives the
Gemini key. See the root README and Ticket 14 for the release gate.
