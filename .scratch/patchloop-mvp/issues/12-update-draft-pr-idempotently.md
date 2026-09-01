# 12 — 幂等更新同一 Issue 的 Draft PR

**What to build:** 让同一 Issue 的重复授权运行继续使用一个活动分支和 Draft PR，并根据 PR 生命周期采取安全、可预测的行为。PatchLoop 追加普通提交，不自动重写历史或处理冲突。

**Blocked by:** 10 — 从有效 patch 创建 Draft PR

**Status:** resolved

- [x] 新运行在修改前查找与 Issue 对应的活动 PatchLoop 分支和 Draft PR。
- [x] 存在活动 PR 时从其当前分支状态继续，并把新 patch 作为普通提交追加到同一 PR。
- [x] concurrency 保证同一 Issue 不会有两个 Publish 流程同时写入分支。
- [x] PatchLoop 不使用 force-push，不重写历史，不自动 rebase，也不自动解决 merge conflict。
- [x] 检测到 merge conflict 或 base 不兼容时停止发布并向维护者报告，不破坏现有 PR。
- [x] PR 已合并时把自动工作视为完成，不创建新分支或 PR。
- [x] PR 已关闭但未合并时拒绝静默重建，并要求未来的显式 restart 机制。
- [x] rerun tests 覆盖活动 PR、并发事件、已合并、关闭未合并、冲突和 GitHub 部分失败。

## Answer

可复用 workflow 现在在 Agent 前通过独立 Prepare Job 查询
`patchloop/issue-<number>` 的分支与 PR 生命周期，并在 workflow 自身使用同一 Issue
的 concurrency group。新工作从默认 base checkout；活动 Draft PR 从其当前受控分支
checkout。已合并 PR 直接结束自动工作；关闭未合并、merge conflict、非 Draft、base/head
不匹配、孤立分支或持续无法确定 mergeability 时停止并报告，不启动 Agent。

Publish 会在写入前再次解析 durable state。活动 PR 的 patch 基于当前 head tree 应用，
生成以当前 head 为父节点的普通 commit，并通过 `force: false` 快进同一 ref，随后更新
同一 PR 正文。实现不包含 force-push、rebase、历史重写或冲突解决。GitHub 对
mergeability 的暂态 `null` 会进行两次有上限的轮询；分支/PR 请求发生不确定传输失败
时会回读 durable state，只有确认安全时才继续或回滚，无法确认则保留现有现场供维护者
处理。

## Comments

- 2026-09-01：最终验证通过 `uv lock --check`、严格 `mypy`、完整 pytest
  （135 passed，包含真实 Docker verifier 场景）、wheel/sdist build 和
  `git diff --check`。双轴 review 初审为 Standards 0 findings、Spec 1 个高优先级
  finding（暂态 `mergeable: null` 未重试）；补充 bounded polling 与回归测试后，复审
  为 Standards 0 findings、Spec 0 findings。验证使用 deterministic GitHub adapter，
  未执行真实 GitHub Actions 或真实仓库写入。
