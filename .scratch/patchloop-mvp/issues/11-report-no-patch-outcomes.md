# 11 — 把无 patch 终态反馈到 Issue

**What to build:** 让 PatchLoop 对没有可发布 patch 的终态提供明确、脱敏、可操作的 Issue feedback 或 workflow summary，不创建空分支或空 PR。

**Blocked by:** 09 — 从 Issue 标签运行可复用 workflow

**Status:** resolved

- [x] `needs_clarification` 向 Issue 发布有限、具体的问题，并说明重新触发所需动作。
- [x] `no_change` 解释为何不需要代码修改，且不创建分支或 PR。
- [x] 授权失败、配置错误、模型错误、Docker/setup 基础设施错误和无合法 patch 的预算耗尽具有不同反馈模板。
- [x] Issue feedback 和 workflow summary 只包含可观察结果，不包含隐藏推理、完整 prompt 或不必要源码。
- [x] credential-shaped values、恶意 Markdown、Issue 内容和 check 输出在反馈前被转义、截断和脱敏。
- [x] 低权限触发拒绝不会泄露仓库私有信息、密钥存在性或内部错误细节。
- [x] GitHub 写入失败保留 workflow summary 和稳定失败状态，且不会重试到产生重复评论。
- [x] contract tests 证明所有无 patch 终态均不会调用分支或 PR 创建操作。

## Answer

已完成无 patch 终态发布。Publish 现在先验证 Agent artifact 的完整性、Issue
身份和 publication intent；有合法 patch 时仍沿用受控 Draft PR 流程，没有 patch
时只调用一次 Issue comment API，不读取 base revision，也不创建 commit、分支或
Pull Request。

`needs_clarification` 评论只包含最多三个已验证问题，并要求回答后移除再重新添加
`patchloop` 标签；`no_change` 会解释无需修改且明确说明未创建分支或 PR。失败反馈
按授权、配置、模型、Docker/setup、基础设施及“预算耗尽但没有合法 patch”分成稳定
模板，只使用 Run Report 中的可观察终态，不发布 prompt、隐藏推理、Issue 原文或
check 输出。

所有动态反馈文本都会先经过集中凭据脱敏，再被单行化、截断和 Markdown/HTML
转义；覆盖 GitHub/OpenAI/Gemini、Authorization/Bearer、AWS access-key 与命名
secret 字段。低权限 Gate summary 不回显 actor、私有仓库或密钥状态。Issue comment
写入失败时保留 workflow summary，返回 `github_issue_feedback_failed`，且不进行自动
重试。Agent 在配置或凭据加载阶段失败时也会保留完整性保护的 publication context，
因此 Publish 能安全反馈这些终态。

## Comments

- 2026-09-01：最终验证通过 `uv lock --check`、严格 `mypy`、完整 pytest
  （120 passed）、wheel/sdist 构建和 `git diff --check`。首次双轴 review 发现一个
  credential-shaped 脱敏缺口和一个重复 outcome 分派 judgement call；已增加
  Bearer/AWS 脱敏回归并合并分派，复审为 Standards 0 findings、Spec 实现 0
  findings。本次使用 fixture/contract tests 验证 GitHub 请求，未向真实 Issue 写入
  评论，也未运行真实 GitHub Actions/Gemini smoke。
