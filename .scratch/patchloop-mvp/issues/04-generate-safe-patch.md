# 04 — 通过受控工具生成首个安全 patch

**What to build:** 让已授权且信息充分的任务通过受控代码工具完成一次仓库探索和修改，生成非空 patch bundle、Run Report 和 Draft PR publication intent。模型只能使用已注册的文件与检查工具，不能获得任意 Shell、Git 或网络执行能力。

**Blocked by:** 03 — 对信息不足的任务给出澄清结果

**Status:** resolved

- [x] Model adapter 能请求列出文件、搜索代码、读取文件、应用 patch、查看 diff 和运行已配置 checks。
- [x] 每个工具调用都使用结构化参数校验；未知工具、无效参数和重复调用返回受控错误而不是执行任意行为。
- [x] 文件读取按需发生，运行不需要 embedding、向量数据库、仓库索引或全仓库 prompt。
- [x] patch 只能修改工作区内的规范化相对路径，并拒绝路径穿越、符号链接逃逸和工作区外写入。
- [x] 模型没有通用 Shell、GitHub 写入、Git 命令或任意网络工具。
- [x] 成功场景返回非空 patch bundle、变更摘要、Run Report 和 Draft PR publication intent。
- [x] patch bundle 与报告具有可供后续 Publish 验证的完整性元数据，且不包含模型凭据。
- [x] acceptance test 使用 deterministic Model 和 Verifier adapters，通过公开 `run` interface 验证最终 diff，而不是内部工具编排。

## Answer

已完成 Ticket 04。公开 `run` interface 现在可以通过 deterministic Patch Model 和 Verifier adapters 驱动按需探索、受控文件修改与 checks，返回内容寻址的 unified diff bundle、Run Report 和 Draft PR publication intent。

受控工具集中注册并校验结构化参数；路径穿越、符号链接、hard link 外部写入、未知工具、无效参数、重复 call id、凭据形态内容和检查后继续修改都会被受控拒绝。澄清与无修改决定仍可在编辑前终止，不会误入 patch 发布流程。

验证结果：30 个 pytest 全部通过，严格 `mypy` 通过，`uv lock --check` 和 `git diff --check` 通过。实现提交为 `721b1de`、`838b8a8` 和 `766f452`。
