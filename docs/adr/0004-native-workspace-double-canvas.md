# ADR-0004：原生 WorkspaceShell 与双层对话画布

- 状态：Accepted
- 日期：2026-09-01
- 实现状态：Verified local preview（Standalone 本机纵向切片）

## 背景

早期 Local Graph Chat 把工作流放在独立 Graph Window 中，并把双击节点定义为激活具体对话、通知聊天页刷新和关闭弹窗。这个交互可以验证路线切换，却把“浏览图”“检查一段对话”和“真正继续这段对话”混成了同一个动作；它还依赖 popup 生命周期和跨窗口同步，无法形成长期 Agent 工作台的原生信息架构。

dsh-synapse 证明了把宿主会话投影为可缩放卡片画布的价值，但 WeavePath 的顶层节点具有更强的路线语义：每个 `ConversationInstance` 是一条具有独立 transcript、checkpoint 和宿主绑定的具体记忆路线，不能退化成一张仅按消息顺序连接的全局 turn 图。

因此需要同时保留两种粒度：顶层管理对话实例和实验路线，节点内部检查该实例本地发生的用户轮次、回答、工具调用与失败。

## 决策

### 1. 工作流成为 WorkspaceShell 的原生视图

主界面由同一个 `WorkspaceShell` 承载：

```text
WorkspaceShell
├─ Chat surface
└─ Workflow surface
   ├─ Workflow Graph
   └─ Turn Canvas（按需钻入）
```

- 顶栏提供明确的“对话 / 工作流”切换；用户不需要发送模型消息来打开工作流。
- 两个 surface 属于同一应用状态树。切换视图不得清空聊天草稿、消息滚动、正在进行的请求或工作流画布镜头。
- `/graph` 可以保留为可选兼容入口或未来 Desktop surface，但不再是默认工作流入口，也不定义领域语义。
- 切换到工作流只改变 UI 视图，不调用 `activate_instance`，不导航宿主，不向 composer 写入文本。

### 2. 使用两层图，而不是在顶层混合两种节点

第一层是 **Workflow Graph**：

- 每张卡片对应一个具体 `ConversationInstance`。
- 边由 `parent_instance_id` 推导，表示不可变的路线继承关系。
- 相同 `topic_id` 的多个实例仍是不同卡片或明确的 route choice，不共享 transcript。
- fork、prune、revision、tombstone 和 host binding 继续使用既有 graph-core 语义。

第二层是 **Turn Canvas**：

- 双击顶层实例卡片进入该实例的 Turn Canvas，不激活对话。
- 一个 turn 以该实例的本地 `user` message 开始，并包含下一条本地 `user` message 之前的本地 assistant、tool message；failure/operation 在相应事件投影可用后作为同一 turn 的扩展时间线。
- Turn Canvas 是 `scope=local` 的只读投影；祖先 checkpoint 中的继承消息不得再次渲染成当前实例的卡片。
- 继承信息只在路线面包屑、checkpoint 摘要和 `inheritedMessageCount` 中展示，并可通过显式检查入口查看。
- 返回第一层时恢复顶层 viewport、节点位置、折叠和 selection；再次进入同一实例时恢复该实例自己的 Turn Canvas 状态。

产品语言可以说“每个对话框内置一张画布”，但实现只复用一个按需加载的 Turn Canvas surface，不在所有顶层节点中同时嵌套 React Flow。

### 3. 浏览、选择和继续对话严格分离

交互定义如下：

| 动作 | 结果 | 是否 activate |
|---|---|---|
| 切换到工作流 | 显示上次工作流镜头 | 否 |
| 单击实例或 route choice | 只改变当前 surface 的 selection | 否 |
| 双击实例或 route choice | 钻入该具体实例的 Turn Canvas | 否 |
| 单击 turn | 显示该 turn 详情 | 否 |
| “返回工作流” | 返回第一层并恢复镜头 | 否 |
| “继续对话” | 验证具体实例，调用 activate/navigate，成功后回到 Chat | 是 |

激活失败时保持当前画布、selection 和草稿，并显示结构化错误与重试入口。不得用 follow-up message、composer 文本或伪 deep link 模拟成功。

### 4. 从具体 turn 分支必须冻结精确 checkpoint cursor

从 Turn Canvas 中的某轮创建子实例时，命令必须同时携带：

```text
source_instance_id
source_cursor_kind
source_cursor_value
source_content_revision
expected_content_revision
```

Standalone 的当前 cursor 约定为：

- `localUserTurn`：`source_cursor_value` 是源实例中的本地用户消息 ID；checkpoint 包含该用户问题以及下一次本地用户提问之前产生的回答、工具和失败事件。
- `instanceHead`：没有选择具体 turn 时，cursor 指向请求接受时的实例头，并记录对应 `source_content_revision`。

checkpoint 继续保存冻结的有效消息快照；cursor 是可审计的分支锚点，不替代快照。请求还必须带 `expected_content_revision`，源实例已变化时返回 409，不能在新的内容头上悄悄重放旧 selection。

未来 HostAdapter 使用 provider-neutral 的 checkpoint reference：

```text
HostCheckpointRef
  provider
  hostConversationId
  cursorKind
  cursorValue
  sourceContentRevision
  digest
  capturedAt
```

具体 adapter 可以映射为 DSH seed boundary、Codex turn cursor、Claude session/message ref 或本地 message ID；这些宿主字段不得进入 graph-core 的 parent/topic 语义。

### 5. UI metadata 与 graph semantics 分离

以下内容是 UI metadata：

- workflow viewport、实例卡片视觉位置和折叠状态；
- 当前 surface 的 selected instance；
- 每个实例 Turn Canvas 的 viewport、turn 位置、折叠和 selected turn；
- 界面语言、面板宽度和其他纯展示偏好。

它们按 `workflow_id`，必要时再按 `instance_id` 命名空间保存。当前可使用浏览器本地存储；未来若跨设备同步，也必须进入独立 preference store。

UI metadata：

- 不修改 `parent_instance_id`、`parent_checkpoint_id`、topic、host binding 或 tombstone；
- 不增加 `graph_revision` 或 `content_revision`；
- 不充当全局 `active_instance_id`；
- 丢失或损坏时只能重置画布布局，不能改变记忆路线。

### 6. 宿主能力必须显式协商并安全降级

HostAdapter 除通用 fork/navigate/inspect 能力外，还需声明：

```text
can_read_local_turns
can_fork_from_checkpoint
supported_checkpoint_cursor_kinds
can_navigate
```

降级规则：

- 无法读取 transcript：显示节点、路线和宿主绑定信息，不伪造 Turn Canvas 内容。
- 能读取 transcript 但无法区分本地与继承消息：标记投影不可靠，默认不提供“从此 turn 分支”。
- 无法按选定 cursor fork：禁用精确分支，或在用户明确确认后提供“从实例头分支”；不能静默扩大继承范围。
- 无法导航：保留画布并提供宿主任务列表、复制标识或 companion 入口；不能宣称已切换。
- 宿主返回的 checkpoint 必须绑定具体 instance 和 content revision；不允许读取或拼接 sibling route。

## 后果

优点：

- 工作流成为长期工作台的一等视图，不再消耗对话上下文来打开。
- 顶层路线保持简洁，节点内部又能提供 dsh-synapse 风格的逐轮检查体验。
- 双击不再产生意外会话切换；用户明确决定何时继续和激活。
- 可以从某一轮精确分支，同时保留 revision、checkpoint 和 sibling memory 隔离。
- 画布布局变化不会污染领域事件、路线 revision 或 Agent Run 的输入真相。

代价：

- 需要维护顶层与每个实例各自的画布状态和读模型。
- Transcript projector 必须正确识别 user、assistant、tool、failure 和注入上下文。
- 不同宿主可支持的精确 cursor 不一致，UI 需要 capability-aware 状态和明确降级。
- 原独立窗口双击即 activate 的自动化与文档已按新语义改写；可选 `/graph` 兼容窗口仍需独立于默认入口继续维护。

## 不变量与验收边界

- Turn Canvas 中不得出现继承 checkpoint 消息作为本地 turn 卡片。
- 双击节点、route choice 或 turn 的网络行为不得包含 activate/navigate。
- 只有显式“继续对话”可以 activate；成功前不得离开工作流 surface。
- 从 turn 分支必须冻结到该 turn 的 cursor，并拒绝 stale `expected_content_revision`。
- UI metadata 的写入不得改变 graph/content revision。
- 同 topic 多路线必须先解析到具体 instance，Turn Canvas 和继续对话都不能使用逻辑 topic 代替实例 ID。
- 本 ADR 的 Standalone 本机纵向切片已完成后端、前端、typecheck/build 与真实浏览器 E2E，状态为 **Verified local preview**；这不代表正式 Codex/Claude HostAdapter、failure/approval 完整事件投影、跨设备同步或生产部署已经完成。

## 本机验证记录

2026-09-01 的统一验证基线包含后端 99 项测试、前端 63 项测试、Python compileall、TypeScript typecheck、production build 和真实浏览器 E2E。浏览器路径确认了原生“对话 / 工作流”切换、双击只钻入 Turn Canvas、local-only 内容投影、从具体 turn 分支、画布状态恢复，以及显式“继续对话”后才 activate 并返回 Chat。

该记录只适用于单用户、本机、Standalone Local Graph Chat 纵向切片。`/graph` 是兼容入口；正式 HostAdapter 的 capability degradation 仍需 adapter contract 与对应宿主 E2E。
