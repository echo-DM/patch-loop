# 03 — 对信息不足的任务给出澄清结果

**What to build:** 让已授权但信息不足的 Issue 经过 LangGraph 和 deterministic Model adapter 后返回 `needs_clarification`，给维护者具体、有限、可回答的问题，同时不创建 patch 或空 PR。

**Blocked by:** 02 — 拒绝未经授权的 Issue 运行

**Status:** resolved

- [x] 授权任务通过公开 `run` interface 进入 LangGraph，但调用方不需要知道节点或边的实现细节。
- [x] deterministic Model adapter 可以产生结构化的澄清决定和问题列表。
- [x] `needs_clarification` 结果包含具体问题、任务标识和没有生成 patch 的原因。
- [x] 澄清问题数量和文本大小受限，且经过日志与报告脱敏。
- [x] 低信任评论可以作为问题背景，但不能使模型跳过授权、扩大权限或改变终态规则。
- [x] `needs_clarification` 不产生 patch bundle，只产生后续可发布的 Issue feedback intent。
- [x] 明确无需修改的任务仍返回 `no_change`，并与信息不足场景保持可观察区别。
- [x] acceptance tests 覆盖缺少复现信息、需求冲突、可信评论完成澄清和无修改必要等场景。

## Answer

已完成 Ticket 03。PatchLoop 现在可以在授权 Issue 信息不足时返回 `needs_clarification`，生成有限且脱敏的问题，并返回 `issue_feedback` intent；不会生成 patch 或空 PR。明确无需修改的任务仍返回独立的 `no_change` 结果。

实现包含内部 LangGraph、deterministic Model adapter、任务与 decision 脱敏、问题边界校验，以及对应的 acceptance tests。

验证结果：25 个 pytest 全部通过，`mypy src` 通过，`git diff --check` 通过。实现提交为 `8906147`、`8fb9896` 和 `43baaba`。
