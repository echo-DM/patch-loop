# 05 — 在硬预算内停止过大或越权修改

**What to build:** 让 PatchLoop 对工具调用、修改规模和运行时间实施不可突破的安全上限。达到预算时保留仍然合法的 patch 并报告 `budget_exhausted`；越权或受保护内容不会进入可发布产物。

**Blocked by:** 04 — 通过受控工具生成首个安全 patch

**Status:** resolved

- [x] 默认预算限制三轮修改验证、六十次工具调用、二十个变更文件、两千行 diff 和三十分钟总运行时间。
- [x] 仓库配置可以降低预算，但无法提高 PatchLoop 的全局安全上限。
- [x] 每次工具调用和 patch 应用前后都会更新并检查相应预算，不允许通过批量参数绕过计数。
- [x] 超过文件数量、diff 行数、单文件大小或允许路径范围的内容不会进入最终 patch bundle。
- [x] 修改 GitHub workflow 权限、PatchLoop 授权策略或其他受保护区域的尝试按照明确策略拒绝并记录。
- [x] 达到预算且已有合法非空 patch 时返回 Draft PR publication intent，并把验证条件标记为 `budget_exhausted`。
- [x] 达到预算但没有合法 patch 时返回失败结果，不创建空 PR intent。
- [x] tests 覆盖每一种预算、配置降限、越权路径、超大 patch 和组合绕过尝试。

## Answer

已完成 Ticket 05。PatchLoop 现在逐个计算受控工具调用，在模型调用、工具调用和 patch 应用前后检查墙钟与修改规模；批量调用无法跨过剩余预算。候选写入超过变更文件数、diff 行数或固定单文件大小上限时只回滚本次写入，已合法的 patch 保持可发布。

达到迭代、工具调用、修改规模或运行时间预算时，有合法非空 patch 会保留 Draft PR publication intent，并将验证条件标记为 `budget_exhausted`；没有合法 patch 则失败且不产生空 PR intent。`.github/workflows/**`、`.patchloop.yml` 与 `.git/**` 作为明确的受保护控制面，拒绝事件会进入脱敏 Run Report。

验证结果：`uv lock --check`、严格 `mypy`、45 个 pytest、wheel/sdist 构建和 `git diff --check` 全部通过。
