# 开发状态

更新日期：2026-08-31

## 状态定义

- **Done**：已实现并有相应验证。
- **Done for current slice**：满足当前纵向切片的明确范围，但不代表长期模块已经完成。
- **In progress**：已有真实实现，但尚未满足对应阶段的退出条件。
- **Scaffolded**：仅建立目录或配置，不能代表功能可运行。
- **Planned**：已有文档边界，尚未实现。
- **Legacy frozen**：可作为参考或兼容入口，只接受缺陷/迁移修改。
- **Visual spec only**：只描述目标界面，不是应用代码。

## 当前状态

| 范围 | 状态 | 说明 |
|---|---|---|
| Phase 0 产品与架构文档 | Done | 愿景、边界、数据所有权、ADR、迁移和验收已记录 |
| `backend/pyproject.toml` | Done | 声明 Python 3.12、FastAPI、Uvicorn 和测试依赖 |
| `backend/graph_core/` | In progress | 已有 SQLite GraphStore、冻结 checkpoint、同 topic 多实例、leaf-first prune、实例级 `contentRevision` 和 workflow `eventRevision`；尚未拆 repository/ports 或 migration runner |
| `backend/api/` | In progress | 已有 health、schemaVersion 1、核心 `/api/v1` 路由、OpenAI-compatible 同步 LLM adapter、稳定 AI 错误码，以及非密钥持久化、模型发现和连接验证接口；尚无 SSE、WebSocket 和正式 application service 层 |
| `backend/tests/` | Done for current slice | 42 项测试通过，覆盖 graph/API、local/effective message scope、AI route context、编辑最近提问的原子提交、失败零写入、生成期间 revision 冲突与 fork checkpoint 完整性、WeavePath/旧版环境变量与数据路径兼容、模型设置安全、稳定 AI 错误协议、路线隔离和 sandbox fallback；adapter contract 仍属后续阶段 |
| `apps/web/` | In progress | React chat/graph/model settings 已可运行；30 项测试与 production build 通过；覆盖节点本地记录/继承记忆分离、编辑/复制最近提问、保存并重新生成、切换竞态与按路线发送锁、record-only/AI 发送、思考/内联错误状态、安全 Markdown/GFM、数字/字符串消息 ID、写入型密钥、空模型发现和中英文设置；手工真实浏览器验收覆盖模型设置入口、固定布局、独立消息滚动、宽版正文、克制 Soft UI 和编辑/取消交互；自动化 E2E 与标准 popup 关闭验收仍待完成 |
| `scripts/dev.ps1` | Done for current slice | 从仓库根目录启动 API:8000 和 Web:5173，可用 `-WebPort` 覆盖 Web 端口 |
| `scripts/check.ps1` | Done for current slice | 统一执行后端测试/compileall 与前端测试/build |
| Local Graph Chat | In progress | create/message/branch/broadcast/route isolation/i18n、双击节点切换、草稿保持、本地记录/继承记忆分离、固定布局、独立消息滚动和 Markdown 渲染已完成手工真实浏览器验收；自动化 E2E 与 popup 自动关闭仍待宿主验收 |
| Browser window sync | Done for current slice | 当前使用 `BroadcastChannel + window.opener.postMessage`；不是 WebSocket |
| Local AI Chat | In progress | 已实现网页/环境变量配置、模型发现、连接验证、OpenAI-compatible 同步回复、编辑最近提问并原子重新生成、思考/内联错误状态、稳定错误码和当前路线消息写回；API key 仅进程内存；尚无 SSE、停止生成、独立回答重试、请求幂等键、凭据库或 metabolize |
| Route Memory / metabolize | Planned | Phase 2 |
| `conversation-workflow-bridge-v4` | Legacy frozen | 当前最完整的 Codex 原型；不再承载新的全局业务状态 |
| `conversation-workflow-skill-v4` | Legacy frozen | 兼容规范、manifest v1 和迁移依据 |
| `conversation-workflow-demo/public/*.html` | Visual spec only | 仅迁移其布局和交互语言，不继续补成长期原生 JS 应用 |
| Codex 正式适配器 | Planned | Phase 3，改为调用 Core Service |
| Desktop companion | Planned | Phase 4 |
| Claude Code 适配器 | Planned | Phase 5 |

## Phase 0 变更纪律

- 旧 v4 只做安全、兼容、关键缺陷和迁移所需修改。
- graph、route、checkpoint、prune 和 operation 的新语义先进入 graph-core/ADR。
- manifest 与全局 DB 不得同时作为在线写入真源。
- demo HTML 不得被包装成“已完成全栈 demo”。
- 在 popup 自动关闭和流式 AI chat 完成前，README 必须区分“可运行工作流 demo”和“完整 Local Graph Chat”。

## Phase 1 剩余工作

1. 在能观察标准 popup 的浏览器宿主中补验双击成功后的自动关窗；单次 activate、聊天切换和草稿保持已经验证。
2. 把核心浏览器闭环固化为可重复运行的 E2E 套件。
3. 在现有 OpenAI-compatible 同步 adapter 上加入 SSE、停止生成、独立回答重试和请求幂等键，再进入 metabolize。
4. 建立 SQLite migration runner，而不是仅靠启动时建表。
5. 建立 StandaloneAdapter、MockAdapter 和 adapter contract test。
6. 评估多进程/跨设备场景后再引入 WebSocket；当前同源浏览器窗口继续使用 browser events。
7. 人工确认并删除旧的 `backend/workflow.db` 测试产物。
