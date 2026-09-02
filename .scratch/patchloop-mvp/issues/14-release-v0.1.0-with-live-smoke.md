# 14 — 发布 v0.1.0 并完成真实 smoke 验收

**What to build:** 把 Ticket 13 产出的候选 commit 作为不可变 SHA 安装到专用私有
仓库 `echo-DM/patch-loop-smoke`，显式运行一次真实 Gemini 与 GitHub Actions
happy-path smoke。只有 smoke 通过后，才在同一个候选 SHA 上创建公开的 `v0.1.0`
tag；失败时保留可诊断证据且不发布版本。

**Blocked by:** 13 — 交付可安装的 PatchLoop MVP

**Status:** resolved

- [x] 在公开的 `echo-DM/patch-loop` 仓库确定已经通过 Ticket 13 离线验收的候选
  commit SHA，并在 smoke 完成前不创建 `v0.1.0` tag。
- [x] 创建或复用私有 `echo-DM/patch-loop-smoke` 仓库；该仓库具有最小 fixture
  项目、启用的 GitHub Actions、caller workflow 和版本化 `.patchloop.yml`，不把
  PatchLoop 源码仓库作为自身测试目标。
- [x] smoke caller 使用候选 commit SHA 固定
  `echo-DM/patch-loop/.github/workflows/patchloop-reusable.yml`，不使用 branch、尚未
  验证的 tag 或 `secrets: inherit`。
- [x] 维护者自行把真实 Gemini Developer API key 配置为 smoke 仓库的 GitHub
  Actions Secret `PATCHLOOP_GEMINI_API_KEY`；密钥值不进入 ticket、聊天、Git、fixture、
  `.env`、命令行参数、日志或验收记录。
- [x] smoke 仓库只使用 caller repository 自动提供的 `GITHUB_TOKEN`，不创建 PAT、
  GitHub App token 或其他长期 GitHub 凭据，并授予 workflow 文档声明的最小 Job
  permissions。
- [x] 由具有 smoke 仓库 `write` 或 `admin` 权限的维护者创建一个只要求简单文本修改
  的测试 Issue，并显式添加 `patchloop` 标签触发一次 workflow run。
- [x] 真实 run 证明 Gate 完成标签与权限授权、Agent 实际调用 Gemini、Docker verifier
  的声明 checks 通过，并由 Publish 在 `patch-loop-smoke` 中创建关联源 Issue 的 Draft
  PR；PatchLoop 源码仓库不接收测试分支或测试 PR。
- [x] 检查 workflow summary、下载 artifact、Issue feedback、Draft PR 正文和 changed
  files：终态与 checks 状态真实一致，Run Report 可读，且没有 Gemini key、GitHub
  token、完整 prompt、隐藏推理或不必要源码泄露。
- [x] 在 ticket 的 `## Answer` 中记录候选 SHA、模型标识、Actions run、测试 Issue、
  Draft PR、checks 结果和验收时间的链接或非敏感标识；不得记录任何 secret 值。
- [x] 验收后关闭但不合并测试 Draft PR，并关闭测试 Issue；保留 smoke 仓库和历史记录
  作为发布证据，后续版本使用新的测试 Issue。
- [x] smoke 全部通过后，在同一个候选 SHA 上创建并推送 `v0.1.0` tag，确认文档中的
  `@v0.1.0` 安装引用可解析；本票不发布 PyPI 包，也不要求额外 GitHub Release。
- [x] 任一真实 smoke 条件失败时不创建或移动 `v0.1.0` tag，不把部分成功描述为发布
  验收通过；保留脱敏证据并另行修复后重新使用新的候选 SHA 验收。

## Answer

PatchLoop `v0.1.0` 已发布在候选 commit
`467f9ef319abb39ff79c823e5bcf50a50fb074a4`。该候选先通过完整离线发布验收：严格
mypy、142 个 pytest（包含真实 Docker verifier 场景）、wheel/sdist、隔离 wheel
安装、MIT 元数据、安装后 CLI 和 whitespace 检查；随后 Standards 与 Spec 双轴
review 均为 0 findings。

最终真实验收使用私有 `echo-DM/patch-loop-smoke`、测试 Issue
[`#4`](https://github.com/echo-DM/patch-loop-smoke/issues/4)、Actions run
[`33586620376`](https://github.com/echo-DM/patch-loop-smoke/actions/runs/33586620376)
和 Draft PR [`#5`](https://github.com/echo-DM/patch-loop-smoke/pull/5)。Run Report 记录模型
`google_gemini_developer_api / gemma-4-31b-it`、1 / 2 iterations、4 / 20 tool calls、
1 / 1 changed files、2 / 40 diff lines，以及 `checks_passed` / `pr_created`。Docker
使用 `alpine:3.22`，声明的 setup 和 check 均通过；PR 只把 `README.md` 中的 fixture
状态从 `pending` 改为 `passed`。

artifact `9830312659` 的下载摘要为
`sha256:fedf75e857df19443be0b8739af720d82a8badbfb7ee08e8845363c701c34e41`；manifest
声明的 payload byte length 与 SHA-256 全部匹配，patch SHA-256 为
`88f76c27aec4cbbea8ffc61776514afbf07cdd0809d73a7750b9cceef51ab6a3`，与 Draft PR
diff 一致。完整 Actions 日志、workflow summary、artifact、Issue、PR 正文与 changed
files 经 Codex 和维护者共同检查，未发现 Gemini key、GitHub token、完整 prompt、
隐藏推理或不必要源码泄露。验收完成时间为 2026-09-02 11:21:44 CST（+0800）。

Draft PR #5 已关闭且未合并，Issue #4 已关闭；失败验收 Issue #1/#2 与 Draft PR #3
也已关闭但保留历史。前两次尝试均未发布 tag：run `33584429826` 在配置解析阶段以
`invalid_config_value` 安全终止且未调用 Gemini；run `33584959834` 功能 happy path
通过，但审计发现 GitHub 展开 `event_json` input 而记录完整 Issue body。对应修复分别
把版本化、可解析的 smoke fixture 纳入源码，并改为从 runner 的
`GITHUB_EVENT_PATH` 读取 webhook，随后都以新候选 SHA 重新验收。

维护者最终批准发布后，annotated tag `v0.1.0` 已创建并推送，远端
`v0.1.0^{}` 精确解析到上述候选 SHA。GitHub 能通过 `@v0.1.0` 读取 reusable
workflow；隔离 Python 3.12 venv 也已从公开 Git tag 成功安装并执行 `patchloop` CLI，
安装包版本为 `0.1.0`。本次没有发布 PyPI 包或额外 GitHub Release。

## Comments

- 2026-09-01：通过 `grill-with-docs` 确认发布边界。Ticket 13 负责可安装交付与完整
  离线验证；本票独立承担需要人类保管凭据、授权外部 GitHub 写入和创建版本 tag 的
  真实发布验收。正式源仓库为公开的 `echo-DM/patch-loop`，smoke 目标为独立私有
  `echo-DM/patch-loop-smoke`；先按候选 SHA 验证，再在同一 SHA 创建 `v0.1.0`。
