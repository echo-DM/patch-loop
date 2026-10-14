# 04 — 在 Actions 摘要中展示 Provider 恢复情况

**What to build:** 用户能在 Actions 摘要看见重试次数、累计等待、恢复情况和停止原因；旧报告依然可展示。

**Blocked by:** 02 — 在次数和累计等待上限内恢复暂时性失败

**Status:** resolved

- [x] Actions 摘要从已建立的报告 JSON 安全摘要显示重试次数、累计 Retry waiting time、已恢复请求数，以及失败时适用的停止原因。
- [x] 明确区分请求恢复和整次运行成功；重试后成功的运行保留成功终态，不展示为未解决失败。
- [x] 旧报告没有重试摘要时仍能生成现有 Actions 摘要；新报告保持旧字段与发布产物完整性规则兼容。
- [x] 只展示经过验证的有界数字和受识别的稳定分类；畸形字段、未知分类及任意 Provider 文本不能直接进入 Actions 摘要。
- [x] 用固定报告样例覆盖无重试、成功恢复、次数或等待耗尽、永久错误和服务端建议超限的展示；后者可使用稳定报告样例，不依赖实时 Provider 请求。
- [x] 保留当前 PR 正文和 Issue 反馈范围；同步报告使用说明，相关离线验收与严格类型检查通过。

## Answer

Implemented Actions rows for validated provider retry attempts, actual waiting,
recovered requests, and recognized stop reasons on failed runs. Request recovery
is displayed separately from Terminal outcome and Checks. Old reports without
the optional object keep existing rows. Invalid counts, nonfinite/out-of-range
waiting, missing fields, and arbitrary stop classifications are rejected before
writing a summary. Unknown provider fields are not displayed. PR/Issue wording
and artifact identity rules retain their existing scope. Fixed policy constants
are shared by retry accounting and Actions validation/display.

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
