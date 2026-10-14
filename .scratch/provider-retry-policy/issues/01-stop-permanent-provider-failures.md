# 01 — 永久 Provider 错误直接结束运行

**What to build:** 明确每日额度耗尽、参数/鉴权/权限/模型标识错误及未识别异常不再重试；暂时错误保留恢复资格，失败报告记录最后一次错误。

**Blocked by:** None — can start immediately

**Status:** resolved

- [x] 识别已确认设计中的暂时性连接错误、超时、HTTP 408、原因不明的 429，以及暂时性 500/502/503/504；这些错误保留 Provider retry 资格。
- [x] 永久客户端错误及其他未识别异常只尝试一次；确认的 Daily quota exhaustion 立即结束，并给出稳定、可操作的说明。
- [x] Daily quota exhaustion 只依据受识别的结构化每日额度标识判断；仅凭 429、RESOURCE_EXHAUSTED 或异常文字不能判定每日额度耗尽。
- [x] 在 SDK 包装异常及异常链中提取有界、规范化的失败元数据；原始异常、响应和凭证不进入模型上下文、报告或日志。
- [x] 连续两次不同的 Provider 失败最终报告最后一次错误，而不是第一次；稳定错误分类与现有终态约定保持兼容。
- [x] 编辑后遇到不可重试错误时，最终结果为 failed，无发布意图和返回补丁；不进入保留部分补丁的预算耗尽分支。
- [x] 保持 SDK 自动重试关闭，现有暂时性错误恢复与消息回滚路径仍可用；所需类型调整随这一行为一起交付。
- [x] 使用假响应覆盖上述完整运行路径，聚焦离线验收与严格类型检查通过；同步更新权威 Gemini 行为说明。

## Answer

已于 2026-10-14 完成 Ticket 01。Provider 失败通过有界异常链及结构化额度数据归一化为固定分类；永久错误、确认的 Daily quota exhaustion 和未识别异常直接终止，现有暂时性错误恢复保留。连续失败报告最后一次错误。

编辑后的 Provider 失败保持 `model` 类别，返回 `failed`、无补丁和无发布意图；即使错误在总时限后才返回，也不会误入预算耗尽的部分补丁发布分支。当前仍只有整次运行一次立即重试，次数/等待预算、服务端等待和摘要属于 Ticket 02-04。

验证：`uv sync --locked` 通过；Gemini 聚焦验收 72 个用例通过，与预算验收合计 83 个通过；完整离线 pytest 为 204 passed、12 skipped（Docker 集成用例）；严格 `uv run --no-sync mypy` 通过。测试使用假 Provider 和时钟，没有 Live Gemini/GitHub 调用。运行测试时使用 `uv run --no-sync` 及临时 uv cache，避免沙箱内 uv 同步的 macOS 系统配置错误。

Standards 审查的 Enum 封装建议已处理；Spec 审查发现的截止时间后失败分类问题已修复并补齐验收，复审均无遗留问题。权威行为说明见 [docs/gemini.md](../../../docs/gemini.md)。
