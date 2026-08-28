# 01 — 建立可执行的 no_change tracer bullet

**What to build:** 建立 PatchLoop 的第一个可运行纵向切片。用户可以通过核心 CLI 提交一个无需修改的任务，系统读取版本化仓库配置，经过统一的 `run` interface，返回结构化、脱敏的 `no_change` Run Report。这个切片同时建立后续 ticket 共用的 fixture acceptance harness，但不暴露 LangGraph 内部结构为测试契约。

**Blocked by:** None — can start immediately

**Status:** resolved

- [x] 项目使用 `uv` 管理 Python 版本、依赖、命令和锁文件，并提供一个可执行的 PatchLoop CLI 入口。
- [x] 版本化仓库配置能够被读取和校验，未知版本、缺少必填字段和超过安全上限的值返回明确的配置错误。
- [x] 一个 fixture 任务可以穿过公开的 `run` interface，并返回 `no_change` 终态、任务标识和可操作说明。
- [x] Run Report 具有稳定的版本和结构，不包含凭据、完整模型上下文或隐藏推理。
- [x] CLI 以可测试的退出状态区分成功终态、任务失败和配置/基础设施错误。
- [x] acceptance test 只断言 CLI 输出、Run Report 和终态，不断言内部模块、节点名称或调用顺序。
- [x] 默认测试完全离线、确定性运行，并成为后续场景复用的 fixture harness。

## Answer

Implemented the first installable `patchloop run` slice with a versioned configuration schema, hard budget ceilings, a public `run` interface, and a versioned sanitized `no_change` Run Report. The reusable acceptance fixture invokes the installed CLI as a subprocess and covers success, task-input failure, and configuration failure exit statuses without depending on internal orchestration.

## Comments

- 2026-08-28: Validated with `uv lock --check`, strict `mypy`, the full offline pytest suite (7 tests), a wheel/sdist build, and `git diff --check`. Post-implementation review added an explicit evaluator adapter, honest failure for tasks that may need edits, structured infrastructure errors, and a shared CLI fixture harness.
