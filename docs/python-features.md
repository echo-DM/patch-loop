# PatchLoop 中的 Python 语言特性

PatchLoop 用 Python 将模型、文件操作、验证和 GitHub 发布组织成有明确边界的程序。
最值得学习的三个用法是：**用 `Protocol` 描述依赖的能力，用数据类和类型标注明确数据，
用函数对象组织受控工具。** 它们分别回答“依赖什么能力”“传递什么数据”和“如何调用能力”。

本文依据当前源码与现有 acceptance tests 归纳实现，不代表一次实际运行或 release 验收。
整体流程见[架构概览](architecture.md)，模型与程序的责任划分见[AI 概念说明](ai-concepts.md)。
项目声明 Python 最低版本为 3.12；文中用法包含语言语法、标准库和类型系统，并非都由
Python 3.12 首次引入。

## 1. Protocol：让核心流程依赖能力

核心循环需要模型给出下一步，但不必了解 Gemini SDK 如何组织请求。
[adapters.py](../src/patchloop/adapters.py) 用 `Protocol` 声明模型契约：

```python
class PatchModel(Protocol):
    def next_turn(
        self,
        task: EvaluationTask,
        observations: tuple[ToolResult, ...],
        available_tools: tuple[ToolDefinition, ...],
    ) -> ModelTurn: ...
```

只要一个对象提供签名符合契约的 `next_turn()`，就可以作为模型依赖接受静态类型检查。
实现类不必继承 `PatchModel`。这是 Python 的鸭子类型与结构化类型检查结合的用法。

实际对象的传递路径是：

```text
真实模型 GeminiPatchModelAdapter ───────┐
                                      ├─→ RunAdapters.model
测试模型 DeterministicPatchModelAdapter ┘          ↓
                                            core._run_patch()
                                                  ↓
                                            model.next_turn(...)
```

[core.py](../src/patchloop/core.py) 的 `RunAdapters` 接收这些依赖；
[gemini.py](../src/patchloop/gemini.py) 提供真实模型实现；确定性模型则按预设顺序返回
`ModelTurn`，并记录收到的工具定义和观察结果。Verifier 同样通过 `VerifierAdapter.verify()`
区分真实 Docker 验证与固定测试结果。`GitHubClient` 则描述授权与评论读取需要的能力，
其 HTTP 实现是 `GitHubApiClient`。

这种组织方式让 acceptance tests 能在不调用真实模型、不写入 GitHub 的条件下，检验
核心调度、工具执行和报告逻辑。例如
[test_generate_safe_patch.py](../tests/acceptance/test_generate_safe_patch.py) 注入预设的
模型回合与验证结果，检查生成的 patch 及其完整性信息。

**边界：** `Protocol` 本身不会创建实现、包装对象或限制对象权限。依赖注入是项目采用的
设计方式，`Protocol` 为它提供可检查的契约；实际权限仍由工具校验、workflow 权限配置和
执行隔离落实。

## 2. 数据类与类型标注：明确每一步的数据

### 2.1 dataclass：表达任务、动作和结果

任务快照、模型回合、工具结果和验证请求都使用数据类。例如
[adapters.py](../src/patchloop/adapters.py) 中的任务：

```python
@dataclass(frozen=True)
class EvaluationTask:
    id: str
    title: str
    body: str
    authorized_by: str | None = None
    supplemental_requirements: tuple[IssueComment, ...] = ()
    reference_material: tuple[IssueComment, ...] = ()
```

`dataclass` 自动生成初始化、打印表示和相等比较等方法，减少样板代码。调用方通过
`task.title` 访问字段，函数签名也能明确写出输入是任务、输出是决定。

`frozen=True` 阻止常规的字段重新赋值，适合表达 Gate 冻结的任务。
评论集合使用 `tuple`，不能追加或删除元素；评论本身也是冻结的数据类。
`ModelTurn`、`ToolResult` 和 `VerificationRequest` 则为模型、工具与验证器之间的数据
传递提供统一对象。

这里的冻结是**浅层冻结**：例如 `ToolCall.arguments` 可以由普通字典提供，即使不能
重新赋值 `arguments` 字段，内部字典仍可能被修改。`Mapping` 标注提供只读访问契约，
也不会自动把传入字典变成不可变对象。

另一个细节是 `ToolDefinition.argument_descriptions` 使用 `field(default_factory=dict)`，
为每个实例创建独立默认字典，避免共享可变默认值。

### 2.2 TypedDict、Literal 与联合类型：表达报告结构和状态

内部对象适合数据类，输出为 JSON 的报告则使用 `TypedDict`。例如
[core.py](../src/patchloop/core.py) 定义：

```python
class DraftPRIntent(TypedDict):
    intent: Literal["draft_pr"]
    reason: Literal["patch_generated"]
    patch_sha256: str
```

`PublicationIntent` 是 `NoPublicationIntent | IssueFeedbackIntent | DraftPRIntent`，
分别表达不发布、Issue 反馈与 Draft PR 发布意图。各类意图具有不同的字段，类型检查器
可以根据 `intent` 的字面量值区分它们。

| 用法 | 在项目中表达什么 |
| --- | --- |
| `TypedDict` | 报告字典的键与值类型，例如 `RunReport` |
| `Literal` | 有限状态，例如检查结果的 `"passed"` 与 `"failed"` |
| `A \| B` | 多种可能的对象或结果，例如任务输入和发布意图 |
| `str \| None` | 值可能不存在，例如授权者 |
| `NotRequired` | 字典中的键可以缺省，例如报告的澄清问题字段 |

`NotRequired` 表达“可以没有这个键”；`T | None` 表达“值可以是 `None`”，两者含义不同。
[pyproject.toml](../pyproject.toml) 启用了严格 mypy 检查，让这些声明用于检查模块间的数据传递。

### 2.3 静态类型与运行时校验各有责任

`TypedDict` 在运行时仍是普通 `dict`；`Literal` 不会自动拒绝错误字符串；数据类构造函数
也不会根据标注验证每个参数。`cast()` 只改变类型检查器对对象的看法，不转换对象。

因此，[config.py](../src/patchloop/config.py) 先检查外部输入，再声明已确认的类型：

```python
def _mapping(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigError(
            "invalid_config_value", f"{path} must be a mapping with string keys."
        )
    return cast(dict[str, object], value)
```

这段代码检查了字典容器和字符串键；具体字段内容由后续解析函数继续检查。
工具参数也通过 `ControlledTools._validate_arguments()` 与各处理函数验证。
`ModelTurn` 虽然声明了三种可选字段，核心循环仍需检查模型是否同时选择了多种动作。

**类型标注帮助发现程序内部的开发错误，运行时校验检查低信任输入。** 两者共同构成数据
契约，但不能互相替代。

## 3. Callable 与绑定方法：把工具组织成能力表

Python 函数和方法可以作为对象被保存、传递和调用。
[controlled_tools.py](../src/patchloop/controlled_tools.py) 利用这一点注册工具：

```python
@dataclass(frozen=True)
class _ToolRegistration:
    definition: ToolDefinition
    handler: Callable[[Mapping[str, object]], object]
```

每条记录保存工具定义与处理函数，例如 `self._read_file`、`self._apply_patch`。
这些是绑定方法，已经携带 `self`，调用时能访问工作区、预算与验证器。
构造函数再通过字典推导式建立“工具名 → 注册记录”的映射。

`execute()` 的执行路径是：

```text
ToolCall
   ↓ 检查调用 ID，按名称查找注册记录
   ↓ 校验参数字段
registration.handler(call.arguments)
   ↓ 处理函数执行具体校验和操作
ToolResult → 下一轮模型观察
```

这个入口统一处理未知工具、重复调用 ID 和参数错误，各处理函数负责相应的路径、内容与
预算检查。函数对象让分发方式简洁；工具能做什么，由注册表和执行路径中的规则共同决定。

函数对象也用于时间依赖：[core.py](../src/patchloop/core.py) 的 `RunAdapters.clock`
类型为 `Callable[[], float]`，默认是 `monotonic`。
[test_hard_budgets.py](../tests/acceptance/test_hard_budgets.py) 中的 `StepClock` 实现
`__call__()`，使实例可以像函数一样调用，按序返回预设时间。因此测试能够模拟超时，
而无须真的等待一分钟。

## 4. 支撑执行细节的其他用法

### 4.1 闭包、nonlocal 与赋值表达式

`core._run_patch()` 内部的 `check_wall_time()` 捕获外层的时钟、开始时间与预算配置，
并通过 `nonlocal elapsed_seconds` 更新外层耗时变量。它让模型调用前后的检查使用同一逻辑。

循环中的 `if (error := check_wall_time()) is not None:` 使用赋值表达式 `:=`，
同时保存检查结果和判断是否停止。

### 4.2 上下文管理器与 finally：管理资源生命周期

[docker_verifier.py](../src/patchloop/docker_verifier.py) 使用
`with tempfile.TemporaryDirectory(...)` 管理一次性工作区，退出上下文时自动清理目录。
Docker 容器的执行约束来自验证器实现与 Docker 配置，临时目录的自动清理只负责目录生命周期。

`ControlledTools._replace_text()` 与 `_replace_bytes()` 使用 `with os.fdopen(...)`
关闭文件，先写入目标目录中的临时文件，再用 `os.replace()` 替换目标。
`finally` 清理剩余临时文件，使正常返回与异常路径都执行清理。

### 4.3 str、bytes 与 None：区分文本编辑和原始快照

模型提交的新文件内容是 `str`，原始文件快照保存为 `bytes | None`。
`_apply_patch()` 用 `read_bytes()` 保存修改前内容，`_restore()` 按原始字节恢复。

| 快照值 | 原始状态 | 恢复动作 |
| --- | --- | --- |
| `None` | 文件不存在 | 删除本次创建的文件 |
| `b""` | 文件存在但为空 | 恢复空文件 |
| 其他 `bytes` | 文件存在且有内容 | 恢复原始字节 |

生成文本 diff 时，`_diff()` 才把快照解码为字符串并交给 `difflib.unified_diff()`。
这个类型区别避免把“文件不存在”和“空文件”混为一谈，也避免恢复时重新编码改变原始内容。

### 4.4 集合、推导式与迭代器：简洁表达检查与顺序

`set` 保存已见的工具调用 ID，并通过集合差计算缺失参数与多余参数。
`frozenset` 保存允许写入的权限值和受保护路径集合，避免集合自身被增删。
`tuple` 表达工具定义、观察结果和文件列表等固定序列，但不会递归冻结其中的对象。

确定性模型使用 `iter()` 和 `next()` 按顺序重放回合，捕获 `StopIteration` 表达回合耗尽。
字典推导式构造工具注册表，生成器表达式用于统计 diff 行数、筛选变化文件等操作。

### 4.5 异常继承与异常链：在边界形成稳定反馈

[errors.py](../src/patchloop/errors.py) 定义带有 `code`、`message` 的 `PatchLoopError`，
再派生任务、配置与基础设施异常。
[cli.py](../src/patchloop/cli.py) 根据异常类别输出 JSON 错误和相应退出码。

工具层捕获预期的 `ToolInputError` 和 `OSError`，转换为结构化 `ToolResult`；
没有把所有异常统一吞掉。解码等操作使用 `raise ... from error` 保留异常因果关系，
帮助定位底层错误。

## 5. 推荐阅读路径

1. 在 [adapters.py](../src/patchloop/adapters.py) 看 `EvaluationTask`、`ModelTurn`、
   `ToolResult`：先理解数据对象及其字段。
2. 继续看 `PatchModel`、`VerifierAdapter` 和确定性实现：理解契约与真实对象的区别。
3. 在 [core.py](../src/patchloop/core.py) 看 `RunAdapters`：理解依赖如何传入核心。
4. 在 [controlled_tools.py](../src/patchloop/controlled_tools.py) 看 `_ToolRegistration`、
   `ControlledTools.__init__()` 与 `execute()`：理解方法如何被保存并调用。
5. 回到 `core._run_patch()`：把模型回合、工具结果、闭包和预算检查串成执行过程。
6. 对照 [test_generate_safe_patch.py](../tests/acceptance/test_generate_safe_patch.py) 与
   [test_hard_budgets.py](../tests/acceptance/test_hard_budgets.py)，观察如何替换模型、验证器和时钟。

验证命令与运行前提统一见 [README](../README.md)，安全边界见[安全说明](security.md)。
