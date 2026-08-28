# 13 — 交付可安装的 PatchLoop MVP

**What to build:** 把已经验证的行为整理为可安装、可理解、可本地复现的开源 MVP。维护者可以添加一个最小 caller workflow 和版本化仓库配置，理解安全限制，并显式运行真实 Gemini/GitHub smoke test。

**Blocked by:** 11 — 把无 patch 终态反馈到 Issue；12 — 幂等更新同一 Issue 的 Draft PR

**Status:** ready-for-agent

- [ ] 文档提供最小 caller workflow、所需 Job permissions、命名 Gemini secret 和版本固定建议。
- [ ] 文档完整描述仓库配置，包括 verifier image、setup、checks、模型和所有可降低预算。
- [ ] 文档解释标签授权、评论信任等级、Docker verifier、secret isolation、Draft PR 状态和一次 workflow run 的状态生命周期。
- [ ] 文档明确 Docker 是 MVP 约束机制而非强敌对代码 sandbox，并说明自托管 runner 风险不在保证范围内。
- [ ] 文档说明使用 `GITHUB_TOKEN` 创建的 PR 可能需要维护者批准原生 CI，且 PatchLoop checks 不能替代完整 CI。
- [ ] 本地 CLI 复现路径和 reusable workflow 使用相同的 `run` interface、配置语义与 Run Report。
- [ ] 默认测试、fixture Docker acceptance、workflow validation 和 credential-redaction cases 作为一条离线验证命令通过。
- [ ] 真实 Gemini 与专用 GitHub 测试仓库 smoke test 只能显式启用，缺少凭据时安全跳过且不属于默认 CI。
- [ ] 发布前验证所有 54 条 spec User Stories 已由实现、测试、文档或明确 Out of Scope 覆盖。
