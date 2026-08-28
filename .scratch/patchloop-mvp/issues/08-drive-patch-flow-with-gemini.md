# 08 — 用 Gemini 驱动受控 patch 流程

**What to build:** 用 Gemini Developer API adapter 驱动已建立的受控探索和 patch 流程，同时保持 Model seam 可替换、默认测试确定性且凭据不进入报告。v0 明确不支持 Vertex AI 或 Gemini 内置执行工具。

**Blocked by:** 04 — 通过受控工具生成首个安全 patch

**Status:** ready-for-agent

- [ ] Gemini adapter 通过 `ChatGoogleGenerativeAI` 接入 Gemini Developer API，并显式选择非 Vertex backend。
- [ ] adapter 只从 PatchLoop 专用秘密输入接收 API key，不采用环境中的其他 Google 凭据或仓库 `.env`。
- [ ] 模型名称来自经过校验的仓库配置，并在 Run Report 中记录非敏感标识。
- [ ] 受控工具以结构化声明提供给 Gemini；函数调用仍由 PatchLoop 校验和执行。
- [ ] Gemini Code Execution、Google Search、URL Context、Computer Use 和 Remote MCP 均不启用。
- [ ] 普通文本、单个/多个工具调用、无效参数、未知工具、超时、限流和 provider 错误映射为稳定的 Model adapter 结果。
- [ ] 日志、LangGraph state、artifact 和报告均不包含 API key 或完整原始模型上下文。
- [ ] 默认 contract tests 使用模拟 provider 响应；真实 Gemini smoke test 必须显式启用且不属于默认测试。
