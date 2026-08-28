# 06 — 在 Docker 中隔离验证 patch

**What to build:** 让 PatchLoop 根据仓库提供的 verifier image、setup 和 checks，在 Ubuntu runner 上的 Docker 临时副本中验证 patch。目标仓库命令拿不到 Gemini 密钥或 GitHub 写 Token，且其文件副作用不会污染权威 patch。

**Blocked by:** 04 — 通过受控工具生成首个安全 patch

**Status:** ready-for-agent

- [ ] 版本化仓库配置要求显式 verifier image、setup 命令和至少一个 check，并对命令与资源配置进行校验。
- [ ] Verifier adapter 在独立临时仓库副本中应用候选 patch，再执行 setup 和 checks。
- [ ] verifier 进程环境不包含 Gemini 密钥、GitHub 写 Token、仓库 `.env` 凭据或 Agent 私有状态。
- [ ] verifier 具有明确的执行时间、内存、进程数量和输出大小限制，超限被规范化为可诊断结果。
- [ ] setup/checks 对临时副本的额外修改在验证结束后丢弃，不会进入权威 patch bundle。
- [ ] check 结果包含命令标识、退出状态、截断并脱敏的输出和失败类别。
- [ ] setup、check 和 Docker 基础设施失败保持可观察区别。
- [ ] integration tests 使用最小 fixture repository 运行真实 Docker，并验证秘密隔离和副作用丢弃。
