# 05 — 在硬预算内停止过大或越权修改

**What to build:** 让 PatchLoop 对工具调用、修改规模和运行时间实施不可突破的安全上限。达到预算时保留仍然合法的 patch 并报告 `budget_exhausted`；越权或受保护内容不会进入可发布产物。

**Blocked by:** 04 — 通过受控工具生成首个安全 patch

**Status:** ready-for-agent

- [ ] 默认预算限制三轮修改验证、六十次工具调用、二十个变更文件、两千行 diff 和三十分钟总运行时间。
- [ ] 仓库配置可以降低预算，但无法提高 PatchLoop 的全局安全上限。
- [ ] 每次工具调用和 patch 应用前后都会更新并检查相应预算，不允许通过批量参数绕过计数。
- [ ] 超过文件数量、diff 行数、单文件大小或允许路径范围的内容不会进入最终 patch bundle。
- [ ] 修改 GitHub workflow 权限、PatchLoop 授权策略或其他受保护区域的尝试按照明确策略拒绝并记录。
- [ ] 达到预算且已有合法非空 patch 时返回 Draft PR publication intent，并把验证条件标记为 `budget_exhausted`。
- [ ] 达到预算但没有合法 patch 时返回失败结果，不创建空 PR intent。
- [ ] tests 覆盖每一种预算、配置降限、越权路径、超大 patch 和组合绕过尝试。
