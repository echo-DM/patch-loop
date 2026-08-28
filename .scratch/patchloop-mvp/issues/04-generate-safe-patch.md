# 04 — 通过受控工具生成首个安全 patch

**What to build:** 让已授权且信息充分的任务通过受控代码工具完成一次仓库探索和修改，生成非空 patch bundle、Run Report 和 Draft PR publication intent。模型只能使用已注册的文件与检查工具，不能获得任意 Shell、Git 或网络执行能力。

**Blocked by:** 03 — 对信息不足的任务给出澄清结果

**Status:** ready-for-agent

- [ ] Model adapter 能请求列出文件、搜索代码、读取文件、应用 patch、查看 diff 和运行已配置 checks。
- [ ] 每个工具调用都使用结构化参数校验；未知工具、无效参数和重复调用返回受控错误而不是执行任意行为。
- [ ] 文件读取按需发生，运行不需要 embedding、向量数据库、仓库索引或全仓库 prompt。
- [ ] patch 只能修改工作区内的规范化相对路径，并拒绝路径穿越、符号链接逃逸和工作区外写入。
- [ ] 模型没有通用 Shell、GitHub 写入、Git 命令或任意网络工具。
- [ ] 成功场景返回非空 patch bundle、变更摘要、Run Report 和 Draft PR publication intent。
- [ ] patch bundle 与报告具有可供后续 Publish 验证的完整性元数据，且不包含模型凭据。
- [ ] acceptance test 使用 deterministic Model 和 Verifier adapters，通过公开 `run` interface 验证最终 diff，而不是内部工具编排。
