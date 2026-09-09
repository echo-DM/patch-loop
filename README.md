# PatchLoop

PatchLoop 是一个有明确边界的 Issue-to-Draft-PR agent，由仓库维护者在自己的
GitHub Actions 账户中运行。维护者为某个 Issue 添加 `patchloop` label 以授权执行；
PatchLoop 随后会冻结任务，让 Gemini 通过六个受控工具提出修改，在 Docker 中验证
候选结果，最后发布如实标注的 Draft PR，或在 Issue 中提供可执行的反馈。

PatchLoop v0 由一个可复用 GitHub workflow（reusable workflow）和 Python 3.12 CLI
组成。它不是托管服务（hosted service）、GitHub App、可执行任意 shell 命令的 agent、
自动合并工具，也不是用于运行恶意代码的强 sandbox。

## 要求

- 目标仓库已启用 GitHub Actions。
- 目标仓库允许 caller 使用 `echo-DM/patch-loop` reusable workflow，以及其中的
  `actions/checkout`、`actions/upload-artifact`、`actions/download-artifact` 和
  `astral-sh/setup-uv` actions。
- 负责授权运行的维护者拥有 `write` 或 `admin` 权限。
- Gemini Developer API key 已保存为目标仓库的 Secret
  `PATCHLOOP_GEMINI_API_KEY`。
- 使用带 Docker 的 Linux GitHub-hosted runner；如果使用 self-hosted runner，
  其所有者需接受[安全说明](docs/security.md)中列出的额外风险。
- 仓库中有明确的 `.patchloop.yml`。PatchLoop 不会自动检测仓库使用的语言、依赖配置
  或检查命令。

无需 personal access token、GitHub App token、Vertex AI project、service account
或 PyPI package。

## 安装到目标仓库

1. 将[最小 caller 示例](examples/patchloop-caller.yml)复制到目标仓库的
   `.github/workflows/patchloop.yml`。
2. 将[完整配置示例](examples/patchloop.yml)复制到 `.patchloop.yml`，并把其中的
   Docker image 和命令替换为适合目标仓库的内容。
3. 添加 Actions Secret `PATCHLOOP_GEMINI_API_KEY`。不要将 key 提交到
   `.patchloop.yml`、`.env`、Issue、fixture 或命令行中。
4. 创建 `patchloop` label。拥有 `write` 或 `admin` 权限的维护者审查任务及可信的
   维护者评论后，才能将该 label 添加到描述足够具体的 Issue。

caller 授予 `contents: write`、`issues: write` 和 `pull-requests: write` 权限；
reusable workflow 会按 job 缩减权限。它只传入指定的 Gemini Secret，绝不使用
`secrets: inherit`。caller 中无权限的 `preflight` job 只判断该 Secret 是否存在，
不会读取或输出其值；缺少 Secret 时，credentialed job 会安全跳过。

当前示例固定到在 `patchloop-sorting-lab` 中通过真实 GitHub Actions/Gemini 端到端验证的
commit `d9fd688fa9b65daf95eb8fea5bf912a368f6b1cd`。若要最稳妥地固定供应链版本
（supply-chain pin），请继续使用完整且不可变的 SHA；不要从 `main` 或其他会变动的
branch 安装。`v0.1.0` 不包含其发布之后加入、并已包含在该 SHA 中的 provider recovery
和多文件 patch 发布修复；后续 release tag 只有在独立 Live smoke 完成后才应替换示例
中的 SHA。

### GitHub Actions 设置

目标仓库的默认 workflow permission 可以保持 `read`，因为 caller 已明确声明每个所需
scope，reusable workflow 又会按 job 进一步缩减。PatchLoop 不需要启用
“Allow GitHub Actions to create and approve pull requests”：它只创建 Draft PR，不会
approve、标记 ready 或 merge。建议保持自动批准关闭，并由维护者人工审查和合并。

如需用 `gh` 恢复建议设置，可将 `OWNER/REPO` 替换为目标仓库：

```bash
gh api --method PUT repos/OWNER/REPO/actions/permissions/workflow \
  -f default_workflow_permissions=read \
  -F can_approve_pull_request_reviews=false
```

这个仓库级默认值不会取代 caller 中显式的 `permissions`。如果组织策略禁止 workflow
获得 write scope，则仓库管理员仍需先调整相应组织策略。

## 仓库配置

`.patchloop.yml` 必须纳入版本控制，且不可省略。完整 schema 如下：

```yaml
version: 1
model: gemini-3.5-flash-lite
verifier:
  image: ghcr.io/astral-sh/uv:python3.12-bookworm-slim
  setup:
    - uv --version
  checks:
    - UV_CACHE_DIR=/tmp/uv-cache UV_PROJECT_ENVIRONMENT=/tmp/patchloop-venv uv run --frozen pytest -q
  limits:
    timeout_seconds: 300
    memory_mb: 1024
    pids: 256
    output_bytes: 200000
budgets:
  max_iterations: 3
  max_tool_calls: 60
  max_changed_files: 2
  max_diff_lines: 300
  max_wall_time_minutes: 10
```

| 字段 | 含义与允许范围 |
| --- | --- |
| `version` | 必须为 `1`。遇到未知版本时，会在 model 执行前失败。 |
| `model` | Gemini Developer API 的 model identifier。建议显式配置；省略时仍默认为 `gemma-4-31b-it`。 |
| `verifier.image` | 每个 setup/check container 使用的指定 Docker image。 |
| `verifier.setup` | 一个或多个预先声明的 setup 命令。 |
| `verifier.checks` | 一个或多个预先声明的 verification 命令。 |
| `verifier.limits.timeout_seconds` | 每条命令的 timeout，范围为 `1..1800`。 |
| `verifier.limits.memory_mb` | container memory 上限，范围为 `6..4096`。 |
| `verifier.limits.pids` | container process 上限，范围为 `1..512`。 |
| `verifier.limits.output_bytes` | 捕获输出的大小上限，范围为 `1..1000000`。 |
| `budgets.max_iterations` | edit-and-verify 的 iteration 次数，范围为 `1..3`。 |
| `budgets.max_tool_calls` | 受控 tool call 次数，范围为 `1..60`。 |
| `budgets.max_changed_files` | changed file 数量，范围为 `1..20`。 |
| `budgets.max_diff_lines` | unified diff 行数，范围为 `1..2000`。 |
| `budgets.max_wall_time_minutes` | 整次运行的 wall-clock time，范围为 `1..30`。 |

仓库可以调低 verifier limit 或预算（budget），但不能超过固定上限。运行前由维护者
选择命令；Gemini 无法添加 shell 命令或扩大权限。

上面的 Python/uv 配置已在 `patchloop-sorting-lab` 中完成真实 GitHub Actions/Gemini
端到端验证。`uv` 的 cache 和 project environment 被放到 container 的 `/tmp`，避免依赖
安装尝试写入挂载的目标仓库。`max_changed_files: 2` 和 `max_diff_lines: 300` 是该小型任务
的限制，不是通用默认值；目标仓库应按预期 Issue 范围调整，但不能超过表中的固定上限。
当前 schema 要求 `setup` 至少包含一条命令；无需安装步骤时可以使用只读的
`uv --version` 一类环境检查。

非 Python 项目应保留相同结构，但必须换成包含所需 runtime、package manager 和测试工具
的 Docker image，并确认配置的 setup/check 命令都能在该 image 中执行。

## 为 Issue 添加 label 后会发生什么

1. **Gate** 在调用 Gemini 前检查 `patchloop` label 和触发者的有效权限。
   `write` 与 `admin` 可以授权运行；权限更低或无法确认时不会运行。
2. **Gate** 冻结 Issue 的标题、正文及符合条件的评论。拥有 `write`/`admin` 权限的
   维护者评论可以补充需求。其他评论、Issue 文本、仓库文件、diff 和检查输出仍属于
   low-trust data，不能改变工具、凭据、预算（budget）或发布策略。
3. **Prepare** 检查持久化的 Issue branch 和 PR lifecycle。已有活跃 Draft PR 时继续
   处理；工作已合并或关闭但未合并时停止；遇到冲突时报告问题，不执行 rebase，也不
   重写历史。
4. **Agent** 可以读取仓库并使用 Gemini Secret，但没有 GitHub write 权限。Gemini
   只能列出文件、搜索代码、读取文件、应用有界 patch、检查 diff，以及请求执行已配置
   的 checks。
5. **Verifier** 在一次性仓库副本和受限 Docker container 中分别运行 setup/check，
   不会向其中传入 Gemini key 或 GitHub write token。setup/check 产生的副作用会被丢弃。
6. **Publish** 校验 manifest、patch identity、Run Report 和 publication intent。它有
   GitHub write 权限，但拿不到 Gemini Secret，也不会执行目标代码。它通过普通 commit
   创建或 fast-forward `patchloop/issue-<number>`，并创建或更新一个 Draft PR。

每个仓库、每个 Issue 使用一个 concurrency group，防止运行重叠。workflow 不使用远程
LangGraph checkpoint；如果任务被取消或 runner 丢失，后续运行会从 GitHub 中持久化的
branch/PR 状态继续。

### 修改配置后重试

caller 只监听 `issues: labeled`。如果一次运行后修改了 `.patchloop.yml` 或 caller，应先
提交修改，再移除并重新添加 `patchloop` label，产生新的 labeled event。GitHub 的
“Re-run jobs” 会继续使用原运行关联的 commit，不能保证采用刚提交的新配置。重新授权前
先阅读 Issue 中的 PatchLoop 反馈；`provider_error`、configuration、setup 和 publication
failure 属于不同阶段，不应仅凭“没有 PR”判断原因。

## 结果与审查状态

终止结果（terminal outcome）与验证状态（verification status）相互独立：

| Terminal outcome | Verification | GitHub 结果 |
| --- | --- | --- |
| `pr_created` | `checks_passed` | Draft PR，包含 PatchLoop checks 已通过的证据（evidence）。 |
| `pr_created` | `checks_failed` | Draft PR，醒目标明 checks 失败。 |
| `pr_created` | `budget_exhausted` | Draft PR，包含最后一个合法 patch 及尚未解决的限制。 |
| `needs_clarification` | `not_run` | 在 Issue 反馈中提出数量受限的问题；不创建 branch 或 PR。 |
| `no_change` | `not_run` | 在 Issue 反馈中说明原因；不创建 branch 或 PR。 |
| `failed` | setup、infrastructure、model、authorization、configuration 或 budget failure | 提供可执行的反馈或摘要（summary）；不创建空 PR。 |

PatchLoop 不会将 PR 标记为 ready，不会 approve、merge、force-push、rebase 或解决 merge
conflict。已关闭但未合并的 PatchLoop PR 会阻止系统静默重建；PR 合并后，该 Issue 的
自动化工作随即结束。

使用仓库 `GITHUB_TOKEN` 创建的 PR 可能不会自动触发目标仓库的所有原生 workflow，
这些 workflow 也可能需要维护者批准。PatchLoop checks 只是生成 patch 的有界证据，
不能代替仓库的完整 CI、branch protection、安全审查（security review）或人工审查
（human review）。

## 本地复现

在 PatchLoop 源码 checkout 中运行：

```bash
uv sync --locked
cp examples/patchloop.yml /path/to/target/.patchloop.yml
uv run patchloop run \
  --task examples/local-task.json \
  --repository /path/to/target
```

默认 CLI mode 无需凭据，只接受明确的 no-change task。无需联系 Gemini，即可用它检查
task/config 解析、终止报告以及安装后的命令。

若要在本地复现真实的 Agent path，请使用一次性的目标仓库 checkout，配置其中的
`.patchloop.yml`，导出专用 key，然后明确选择启用：

```bash
export PATCHLOOP_GEMINI_API_KEY='set this outside the repository'
uv run patchloop run \
  --live \
  --task /path/to/task.json \
  --repository /path/to/disposable-target
```

`--live` 会使用 Gemini 和 Docker，可能消耗 quota 或修改传入的 workspace，但仍不会
写入 GitHub。两种 CLI mode 与 reusable workflow 都调用公开的
`patchloop.core.run` interface、加载相同的 `.patchloop.yml` schema，并输出相同的
version-1 Run Report contract；区别只在于注入的 task/model/verifier/GitHub adapter。

创建 `v0.1.0` 后，还可以从固定的源码 tag 安装 CLI：

```bash
uv tool install git+https://github.com/echo-DM/patch-loop.git@v0.1.0
```

## 验证与 live smoke

只需一条命令即可完成无需凭据的全部验证：

```bash
uv run python scripts/validate_offline.py
```

该命令要求 Docker daemon 正在运行，并且已预加载 `alpine:3.22` fixture image。随后会
检查 lock、strict typing、完整 pytest suite（包括真实 Docker acceptance、workflow
contract 和 credential-redaction 场景），构建 wheel/sdist，将 wheel 安装到隔离的
临时环境中，执行其 `patchloop` entry point，最后检查 whitespace。这里的 “offline”
表示不会执行真实 Gemini 或 GitHub 操作；dependency setup 仍需要已填充的 uv cache，
或能够正常访问 package index。

live smoke 独立运行，需要明确启用，并且是 release gate：

- [Gemini adapter smoke](tests/smoke/gemini_smoke.py) 同时需要
  `PATCHLOOP_RUN_GEMINI_SMOKE=1` 和 `PATCHLOOP_GEMINI_API_KEY`。
- [专用 GitHub smoke caller](examples/patchloop-smoke-caller.yml) 必须安装到私有 Smoke
  仓库中，例如 `echo-DM/patch-loop-smoke`；同时还要将
  [smoke fixture 配置](examples/patchloop-smoke.yml)作为 `.patchloop.yml` 安装，并把
  两处 placeholder ref 替换为同一个候选 commit SHA。fixture Issue 要求将 README 中
  的确切文本 `Release smoke fixture: pending.` 改为
  `Release smoke fixture: passed.`。设置仓库 variable
  `PATCHLOOP_RUN_LIVE_SMOKE=1`；如果没有这个明确的 flag 或指定的 Gemini Secret，
  preflight 会安全地跳过需要凭据的 job。
- [GitHub 状态检查](tests/smoke/github_smoke.py) 在 reusable workflow 结束后运行，使用
  caller 仓库自动提供的 `GITHUB_TOKEN`；它会确认预期的 Draft PR 存在，并且绝不会
  收到 Gemini Secret。
- 缺少 enable flag 或凭据时，独立 smoke test 会安全跳过。默认 pytest 和默认 CI
  都不包含 live smoke。

另请参阅 [Gemini 集成](docs/gemini.md)、[安全说明](docs/security.md)和
[54 条 story 的覆盖矩阵](docs/user-story-coverage.md)。实际 release acceptance 和
`v0.1.0` 的创建由 [Ticket 14](.scratch/patchloop-mvp/issues/14-release-v0.1.0-with-live-smoke.md)
单独跟踪。

## 许可证

PatchLoop 采用 [MIT License](LICENSE)。
