# PatchLoop security boundary

PatchLoop separates task authorization, model access, target-code execution,
and GitHub publication. These controls reduce accidental authority and secret
exposure; they do not turn GitHub Actions or Docker into a hostile-code sandbox.

## Trust and authorization

Adding the `patchloop` label is the v0 execution approval. Gate accepts it only
when the triggering actor's effective repository permission is `write` or
`admin`, and freezes the Issue before Gemini is called. The authorized Issue
title/body and frozen `write`/`admin` comments define requirements. External
comments, repository content, source excerpts, diffs, and check output are
low-trust data: they may inform a patch but cannot grant tools, secrets, network
access, budgets, or publication rights.

Gate reads the triggering webhook payload from the GitHub runner's
`GITHUB_EVENT_PATH`. The caller does not serialize the event as a reusable
workflow input, because GitHub expands workflow inputs in job setup logs and
would otherwise disclose the complete Issue task there.

The model has six controlled proposals: list files, search code, read a file,
apply a patch, inspect the diff, and request configured checks. PatchLoop
validates every proposal. There is no model-controlled shell, Git, GitHub API,
web search, URL context, code execution, computer use, or remote MCP.

## Credential and permission separation

| Stage | GitHub permission | Gemini key | Executes target commands |
| --- | --- | --- | --- |
| Gate | Read contents and Issues | No | No |
| Prepare | Read contents/PRs; write Issue feedback | No | No |
| Agent | Read contents | Yes | Only through Verifier |
| Verifier container | None | No | Yes, declared setup/checks only |
| Publish | Write contents, Issues, and PRs | No | No |

The caller passes one named secret, `PATCHLOOP_GEMINI_API_KEY`. PatchLoop does
not read `GOOGLE_API_KEY`, `GEMINI_API_KEY`, Google Cloud credentials, target
repository `.env` files, PATs, or GitHub App tokens as model credentials.
Secrets are excluded from LangGraph state, artifacts, Run Reports, summaries,
Issue feedback, and PR bodies. Credential-shaped output is redacted and bounded,
but maintainers must still avoid placing secrets in task text or repositories.

## Docker verifier boundary

Each declared setup/check runs in a fresh container against a disposable copy of
the candidate repository. The authoritative patch workspace is separate, so
command side effects do not become published changes. `.git`, `.env`, PatchLoop
private state, the Gemini key, and the GitHub write token are excluded.

Containers receive time, memory, PID, capability, privilege-escalation, and
captured-output constraints. PatchLoop bounds Docker lifecycle operations and
cleans up labelled containers after completion or failure.

This is an MVP constraint mechanism, not a security boundary against malicious
code with a container escape, kernel exploit, Docker daemon exploit, runner
compromise, side-channel, or dependency-supply-chain attack. The Docker host and
GitHub runner remain trusted infrastructure.

## Self-hosted runners

PatchLoop's v0 guarantees are written for disposable GitHub-hosted Ubuntu
runners. A self-hosted runner can retain files, caches, credentials, Docker
state, network access, and host-level side effects across jobs. PatchLoop does
not harden, attest, reset, or monitor that host. Operators who choose self-hosted
runners own host isolation, egress policy, daemon security, cleanup, patching,
and incident response; untrusted repositories should not share a privileged
self-hosted runner.

## Publication and native CI

Publish consumes only manifest-declared, hash- and length-verified payloads. It
revalidates task identity, base branch, patch content, report, and publication
intent before GitHub writes, and never executes target code. It uses ordinary
commits and non-force ref updates.

GitHub may suppress or require approval for repository-native workflows caused
by a PR created with `GITHUB_TOKEN`. PatchLoop intentionally does not add a PAT
or GitHub App credential to bypass that behavior. A maintainer must inspect the
Draft PR and run/approve the repository's normal CI as required. Passing
PatchLoop checks does not mean the full repository CI, security review, branch
protection, or human review has passed.

## Explicit non-guarantees

PatchLoop v0 does not provide strong hostile-code sandboxing, automatic merge,
automatic approval, branch-protection bypass, force-push, automatic rebase,
conflict resolution, closed-PR recreation, multi-tenant isolation, remote
checkpoints, provider choice beyond Gemini Developer API, or safe execution on
native Windows/macOS runners.
