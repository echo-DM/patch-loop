# 07 — 根据 check 失败修补并有界停止

**What to build:** 让 Agent 将 Docker checks 的规范化失败作为下一轮修改输入，在预算内修复 patch 并重新验证。能够修复时返回 checks passed；无法修复时保留有效 partial patch 和最后一次失败证据。

**Blocked by:** 05 — 在硬预算内停止过大或越权修改；06 — 在 Docker 中隔离验证 patch

**Status:** resolved

- [x] 第一次 check 失败会作为有界、脱敏的反馈进入下一轮 Agent 决策。
- [x] Agent 可以基于失败证据修改原候选 patch，并在新的临时副本中重新验证。
- [x] checks 通过时返回 `pr_created` publication intent、`checks_passed` 条件和每轮可观察证据。
- [x] 达到最大迭代次数且仍失败时返回合法 partial patch、`checks_failed` 或 `budget_exhausted` 条件及最后失败摘要。
- [x] 每一轮共享全局工具、diff、文件和时间预算，不会在重试时重置安全计数。
- [x] 无合法 patch 的模型失败、verifier 基础设施失败和普通 check 失败得到不同终态。
- [x] Run Report 记录轮数和可观察结果，但不保存隐藏推理、完整 prompt 或秘密。
- [x] acceptance test 包含首轮失败后修复成功、持续失败到上限和基础设施中断三条完整路径。

## Answer

已完成 Ticket 07。普通 check 失败现在以仓库配置的输出上限进行截断，并在脱敏后作为结构化反馈交给下一轮模型；修复循环继续使用同一个受控工具实例，因此迭代、工具调用、变更文件、diff 和墙钟预算不会在重试时重置。每次验证仍由 Docker verifier 从当前候选 patch 创建一次性副本。

Run Report 的 `verification.attempts` 记录每轮编号、候选 patch SHA-256、setup/check 结果和失败类别。当前 patch 一旦通过 checks，循环立即停止并返回 `pr_created`、`checks_passed` 与 Draft PR intent；持续失败到迭代上限时保留最后合法 partial patch，返回 `budget_exhausted` 和最后失败摘要。模型未产生合法 patch、verifier setup/基础设施中断和普通 check 失败保持不同终态。

最终验证通过：`uv lock --check`、严格 `mypy`、72 个 pytest（包含真实 Docker verifier 场景）、wheel/sdist 构建、`git diff --check`、无残留 verifier 容器，以及双轴 code review（Standards 0 findings，Spec 0 findings）。实现提交为 `3e01dea`、`18bc3a8`、`1365a5e` 和 `65a8b1e`。

## Comments

- 2026-08-31：完成失败反馈修复循环、逐轮证据、预算终止与基础设施中断路径；Ticket 由 `ready-for-agent` 经 `claimed` 更新为 `resolved`。
