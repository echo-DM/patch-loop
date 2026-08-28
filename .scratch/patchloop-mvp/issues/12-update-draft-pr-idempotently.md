# 12 — 幂等更新同一 Issue 的 Draft PR

**What to build:** 让同一 Issue 的重复授权运行继续使用一个活动分支和 Draft PR，并根据 PR 生命周期采取安全、可预测的行为。PatchLoop 追加普通提交，不自动重写历史或处理冲突。

**Blocked by:** 10 — 从有效 patch 创建 Draft PR

**Status:** ready-for-agent

- [ ] 新运行在修改前查找与 Issue 对应的活动 PatchLoop 分支和 Draft PR。
- [ ] 存在活动 PR 时从其当前分支状态继续，并把新 patch 作为普通提交追加到同一 PR。
- [ ] concurrency 保证同一 Issue 不会有两个 Publish 流程同时写入分支。
- [ ] PatchLoop 不使用 force-push，不重写历史，不自动 rebase，也不自动解决 merge conflict。
- [ ] 检测到 merge conflict 或 base 不兼容时停止发布并向维护者报告，不破坏现有 PR。
- [ ] PR 已合并时把自动工作视为完成，不创建新分支或 PR。
- [ ] PR 已关闭但未合并时拒绝静默重建，并要求未来的显式 restart 机制。
- [ ] rerun tests 覆盖活动 PR、并发事件、已合并、关闭未合并、冲突和 GitHub 部分失败。
