# Gemini adapter

PatchLoop v0 uses `ChatGoogleGenerativeAI` with the Gemini Developer API. The
adapter explicitly sets `vertexai=False`; Vertex AI, Application Default
Credentials, Google Search, URL Context, Code Execution, Computer Use, and
Remote MCP are not enabled.

## Configuration

The repository may choose a model in `.patchloop.yml`:

```yaml
version: 1
model: gemma-4-31b-it
```

If `model` is omitted, PatchLoop uses `gemma-4-31b-it`. Model identifiers are
validated before execution and the selected non-sensitive identifier is stored
in the Run Report.

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
retries; timeouts, rate limits, and other provider failures receive stable error
codes without persisting the provider exception text.

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
