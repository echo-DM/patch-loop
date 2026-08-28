# 02 — 拒绝未经授权的 Issue 运行

**What to build:** 让 PatchLoop 从 GitHub Issue 事件形成不可变任务快照，并在任何模型调用前验证触发者权限。拥有 `write` 或 `admin` 权限的维护者可以授权运行，权限更低的用户得到清晰拒绝，且不消耗 Gemini 配额。

**Blocked by:** 01 — 建立可执行的 no_change tracer bullet

**Status:** ready-for-agent

- [ ] 输入事件只有在添加目标标签时才被识别为 PatchLoop 触发请求。
- [ ] GitHub adapter 能把事件、Issue、触发者和评论规范化为一次不可变任务快照。
- [ ] 有效权限为 `write` 或 `admin` 时允许进入执行模块；其他权限、未知用户和权限查询失败得到不同的可观察结果。
- [ ] 被拒绝的运行不会调用 Model adapter、Verifier adapter 或任何仓库写操作。
- [ ] Issue 标题和正文被记录为经维护者授权的任务要求。
- [ ] 有写权限作者的评论被归类为补充要求；其他评论被隔离为低信任参考资料。
- [ ] 外部评论中的提示词不能改变工具、预算、凭据、发布或授权策略。
- [ ] 授权与拒绝场景均通过黑盒 `run` interface 和固定 GitHub fixtures 验证。
