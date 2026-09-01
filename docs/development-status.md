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

## 当前状态

| 范围 | 状态 | 说明 |
|---|---|---|
| Phase 0 产品与架构文档 | Done | 愿景、边界、数据所有权、ADR、迁移和验收已记录 |
| `backend/pyproject.toml` | Done | 声明 Python 3.12、FastAPI、Uvicorn 和测试依赖 |
| `backend/graph_core/` | In progress | 已验证 SQLite GraphStore、冻结 checkpoint、同 topic 多实例、leaf-first prune、实例级 `contentRevision`、workflow `eventRevision`、schema v1/v2/v3/v4→v5 前向迁移、checkpoint cursor、local-only turn projection 和从具体用户 turn 冻结 checkpoint；仍未拆稳定 repository/ports，也没有自动 rollback/downgrade |
| `backend/api/` | In progress | health/SQLite schema version 为 5；核心 `/api/v1`、OpenAI-compatible 同步 Local Chat、模型设置、Route-to-Agent Run preview、Engineering Lab preview、`GET .../turns` 和精确 fork 已完成自动化验证；graph snapshot 与 legacy manifest 协议仍为 schema v1，尚无 SSE、WebSocket、认证/多租户边界和完整 application service 分层 |
| `backend/tests/` | In progress | graph/API、路线隔离、AI 设置、Agent Runtime、工程记录、工具安全、turn cursor 和 v1/v2/v3/v4→v5 migration 已纳入套件；2026-09-01 当前完整后端套件为 103 项通过 |
| `apps/web/` | In progress | React chat/model settings、可读 Agent 执行时间线、原生 `WorkspaceShell`、双层画布和 Engineering Lab 已实现；当前前端套件为 65 项通过，typecheck/production build、既有主路径 E2E 与实验室只读浏览器验收已完成，实验室写入路径、真实窄屏和失败 Run 浏览器路径仍待补验 |
| WorkspaceShell / 双层画布 | Verified local preview | 默认入口为同页“对话 / 工作流”；顶层是 `ConversationInstance` 图，双击钻入 local-only Turn Canvas，只有显式“继续对话”才 activate。已完成自动化与真实浏览器 E2E，边界见 ADR-0004 |
| `scripts/dev.ps1` | Done for current slice | 从仓库根目录启动 API:8000 和 Web:5173，可用 `-WebPort` 覆盖 Web 端口 |
| `scripts/check.ps1` | Done for current slice | 统一执行后端测试/compileall 与前端测试/build |
| Local Graph Chat | Verified local preview | create/message/branch/route isolation/i18n、草稿保持、本地记录/继承记忆分离、Markdown、原生工作流切换、双击钻入 Turn Canvas、精确 turn 分支和显式 Continue 已完成本机自动化与真实浏览器 E2E；SSE、正式 HostAdapter 和完整 Phase 1 退出条件仍未完成 |
| Browser window sync | Done for current slice | 旧版与可选 `/graph` 兼容入口使用 `BroadcastChannel + window.opener.postMessage`；原生 WorkspaceShell 内的普通视图切换/selection 不依赖它，也不是 WebSocket |
| Local AI Chat | In progress | 已实现网页/环境变量配置、模型发现、连接验证、OpenAI-compatible 同步回复、编辑最近提问并原子重新生成、思考/内联错误状态、稳定错误码和当前路线消息写回；API key 仅进程内存；尚无 SSE、停止生成、独立回答重试、请求幂等键、凭据库或 metabolize |
| Route-to-Agent Run v1 | Verified local preview | 已验证 execution brief、具体路线 frozen context、durable event journal、`safe_calculator` / `1.0.0`、生产 OpenAI-compatible adapter、测试专用 `ScriptedMockAgentAdapter`、idempotency/revision/interrupted recovery，以及 Web run dialog/timeline；仅限本机单进程/单 worker 的同步窄切片，不代表 Phase 2 完成 |
| Engineering Lab v1 | Done for current slice | schema v5 已加入不读取 transcript 的 2–4 分支对比、显式知识/Artifact 合并、路线作用域接纳知识、版本化 Artifact/数据集和实验快照；自动 evaluator/scorer、参数矩阵、Artifact diff/外部文件引用仍未实现 |
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
- README 必须区分“Verified local preview”与“完整 Local Graph Chat / 生产可用”；可选 popup 生命周期不再是默认入口的完成条件。

## Phase 1 剩余工作

1. 在现有 OpenAI-compatible 同步 Local Chat 上加入 SSE、停止生成和独立回答重试；Agent Run 的幂等键不等于聊天请求幂等。
2. 为 schema v5 前向 migration runner 补齐升级矩阵、失败恢复、备份和 rollback/downgrade 发布策略。
3. 建立正式 Standalone HostAdapter、Host MockAdapter 和 adapter contract test，覆盖 `can_read_local_turns`、精确 cursor fork 与导航降级；测试专用 `ScriptedMockAgentAdapter` 不替代 HostAdapter。
4. 扩展 Tool/Failure/Approval Timeline，并为宿主注入上下文、归档事件和 transcript projector 增加 typed classifier。
5. 评估多进程/跨设备场景后再引入 WebSocket；可选同源 `/graph` 兼容窗口继续使用 browser events。
6. 人工确认并删除旧的 `backend/workflow.db` 测试产物。

## WorkspaceShell / 双层画布验证记录

2026-09-01 的 **Verified local preview** 记录如下：

- Python 3.12 统一后端套件 103 项通过，包含 schema v1/v2/v3/v4→v5 migration、local-only turn projection、精确 turn cursor、工程记录、stale revision、幂等和路线隔离；
- 前端 Vitest 统一套件 65 项通过，包含 WorkspaceShell 三视图切换、双击仲裁、Turn Canvas、Engineering Lab 的受控合并，以及画布 metadata 持久化；
- Python compileall、TypeScript typecheck 与 production build 通过；
- 真实浏览器 E2E 确认工作流在当前页面原生打开、双击只进入节点内部画布、继承消息不冒充本地 turn、从具体 turn 创建分支，以及显式 Continue 后才切回对应聊天；
- `/graph` 保留为兼容入口，不是默认工作流入口，也不改变上述交互语义。

该验证仅覆盖本机单用户 Standalone slice；正式 Codex/Claude HostAdapter、failure/approval 完整事件投影、跨设备同步、真实窄屏和生产部署不在本次范围内。

## Route-to-Agent Run v1 验证记录

2026-09-01 的 **Verified local preview** 记录如下：

- 当前 Python 3.12 统一后端套件 103 项通过，其中覆盖 run create/read/events/metrics、frozen context、event sequence、重启恢复、工具安全、adapter contract、idempotency 与 revision；
- 当前前端 Vitest 统一套件 65 项通过，其中覆盖 brief、可读执行时间线、重复提交保护、失败 Run 恢复、事件分页、工程实验室和路线切换竞态；
- Python compileall、TypeScript typecheck 与 production build 通过；仍有非阻塞 chunk-size 警告；
- 真实浏览器使用受控 OpenAI-compatible 假上游完成 `数据集 → 情感分析` 路线的模型→`safe_calculator`→timeline→最终回答闭环；兄弟路线 canary 未进入请求；
- 浏览器 E2E 验证的是桌面视口成功路径；失败 Run 目前有组件测试，真实窄屏响应式行为也尚未完成浏览器验收。

逐项证据和剩余局限见 [Route-to-Agent Run v1](route-to-agent-run-v1.md)。本机验证仍不包含 SSE/cancel、任意 shell/文件/网络工具、自动 evaluator/scorer、参数矩阵、多 Agent 或正式 Codex/Claude adapter；当前也没有生产级认证、多租户授权和公网部署保证。
