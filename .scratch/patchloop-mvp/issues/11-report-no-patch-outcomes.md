# 11 — 把无 patch 终态反馈到 Issue

**What to build:** 让 PatchLoop 对没有可发布 patch 的终态提供明确、脱敏、可操作的 Issue feedback 或 workflow summary，不创建空分支或空 PR。

**Blocked by:** 09 — 从 Issue 标签运行可复用 workflow

**Status:** ready-for-agent

- [ ] `needs_clarification` 向 Issue 发布有限、具体的问题，并说明重新触发所需动作。
- [ ] `no_change` 解释为何不需要代码修改，且不创建分支或 PR。
- [ ] 授权失败、配置错误、模型错误、Docker/setup 基础设施错误和无合法 patch 的预算耗尽具有不同反馈模板。
- [ ] Issue feedback 和 workflow summary 只包含可观察结果，不包含隐藏推理、完整 prompt 或不必要源码。
- [ ] credential-shaped values、恶意 Markdown、Issue 内容和 check 输出在反馈前被转义、截断和脱敏。
- [ ] 低权限触发拒绝不会泄露仓库私有信息、密钥存在性或内部错误细节。
- [ ] GitHub 写入失败保留 workflow summary 和稳定失败状态，且不会重试到产生重复评论。
- [ ] contract tests 证明所有无 patch 终态均不会调用分支或 PR 创建操作。
