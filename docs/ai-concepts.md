# PatchLoop：如何 Harness 一个代码 Agent

PatchLoop 的重点不在于让 Gemini “更会写代码”，而在于如何把一个会提出行动建议的模型，
放进真实 GitHub 仓库仍然可控的运行框架中。

这个运行框架叫 **Agent harness**。它不是模型本身，而是模型周围负责授权、工具、限制、
验证、发布和审计的一整套机制。PatchLoop 将一个明确授权的 GitHub Issue 处理为可供人审查
的 Draft PR；如果不能安全完成，则给出反馈或失败记录，而不是勉强创建空 PR。

## 先区分两个角色

| 角色 | 要解决的问题 | 在 PatchLoop 中的责任 |
| --- | --- | --- |
| **Gemini（Agent）** | “下一步可能该做什么？” | 根据任务和观察结果，提议读取、搜索、修改或运行检查。 |
| **PatchLoop（Harness）** | “这一步能否做，做后是否可信？” | 验证提议、执行允许的动作、限制资源、验证 patch，并控制 GitHub 发布。 |

这一区分很重要。模型输出不是命令，而是待验证的提议。即使模型出错，或被仓库文字、Issue
评论、测试输出中的恶意内容误导，它也不能自行扩大权限或直接写入 GitHub。

## 一次运行：模型在护栏内工作

```text
维护者为 Issue 添加 patchloop label
              ↓
Gate：验证触发者权限，冻结任务快照
              ↓
Agent：Gemini 提出受控工具调用
              ↓
Harness：校验调用、路径、内容和预算
              ↓
Verifier：在一次性 Docker 副本中运行预设检查
              ↓
Publish：复核证据和 patch，创建或更新 Draft PR
```

这不是一条“模型做完就自动提交”的流水线。每一步都有不同的权限和输入边界，模型只参与
Agent 阶段的“判断下一步”。

## 1. 授权 harness：谁能启动 Agent

PatchLoop 不会因为任意一条 Issue 或评论就开始修改代码。Gate 只接受由拥有 `write` 或
`admin` 权限的维护者添加 `patchloop` label 所触发的运行；随后冻结该 Issue 的标题、正文和
符合条件的维护者评论，形成这一次运行的任务快照。

这是 harness 的第一道边界：**模型面对的任务，不等于任何人都能实时改写的聊天窗口。**

其他评论、仓库文件、diff 和检查输出仍然会提供给模型作为参考，但被视为 low-trust data。
它们可以帮助定位问题，不能修改可用工具、凭据、预算或发布策略。这样即使其中含有“忽略
之前的限制”之类的 prompt injection 文本，也不会成为系统权限。

实现入口：`src/patchloop/github_api.py` 与公开执行接口 `patchloop.core.run`。

## 2. 能力 harness：模型只能提议六个工具

Gemini 接入的是 Gemini Developer API 的 function calling，而不是一个任意 shell。它只看得到
六个结构化工具：

| 工具 | 模型可以提议的动作 |
| --- | --- |
| `list_files` | 列出工作区文件 |
| `search_code` | 搜索文本 |
| `read_file` | 读取 UTF-8 文本文件 |
| `apply_patch` | 替换一个 UTF-8 文件内容 |
| `inspect_diff` | 查看当前 unified diff |
| `run_checks` | 请求运行维护者已配置的检查 |

`GeminiPatchModelAdapter` 将模型响应转为 `ModelTurn`，而 `ControlledTools` 才真正执行
工具。后者会拒绝未知工具、重复 call ID、参数不匹配和不合法的路径。因此模型没有 Git、
网络、GitHub API、Web 搜索、Code Execution 或任意终端命令的入口。

实现入口：`src/patchloop/gemini.py`、`src/patchloop/controlled_tools.py`。

## 3. 调度 harness：谁控制修复循环

真正的循环在 `patchloop.core._run_patch()`，不是在 Gemini 内部，也不是一个无限运行的
LangGraph 图。它反复执行下面的受控回合：

1. 将冻结任务、上一轮工具结果和工具定义交给模型；
2. 接收且只接受一种模型动作：工具调用、完成说明，或不修改的决定；
3. 对每个工具调用检查总耗时和调用次数，再交给 `ControlledTools.execute()`；
4. 将结果记录为下一轮的观察；模型请求 `run_checks` 时，读取 verifier 的结果；
5. 成功、失败、超预算或违反规则时停止，并生成规范化 Run Report。

这意味着“Agent loop”不是模型在自由探索，而是 harness 在驱动一个有限状态的交互循环。
`LangGraph` 当前仅用于任务评估的轻量状态图封装；受控的修复调度、终止条件和结果生成，
都由 `core.py` 执行。

一个关键检查是 patch identity：只有最终 patch 的 SHA-256 与刚通过验证的 patch 相同，
PatchLoop 才把它视为已验证。模型不能先验证 A，再改成 B，却声称 B 通过了测试。

实现入口：`src/patchloop/core.py`。

## 4. 资源与变更 harness：能改多少、不能改哪里

每次 `apply_patch` 都不是无条件写文件。Harness 会检查文本是否带有凭据形态、目标是否为
合法 UTF-8 文件，以及是否触及受保护路径，例如 `.git`、`.github/workflows`、`.patchloop`
和 `.patchloop.yml`。

它还累计资源预算：编辑/验证迭代、工具调用、修改文件数、diff 行数和 wall-clock time 都有
上限；单文件也有固定字节上限。`.patchloop.yml` 可将这些限制调低，不能调高。

超过文件、diff 或大小限制时，当前这一次写入会回滚，较早且合法的修改仍保留。这是“失败时
保持已知安全状态”，而不是让一次失控提议毁掉整个候选 patch。

## 5. 验证 harness：运行什么代码由维护者决定

模型可以请求检查，不能发明检查命令。实际的 Docker image、setup 和 checks 都在目标仓库
受版本控制的 `.patchloop.yml` 中声明。

Verifier 为每个 setup/check 使用候选仓库的一次性副本和受资源限制的 Docker 容器。容器不
接收 Gemini Key、GitHub 写入 token 或 PatchLoop 私有状态；其中产生的文件副作用也不会直接
变成发布内容。

Docker 在这里是验证约束，不是针对恶意代码的万能 sandbox。它降低了误操作和资源耗尽的
风险，但不保证防御容器逃逸、Docker daemon 攻破、runner 攻破或恶意依赖。详见
[安全说明](security.md)。

实现入口：`src/patchloop/docker_verifier.py`。

## 6. 权限 harness：把思考、执行与发布拆开

| 阶段 | Gemini Key | GitHub 写权限 | 执行目标仓库命令 |
| --- | --- | --- | --- |
| Gate | 无 | 仅必要的 Issue 反馈 | 否 |
| Agent | 有 | 无 | 仅能通过 Verifier 请求 |
| Verifier container | 无 | 无 | 仅预先声明的 setup/checks |
| Publish | 无 | 有 | 否 |

因此，能调用模型的阶段不能发布 PR；能发布 PR 的阶段又没有模型密钥，且不执行目标代码。
这不是“信任模型别越权”，而是让模型没有越权所需的能力。

## 7. 证据 harness：结果为什么值得人审查

PatchLoop 会输出版本化 Run Report，记录模型标识、patch SHA-256、修改文件、验证尝试、
预算使用和经脱敏处理的错误。Publish 只消费 manifest 声明且重新验证过的产物，并再次核对
任务身份、patch、报告和发布意图。

终止结果与验证状态分开表达：

| 情况 | Harness 的结果 |
| --- | --- |
| 合法 patch，检查通过 | 创建或更新 Draft PR，并附验证证据。 |
| 合法 patch，检查失败或预算耗尽 | 仍可创建 Draft PR，但如实标明限制。 |
| 信息不足 | 在 Issue 中提出受限数量的澄清问题；不建分支、不建 PR。 |
| 无需修改、授权失败、配置失败或基础设施失败 | 返回可执行反馈或失败摘要；绝不创建空 PR。 |

无论哪种情况，PatchLoop 都不会自动 approve、merge、force-push、rebase 或解决冲突。人类
维护者仍拥有最终的代码审查、CI 与合并决定权。

## 8. 模型适配器与真实调用边界

`GeminiPatchModelAdapter` 使用 `ChatGoogleGenerativeAI` 和 Gemini Developer API，默认模型
为 `gemini-3.5-flash-lite`，并显式设置 `vertexai=False`。它只读取专用环境变量
`PATCHLOOP_GEMINI_API_KEY`，不会从 `.env`、通用 Google 环境变量或 Google Cloud 凭据中取值。

默认测试使用模拟的模型响应，覆盖 harness 的行为，但不会调用 Gemini。真实调用要求显式
提供专用 key 并启用 live smoke，可能消耗 quota。因而“离线测试通过”表示控制逻辑符合预期，
不等于刚刚完成了一次真实 Gemini 或 GitHub Actions 的端到端运行。启用方式见
[Gemini 集成](gemini.md)。

## 用这份项目学习什么

阅读 PatchLoop 时，最值得学习的问题不是“prompt 怎么写”，而是：

- 怎样将模型输出降级为可验证的提议；
- 怎样把不可信文本与执行权限分开；
- 怎样通过能力白名单、预算和事务性回滚约束 Agent；
- 怎样将验证环境与密钥、发布权限隔离；
- 怎样将一个不确定的模型过程，交付为带证据、可人工审查的工程结果。

这就是 PatchLoop 作为代码 Agent harness 的核心价值。
