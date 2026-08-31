# 08 — 用 Gemini 驱动受控 patch 流程

**What to build:** 用 Gemini Developer API adapter 驱动已建立的受控探索和 patch 流程，同时保持 Model seam 可替换、默认测试确定性且凭据不进入报告。v0 明确不支持 Vertex AI 或 Gemini 内置执行工具。

**Blocked by:** 04 — 通过受控工具生成首个安全 patch

**Status:** resolved

- [x] Gemini adapter 通过 `ChatGoogleGenerativeAI` 接入 Gemini Developer API，并显式选择非 Vertex backend。
- [x] adapter 只从 PatchLoop 专用秘密输入接收 API key，不采用环境中的其他 Google 凭据或仓库 `.env`。
- [x] 模型名称来自经过校验的仓库配置，并在 Run Report 中记录非敏感标识。
- [x] 受控工具以结构化声明提供给 Gemini；函数调用仍由 PatchLoop 校验和执行。
- [x] Gemini Code Execution、Google Search、URL Context、Computer Use 和 Remote MCP 均不启用。
- [x] 普通文本、单个/多个工具调用、无效参数、未知工具、超时、限流和 provider 错误映射为稳定的 Model adapter 结果。
- [x] 日志、LangGraph state、artifact 和报告均不包含 API key 或完整原始模型上下文。
- [x] 默认 contract tests 使用模拟 provider 响应；真实 Gemini smoke test 必须显式启用且不属于默认测试。

## Answer

已完成 Ticket 08。`GeminiPatchModelAdapter` 通过官方
`langchain-google-genai` 的 `ChatGoogleGenerativeAI` 接入 Gemini Developer
API，固定 `vertexai=False`，只读取 `PATCHLOOP_GEMINI_API_KEY`，并关闭 SDK
重试、设置 60 秒 provider request timeout。`.patchloop.yml` 的模型标识会在
联网前校验；省略时默认使用 `gemma-4-31b-it`。公共 `run` seam 会拒绝 adapter
与配置模型不一致的请求，Run Report 同步记录 provider 和真实配置模型。

Gemini 只收到六个结构化受控工具声明，任何函数调用仍由 PatchLoop 执行参数、
路径、预算和 patch policy 校验。普通文本、单个或多个工具调用、无效参数、未知
工具、timeout、rate limit 和通用 provider error 均有确定性 contract coverage；
异常原文、API key 和完整 provider message history 不会进入报告、artifact、日志或
LangGraph state。使用与安全边界记录在 `docs/gemini.md`。

最终验证通过：`uv lock --check`、严格 `mypy`、70 个默认 pytest；12 个真实
Docker 场景因本机缺少 `alpine:3.22` fixture image 而条件跳过。wheel 和 sdist
构建成功，双轴 code review 最终为 Standards 0 findings、Spec 0 findings。真实
Gemini smoke test 位于默认发现规则之外，必须显式设置
`PATCHLOOP_RUN_GEMINI_SMOKE=1` 才运行；本次未运行 live smoke，未消费 Gemini
API。实现提交为 `32972bb`、`1e3c02b` 和 `3e1dd4e`。

## Comments

- 2026-08-31：完成 Gemini Developer API adapter、专用凭据隔离、受控函数调用、稳定错误映射、显式 live smoke 边界与相关文档；Ticket 由 `ready-for-agent` 经 `claimed` 更新为 `resolved`。
