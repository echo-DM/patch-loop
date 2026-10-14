# 03 — 遵守服务端等待建议和剩余运行时长

**What to build:** 限流恢复遵守服务端最低等待；等待放不进预算时直接结束，每次请求超时随剩余总运行时间收紧。

**Blocked by:** 02 — 在次数和累计等待上限内恢复暂时性失败

**Status:** resolved

- [x] 读取受识别的结构化 RetryInfo 与 Retry-After，支持非负时长和合法 HTTP 日期；经过的日期视为零最低等待。
- [x] 同时有多个有效最低等待建议时取最长者；建议优先于默认退避，缺失、畸形或非有限值回退到默认策略。
- [x] 最低等待超过剩余等待预算或会耗尽剩余总时长时，不等待、不提前重试；只减少额外抖动时不得低于最低等待。
- [x] 每次 Provider 请求采用 60 秒与剩余总运行时长中的较小值作为请求超时；剩余时长耗尽时不发出新请求。
- [x] 等待和请求返回后继续检查运行时钟；不延长原有总时长上限，也不宣称网络超时能强制取消全部本地处理。
- [x] 使用已建立的安全 JSON 摘要解释等待建议导致的终止；不持久化原始 headers、响应 body 或异常链，Provider 恢复终止继续不产生发布意图。
- [x] 使用假响应和假时钟覆盖冲突建议、无效建议、过期日期、剩余预算不足、动态请求超时与编辑后终止；聚焦离线验收及严格类型检查通过，并同步权威说明。

## Answer

Implemented bounded RetryInfo/Retry-After normalization across SDK causes,
choosing the longest valid minimum and falling back for malformed/nonfinite data.
Legal HTTP dates include the three HTTP date forms; expired dates mean zero.
Provider minimum delays cannot be shortened to fit waiting/runtime budgets.
Each request receives min(60 seconds, remaining runtime). Clock checks remain
before/after waiting and after requests. Below the SDK's one-millisecond
resolution, no wait or new request is issued, avoiding a zero SDK timeout.
Provider failures after local edits or deadlines suppress publication and patch
return. Only normalized numeric hints cross the adapter seam; raw metadata is
excluded from reports, model context, and logs.

Validation on 2026-10-14:

- Fake provider responses, monotonic/wall clocks, wait functions, and jitter; no
  live credentials or real retry sleeps.
- Focused Gemini, retry-summary, hard-budget, and workflow acceptance checks passed.
- `uv sync --locked`: locked environment audited, no dependency/lockfile changes.
- `uv run --no-sync mypy`: no issues in 21 source files.
- Full `uv run --no-sync pytest`: 277 passed, 12 Docker integration checks skipped.
- Standards review: no hard violations; shared-limit suggestion resolved.
- Spec review: illegal HTTP dates and submillisecond SDK timeout issues fixed
  with acceptance cases; no remaining findings on re-review.
- Whitespace and local documentation links checked. No live Gemini/GitHub smoke
  or Docker-backed release validation was performed.

Authoritative behavior: [Gemini retry documentation](../../../docs/gemini.md).
Implementation evidence: [feature specification](../spec.md#current-implementation-evidence).
