# 10 — 从有效 patch 创建 Draft PR

**What to build:** 让独立 Publish Job 从已验证 artifact 创建受控分支和 Draft PR。Publish 不接收 Gemini secret、不运行目标仓库代码，并对 checks passed、checks failed 和 budget exhausted 三种有效 patch 状态作出诚实说明。

**Blocked by:** 09 — 从 Issue 标签运行可复用 workflow

**Status:** ready-for-agent

- [ ] Publish Job 只获得创建分支、Issue 反馈和 Pull Request 所需的写权限，不接收 Gemini secret。
- [ ] Publish 在任何 Git 或 GitHub 写入前验证 artifact 身份、完整性、任务编号、base branch 和 patch 安全约束。
- [ ] Publish 只应用声明的 patch bundle，不执行目标仓库 setup、checks、hooks 或模型生成命令。
- [ ] 一个有效非空 patch 创建受控 Issue 分支和 Draft PR，并使用普通提交而不是 force-push。
- [ ] PR 正文关联源 Issue，列出变更摘要、checks、轮数、预算、未解决失败和 PatchLoop checks 的适用范围。
- [ ] `checks_passed`、`checks_failed` 和 `budget_exhausted` 在 PR 中具有清晰且不可混淆的呈现。
- [ ] checks 未通过或预算耗尽时仍创建 Draft PR，但不会标记 ready、批准或自动合并。
- [ ] GitHub adapter contract tests 验证分支、提交、PR 请求和失败回滚，不修改真实仓库。
