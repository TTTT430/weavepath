# 总体架构与领域模型

本文主要描述目标架构。当前 Phase 1 已建立 SQLite GraphStore、FastAPI 路由、可运行 React chat/graph 页面和最小 OpenAI-compatible 同步 LLM adapter；正式 application ports/adapters、SSE、服务端事件流和 migration runner 仍是目标结构。当前两个同源浏览器窗口通过 `BroadcastChannel + postMessage` 同步 activate，不使用 WebSocket。

## 分层

### UI Shells

- Web Chat：本地对话和固定工作流入口。
- Graph Window：独立浏览器/Desktop 窗口中的主图界面。
- Desktop Shell：后续单实例、任务栏入口和协议唤起。
- Codex Widget：宿主内快速入口与迷你图。
- Claude Command/Plugin：启动或聚焦 companion app，并提供宿主能力。

UI 只持有临时 selection、zoom、language 等展示状态。结构真相必须从 Core Service 获取。

### Application Services

- Command service：create、fork、bind、archive 等结构变更。
- Query service：graph read model、route choice、record pagination。
- Host operation saga：协调 SQLite 与不可事务化的宿主副作用。
- Event service（目标）：未来按需要通过 WebSocket 发布 `graph.changed`、`instance.activated`、`checkpoint.ready`；当前浏览器窗口仅用 browser events 同步 activate。
- Context builder：只沿选定祖先路线构建上下文。

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
  readiness, created_at

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

## Revision 与并发

- `graph_revision` 只在 parent、status、host binding、tombstone 等结构变更时增加。
- `content_revision` 属于具体实例，在消息、foundation 或摘要变化时增加。
- fork/prune 接收 `expected_graph_revision`，不匹配返回 409。
- 后台 metabolize 以 `instance_id + expected_content_revision/checkpoint_id` 提交，禁止把迟到结果写到新的路线状态。
- 外部命令要求 `idempotency_key`。

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
can_navigate
can_read_transcript
can_archive
can_rename
can_open_external_window
```

UI 根据 capability 显示动作。缺失导航能力时打开 companion app，不得用 composer 文本、伪 deep link 或模型消息模拟切换。

### 适配器职责

- StandaloneAdapter：Core Service 持有消息；从 checkpoint 创建本地子会话。
- CodexAdapter：从 legacy v4 的 metadata、`callHostTool` 和验证逻辑演化；默认只保存 task binding 和图元数据。
- ClaudeAdapter：只实现公开能力；无法原生导航时返回明确降级结果。
- MockAdapter：用于 saga、revision 和错误恢复的确定性测试。

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

## 独立窗口

第一阶段由 Web Chat 的用户点击直接执行：

```text
window.open('/workflows/{workflowId}/graph')
```

当前 Graph Window 在 activate 或 prune-commit 成功后，通过 `BroadcastChannel` 和 `window.opener.postMessage` 通知 Chat 重新 hydrate 具体实例。双击节点的单次 activate、具体路线切换和非空草稿保持已完成手工真实浏览器验收；由聊天页创建的标准 popup 在成功后自动关闭和自动化 E2E 仍需补充。未来只有在需要服务端主动推送或跨设备同步时才引入 WebSocket。

后续 companion service：

- 只监听 `127.0.0.1`；
- 单实例；
- 每次启动生成临时访问 token；
- UI 与 API 同源；
- 插件通过受限 loopback API 聚焦指定 workflow；
- 不接受任意文件路径，不开放局域网。

Tauri/Electron 只包装同一 React/API，不创建第三套状态模型。
