# PatchLoop

PatchLoop is a bounded Issue-to-Draft-PR agent that repository maintainers run
inside their own GitHub Actions account. A maintainer authorizes one Issue with
the `patchloop` label; PatchLoop freezes the task, lets Gemini propose changes
through six controlled tools, verifies the candidate in Docker, and publishes an
honestly labelled Draft PR or actionable Issue feedback.

PatchLoop v0 is a reusable GitHub workflow plus a Python 3.12 CLI. It is not a
hosted service, GitHub App, arbitrary-shell agent, automatic merger, or strong
sandbox for hostile code.

## Requirements

- A target repository with GitHub Actions enabled.
- A maintainer with `write` or `admin` permission to authorize runs.
- A Gemini Developer API key stored as the target repository secret
  `PATCHLOOP_GEMINI_API_KEY`.
- A Linux GitHub-hosted runner with Docker, or a self-hosted runner whose owner
  accepts the additional risks described in [Security](docs/security.md).
- An explicit `.patchloop.yml`; PatchLoop does not detect a repository's
  language, dependency setup, or checks.

No personal access token, GitHub App token, Vertex AI project, service account,
or PyPI package is required.

## Install in a target repository

1. Copy [the minimal caller](examples/patchloop-caller.yml) to
   `.github/workflows/patchloop.yml` in the target repository.
2. Copy [the complete configuration example](examples/patchloop.yml) to
   `.patchloop.yml` and replace the image and commands with ones appropriate for
   the target repository.
3. Add the Actions secret `PATCHLOOP_GEMINI_API_KEY`. Never commit the key to
   `.patchloop.yml`, `.env`, an Issue, a fixture, or a command line.
4. Create the `patchloop` label. Add it to a sufficiently specific Issue only
   after a `write` or `admin` maintainer has reviewed the task and trusted
   maintainer comments.

The caller grants `contents: write`, `issues: write`, and
`pull-requests: write`; the reusable workflow reduces those permissions per
job. It passes only the named Gemini secret, never `secrets: inherit`.

The released example uses
`echo-DM/patch-loop/.github/workflows/patchloop-reusable.yml@v0.1.0`. A release
tag is created only after live acceptance. Before that release, replace the ref
with a reviewed commit SHA. For the strongest supply-chain pin, keep using the
full immutable SHA; do not install from `main` or another moving branch.

## Repository configuration

`.patchloop.yml` is versioned and required. This is the complete schema:

```yaml
version: 1
model: gemma-4-31b-it
verifier:
  image: ghcr.io/astral-sh/uv:python3.12-bookworm-slim
  setup:
    - uv sync --frozen
  checks:
    - uv run mypy
    - uv run pytest
  limits:
    timeout_seconds: 1800
    memory_mb: 4096
    pids: 512
    output_bytes: 1000000
budgets:
  max_iterations: 3
  max_tool_calls: 60
  max_changed_files: 20
  max_diff_lines: 2000
  max_wall_time_minutes: 30
```

| Field | Meaning and accepted range |
| --- | --- |
| `version` | Must be `1`. Unknown versions fail before model execution. |
| `model` | Gemini Developer API model identifier. Defaults to `gemma-4-31b-it` when omitted. |
| `verifier.image` | Explicit Docker image used for every setup/check container. |
| `verifier.setup` | One or more predeclared setup commands. |
| `verifier.checks` | One or more predeclared verification commands. |
| `verifier.limits.timeout_seconds` | Per-command timeout, `1..1800`. |
| `verifier.limits.memory_mb` | Container memory, `6..4096`. |
| `verifier.limits.pids` | Container process limit, `1..512`. |
| `verifier.limits.output_bytes` | Captured output limit, `1..1000000`. |
| `budgets.max_iterations` | Edit-and-verify iterations, `1..3`. |
| `budgets.max_tool_calls` | Controlled tool calls, `1..60`. |
| `budgets.max_changed_files` | Changed files, `1..20`. |
| `budgets.max_diff_lines` | Unified-diff lines, `1..2000`. |
| `budgets.max_wall_time_minutes` | Whole-run wall clock, `1..30`. |

Repositories may lower any verifier limit or budget, but cannot raise the fixed
ceilings. Commands are selected by maintainers before a run; Gemini cannot add
shell commands or increase authority.

## What happens after labelling an Issue

1. **Gate** verifies the `patchloop` label and the triggering actor's effective
   permission before Gemini is called. `write` and `admin` authorize; lower or
   unknown permissions do not.
2. **Gate** freezes the Issue title/body and eligible comments. Comments from
   `write`/`admin` maintainers can supplement requirements. Other comments,
   Issue text, repository files, diffs, and check output remain low-trust data;
   none can change tools, credentials, budgets, or publication policy.
3. **Prepare** inspects the durable Issue branch and PR lifecycle. It continues
   one active Draft PR, stops merged or closed-unmerged work, and reports
   conflicts rather than rebasing or rewriting history.
4. **Agent** has repository read access and the Gemini secret, but no GitHub
   write permission. Gemini can only list files, search code, read files, apply
   bounded patches, inspect the diff, and request configured checks.
5. **Verifier** runs each setup/check in a disposable repository copy and a
   constrained Docker container without the Gemini key or GitHub write token.
   Setup/check side effects are discarded.
6. **Publish** verifies the manifest, patch identity, Run Report, and publication
   intent. It has GitHub write permission but no Gemini secret and never executes
   target code. It creates or fast-forwards `patchloop/issue-<number>` with an
   ordinary commit and creates or updates one Draft PR.

One concurrency group per repository and Issue prevents overlapping runs. The
workflow uses no remote LangGraph checkpoint; cancellation or runner loss starts
a later run from durable GitHub branch/PR state.

## Outcomes and review state

Terminal outcome and verification status are separate:

| Terminal outcome | Verification | GitHub result |
| --- | --- | --- |
| `pr_created` | `checks_passed` | Draft PR with passing PatchLoop evidence. |
| `pr_created` | `checks_failed` | Draft PR that prominently reports failed checks. |
| `pr_created` | `budget_exhausted` | Draft PR containing the last legal patch and unresolved limits. |
| `needs_clarification` | `not_run` | Bounded questions in Issue feedback; no branch or PR. |
| `no_change` | `not_run` | Explanation in Issue feedback; no branch or PR. |
| `failed` | setup, infrastructure, model, authorization, configuration, or budget failure | Actionable feedback/summary; no empty PR. |

PatchLoop never marks a PR ready, approves it, merges it, force-pushes, rebases,
or resolves merge conflicts. A closed-unmerged PatchLoop PR blocks silent
recreation; a merged PR ends automated work for that Issue.

PRs created with a repository `GITHUB_TOKEN` may not automatically start all of
the target repository's native workflows, or those workflows may require a
maintainer's approval. PatchLoop checks are bounded evidence for the generated
patch, not a replacement for the repository's complete CI, branch protection,
security review, or human review.

## Local reproduction

From a PatchLoop source checkout:

```bash
uv sync --locked
cp examples/patchloop.yml /path/to/target/.patchloop.yml
uv run patchloop run \
  --task examples/local-task.json \
  --repository /path/to/target
```

The default CLI mode is credential-free and only accepts an explicit no-change
task. It is useful for checking task/config parsing, terminal reporting, and the
installed command without contacting Gemini.

To reproduce the real Agent path locally, use a disposable target checkout,
configure its `.patchloop.yml`, export the dedicated key, and opt in:

```bash
export PATCHLOOP_GEMINI_API_KEY='set this outside the repository'
uv run patchloop run \
  --live \
  --task /path/to/task.json \
  --repository /path/to/disposable-target
```

`--live` uses Gemini and Docker and may consume quota or modify the supplied
workspace. It still performs no GitHub writes. Both CLI modes and the reusable
workflow call the public `patchloop.core.run` interface, load the same
`.patchloop.yml` schema, and emit the same version-1 Run Report contract; only
their injected task/model/verifier/GitHub adapters differ.

After `v0.1.0` exists, the CLI can also be installed from the pinned source tag:

```bash
uv tool install git+https://github.com/echo-DM/patch-loop.git@v0.1.0
```

## Validation and live smoke

The complete credential-free validation is one command:

```bash
uv run python scripts/validate_offline.py
```

It requires a running Docker daemon and a preloaded `alpine:3.22` fixture image,
then checks the lock, strict typing, the complete pytest suite (including real
Docker acceptance, workflow contracts, and credential-redaction scenarios),
wheel/sdist builds, and whitespace. It never calls Gemini or writes to GitHub.

Live smoke is separate, explicit, and release-gated:

- [Gemini adapter smoke](tests/smoke/gemini_smoke.py) requires both
  `PATCHLOOP_RUN_GEMINI_SMOKE=1` and `PATCHLOOP_GEMINI_API_KEY`.
- [Dedicated GitHub smoke caller](examples/patchloop-smoke-caller.yml) must be
  installed in a separate private target such as `echo-DM/patch-loop-smoke`,
  with both placeholder refs replaced by the same candidate commit SHA.
- [GitHub state inspection](tests/smoke/github_smoke.py) runs after the reusable
  workflow with the caller repository's automatic `GITHUB_TOKEN`; it verifies
  that the expected Draft PR exists and never receives the Gemini secret.
- Missing enable flags or credentials safely skip the standalone smoke tests.
  No live smoke belongs to default pytest or default CI.

See [Gemini integration](docs/gemini.md), [Security](docs/security.md), and the
[54-story coverage matrix](docs/user-story-coverage.md). Actual release
acceptance and creation of `v0.1.0` are tracked separately by
[Ticket 14](.scratch/patchloop-mvp/issues/14-release-v0.1.0-with-live-smoke.md).
