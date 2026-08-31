# 10 — 从有效 patch 创建 Draft PR

**What to build:** 让独立 Publish Job 从已验证 artifact 创建受控分支和 Draft PR。Publish 不接收 Gemini secret、不运行目标仓库代码，并对 checks passed、checks failed 和 budget exhausted 三种有效 patch 状态作出诚实说明。

**Blocked by:** 09 — 从 Issue 标签运行可复用 workflow

**Status:** resolved

- [x] Publish Job 只获得创建分支、Issue 反馈和 Pull Request 所需的写权限，不接收 Gemini secret。
- [x] Publish 在任何 Git 或 GitHub 写入前验证 artifact 身份、完整性、任务编号、base branch 和 patch 安全约束。
- [x] Publish 只应用声明的 patch bundle，不执行目标仓库 setup、checks、hooks 或模型生成命令。
- [x] 一个有效非空 patch 创建受控 Issue 分支和 Draft PR，并使用普通提交而不是 force-push。
- [x] PR 正文关联源 Issue，列出变更摘要、checks、轮数、预算、未解决失败和 PatchLoop checks 的适用范围。
- [x] `checks_passed`、`checks_failed` 和 `budget_exhausted` 在 PR 中具有清晰且不可混淆的呈现。
- [x] checks 未通过或预算耗尽时仍创建 Draft PR，但不会标记 ready、批准或自动合并。
- [x] GitHub adapter contract tests 验证分支、提交、PR 请求和失败回滚，不修改真实仓库。

## Answer

已完成受控 Draft PR 发布。Gate 现在冻结 `repository`、Issue number、base
branch 和 task identity，并把这份 publication context 连同 patch、Run Report
和 publication intent 一起经过 manifest 哈希保护传给 Publish。Publish 会先验证
artifact 的文件集合、哈希、身份交叉引用、verification 状态、预算、diff 路径与
内容，再从固定 base commit 读取原文件并应用唯一声明的 unified diff；在所有正文
字段和 workflow summary 字段验证完成前，不创建 Git object、分支或 PR。

真实 GitHub adapter 使用 Git Database API 创建 blobs、tree、普通 commit 和固定的
`patchloop/issue-<number>` ref，再创建 Draft PR。现有普通文件和可执行文件的 mode
分别保留为 `100644`/`100755`，新文本文件使用 `100644`。PR 创建失败会删除新分支；
分支创建遇到不确定的传输失败时会重新读取 ref，只删除确认指向本次 commit 的分支，
回滚失败和无法判定状态都有稳定错误码。Ticket 12 范围内的已有活动分支更新仍未实现。

PR 正文关联源 Issue，并分别显示变更摘要、changed files、checks、迭代/工具/文件/
diff/时间预算、未解决失败和 PatchLoop checks 范围；`checks_passed`、
`checks_failed`、`budget_exhausted` 三种状态均保持 Draft，不会 ready、批准或合并。

## Comments

- 2026-08-31：最终验证通过 `uv lock --check`、严格 `mypy`、完整 pytest
  （94 passed，12 个真实 Docker 场景按环境条件跳过）、wheel/sdist build 和
  `git diff --check`。首次双轴 review 发现 0 个 Standards 硬违规、3 个可维护性
  judgement calls，以及 2 个 Spec 高优先级缺口和 1 个部分覆盖；已通过拆分 patch/
  report 模块、在写入前完成渲染验证、保留 executable mode，并补充分支不确定写入
  与 rollback-failure contract tests 修正。复审结果为 Standards 0 个硬违规（保留
  2 个非阻塞 judgement calls）、Spec 0 findings。
