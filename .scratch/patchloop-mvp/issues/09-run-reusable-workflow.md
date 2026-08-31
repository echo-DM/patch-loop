# 09 — 从 Issue 标签运行可复用 workflow

**What to build:** 让目标仓库通过一个最小 caller workflow 响应 `patchloop` 标签并调用 PatchLoop reusable workflow。Gate 与 Agent 在独立 Job 中执行，权限和秘密按最小范围传递，最终产生经过完整性保护的 patch/report artifact。

**Blocked by:** 02 — 拒绝未经授权的 Issue 运行；07 — 根据 check 失败修补并有界停止；08 — 用 Gemini 驱动受控 patch 流程

**Status:** claimed

- [ ] reusable workflow 接收调用仓库的 Issue event、配置和命名 Gemini secret，不依赖隐式继承全部 secrets。
- [ ] caller 只在目标标签事件上调用 workflow，并为同一 Issue 设置唯一 concurrency group。
- [ ] Gate Job 具有读取 Issue、元数据和仓库内容所需的最小权限，拒绝场景不会启动 Agent Job。
- [ ] Agent Job 具有仓库只读权限和 Gemini secret，但没有内容、Issue 或 Pull Request 写权限。
- [ ] Agent Job 在一次 workflow run 内完成，不依赖远程 LangGraph checkpoint 或跨运行状态存储。
- [ ] patch bundle、Run Report 和 publication intent 通过显式 artifact 传递，并附带可验证完整性元数据。
- [ ] workflow summary 展示脱敏终态、checks 和预算信息，但不输出完整 GitHub context 或秘密。
- [ ] workflow validation 覆盖语法、输入、secret flow、权限降级、concurrency、artifact 和 Job 条件。
