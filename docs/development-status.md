# 开发状态

更新日期：2026-09-01

## 状态定义

- **Done**：已实现并有相应验证。
- **Done for current slice**：满足当前纵向切片的明确范围，但不代表长期模块已经完成。
- **In progress**：已有真实实现，但尚未满足对应阶段的退出条件。
- **Scaffolded**：仅建立目录或配置，不能代表功能可运行。
- **Planned**：已有文档边界，尚未实现。
- **Legacy frozen**：可作为参考或兼容入口，只接受缺陷/迁移修改。
- **Visual spec only**：只描述目标界面，不是应用代码。
- **Verified local preview**：在记录的本机环境中完成自动化与端到端验证，但仍受明确平台、部署和功能边界限制。
- **Implementation candidate / Unverified**：已有候选实现或契约，但尚未收到足以支持公开完成声明的测试结果。

## 当前状态

| 范围 | 状态 | 说明 |
|---|---|---|
| Phase 0 产品与架构文档 | Done | 愿景、边界、数据所有权、ADR、迁移和验收已记录 |
| `backend/pyproject.toml` | Done | 声明 Python 3.12、FastAPI、Uvicorn 和测试依赖 |
| `backend/graph_core/` | In progress | 已有 SQLite GraphStore、冻结 checkpoint、同 topic 多实例、leaf-first prune、实例级 `contentRevision`、workflow `eventRevision` 和 schema v1→v3 前向迁移；尚未拆稳定 repository/ports，也没有自动 rollback/downgrade |
| `backend/api/` | In progress | health/SQLite schema version 为 3；已有核心 `/api/v1`、OpenAI-compatible 同步 Local Chat、模型设置，以及 Route-to-Agent Run preview 路由；graph snapshot 与 legacy manifest 协议仍为 schema v1；尚无 SSE、WebSocket、认证/多租户边界和完整 application service 分层 |
| `backend/tests/` | In progress | 已有 graph/API、路线隔离、AI 设置、Agent Runtime、工具安全和 v1/v2→v3 迁移测试；2026-09-01 完整后端套件 86 项通过 |
| `apps/web/` | In progress | React chat/graph/model settings 与 Agent Run preview UI 已实现；2026-09-01 前端 47 项测试、typecheck/production build 与 Run 成功路径真实浏览器 E2E 通过，真实窄屏和失败 Run 浏览器路径仍待补验 |
| `scripts/dev.ps1` | Done for current slice | 从仓库根目录启动 API:8000 和 Web:5173，可用 `-WebPort` 覆盖 Web 端口 |
| `scripts/check.ps1` | Done for current slice | 统一执行后端测试/compileall 与前端测试/build |
| Local Graph Chat | In progress | create/message/branch/broadcast/route isolation/i18n、双击节点切换、草稿保持、本地记录/继承记忆分离、固定布局、独立消息滚动和 Markdown 渲染已完成手工真实浏览器验收；自动化 E2E 与 popup 自动关闭仍待宿主验收 |
| Browser window sync | Done for current slice | 当前使用 `BroadcastChannel + window.opener.postMessage`；不是 WebSocket |
| Local AI Chat | In progress | 已实现网页/环境变量配置、模型发现、连接验证、OpenAI-compatible 同步回复、编辑最近提问并原子重新生成、思考/内联错误状态、稳定错误码和当前路线消息写回；API key 仅进程内存；尚无 SSE、停止生成、独立回答重试、请求幂等键、凭据库或 metabolize |
| Route-to-Agent Run v1 | Verified local preview | 已验证 execution brief、具体路线 frozen context、durable event journal、`safe_calculator` / `1.0.0`、生产 OpenAI-compatible adapter、测试专用 `ScriptedMockAgentAdapter`、idempotency/revision/interrupted recovery，以及 Web run dialog/timeline；仅限本机单进程/单 worker 的同步窄切片，不代表 Phase 2 完成 |
| Route Memory / metabolize | Planned | Phase 3 |
| `conversation-workflow-bridge-v4` | Legacy frozen | 当前最完整的 Codex 原型；不再承载新的全局业务状态 |
| `conversation-workflow-skill-v4` | Legacy frozen | 兼容规范、manifest v1 和迁移依据 |
| `conversation-workflow-demo/public/*.html` | Visual spec only | 仅迁移其布局和交互语言，不继续补成长期原生 JS 应用 |
| Codex 正式适配器 | Planned | Phase 9，与 Claude 共用 capability-aware HostAdapter 契约 |
| Claude Code 正式适配器 | Planned | Phase 9，不假设与 Codex 具有相同会话语义 |
| Desktop companion / packaging | Planned | Phase 11，loopback authentication 与凭据/权限治理必须同时设计 |

## Phase 0 变更纪律

- 旧 v4 只做安全、兼容、关键缺陷和迁移所需修改。
- graph、route、checkpoint、prune 和 operation 的新语义先进入 graph-core/ADR。
- manifest 与全局 DB 不得同时作为在线写入真源。
- demo HTML 不得被包装成“已完成全栈 demo”。
- 在 popup 自动关闭和流式 AI chat 完成前，README 必须区分“可运行工作流 demo”和“完整 Local Graph Chat”。

## Phase 1 剩余工作

1. 在能观察标准 popup 的浏览器宿主中补验双击成功后的自动关窗；单次 activate、聊天切换和草稿保持已经验证。
2. 把核心浏览器闭环固化为可重复运行的 E2E 套件。
3. 在现有 OpenAI-compatible 同步 Local Chat 上加入 SSE、停止生成和独立回答重试；Agent Run 的幂等键不等于聊天请求幂等。
4. 为现有 schema v3 前向 migration runner 补齐升级矩阵、失败恢复、备份和 rollback/downgrade 发布策略。
5. 建立正式 Standalone HostAdapter、Host MockAdapter 和 adapter contract test；测试专用 `ScriptedMockAgentAdapter` 不替代 HostAdapter。
6. 评估多进程/跨设备场景后再引入 WebSocket；当前同源浏览器窗口继续使用 browser events。
7. 人工确认并删除旧的 `backend/workflow.db` 测试产物。

## Route-to-Agent Run v1 验证记录

2026-09-01 的 **Verified local preview** 记录如下：

- Python 3.12 后端套件 86 项通过，覆盖 run create/read/events、frozen context、event sequence、重启恢复、工具安全、adapter contract、idempotency 与 revision；
- 前端 Vitest 47 项通过，覆盖 brief、重复提交保护、失败 Run 恢复、事件分页与路线切换竞态；
- Python compileall、TypeScript typecheck 与 production build 通过；主 bundle 约 570 kB，仍有非阻塞 chunk-size 警告；
- 真实浏览器使用受控 OpenAI-compatible 假上游完成 `数据集 → 情感分析` 路线的模型→`safe_calculator`→timeline→最终回答闭环；兄弟路线 canary 未进入请求；
- 浏览器 E2E 验证的是桌面视口成功路径；失败 Run 目前有组件测试，真实窄屏响应式行为也尚未完成浏览器验收。

逐项证据和剩余局限见 [Route-to-Agent Run v1](route-to-agent-run-v1.md)。本机验证不包含 SSE/cancel、任意 shell/文件/网络工具、artifacts、evaluation、多 Agent 或正式 Codex/Claude adapter；当前也没有生产级认证、多租户授权和公网部署保证。
