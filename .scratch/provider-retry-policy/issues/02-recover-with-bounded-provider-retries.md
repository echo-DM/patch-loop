# 02 — 在次数和累计等待上限内恢复暂时性失败

**What to build:** 暂时性 Provider 失败可经过退避恢复；每请求最多额外 3 次、全运行最多额外 6 次，累计 Retry waiting time 最多 60 秒，报告 JSON 可观察恢复用量。

**Blocked by:** 01 — 永久 Provider 错误直接结束运行

**Status:** resolved

- [x] 每个待完成请求最多额外重试 3 次，整次运行最多额外重试 6 次；首次尝试不计为重试，只有实际发起额外请求才增加计数。
- [x] 成功后下一请求重新获得每请求的重试机会；全运行重试次数与累计 Retry waiting time 不重置。
- [x] 没有服务端等待建议时，依次等待 5、10、20 秒，添加 0–1 秒均匀随机抖动；全部等待共同消耗 60 秒上限。
- [x] 基础等待放不进剩余预算时直接结束，不缩短基础退避；只因抖动超出时允许减少抖动。等待前后检查实际单调时钟与总运行预算，超限后不再等待或重试。
- [x] 重试同一个待完成请求，不重复工具动作或工具观察输入；重试不侵占正常模型回合额度，不增加工具调用或验证迭代计数。
- [x] 重试机会或等待上限耗尽时按 Provider 失败结束，即使已编辑文件也没有发布意图或返回补丁；现有工具与总时长预算语义保持独立。
- [x] 报告 JSON 增加可选安全摘要，包含重试次数、累计等待、已恢复请求数及适用的停止原因；成功恢复不向 errors 添加未解决错误，也不改变运行成功状态。
- [x] 采用固定默认值，不增加目标仓库配置项；维护旧报告的读取兼容性，SDK 自动重试继续关闭。
- [x] 使用假响应、时钟、等待和随机数验证分散故障、多请求共享预算、边界与编辑后恢复；聚焦离线验收和严格类型检查通过，并更新对应行为说明。

## Answer

Implemented shared run-local provider recovery in core and provider_retries.
Each pending request has at most three additional attempts, each run at most six,
with actual monotonic waiting shared across a 60-second scheduling allowance.
Fallback waits are 5/10/20 seconds plus 0-1 seconds jitter; only jitter is trimmed.
Success resets request eligibility, while run counters remain. Retries preserve
pending inputs and useful model turns without repeating tools or verification.
Provider exhaustion remains failed/model with no returned patch or publication.
The optional report object records attempts, actual waiting, recovered requests,
and stable stop reasons. SDK retries remain disabled and no YAML settings were added.

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
