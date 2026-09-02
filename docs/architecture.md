# 总体架构与领域模型

本文主要描述目标架构。当前自动化验证基线包含 SQLite GraphStore、schema v6 前向迁移、FastAPI 路由、原生 React `WorkspaceShell`、双层画布、Engineering Lab v1 和最小 OpenAI-compatible 同步 LLM adapter；正式 application/host ports、SSE、服务端事件流、migration rollback 与发布策略仍是目标结构。WorkspaceShell/双层画布和 Route-to-Agent Run v1 已完成窄范围真实浏览器验收，Engineering Lab v1 已完成自动化验证；这些都不等于完整 Agent Runtime、跨宿主集成或阶段完成。

## 分层

### UI Shells

- WorkspaceShell（本机预览已验证）：同一个应用内承载“对话 / 工作流”两个长期 surface，切换时保持聊天草稿、请求状态、消息滚动和画布镜头。
- Workflow Canvas（本机预览已验证）：第一层显示具体 `ConversationInstance` 路线图，双击实例按需钻入第二层 local-only Turn Canvas；第二层可向该实例继续发送消息或从精确 turn 创建并回答子分支，双击本身不 activate。
- Web Chat：本地对话 surface；只有用户在工作流中显式选择“继续对话”后才接受 activate 结果。
- Graph Window：保留为 `/graph` 可选兼容入口或未来 Desktop surface，不再是默认工作流入口，也不拥有独立领域状态。
- Desktop Shell：后续单实例、任务栏入口和协议唤起。
- Codex Widget：宿主内快速入口与迷你图。
- Claude Command/Plugin：启动或聚焦 companion app，并提供宿主能力。

UI 只持有临时 selection、viewport、节点视觉位置、折叠、language 等展示状态。它们必须按 workflow/instance 命名空间保存，且不得改变 parent/checkpoint、host binding、tombstone、`graph_revision` 或 `content_revision`。结构真相必须从 Core Service 获取。

### Application Services

- Command service：create、fork、bind、archive 等结构变更。
- Query service：graph read model、route choice、record pagination。
- Host operation saga：协调 SQLite 与不可事务化的宿主副作用。
- Event service（目标）：未来按需要通过 WebSocket 发布 `graph.changed`、`instance.activated`、`checkpoint.ready`；同一 `WorkspaceShell` 内的视图切换不需要跨窗口事件，可选 `/graph` 兼容窗口仍可暂用 browser events。
- Context builder：只沿选定祖先路线构建上下文。
- Agent Runtime service（本机预览）：从具体 instance 冻结有效路线消息和 execution brief，驱动同步 model/tool loop，并把 run、step、event、tool call/result 和最终答案写入 SQLite。

### graph-core

graph-core 是纯领域层，提供：

```text
create_workflow
register_root
fork_instance
route_for_instance
routes_for_topic
activate_instance
prune_plan
prune_commit
bind_host_task
validate_invariants
```

它不能直接访问 HTTP、MCP、Widget、LLM 或具体宿主 API。

Phase 1 的物理包路径是 `backend/graph_core/`。application services、ports 和 adapters 先放在 `backend/api/` 下分包；只有通过后续 ADR 才可整体迁移到 `src/cothinker/...`。

### Ports

```text
GraphRepository
TranscriptStore
HostAdapter
MemorySummarizer
EventPublisher
CredentialStore
Clock / IdGenerator
AgentModelPort / ToolRegistry
```

## 路线实例模型

```text
Workspace
  └─ Workflow
       ├─ Topic
       └─ ConversationInstance
            ├─ parent_instance_id -> ConversationInstance
            ├─ parent_checkpoint_id -> Checkpoint
            ├─ topic_id -> Topic
            └─ host binding / local transcript
```

建议最小实体：

```text
Workflow
  id, workspace_id, title, root_instance_id, graph_revision, status

Topic
  id, workflow_id, title

ConversationInstance
  id, workflow_id, topic_id
  parent_instance_id, parent_checkpoint_id
  explicit_title, status, content_revision
  created_at, archived_at

Checkpoint
  id, instance_id, message_prefix
  content_hash, foundation_snapshot_id
  source_cursor_kind, source_cursor_value
  source_content_revision, readiness, created_at

HostBinding
  instance_id, adapter
  host_profile_id, external_host_id, external_thread_id
  canonical_project_path, capabilities_snapshot

Tombstone
  instance_id, prune_revision, archived_at, reason
```

### Topic 与实例

`topic_id` 表示逻辑主题，不表示共享记忆。例如：

```text
A
├─ B ─ C ─ D1(topic=D)
└─ E ───── D2(topic=D)
```

D1 和 D2 可以在 UI 中聚合显示为一个 D，并提供两条路线选择，但它们必须拥有不同的：

- instance ID；
- parent；
- checkpoint；
- host thread/session binding；
- transcript 与 content revision。

路线通过 `parent_instance_id` 递归计算，不保存容易失真的可变路径数组。

### Root、Current、Selected、Last Opened

- `root_instance_id`：workflow 的不可变结构属性。
- `current_instance_id`：由当前宿主 task/session binding 推导，不写成全局图真相。
- `selected_instance_id`：某个 UI surface 的临时状态。
- `last_opened_instance_id`：可选的用户偏好。

多个窗口或宿主同时打开时，不允许用一个全局 `active_node_id` 覆盖它们。

## 双层画布读模型与命令入口

顶层 Workflow Graph 的卡片始终代表具体 `ConversationInstance`，边仍由 `parent_instance_id` 推导。它不能混入单条消息、工具调用或纯视觉分组节点，否则 prune、route choice 和 checkpoint 语义会变得含糊。

双击顶层卡片进入该实例的 Turn Canvas。Turn Canvas 是按需构造的 `scope=local` 读模型：每个本地用户消息开始一个 turn，直到下一条本地用户消息之前的本地 assistant/tool message 都归入该 turn；failure/operation 在相应事件投影可用后扩展同一时间线。祖先路线消息动态用于有效上下文，但不在子实例画布中重复投影；UI 单独显示 memory route、checkpoint anchor 和继承消息数量。

Turn Canvas surface 同时暴露受控命令入口，但不维护第二份 transcript：画布 composer 调用与 Chat 相同的 route-aware chat/message API；轮次卡片的分支动作携带精确 `anchorMessageId`、`expectedContentRevision` 和 `idempotencyKey`，创建 `surface_scope=turn` 的真实内部 `ConversationInstance` 后生成首个回答。内部实例拥有独立 checkpoint/transcript，归属于一个顶层 owner，只在 Turn Tree 出现；`GET graph` 和 topic route chooser 只返回 `surface_scope=workflow`。激活内部路线时 graph 同时返回顶层 `activeInstanceId` 与具体 `activeRouteInstanceId`，Chat 使用后者读写同一 SQLite 真源。

```text
Workflow Graph
└─ ConversationInstance B
   └─ Turn Canvas (local projection + route-scoped commands)
      ├─ B / user turn 1
      │  └─ assistant + tool + failure events
      └─ B / user turn 2
         └─ assistant + tool + failure events
```

视图动作与领域动作分开：selection 和钻入只更新 UI metadata；显式“继续对话”才调用 `activate_instance`/HostAdapter navigation。激活成功后才切回 Chat，失败则保留画布和草稿。完整决策见 [ADR-0004](adr/0004-native-workspace-double-canvas.md)。

### 精确 turn checkpoint cursor

从某一 turn 创建子实例时，fork 命令携带 `source_instance_id + cursor kind/value + expected_content_revision`。Standalone 当前使用：

- `localUserTurn`：value 为本地用户消息 ID，记录精确分支锚点；创建时快照截至该轮，但运行时上下文跟随父实例最新状态；
- `instanceHead`：value 对应请求接受时的实例 content revision。

checkpoint 同时保留不可变消息快照，cursor 只提供可审计锚点；运行时有效上下文沿 parent 链递归读取当前消息，不依赖静态快照，因此父节点后续新增或修改会进入子路线。内容 revision 已变化时拒绝 fork，不能把旧 turn selection 默默应用到新实例头。外部宿主由 adapter 把自己的 seed/turn/message cursor 映射为通用 `HostCheckpointRef`；不支持精确 cursor 时必须结构化降级，不能静默改为整段会话继承。

## Revision 与并发

- `graph_revision` 只在 parent、status、host binding、tombstone 等结构变更时增加。
- `content_revision` 属于具体实例，在消息、foundation 或摘要变化时增加。
- fork/prune 接收 `expected_graph_revision`，不匹配返回 409。
- 后台 metabolize 以 `instance_id + expected_content_revision/checkpoint_id` 提交，禁止把迟到结果写到新的路线状态。
- 外部命令要求 `idempotency_key`。

## Route-to-Agent Run v1 本机预览

当前物理包是 `backend/agent_runtime/`，通过 `backend/api/app.py` 暴露同步 HTTP 入口。该窄切片已完成本机自动化与浏览器 E2E 验证，但它不是完整的长期 Agent Runtime，也没有生产部署保证。

- 一个 run 绑定一个 `workflow_id + instance_id + input_content_revision`。
- frozen context 保存该具体路线的 `memoryRoute`、当次 `availableTools`、有效消息和 execution brief；兄弟路线不参与组装。
- 模型元数据通过 allowlist 后持久化，API key/token 不进入 model snapshot。
- 生产路径使用用户已配置的 OpenAI-compatible provider；`ScriptedMockAgentAdapter` 只通过测试注入。
- 当前唯一工具是 `safe_calculator` / `1.0.0`，无 shell、文件系统或网络能力。
- 同步请求最多执行有限轮 model/tool loop；无后台队列、SSE、取消或 resume。
- completion 只有在 instance 仍 active 且 content revision 未变化时，才原子写入本地 assistant message；run 另存不可变 `final_answer`。
- 启动时遗留的 `queued` / `running` run 转为 `interrupted`，不会自动继续执行。

schema v3 引入且在当前 schema v6 中继续使用的 runtime 表包括 `agent_runs`、`run_steps`、`run_events`、`tool_calls` 和 `tool_results`。schema v4 为 checkpoint 增加精确 cursor 字段；schema v5 增加 Artifact、accepted knowledge merge、dataset 和 experiment snapshot 表；schema v6 为 `conversation_instances` 增加 `surface_scope` 与 `owner_instance_id`，并将旧版误入顶层的精确 turn 分支原地迁移为内部路线。迁移由 `schema_migrations` 记录并在 `GraphStore` 打开数据库时前向执行；自动 downgrade/rollback 尚未实现。

## HostAdapter 能力协议

```python
class HostAdapter(Protocol):
    def capabilities(self) -> HostCapabilities: ...
    async def resolve_current_context(self, request_context) -> HostContext: ...
    async def list_conversations(self, cursor=None) -> Page: ...
    async def fork(self, source, checkpoint, prompt, options, operation_id) -> HostBinding: ...
    async def navigate(self, binding, operation_id) -> HostResult: ...
    async def inspect(self, binding, cursor=None, limit=50) -> Page: ...
    async def archive(self, binding, operation_id) -> HostResult: ...
    async def rename(self, binding, title, operation_id) -> HostResult: ...
```

能力必须显式协商：

```text
can_fork
can_fork_from_checkpoint
can_navigate
can_read_transcript
can_read_local_turns
can_archive
can_rename
can_open_external_window
supported_checkpoint_cursor_kinds
```

UI 根据 capability 显示动作。缺失导航能力时保留画布并提供 companion app/宿主任务列表降级，不得用 composer 文本、伪 deep link 或模型消息模拟切换。无法区分 local/inherited transcript 时不得伪造 local-only Turn Canvas；无法按选定 cursor fork 时禁用精确分支，或经用户明确确认后从实例头分支。

### 适配器职责

- StandaloneAdapter：Core Service 持有消息；从 checkpoint 创建本地子会话。
- CodexAdapter：从 legacy v4 的 metadata、`callHostTool` 和验证逻辑演化；默认只保存 task binding 和图元数据。
- ClaudeAdapter：只实现公开能力；无法原生导航时返回明确降级结果。
- Host MockAdapter（目标）：用于 host saga、revision 和错误恢复的确定性测试；不要与当前 Agent Runtime 的测试专用 `ScriptedMockAgentAdapter` 混为生产 adapter。

适配器不能决定 parent/topic/prune 语义，也不能绕过 graph-core 直接改数据库。

## Operation Saga

宿主 fork/archive 无法加入 SQLite 事务，必须记录 operation：

```text
validate command + revision
→ create operation and pending instance in SQLite
→ call HostAdapter
→ persist returned binding
→ commit graph mutation
→ optional rename / prompt / navigate
→ emit event
```

失败策略：

- 宿主尚未产生资源：回滚 pending instance。
- 宿主已产生资源但绑定提交失败：尝试归档；失败则标记 `orphaned`。
- 重试复用同一个 `operation_id/idempotency_key`。
- prune 先生成 leaf-first plan；所有宿主 archive 成功后才提交 tombstone。
- 部分 archive 失败时不提交图变更，并向用户显示可重试计划。

## 原生 WorkspaceShell 与可选兼容窗口

默认工作流入口改为同一 React 应用中的原生视图切换：

```text
WorkspaceShell
  [对话] [工作流]
        └─ Workflow Graph
             └─ double click → Turn Canvas
                  └─ explicit Continue → activate → Chat
```

Chat 与 Workflow surface 保持挂载，切换不通过 `window.open`，也不需要用 `BroadcastChannel` 表达普通 selection 或 drill-down。顶层和每个实例的 viewport、视觉位置、折叠和 selection 是独立 UI metadata；它们丢失时只重置布局，不得改变图或记忆路线。

`/graph?workflow=...` 继续包装同一个 `WorkspaceCanvas`，用于兼容旧入口或未来独立 Desktop surface。该兼容窗口只在显式 Continue/activate 或 prune 等真实 mutation 后通知其他页面，不得把双击节点解释为 activate。默认 WorkspaceShell 与双层画布已完成自动化和真实浏览器 **Verified local preview**；旧版“独立弹窗双击即切换并自动关闭”不再是默认产品语义，也不能替代对兼容窗口本身的独立验收。

未来只有在服务端主动推送、多个进程或跨设备同步确有需要时才引入 WebSocket。

后续 companion service：

- 只监听 `127.0.0.1`；
- 单实例；
- 每次启动生成临时访问 token；
- UI 与 API 同源；
- 插件通过受限 loopback API 聚焦指定 workflow；
- 不接受任意文件路径，不开放局域网。

Tauri/Electron 只包装同一 React/API，不创建第三套状态模型。
