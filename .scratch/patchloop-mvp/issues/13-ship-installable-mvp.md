# 13 — 交付可安装的 PatchLoop MVP

**What to build:** 把已经验证的行为整理为可安装、可理解、可本地复现的开源 MVP。维护者可以添加一个最小 caller workflow 和版本化仓库配置，理解安全限制，并显式运行真实 Gemini/GitHub smoke test。

**Blocked by:** 11 — 把无 patch 终态反馈到 Issue；12 — 幂等更新同一 Issue 的 Draft PR

**Status:** resolved

- [x] 文档提供最小 caller workflow、所需 Job permissions、命名 Gemini secret 和版本固定建议。
- [x] 文档完整描述仓库配置，包括 verifier image、setup、checks、模型和所有可降低预算。
- [x] 文档解释标签授权、评论信任等级、Docker verifier、secret isolation、Draft PR 状态和一次 workflow run 的状态生命周期。
- [x] 文档明确 Docker 是 MVP 约束机制而非强敌对代码 sandbox，并说明自托管 runner 风险不在保证范围内。
- [x] 文档说明使用 `GITHUB_TOKEN` 创建的 PR 可能需要维护者批准原生 CI，且 PatchLoop checks 不能替代完整 CI。
- [x] 本地 CLI 复现路径和 reusable workflow 使用相同的 `run` interface、配置语义与 Run Report。
- [x] 默认测试、fixture Docker acceptance、workflow validation 和 credential-redaction cases 作为一条离线验证命令通过。
- [x] 真实 Gemini 与专用 GitHub 测试仓库 smoke test 只能显式启用，缺少凭据时安全跳过且不属于默认 CI。
- [x] 发布前验证所有 54 条 spec User Stories 已由实现、测试、文档或明确 Out of Scope 覆盖。

## Answer

PatchLoop MVP 现在提供公开安装指南、最小 caller workflow、完整版本 1
`.patchloop.yml` 示例、安全边界说明和逐条 54 User Stories 覆盖矩阵。caller 固定到
计划发布的 `v0.1.0`，并建议高安全需求采用不可变 commit SHA；仓库只需命名
`PATCHLOOP_GEMINI_API_KEY` Secret 和 caller 声明的权限，不需要 PAT、GitHub App、
Vertex AI 或 PyPI。

本地 `patchloop run` 默认保持无凭据的确定性 no-change 路径；显式 `--live` 才注入
Gemini Developer API 与 Docker verifier。两种 CLI 模式和 reusable workflow 都调用
公开 `patchloop.core.run`，读取相同配置语义并输出版本 1 Run Report。`--live` 可能
消费 Gemini 配额并修改传入的本地 workspace，但不执行 GitHub 写入。

`uv run python scripts/validate_offline.py` 作为一条发布前离线验收命令，强制确认
Docker daemon 与 `alpine:3.22` fixture image，运行锁文件检查、严格 mypy、完整 pytest、
wheel/sdist 构建、隔离 wheel 安装、MIT 包元数据和安装后 CLI 执行，再检查 diff
空白。这里的“离线”表示不调用真实 Gemini、不写真实 GitHub；首次依赖准备仍可能访问
包索引。

真实发布 smoke 使用独立私有 Smoke repository。模板只有在仓库变量
`PATCHLOOP_RUN_LIVE_SMOKE=1` 且命名 Gemini Secret 存在时才进入 credentialed Jobs；
否则 preflight 安全跳过。Gemini 与 GitHub standalone smoke 文件也在缺少显式开关或
凭据时跳过，且不属于默认 pytest/CI。候选 SHA 的真实 Gemini、GitHub Actions 和
Draft PR 验收，以及同 SHA 的 `v0.1.0` tag，仍由 Ticket 14 承担。

## Comments

- 2026-09-01：最终执行 `uv run python scripts/validate_offline.py` 通过：严格 mypy、
  139 个 pytest（包含 14 个真实 Docker verifier 场景，无跳过）、wheel/sdist、隔离
  wheel 安装、MIT `License-Expression`、安装后 CLI 运行与 `git diff --check` 均成功。
  无凭据单独运行 Gemini/GitHub smoke 得到 2 skipped，未调用真实 Gemini、未运行真实
  GitHub Actions、未创建真实 Issue/分支/Draft PR，也未消费 API 配额。
- 2026-09-01：双轴 review 初审发现 smoke 缺凭据未安全跳过、构建产物未被实际安装、
  Smoke repository 术语漂移、重复 fixture setup 和候选 SHA 双点配置风险；全部修复后
  复审为 Standards 0 findings、Spec 0 findings。用户选择 MIT，包和仓库均记录标准
  MIT 许可证。实现提交为 `d0ff919`、`e98260c`、`7b3e3c1`、`a69ceea` 和
  `ddf9960`；所有提交仅在本地，未 push。
