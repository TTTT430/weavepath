# 阶段路线与 Local Graph Chat

## Phase 0：边界冻结

- 冻结 legacy v4 Codex adapter。
- 接受三项 ADR。
- 明确全局 DB、manifest 迁移和数据所有权。
- demo HTML 定位为视觉规格。
- 建立最小目录与依赖配置；此阶段不要求应用可启动。

退出条件：新功能有明确归属，不再继续向旧 bridge 单体增加跨模块状态。

## Phase 1：Local Graph Chat（进行中）

目标是在不依赖宿主私有能力的情况下，完整验证图、路线记忆和独立窗口。

当前已落地图存储、核心 HTTP API、可运行 React chat/graph 页面、独立浏览器窗口、OpenAI-compatible 同步 AI adapter 和网页模型设置。此前 Local Graph Chat 验收覆盖 create、message、模型设置入口、从非当前节点 branch、跨窗口广播刷新、同 topic 多路线选择、路线隔离、i18n、双击单次 activate、非空草稿保持、节点本地记录/继承路线记忆分离、固定页面布局、独立消息滚动、安全 Markdown/GFM 渲染，以及最近提问的编辑/取消交互。同步 AI 请求具有“正在思考”与本地化内联错误状态，后端稳定区分超时、服务不可用和空响应；编辑并重新生成采用只读 prepare + 原子 commit，模型失败零写入，并发修改返回 409，已有子节点不回写。节点切换使用请求防串线保护，同一路线具有同步发送锁。Route-to-Agent Run v1 已完成窄范围本机自动化与真实浏览器 E2E；SSE、停止生成和 metabolize 尚未实现。

日常启动与验证分别使用根目录的 `scripts/dev.ps1` 和 `scripts/check.ps1`；前者固定 API 端口 8000，Web 默认端口 5173。

最小 API：

```text
POST /api/v1/workflows
GET  /api/v1/workflows/{id}/graph
POST /api/v1/workflows/{workflowId}/instances/{id}/fork
POST /api/v1/workflows/{workflowId}/instances/{id}/activate
GET  /api/v1/workflows/{workflowId}/instances/{id}/messages
GET  /api/v1/workflows/{workflowId}/topics/{id}/routes
POST /api/v1/workflows/{workflowId}/instances/{id}/prune-plan
POST /api/v1/workflows/{workflowId}/instances/{id}/prune-commit
GET  /api/v1/ai/status
POST /api/v1/workflows/{workflowId}/instances/{id}/chat       # synchronous
POST /api/v1/workflows/{workflowId}/instances/{id}/messages/{messageId}/regenerate
POST /api/v1/workflows/{workflowId}/instances/{id}/runs       # verified local preview, synchronous
GET  /api/v1/workflows/{workflowId}/instances/{id}/runs       # preview list
GET  /api/v1/runs/{runId}                                     # preview global-ID detail
GET  /api/v1/runs/{runId}/events                              # preview paged journal
POST /api/v1/chat/stream                                      # planned SSE
WS   /api/v1/events                                           # planned
```

实现范围：

- FastAPI、React、schemaVersion 3 SQLite schema 与启动时前向 migrations；rollback/downgrade 尚未完成；
- GraphStore 持有 Local Chat transcript；OpenAI-compatible LLM port 已实现，正式 Standalone HostAdapter 尚未拆出；
- workflow、topic、instance、checkpoint、local message、tombstone；
- 从 Co-Thinker 复用 SSE 与中断保护（planned）；
- 从 v4 widget 提取正交布局、route chooser 和中英文界面；
- 从 demo 迁移聊天页、workflow button 和图窗口视觉；
- `window.open` 独立浏览器窗口；
- 当前以 `BroadcastChannel + postMessage` 同步 activate；WebSocket/event stream 为后续能力。

建议实现顺序：

1. `backend/graph_core`：实体、不变量、in-memory repository 和纯领域测试。
2. `backend/api`：SQLite repositories、Standalone command/query service；`backend/agent_runtime` 承载已验证本机 preview 的 run repository/service、model port 与 tool registry。
3. FastAPI：health、workflow/fork/activate/prune API 和统一错误结构。
4. `apps/web`：聊天页与图窗口的最小 React 入口。
5. 浏览器事件完成当前窗口同步；同步 LLM adapter 已落地，后续加入 SSE，只有服务端事件确有需要时再加入 WebSocket。
6. E2E：完成下方 A/B/C/D 验收后才开始 Codex 集成。

### 必须通过的验收

创建：

```text
A
├─ B ─ C ─ D1(topic=D)
└─ E ───── D2(topic=D)
```

验收项：

1. 工作流按钮直接打开图窗口，不发送聊天消息。**已验证。**
2. D 的 route chooser 同时显示 `A-B-C-D` 与 `A-E-D`。**具体路线展示、双击选择及消息隔离已在真实浏览器验证。**
3. B 中写入 `B_ONLY` 后，D2 的 context、inspect 和摘要均不得出现它。**消息路线隔离已验证；摘要系统尚未实现。**
4. E 中写入 `E_ONLY` 后，D1 不得出现它。**消息路线隔离已验证。**
5. fork 后父节点继续聊天，已创建子节点仍使用原 checkpoint。**后端测试已验证。**
6. 双击 D2 只发送一次 activate；聊天页切换到 D2，输入框原内容不变。**单次 activate、路线切换和非空草稿保持已完成手工真实浏览器验收；标准 popup 自动关闭和自动化 E2E 仍待完成。**
7. prune B 的计划只包含 B、C、D1，并按 leaf-first 顺序归档；E、D2 保持 active。
8. revision 冲突返回 409，不覆盖新结构。
9. 服务重启后图、消息、checkpoint 和 tombstone 完整恢复。**持久化基础已实现，完整重启 E2E 仍待单独记录。**
10. 中英文只改变 UI chrome，不改变 workflow、topic、节点名称和消息。**自动测试与手工真实浏览器验收已覆盖。**
11. 后台摘要迟到时不能写入错误 instance 或改变 graph revision。**Planned：metabolize 尚未实现。**
12. E2E 测试不依赖 Codex/Claude；Agent Runtime 使用测试专用 `ScriptedMockAgentAdapter` 复现 model turns，正式 HostAdapter mock 仍是后续工作。

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
