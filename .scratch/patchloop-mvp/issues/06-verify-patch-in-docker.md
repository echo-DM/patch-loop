# 06 — 在 Docker 中隔离验证 patch

**What to build:** 让 PatchLoop 根据仓库提供的 verifier image、setup 和 checks，在 Ubuntu runner 上的 Docker 临时副本中验证 patch。目标仓库命令拿不到 Gemini 密钥或 GitHub 写 Token，且其文件副作用不会污染权威 patch。

**Blocked by:** 04 — 通过受控工具生成首个安全 patch

**Status:** resolved

- [x] 版本化仓库配置要求显式 verifier image、setup 命令和至少一个 check，并对命令与资源配置进行校验。
- [x] Verifier adapter 在独立临时仓库副本中应用候选 patch，再执行 setup 和 checks。
- [x] verifier 进程环境不包含 Gemini 密钥、GitHub 写 Token、仓库 `.env` 凭据或 Agent 私有状态。
- [x] verifier 具有明确的执行时间、内存、进程数量和输出大小限制，超限被规范化为可诊断结果。
- [x] setup/checks 对临时副本的额外修改在验证结束后丢弃，不会进入权威 patch bundle。
- [x] check 结果包含命令标识、退出状态、截断并脱敏的输出和失败类别。
- [x] setup、check 和 Docker 基础设施失败保持可观察区别。
- [x] integration tests 使用最小 fixture repository 运行真实 Docker，并验证秘密隔离和副作用丢弃。

## Answer

已完成 Ticket 06。PatchLoop 现在提供公开 `DockerVerifierAdapter`：它在一次性仓库副本中重建并应用候选 patch，依次执行仓库声明的 setup/checks；权威 workspace 只保留模型生成的 patch，容器命令产生的额外文件修改会随临时副本丢弃。

verifier 配置显式声明 image、非空 setup/checks，以及单命令超时、内存、PID 和输出上限。容器看不到 Agent 进程中的 Gemini/GitHub 凭据、仓库 `.env` 或 PatchLoop 私有状态；`.env` 候选修改也被受控工具拒绝。setup、普通 check、超时、OOM、PID、输出上限与 Docker 基础设施失败使用稳定命令 ID、退出状态、脱敏有界输出和失败类别分别报告。

最终验证通过：`uv lock --check`、严格 `mypy`、68 个 pytest（其中 14 个 Docker verifier 场景实际运行 `alpine:3.22`）、wheel/sdist 构建、`git diff --check` 和双轴 code review（Standards 0 findings，Spec 0 findings）。实现提交为 `5b3857d`、`a9304c5` 和 `0461654`。

## Comments

- 2026-08-30：实现、真实 Docker 验证、review 修复与最终复审完成；Ticket 由 `ready-for-agent` 经 `claimed` 更新为 `resolved`。
