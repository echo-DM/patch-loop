# 07 — 根据 check 失败修补并有界停止

**What to build:** 让 Agent 将 Docker checks 的规范化失败作为下一轮修改输入，在预算内修复 patch 并重新验证。能够修复时返回 checks passed；无法修复时保留有效 partial patch 和最后一次失败证据。

**Blocked by:** 05 — 在硬预算内停止过大或越权修改；06 — 在 Docker 中隔离验证 patch

**Status:** ready-for-agent

- [ ] 第一次 check 失败会作为有界、脱敏的反馈进入下一轮 Agent 决策。
- [ ] Agent 可以基于失败证据修改原候选 patch，并在新的临时副本中重新验证。
- [ ] checks 通过时返回 `pr_created` publication intent、`checks_passed` 条件和每轮可观察证据。
- [ ] 达到最大迭代次数且仍失败时返回合法 partial patch、`checks_failed` 或 `budget_exhausted` 条件及最后失败摘要。
- [ ] 每一轮共享全局工具、diff、文件和时间预算，不会在重试时重置安全计数。
- [ ] 无合法 patch 的模型失败、verifier 基础设施失败和普通 check 失败得到不同终态。
- [ ] Run Report 记录轮数和可观察结果，但不保存隐藏推理、完整 prompt 或秘密。
- [ ] acceptance test 包含首轮失败后修复成功、持续失败到上限和基础设施中断三条完整路径。
