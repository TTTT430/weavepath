# 数据所有权

## 原则

WeavePath 保存完成 Agent 工作路线所需的最小数据。图元数据由 Core Service 拥有；宿主 transcript、项目文件和密钥继续由其原系统拥有，除非用户明确选择导入或索引。

| 数据 | 长期真源 | 默认是否复制到全局 DB | 说明 |
|---|---|---:|---|
| Workflow、topic、instance、parent、revision | 全局 SQLite | 是 | graph-core 的结构真相 |
| Host task/session ID | 全局 SQLite | 是 | 只保存 binding 和必要 capability |
| Codex/Claude transcript | 对应宿主 | 否 | 按需 inspect；索引需用户开启 |
| Standalone transcript | 全局 SQLite | 是 | Local Chat 自己拥有 |
| Checkpoint | 全局 SQLite | 是 | 分支时记录不可变锚点、创建时快照并绑定具体 instance/revision；运行时上下文沿 parent 路线动态读取 |
| Foundation、route digest | 全局 SQLite（planned） | 尚未实现 | 未来必须绑定具体 instance/checkpoint |
| Agent run brief、状态、step、event | 全局 SQLite | 是 | Route-to-Agent Run v1 verified local preview；run 绑定具体 instance/revision |
| Agent frozen context snapshot | 全局 SQLite | 是 | 复制当次具体路线的完整 effective messages、memory route、工具规格与 brief；用于可复现性，不是缓存 |
| Tool call 参数与 tool result | 全局 SQLite | 是 | 当前只允许 `safe_calculator` / `1.0.0`；保存 hash、耗时和稳定错误码 |
| Agent final answer | 全局 SQLite | 是 | 同时写入节点 assistant message；run 内另存不可变副本，后续重新生成聊天不改写它 |
| Agent model snapshot | 全局 SQLite | 是 | 仅 allowlist 的 provider/model/base URL/timeout/system prompt 等非凭据字段 |
| 临时任务摘要 | 宿主或派生缓存 | 可选 | 不能混入兄弟路线记忆 |
| 项目文件、数据集、实验输出 | 项目文件系统 | 否 | 数据库仅存路径、hash、版本、provenance |
| API key、OAuth token | 当前进程内存；部署时可来自环境变量；未来系统凭据库 | 否 | 禁止回显，禁止写 SQLite、工作流或 `model-settings.json` |
| UI selection、zoom、打开面板 | 当前 surface | 否 | 不是图领域状态 |
| 语言、主题等偏好 | settings | 是 | 不得翻译用户的对话名称 |

## transcript 与索引

- 外部宿主 transcript 默认只通过 HostAdapter 分页读取。
- 节点摘要必须标记来源 instance 和生成 checkpoint。
- 启用全局搜索时，用户应选择“仅索引摘要”或“索引完整记录”。
- 删除索引副本不能删除宿主原始记录；归档宿主任务也不能被描述为永久删除。

## Agent Run 快照与读取边界

Route-to-Agent Run v1 为了可复现性，会把选定具体路线的 effective messages 复制到 `agent_runs.context_snapshot_json`。这与“外部宿主 transcript 默认不复制”并不冲突：当前已验证的本机 preview 只运行 WeavePath 自己拥有的 Local Chat 消息；未来若接入 Codex/Claude，必须先定义显式导入/索引授权，不能把宿主 transcript 静默复制进 run。

context snapshot 当前包含：

- `workflowId`、`instanceId`、接受的 `inputContentRevision`；
- 从根到目标 instance 的 `memoryRoute`；
- 当次 `availableTools` 规格；
- 该具体路线的完整 effective messages；
- `objective`、`constraints`、`deliverables`、`acceptanceChecks`。

数据库还保存 request/context SHA-256、经过 allowlist 的 model snapshot、事件 payload、tool arguments/results 和最终答案。API key、Authorization header、环境变量和 provider 原始错误正文不得进入这些字段。

Run summary/detail 会返回 context hash、memory route 与 available tool 规格，但不返回 frozen messages 或完整 context snapshot。`GET /api/v1/runs/{runId}` 与 `/events` 当前按全局 run ID 查询，不再重复要求 workflow/instance。这个形状仅适用于当前单用户 loopback preview；它没有身份认证或租户授权边界，服务不得直接暴露到公网。未来多用户模式必须在该查询前增加 workspace/tenant 授权，而不是依赖 run ID 难以猜测。

## 路线隔离

Context builder 只可读取：

1. 当前实例自己的消息；
2. parent 链上祖先实例当前的消息（沿路线动态读取）；checkpoint 创建时快照仅用于审计；
3. 用户显式授权的跨路线 transfer。

禁止自动读取：

- sibling transcript；
- 相同 `topic_id` 的其他实例；
- 兄弟路线的消息；
- 另一个 workspace 的内容。

显式 transfer 必须保存 source instance、source checkpoint、target instance、用户确认和摘要/hash，以便审计其来源。

## 本地数据库与备份

默认位置：

- Windows：`%LOCALAPPDATA%\WeavePath\data\workspace.db`
- macOS/Linux（无 XDG 时）：`~/.local/share/weavepath/data/workspace.db`
- Linux（设置 XDG 时）：`$XDG_DATA_HOME/weavepath/data/workspace.db`

为避免改名造成数据丢失，当新路径不存在而旧版 `CoThinker Workspace` / `co-thinker-workspace` 数据库存在时，WeavePath 会原地复用旧数据库；不会自动复制、重命名或删除。

优先使用 `WEAVEPATH_DATA_DIR` 覆盖数据目录，或用 `WEAVEPATH_DB` 直接指定数据库文件；`COTHINKER_DATA_DIR` 与 `COTHINKER_WORKFLOW_DB` 仅为兼容入口。当前 schema version 为 3，SQLite 已启用 WAL、外键和 `schema_migrations` 前向迁移；自动 downgrade、rollback 与发布级恢复工具仍待实现。备份必须使用 SQLite backup API 或一致性快照，不直接复制正在写入的 WAL 组合。

仓库中的 `backend/workflow.db` 是早期测试产物，不是长期真源；`*.db` 已被忽略。删除是独立的人工确认操作，不由启动或文档脚本自动执行。
