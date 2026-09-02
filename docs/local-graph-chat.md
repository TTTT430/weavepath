# 阶段路线与 Local Graph Chat

## Phase 0：边界冻结

- 冻结 legacy v4 Codex adapter。
- 接受四项 ADR，包括原生 WorkspaceShell 与双层画布决策。
- 明确全局 DB、manifest 迁移和数据所有权。
- demo HTML 定位为视觉规格。
- 建立最小目录与依赖配置；此阶段不要求应用可启动。

退出条件：新功能有明确归属，不再继续向旧 bridge 单体增加跨模块状态。

## Phase 1：Local Graph Chat（进行中）

目标是在不依赖宿主私有能力的情况下，完整验证图、路线记忆、原生 WorkspaceShell 和节点内部 Turn Canvas。独立 `/graph` 窗口降为可选兼容入口，不再定义默认交互。

已验证基线包含图存储、核心 HTTP API、React chat/graph 页面、可选独立浏览器窗口、OpenAI-compatible AI adapter 和网页模型设置。此前 Local Graph Chat 验收覆盖 create、message、模型设置入口、从非当前节点 branch、跨窗口广播刷新、同 topic 多路线选择、路线隔离、i18n、旧版双击单次 activate、非空草稿保持、节点本地记录/继承路线记忆分离、固定页面布局、独立消息滚动、安全 Markdown/GFM 渲染，以及最近提问的编辑/取消交互。AI 请求支持“正在思考”、SSE 逐 token 草稿、停止生成、失败回答重试、幂等键和本地化内联错误状态，后端稳定区分超时、服务不可用和空响应；编辑并重新生成采用只读 prepare + 原子 commit，模型失败零写入，并发修改返回 409，已有子节点不回写。节点切换使用请求防串线保护，同一路线具有同步发送锁。Route-to-Agent Run v1 已完成窄范围本机自动化与真实浏览器 E2E；正式 HostAdapter 和 metabolize 尚未实现。

依据 [ADR-0004](adr/0004-native-workspace-double-canvas.md)，当前默认交互已改为同页“对话 / 工作流”切换：双击具体实例只进入该实例的 local-only Turn Canvas；只有显式“继续对话”才 activate；可以从选定本地用户 turn 记录精确 checkpoint 锚点。该 Standalone 纵向切片已完成自动化和真实浏览器 **Verified local preview**，但不代表正式宿主适配器或完整 Phase 1 已完成。

日常启动与验证分别使用根目录的 `scripts/dev.ps1` 和 `scripts/check.ps1`；前者固定 API 端口 8000，Web 默认端口 5173。

最小 API：

```text
POST /api/v1/workflows
GET  /api/v1/workflows/{id}/graph
POST /api/v1/workflows/{workflowId}/instances/{id}/fork
POST /api/v1/workflows/{workflowId}/instances/{id}/fork-chat  # exact turn route; first prompt optional
PATCH /api/v1/workflows/{workflowId}/instances/{id}           # revision-safe rename
POST /api/v1/workflows/{workflowId}/instances/{id}/activate
GET  /api/v1/workflows/{workflowId}/instances/{id}/messages
GET  /api/v1/workflows/{workflowId}/instances/{id}/turns       # verified local-only projection
GET  /api/v1/workflows/{workflowId}/instances/{id}/turn-tree   # owner + internal dialogue routes
GET  /api/v1/workflows/{workflowId}/topics/{id}/routes
POST /api/v1/workflows/{workflowId}/instances/{id}/prune-plan
POST /api/v1/workflows/{workflowId}/instances/{id}/prune-commit
GET  /api/v1/ai/status
POST /api/v1/workflows/{workflowId}/instances/{id}/chat       # JSON by default; SSE when Accept: text/event-stream
POST /api/v1/workflows/{workflowId}/instances/{id}/chat/stream # SSE stream
POST /api/v1/workflows/{workflowId}/instances/{id}/chat/{requestId}/cancel # cooperative cancel
POST /api/v1/workflows/{workflowId}/instances/{id}/messages/{messageId}/regenerate
POST /api/v1/workflows/{workflowId}/instances/{id}/runs       # verified local preview, synchronous
GET  /api/v1/workflows/{workflowId}/instances/{id}/runs       # preview list
GET  /api/v1/runs/{runId}                                     # preview global-ID detail
GET  /api/v1/runs/{runId}/events                              # preview paged journal
WS   /api/v1/events                                           # planned
```

SSE 使用 `message.started`、`message.delta`、`message.completed`、
`message.failed` 和 `message.cancelled` 事件。客户端应为每次发送生成
`idempotencyKey`；相同工作流、节点、键和内容的重放会返回已完成结果，
不同内容复用同一键会返回冲突。取消或上游失败时不会写入半成品 assistant 消息。

fork 请求支持 `anchorMessageId` 与 `expectedContentRevision`。选定本地用户 turn 时，checkpoint 快照记录到该 turn 完成处并保留为审计锚点；运行时上下文仍跟随源实例最新路线；源实例 revision 已变化则返回 409。schema v4 为 checkpoint 增加 provider-neutral 的 cursor kind/value；接口、迁移和冲突路径已完成本机测试。

实现范围：

- FastAPI、React 和已验证的 schemaVersion 7 SQLite 前向迁移；rollback/downgrade 尚未完成；
- GraphStore 持有 Local Chat transcript；OpenAI-compatible LLM port 已实现，正式 Standalone HostAdapter 尚未拆出；
- workflow、topic、instance、checkpoint、local message、tombstone；
- Turn projector 只读取具体实例的 local messages，将一个用户问题及下一用户问题前的 assistant/tool message 组织为一张 turn 卡片；沿祖先路线动态继承的消息不重复投影，failure/operation 事件扩展仍是后续工作；
- Turn Canvas composer 与普通 Chat 调用同一条 route-aware 消息链路并写入同一 SQLite 消息真源，不复制 transcript；从 turn 卡片发起的 `fork-chat` 以精确 anchor 幂等创建 `surface_scope=turn` 的内部实例。首条问题可省略，空路线由 `routeNodes` 立即投影为占位卡；提供首条问题时可立即生成回答。内部实例只进入 owner 的 Turn Tree，不进入第一层 graph；
- 从 Co-Thinker 复用 SSE 与中断保护（planned）；
- 两层画布使用统一的 Synapse 式卡片、连线、卡片边缘快捷分支和右侧检查器，并提供一致的浅色/深色 token；这是对 dsh-synapse 画布体验的借鉴，不改变 WeavePath 的两层路线模型；
- 默认使用 `WorkspaceShell` 内的 Chat/Workflow 两个持久 surface；第一层 Workflow Graph 和第二层 Turn Canvas 复用一个按需加载的画布区域，不在每个节点中嵌套多个 React Flow；
- 顶层和每个实例的 viewport、视觉位置、折叠与 selection 是独立 UI metadata，不改变 graph/content revision；
- `/graph` 与 `window.open` 仅保留为可选兼容入口；它可继续使用 `BroadcastChannel + postMessage` 同步真实 mutation，WebSocket/event stream 为后续能力。

落地顺序与后续：

1. `backend/graph_core`：checkpoint cursor、local-only turn read model、精确 turn fork 与 migration 测试已完成本机验证。
2. `backend/api`：turns query 和带 revision 的 anchor fork 已落地；`backend/agent_runtime` 继续承载已验证本机 preview 的 run repository/service、model port 与 tool registry。
3. `apps/web`：`WorkspaceShell` 已成为默认入口并保持 Chat surface；Workflow surface 在顶层实例图和节点内部 Turn Canvas 间按需钻入。
4. Tool/Failure/Approval 完整 Timeline 仍待扩展；当前最小 turn 卡片不得把继承消息当成本地内容。
5. Standalone/Host Mock capability contract 和正式 Codex/Claude 集成仍是后续，需明确 transcript、精确 cursor fork 和 navigation 的降级结果。
6. 下方 A/B/C/D/turn 的 Standalone 自动化与主路径真实浏览器 E2E 已完成；跨宿主验收不能复用该完成声明。

### 必须通过的验收

创建：

```text
A
├─ B ─ C ─ D1(topic=D)
└─ E ───── D2(topic=D)
```

验收项：

1. 工作流按钮在当前 WorkspaceShell 内切换到 Workflow surface，不发送聊天消息，不丢失草稿、消息滚动或正在进行的请求。**Verified local preview。**
2. D 的 route chooser 同时显示 `A-B-C-D` 与 `A-E-D`；单击只选择具体实例，双击只进入该实例的 Turn Canvas，不 activate。**Verified local preview。**
3. B 中写入 `B_ONLY` 后，D2 的 context、inspect 和摘要均不得出现它。**消息路线隔离已验证；摘要系统尚未实现。**
4. E 中写入 `E_ONLY` 后，D1 不得出现它。**消息路线隔离已验证。**
5. fork 后父节点继续聊天，已创建子节点会动态看到父路线最新消息；创建时 checkpoint 快照仍保留用于审计。**后端测试已验证。**
6. 双击 D2 不发送 activate；只有在 D2 inspector 或 Turn Canvas 中单击“继续对话”才发送一次 activate，成功后切回 D2 聊天且输入框原内容不变，失败则保留画布。**Verified local preview。**
7. prune B 的计划只包含 B、C、D1，并按 leaf-first 顺序归档；E、D2 保持 active。
8. revision 冲突返回 409，不覆盖新结构。
9. 服务重启后图、消息、checkpoint 和 tombstone 完整恢复。**持久化基础已实现，完整重启 E2E 仍待单独记录。**
10. 中英文只改变 UI chrome，不改变 workflow、topic、节点名称和消息。**自动测试与手工真实浏览器验收已覆盖。**
11. 后台摘要迟到时不能写入错误 instance 或改变 graph revision。**Planned：metabolize 尚未实现。**
12. E2E 测试不依赖 Codex/Claude；Agent Runtime 使用测试专用 `ScriptedMockAgentAdapter` 复现 model turns，正式 HostAdapter mock 仍是后续工作。
13. 进入 B 的 Turn Canvas 时，只显示 B 本地用户 turns 及其本地 assistant/tool message；A 的 checkpoint 内容只显示为路线与继承摘要，不能成为 B 的卡片。failure/operation 扩展仍是后续工作。**Verified local preview。**
14. 从 B 的第 2 个本地用户 turn 创建 C 时，C 保留该精确 checkpoint 锚点和创建时快照；运行时上下文会继续跟随 B，因此 B3 及之后新增/修改的消息会进入 C；stale `expectedContentRevision` 仍返回 409。**Verified local preview。**
15. 顶层与 B/D1/D2 各自的 viewport、节点位置、折叠和 selection 独立恢复，写入这些 UI metadata 不增加 graph/content revision。**Verified local preview。**
16. 无 `can_read_local_turns` 的宿主不伪造 Turn Canvas；无精确 cursor fork 能力时禁用该动作或经确认降级到实例头；无 navigation 时不宣称已切换。**Planned adapter contract。**
17. 在 B 的 Turn Canvas 发送问题后，刷新普通 Chat 与 Turn Canvas 必须看到同一条本地记录；发送动作不隐式 activate B。**Verified local preview。**
18. 从 B 的任意 turn 卡片创建子分支时，首个问题写入新子实例并在模型可用时立即回答；幂等重放不得重复创建实例或回答，兄弟路线内容不得进入模型上下文。**Verified local preview。**
19. 第 18 项创建的实例必须归属于 B 的内部 Turn Tree，第一层 Workflow Graph 仍只显示 B；选择内部路线并继续对话后，Chat 标题仍为 B，但消息 API 使用该内部路线 ID。schema v6 会把旧版误入第一层的 exact-turn 子节点原地迁移，不删除记录。**Verified local preview。**

20. 顶层实例卡和 turn 卡的 `＋` 可以不经必填表单直接创建对应 scope 的子分支。没有标题和首条内容时先生成 `新分支 N`；空 turn 路线必须立即显示，不能跑到第一层。**自动化已验证。**

21. schema v7 持久化 `title_is_generated`。系统生成标题会在第一条本地用户消息到达时更新为最多 48 字摘要并增加 `graphRevision`；用户显式重命名后永不自动覆盖。旧数据库标题升级时一律按用户所有处理。**后端自动化已验证。**

当前本机验证使用统一套件：后端 108 项并通过 Python compileall；前端继续通过统一测试、TypeScript typecheck 和 production build 验证。这里的“通过”只覆盖 Standalone 本机预览；正式 HostAdapter、真实窄屏、failure/approval 完整时间线和生产部署不在范围内。

## Phase 2：Agent Runtime 与 Tool Registry（本机预览切片）

Route-to-Agent Run v1 已验证同步 durable run 的第一条窄链路。它不改变 Phase 1 的图与 checkpoint 不变量：run 必须绑定一个 active instance，并按接受的 `contentRevision` 冻结该路线的 effective messages。

这个 **Verified local preview** 包括 execution brief、持久化 run/step/event/tool journal、唯一安全工具 `safe_calculator` / `1.0.0`、生产 OpenAI-compatible adapter、测试专用 `ScriptedMockAgentAdapter`，以及 run dialog/timeline。API 合同和逐项本机验收记录以 [Route-to-Agent Run v1](route-to-agent-run-v1.md) 为准。

这个切片没有 SSE、取消、任意 shell/文件/网络工具、artifacts、evaluation、多 Agent，也没有正式 Codex/Claude adapter。

## Phase 3：Route-aware Memory 与 Context Engineering

- 移植 foundation、judge、brief。
- route digest、context budget 和显式 cross-route transfer。
- 后台自动更新摘要，AI 只建议分支。

当前同步 Local Chat 与 Agent Run frozen context 都不是完整的 Route Memory。Phase 3 才会补齐 route digest、metabolize、judge、context budget 和可审计 transfer；在此之前不得宣称已有完整 Co-Thinker thinker/metabolize 能力。

## 后续阶段

长期阶段编号只以 [Agent 工程路线图](agent-engineering-roadmap.md) 为准：Phase 4 streaming/cancellation/retry，Phase 5 evaluation，Phase 6 observability，Phase 7 artifacts，Phase 8 multi-agent，Phase 9 Codex/Claude adapters，Phase 10 automation，Phase 11 security/packaging/deployment。Desktop companion 是 Phase 11 packaging 的一部分，不另造一套领域状态。
