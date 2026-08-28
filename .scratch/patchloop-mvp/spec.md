# PatchLoop MVP

Status: ready-for-agent

## Problem Statement

开源项目维护者经常收到描述明确、理论上可以直接实现的 GitHub Issue，但把 Issue 转化为可审查的 Pull Request 仍需要重复完成代码检索、修改、测试、失败诊断、再次修改和结果说明。现有通用编码 Agent 往往需要维护者手动启动，或者拥有过大的 Shell、仓库和凭据权限；它们也很难在 GitHub Actions 中同时满足可安装、可复现、成本有上限和不向目标仓库代码暴露模型密钥等要求。

维护者需要一个开源、可安装到自己仓库的 Agent：由有代码写权限的维护者明确授权后，读取 GitHub Issue 和仓库配置，在受限预算内生成并验证 patch，最后创建一个诚实呈现验证结果的 Draft PR。Agent 必须把 Issue 和仓库内容视为低信任任务数据，限制模型能够执行的操作，隔离 Gemini 凭据与目标仓库命令，并在不能安全完成时给出可操作的结果，而不是假装成功。

## Solution

PatchLoop 提供一个可复用的 GitHub Actions workflow，以及一个用 Python、LangGraph 和 `uv` 构建的核心 CLI。目标仓库监听 Issue 的 `patchloop` 标签事件，并把事件交给可复用 workflow。Workflow 首先验证触发者是否拥有仓库写权限并冻结本次 Issue 快照，然后运行 PatchLoop 的受控“探索—修改—验证”循环，最后由独立的发布阶段创建或更新 Draft PR。

PatchLoop 使用 Gemini Developer API 进行推理和受控工具调用。模型只能列出文件、搜索代码、读取文件、应用 patch、查看 diff 和请求执行仓库预先声明的 checks；模型没有任意 Shell、GitHub 写入、Gemini 内置代码执行、Web 搜索、URL Context 或 Remote MCP 权限。仓库提供 `.patchloop.yml`，声明验证容器、setup、checks 和可降低的执行预算。

目标仓库命令运行在不含 Gemini 密钥和 GitHub 写 Token 的 Docker verifier 中。Agent 阶段产生 patch bundle、检查证据和脱敏 Run Report，Publish 阶段只应用产物、推送受控分支并创建或更新 Draft PR，不执行目标仓库代码。只要产生有效 patch，即使 checks 仍失败或预算耗尽，也会创建 Draft PR 并如实标记状态；信息不足、没有修改必要、授权失败或基础设施失败时不创建空 PR，而是在 Issue 或 workflow summary 中给出明确结果。

## User Stories

1. As a repository maintainer, I want to trigger PatchLoop by adding a `patchloop` label, so that running the Agent is an explicit authorization rather than an automatic response to every Issue.
2. As a repository maintainer, I want only users with code write permission to authorize a run, so that triage-only and external users cannot spend model budget or initiate repository changes.
3. As a repository maintainer, I want PatchLoop to freeze the Issue snapshot at run start, so that requirements cannot change unnoticed during execution.
4. As a repository maintainer, I want the Issue title and body to become the task specification after authorization, so that the Agent works from the problem I approved.
5. As a repository maintainer, I want comments from write-authorized users to supplement requirements, so that maintainers can clarify an Issue before triggering the run.
6. As a repository maintainer, I want external comments classified as reference material rather than authoritative instructions, so that community evidence remains useful without changing security policy or task scope.
7. As a repository maintainer, I want unauthorized triggers rejected before Gemini is called, so that rejected attempts consume no model quota.
8. As a repository maintainer, I want one active run per Issue, so that concurrent events cannot race to modify the same branch.
9. As a repository maintainer, I want one active PatchLoop branch and Draft PR per Issue, so that repeated runs do not create duplicate PRs.
10. As a repository maintainer, I want repeated runs to append normal commits to the existing PatchLoop branch, so that the evolution of the Agent's work remains reviewable.
11. As a repository maintainer, I want PatchLoop to avoid force-pushing or rewriting history, so that reviewers do not lose existing commit context.
12. As a repository maintainer, I want merge conflicts reported rather than automatically rebased or resolved, so that Git history changes remain under human control.
13. As a repository maintainer, I want a closed-unmerged PatchLoop PR to block silent recreation, so that a human decision to close work is respected.
14. As a repository maintainer, I want a merged PatchLoop PR to mark the Issue's automated work complete, so that the Agent does not reopen completed work.
15. As a repository maintainer, I want to install PatchLoop through a small caller workflow, so that the repository does not duplicate its multi-job orchestration.
16. As a repository maintainer, I want the reusable workflow to run in my repository's GitHub Actions context, so that billing, source access and permissions remain under my account.
17. As a repository maintainer, I want PatchLoop to use the repository-provided `GITHUB_TOKEN`, so that installation does not require a personal access token or GitHub App credential.
18. As a repository maintainer, I want the Agent, verifier and publisher to have different permissions, so that no stage possesses both untrusted code execution and repository write access.
19. As a repository maintainer, I want the Gemini API key to exist only in the Agent stage, so that setup and test scripts cannot read or exfiltrate it.
20. As a repository maintainer, I want secrets excluded from LangGraph state, checkpoints, artifacts, logs and PR text, so that diagnostics cannot become a credential leak.
21. As a repository maintainer, I want model credentials supplied through a named GitHub Secret, so that they are not committed to repository configuration.
22. As a repository maintainer, I want to select the Gemini model explicitly, so that cost and capability choices remain visible and reproducible.
23. As a repository maintainer, I want PatchLoop to use Gemini Developer API only in v0, so that installation avoids Vertex AI projects, service accounts and cloud-specific configuration.
24. As a repository maintainer, I want the Agent to search and read only relevant files on demand, so that large repositories do not require an embedding index or full-repository prompt.
25. As a repository maintainer, I want the model limited to controlled code tools, so that it cannot invent arbitrary shell commands, network calls or Git operations.
26. As a repository maintainer, I want verification commands declared before the run, so that the model cannot expand its own execution authority.
27. As a repository maintainer, I want arbitrary implementation languages supported through an explicit verifier image and commands, so that PatchLoop's Python implementation does not restrict target repositories to Python.
28. As a repository maintainer, I want setup and checks executed in an isolated temporary repository copy, so that test side effects cannot alter the patch that will be published.
29. As a repository maintainer, I want verifier containers to have time, memory, process and log limits, so that hostile or broken checks cannot consume unbounded runner resources.
30. As a repository maintainer, I want the Agent to use check failures as feedback for another edit, so that it can repair its own mistakes within a bounded loop.
31. As a repository maintainer, I want a default maximum of three edit-and-verify iterations, so that a difficult Issue cannot loop indefinitely.
32. As a repository maintainer, I want limits on tool calls, changed files, diff lines and wall-clock time, so that model cost and review size are bounded.
33. As a repository maintainer, I want repository configuration to lower budgets but not exceed PatchLoop's safety caps, so that local preferences cannot silently disable global guardrails.
34. As a repository maintainer, I want a valid patch published even when checks fail, so that I can inspect and continue useful partial work in a Draft PR.
35. As a repository maintainer, I want budget exhaustion represented explicitly, so that a partial Draft PR is never confused with completed work.
36. As a repository maintainer, I want missing or ambiguous Issue information to produce specific clarification questions rather than guessed requirements, so that the Agent does not solve a different problem.
37. As a repository maintainer, I want no-change outcomes explained without creating an empty branch or PR, so that the repository stays free of meaningless automation artifacts.
38. As a repository maintainer, I want configuration and infrastructure failures distinguished from code-check failures, so that I know whether to fix the repository, the workflow or the patch.
39. As a pull request reviewer, I want the Draft PR body to summarize intent, changed areas, checks, iteration count, limits reached and unresolved failures, so that I can evaluate the patch without reading workflow logs first.
40. As a pull request reviewer, I want the report to describe observable actions and results rather than hidden model reasoning, so that review evidence is concrete and auditable.
41. As a pull request reviewer, I want failed checks clearly visible in the PR, so that automation never presents an unverified patch as ready.
42. As a pull request reviewer, I want the PR linked to its source Issue, so that requirements and implementation remain traceable.
43. As a project contributor, I want PatchLoop to leave actionable Issue comments when it cannot create a PR, so that I know what information or configuration is missing.
44. As a project contributor, I want external Issue text treated as low-trust task data, so that prompt-like content cannot override tool, budget, credential or publishing policy.
45. As a PatchLoop developer, I want one black-box execution interface, so that most behavior can be tested without asserting LangGraph node structure.
46. As a PatchLoop developer, I want model, verifier and GitHub behavior provided through adapters, so that default tests remain deterministic and offline.
47. As a PatchLoop developer, I want fixture repositories to exercise real patching and container checks, so that tests validate user-visible behavior rather than mocks alone.
48. As a PatchLoop developer, I want live Gemini and live GitHub tests to be manual and explicit, so that default CI remains credential-free, deterministic and cost-free.
49. As a PatchLoop operator, I want a sanitized Run Report for every terminal outcome, so that failures can be diagnosed without retaining raw prompts or repository content unnecessarily.
50. As a PatchLoop operator, I want an interrupted workflow rerun to start cleanly from the current repository state, so that v0 needs no external checkpoint store.
51. As a security reviewer, I want Publish to consume only a declared patch bundle and report, so that it never executes code produced by the Agent.
52. As a security reviewer, I want privileged and unprivileged stages visible as separate workflow jobs, so that permissions can be inspected in workflow logs and configuration.
53. As an open-source adopter, I want the core available as a local CLI, so that I can debug configuration and behavior without first publishing a GitHub event.
54. As an open-source adopter, I want the same core interface used locally and by GitHub Actions, so that local reproduction matches automation behavior.

## Implementation Decisions

- PatchLoop v0 is a single-context Python project managed with `uv`; Node-related repository work uses `pnpm` where applicable.
- The product consists of a reusable GitHub workflow as the primary installation interface and a Python CLI as the core execution and local-debugging interface. A Composite Action is not part of v0.
- The workflow contains separate Gate, Agent and Publish modules. Permissions are granted by the caller and reduced per job; the called workflow cannot depend on elevating caller permissions.
- The Gate module accepts the triggering GitHub event, verifies the `patchloop` label, resolves the triggering actor's effective repository permission and freezes the Issue snapshot. Effective `write` and `admin` authorize execution; roles mapped below write do not.
- The authority model treats the authorized Issue title and body as task requirements. Comments from write-authorized users may supplement requirements. Other comments are isolated as low-trust evidence and cannot change system policy, tool authority, budgets or publication behavior.
- The primary execution module is a deep module exposed through one black-box `run` interface. Its input includes the immutable task snapshot, repository workspace, validated repository configuration and injected adapters. Its output is a terminal outcome, patch bundle, verification evidence, sanitized Run Report and publication intent.
- LangGraph implements the internal task state machine and bounded edit-and-verify loop. Callers and acceptance tests depend on the execution interface and terminal outcomes, not graph node names or topology.
- Terminal outcomes are `pr_created`, `needs_clarification`, `no_change` and `failed`. A created PR also reports whether checks passed, checks failed or execution budget was exhausted. Authorization, configuration, infrastructure and model failures remain distinguishable.
- The controlled code-tool interface contains file listing, code search, file reading, patch application, diff inspection and a request to run configured checks. It exposes no general Shell interface.
- Git operations, GitHub writes and publication metadata are trusted application behavior, never model-selected tools. Branch names, Issue numbers, PR identity and base branch are validated structured state.
- The repository configuration is versioned and explicit. It declares the Gemini model, verifier container image, setup commands, check commands and configurable execution budgets. At least one check is required for a successful-check outcome.
- PatchLoop supports target repositories in any programming language through repository-supplied container and command configuration; it does not perform zero-configuration language or package-manager detection in v0.
- Default budgets are three edit-and-verify iterations, sixty controlled tool calls, twenty changed files, two thousand diff lines and thirty minutes wall-clock time. Repository configuration may reduce these values; PatchLoop enforces non-overridable safety ceilings.
- Patch and path validation prevents writes outside the checked-out workspace, changes to PatchLoop workflow authority, unsafe path traversal and publication of changes exceeding configured limits.
- Gemini integration uses a model-client interface with one v0 adapter backed by `ChatGoogleGenerativeAI` and Gemini Developer API. The adapter receives the API key explicitly and explicitly selects the non-Vertex backend.
- The GitHub caller passes a secret dedicated to the Gemini adapter. Ambient Google credentials, repository `.env` files and implicit Vertex configuration are not model credential sources.
- Gemini built-in Code Execution, Google Search, URL Context and Remote MCP are disabled. Gemini function calls are proposals; PatchLoop validates and executes only registered controlled tools.
- LangGraph state and reports contain structured task and execution data but exclude credentials. Raw hidden reasoning is neither requested nor persisted. Source excerpts and tool results are bounded and sanitized before logging.
- The Agent module has repository read access and the Gemini secret but no repository write permission. It may inspect source and construct a patch, but target setup and check commands execute only through the verifier.
- The Verifier module runs on Ubuntu using Docker. It receives a disposable repository copy and no Gemini key or GitHub write token. Setup may obtain dependencies according to repository configuration; checks run under explicit resource, duration and output limits.
- The repository copy used for verification is separate from the authoritative patch workspace. Mutations made by setup or checks are discarded and never silently included in the published patch.
- The Agent consumes normalized check status and bounded logs as feedback. It may revise the patch until checks pass or a budget is exhausted.
- The Agent module emits a content-addressed or otherwise integrity-checked patch bundle plus a sanitized report for the Publish module. Job-to-job transfer is explicit and contains no model secret.
- The Publish module owns repository content write, Issue write and Pull Request write permissions, but receives no Gemini secret and executes no target repository code. It validates the artifact before applying it.
- A valid non-empty patch creates a Draft PR even when checks fail or budget is exhausted. The PR body prominently reports verification status and unresolved problems.
- An ambiguous task produces targeted clarification questions and no PR. A no-change outcome explains why no patch is needed. Authorization, configuration or infrastructure failures produce actionable diagnostics without an empty PR.
- PatchLoop uses the caller repository's `GITHUB_TOKEN`; v0 does not require a PAT or GitHub App installation token. Documentation states that repository-native PR workflows may await maintainer approval and that PatchLoop checks are not a substitute for the repository's complete CI.
- One Issue maps to one active PatchLoop branch and Draft PR. Repeated runs continue from and append commits to the active branch. PatchLoop does not force-push, rewrite history, automatically rebase or automatically resolve merge conflicts.
- A merged PatchLoop PR ends automated work for the Issue. A closed-unmerged PR blocks silent recreation; restarting it requires an explicit future restart mechanism rather than an ordinary label event.
- One concurrency group per Issue prevents overlapping runs. The v0 state lifetime is one GitHub workflow run; cancellations, runner loss and timeouts restart from current durable Git/PR state rather than a remote LangGraph checkpoint.
- The Run Report records task identity, terminal outcome, changed-file summary, check commands and results, iteration/tool budgets, resource-limit events and actionable errors. It redacts secrets and avoids storing full model context or unnecessary repository contents.

## Testing Decisions

- The primary test seam is the black-box PatchLoop execution interface used by the CLI. Tests supply an Issue snapshot, actor permission, fixture repository, repository configuration and deterministic adapters, then assert the terminal result, patch bundle, verification evidence, report and publication intent.
- Good tests assert observable behavior: resulting file changes, check outcomes, budgets, reports, publication decisions and adapter requests. They do not assert prompt wording, private model reasoning, LangGraph node names, internal edge order or incidental tool-call ordering unless ordering is itself a public safety invariant.
- The execution module is covered through scenario-style acceptance tests for successful checks, failed checks with a Draft PR, budget exhaustion with a Draft PR, ambiguity without a PR, no-change behavior, authorization rejection and each configuration/infrastructure failure class.
- Authority tests cover write/admin authorization, lower-permission rejection, Issue snapshot immutability, trusted maintainer comments and isolation of external comments from policy decisions.
- Controlled-tool tests cover path traversal, writes outside the workspace, unknown tools, invalid arguments, oversized files, too many changed files, excessive diff lines and attempts to modify protected workflow authority.
- Budget tests cover iteration, tool-call, changed-file, diff-line, wall-clock and log-output ceilings, including proof that repository configuration can lower but not raise global safety caps.
- Verifier integration tests run real Docker checks against minimal fixture repositories. They prove that verifier processes receive no Gemini key or repository write credential and that verifier mutations do not alter the authoritative patch.
- Gemini adapter contract tests use simulated provider responses for ordinary text, single and multiple tool calls, malformed arguments, unknown tools, rate limits, timeouts and provider errors. Default tests never call Gemini.
- GitHub adapter contract tests use fixed event and API fixtures to verify permission lookup, Issue/comment normalization, branch identity, active-PR lookup, Draft PR creation/update, Issue comments and conflict/closed/merged behavior.
- Reusable-workflow tests validate syntax, caller inputs, named-secret flow, per-job permission reduction, concurrency grouping, artifact transfer and the absence of target-code execution in Publish.
- Report tests seed credential-shaped values and hostile Issue content, then prove that logs, artifacts, workflow summaries, comments and PR bodies remain sanitized and render external content as data.
- Idempotency tests prove that repeated execution for one Issue appends to one active branch/PR, while merged, closed-unmerged and conflicted states produce their specified outcomes.
- Manual smoke tests may call a real Gemini Developer API and operate on a dedicated GitHub test repository. They require explicit credentials and invocation and remain outside the default test suite and CI.
- The repository has no existing implementation or test prior art. The first tracer-bullet ticket establishes the black-box execution harness and fixture conventions; later tickets reuse that seam instead of creating module-specific acceptance surfaces.

## Out of Scope

- A hosted multi-tenant PatchLoop service or centrally operated GitHub App.
- Vertex AI, Google Cloud service accounts, Application Default Credentials and multi-provider model support.
- OpenAI-compatible endpoints, Anthropic, OpenAI or other model adapters.
- Composite Action packaging.
- Automatic execution when an Issue is opened, edited or commented on.
- Slash-command triggers through Issue comments.
- Allowing triage-only or external users to authorize runs.
- Automatic plan approval before editing; the authorized label is the v0 execution approval.
- Automatic merge, marking a Draft PR ready, approving reviews or bypassing branch protection.
- Personal access tokens or separate GitHub App tokens to force automatic downstream CI execution.
- Automatic rebase, force-push, history rewriting or merge-conflict resolution.
- Reopening or silently replacing a closed-unmerged PatchLoop PR.
- Cross-workflow LangGraph checkpoints, remote state stores and interrupted-run continuation.
- Vector databases, embeddings, repository indexing and RAG.
- Zero-configuration detection of repository language, dependency installation or check commands.
- Arbitrary model-controlled Shell, network, Git or GitHub commands.
- Gemini built-in Code Execution, Google Search, URL Context, Computer Use or Remote MCP.
- Strong sandbox claims against container escape, kernel vulnerabilities or hostile self-hosted runners.
- Native Windows or macOS verifier support.
- Treating PatchLoop checks as a complete replacement for repository CI.
- Default-CI calls to real Gemini or mutation of a live GitHub repository.

## Further Notes

- Security language should describe the Docker verifier as a constrained MVP isolation mechanism, not as a complete hostile-code sandbox.
- The public contract should distinguish terminal outcome from verification condition. For example, a Draft PR can be created while checks failed or budget was exhausted.
- Repository content, Issue text, comments, diffs and check output are all low-trust data. They may inform the task but cannot override authorization, credentials, tools, budgets or publication policy.
- The exact configuration schema and Run Report schema should be versioned from their first release so future changes can fail clearly instead of being interpreted ambiguously.
- Documentation should explain that `GITHUB_TOKEN`-created PRs may require a maintainer to approve the repository's native CI workflows.
- The next phase should split this spec into dependency-ordered tracer-bullet tickets, beginning with the black-box execution seam and preserving vertical behavior through configuration, controlled editing, verification and publication.
