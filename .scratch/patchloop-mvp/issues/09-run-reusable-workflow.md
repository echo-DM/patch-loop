# 09 — 从 Issue 标签运行可复用 workflow

**What to build:** 让目标仓库通过一个最小 caller workflow 响应 `patchloop` 标签并调用 PatchLoop reusable workflow。Gate 与 Agent 在独立 Job 中执行，权限和秘密按最小范围传递，最终产生经过完整性保护的 patch/report artifact。

**Blocked by:** 02 — 拒绝未经授权的 Issue 运行；07 — 根据 check 失败修补并有界停止；08 — 用 Gemini 驱动受控 patch 流程

**Status:** resolved

- [x] reusable workflow 接收调用仓库的 Issue event、配置和命名 Gemini secret，不依赖隐式继承全部 secrets。
- [x] caller 只在目标标签事件上调用 workflow，并为同一 Issue 设置唯一 concurrency group。
- [x] Gate Job 具有读取 Issue、元数据和仓库内容所需的最小权限，拒绝场景不会启动 Agent Job。
- [x] Agent Job 具有仓库只读权限和 Gemini secret，但没有内容、Issue 或 Pull Request 写权限。
- [x] Agent Job 在一次 workflow run 内完成，不依赖远程 LangGraph checkpoint 或跨运行状态存储。
- [x] patch bundle、Run Report 和 publication intent 通过显式 artifact 传递，并附带可验证完整性元数据。
- [x] workflow summary 展示脱敏终态、checks 和预算信息，但不输出完整 GitHub context 或秘密。
- [x] workflow validation 覆盖语法、输入、secret flow、权限降级、concurrency、artifact 和 Job 条件。

## Answer

已完成 Ticket 09。`.github/workflows/patchloop-reusable.yml` 通过 runner 自带的
`GITHUB_EVENT_PATH`、显式 `config_path` 和命名 `gemini_api_key` 连接三个独立
Job。Gate
只有 Issue/仓库读取权限，使用调用仓库的 `GITHUB_TOKEN` 验证触发者并冻结、
脱敏任务；拒绝结果令 Agent Job 条件为 false。Agent 只有仓库读取权限，Gemini
secret 只注入受控执行步，目标 setup/checks 仍由无秘密的 Docker verifier 执行，
且不使用远程 checkpoint。

`examples/patchloop-caller.yml` 只监听 `issues/labeled` 的 `patchloop` 标签，并按
仓库与 Issue number 串行化运行。Gate task 和 Agent result 都通过显式 artifact
传递；最终 artifact 包含 patch、版本化 Run Report、publication intent 和逐文件
SHA-256/字节数 manifest，消费端还会交叉验证 patch identity 与两份 publication
intent。Agent 在任务、配置或密钥加载失败时也会生成完整性保护的失败报告，保留
已知 Issue identity、配置模型/checks 和仓库降低后的预算。Publish 占位 Job 目前
只验证 artifact 并写脱敏 summary；仓库/Issue/PR 写入仍属于 Ticket 10/11。

## Comments

- 2026-08-31：最终验证通过 `uv lock --check`、严格 `mypy`、完整 pytest
  （80 passed，12 个真实 Docker 场景因本机 Docker daemon 不可用而跳过）、
  wheel/sdist 构建和 `git diff --check`。双轴 code review 最终为 Standards 0
  findings、Spec 0 findings。本次未运行真实 GitHub Actions/Gemini smoke；实现提交
  为 `2f7df33`、`0b75176` 和 `ce6b46f`。
