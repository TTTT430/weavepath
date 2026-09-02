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
- Turn Canvas 是顶层对话 owner 的内部 Turn Tree；owner 基础路线与 `surface_scope=turn` 的内部路线共同投影为逐轮卡片，surface 可提交 route-scoped chat/fork 命令，但不维护第二份 transcript。沿祖先路线动态继承的消息不得再次渲染成当前路线的卡片。
- 继承信息只在路线面包屑、checkpoint 摘要和 `inheritedMessageCount` 中展示，并可通过显式检查入口查看。
- 返回第一层时恢复顶层 viewport、节点位置、折叠和 selection；再次进入同一实例时恢复该实例自己的 Turn Canvas 状态。

两层画布共享一套借鉴 dsh-synapse 的工作台视觉语法：浅色画布上的卡片、柔和连线、卡片边缘动作、画布控制和右侧详情检查器；深色主题只替换一致的颜色 token，不改变几何和信息层级。该决策不复制 dsh-synapse 的数据模型，也不宣称像素级复刻。

卡片边缘的 `＋` 是高频快捷命令：顶层卡片直接创建 workflow 子实例，turn 卡片直接创建 owner 内部的 `surface_scope=turn` 路线。两者都允许先不填写标题或首条内容。空内部路线必须通过 route-level read model 返回并立即显示占位卡，不能因为尚无本地 turn 而从第二层消失。

产品语言可以说“每个对话框内置一张画布”，但实现只复用一个按需加载的 Turn Canvas surface，不在所有顶层节点中同时嵌套 React Flow。

### 3. 浏览、选择和继续对话严格分离

交互定义如下：

| 动作 | 结果 | 是否 activate |
|---|---|---|
| 切换到工作流 | 显示上次工作流镜头 | 否 |
| 单击实例或 route choice | 只改变当前 surface 的 selection | 否 |
| 双击实例或 route choice | 钻入该具体实例的 Turn Canvas | 否 |
| 单击 turn | 显示该 turn 详情 | 否 |
| 在 Turn Canvas composer 发送 | 向该具体实例追加用户消息，并按当前模型配置生成回答 | 否 |
| 点击实例卡片的 `＋` | 立即创建 workflow 子实例；标题和首条内容可省略 | 否 |
| 点击 turn 卡片的 `＋` | 从精确 cursor 立即创建内部子路线；首条问题可稍后输入 | 否 |
| 提交带首条问题的 turn 分支 | 从精确 cursor 创建内部子路线，并在模型可用时生成回答 | 否 |
| “返回工作流” | 返回第一层并恢复镜头 | 否 |
| “继续对话” | 验证具体实例，调用 activate/navigate，成功后回到 Chat | 是 |

激活失败时保持当前画布、selection 和草稿，并显示结构化错误与重试入口。不得用 follow-up message、composer 文本或伪 deep link 模拟成功。

### 4. 从具体 turn 分支必须记录精确 checkpoint cursor

从 Turn Canvas 中的某轮创建内部路线时，命令必须同时携带：

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

checkpoint 继续保存创建时的有效消息快照；cursor 是可审计的分支锚点，不替代快照。运行时有效上下文沿 parent 链读取最新消息，因此源实例后续新增或修改会进入已有子路线。请求还必须带 `expected_content_revision`，源实例已变化时返回 409，不能在新的内容头上悄悄重放旧 selection。

Standalone 的 `fork-chat` 命令始终创建带 `surface_scope=turn` 和顶层 `owner_instance_id` 的内部实例，不能出现在第一层 Workflow Graph。命令允许省略 `initialMessage`：此时只创建路线、不调用模型，并由 `routeNodes` 让空路线保持可见。携带首条问题时，同一 idempotency key 覆盖“创建内部路线实例 + 生成首个回答”；创建成功而模型失败时保留用户问题并返回稳定 `replyErrorCode`，UI 继续选中该内部路线并显示失败状态；相同命令重试不得重复创建实例或重复已完成的回答。

省略显式标题时，Core 优先从同一创建命令的首条问题生成最多 48 字的摘要；如果连问题也省略，则按同一父实例和 surface 的子分支数生成 `新分支 N`。schema v7 用 `title_is_generated` 保存标题来源。空路线收到第一条本地用户消息时，只有标题仍为系统生成状态才自动更新摘要并增加 `graph_revision`；任意显式重命名都会把标题转为用户所有，即使用户名称恰好仍是 `新分支 N` 形式也不能被覆盖。

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
- 界面语言、浅色/深色主题、面板宽度和其他纯展示偏好；主题只改变界面 chrome，不翻译用户的对话标题或消息。

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

- Turn Canvas 中不得出现沿祖先路线动态继承的消息作为本地 turn 卡片。
- 双击节点、route choice 或 turn 的网络行为不得包含 activate/navigate。
- 只有显式“继续对话”可以 activate；成功前不得离开工作流 surface。
- 从 turn 分支必须绑定到该 turn 的 cursor，并拒绝 stale `expected_content_revision`；checkpoint 创建快照只用于审计，子路线的有效上下文继续动态读取父路线最新消息。
- Turn Canvas composer 与 Chat 必须写入同一个实例消息真源；任一 surface 刷新后看到的本地 turns 必须一致。
- 精确轮次分支只能出现在 owner 的第二层 Turn Tree；第一层 graph、topic route chooser 和实验分支列表不得把它当作新的工作流对话框。
- 激活内部路线后，普通 Chat 的标题仍是顶层 owner 名称，但消息读写目标必须是具体 `activeRouteInstanceId`。
- 画布发送和精确分支不得隐式 activate 当前实例或子实例。
- UI metadata 的写入不得改变 graph/content revision。
- 自动标题和显式重命名属于图元数据变更，必须增加 `graph_revision`；纯视觉 theme、viewport 和 selection 仍不得改变领域 revision。
- 同 topic 多路线必须先解析到具体 instance，Turn Canvas 和继续对话都不能使用逻辑 topic 代替实例 ID。
- 本 ADR 的 Standalone 本机纵向切片已完成后端、前端、typecheck/build 与真实浏览器 E2E，状态为 **Verified local preview**；这不代表正式 Codex/Claude HostAdapter、failure/approval 完整事件投影、跨设备同步或生产部署已经完成。

## 本机验证记录

2026-09-02 的当前后端验证基线为 108 项测试和 Python compileall；前端继续使用统一测试、TypeScript typecheck 和 production build。自动化路径确认了原生三视图切换、双击只钻入 Turn Canvas、内部分支不泄漏到顶层、画布/Chat 通过具体 `activeRouteInstanceId` 共用消息真源、空内部路线可见、从具体 turn 幂等创建并回答内部路线、自动/手动标题所有权、画布状态恢复，以及只有显式“继续对话”才 activate。数据库前向迁移当前到 schema v7；此前 schema v6 的内部路线迁移语义保持不变。

该记录只适用于单用户、本机、Standalone Local Graph Chat 纵向切片。`/graph` 是兼容入口；正式 HostAdapter 的 capability degradation 仍需 adapter contract 与对应宿主 E2E。
