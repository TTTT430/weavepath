# 数据所有权

## 原则

WeavePath 保存完成 Agent 工作路线所需的最小数据。图元数据由 Core Service 拥有；宿主 transcript、项目文件和密钥继续由其原系统拥有，除非用户明确选择导入或索引。

| 数据 | 长期真源 | 默认是否复制到全局 DB | 说明 |
|---|---|---:|---|
| Workflow、topic、instance、parent、revision | 全局 SQLite | 是 | graph-core 的结构真相 |
| Instance title 与标题来源 | 全局 SQLite | 是 | schema v7 区分系统生成标题与用户标题；用户标题不得被自动命名覆盖 |
| Host task/session ID | 全局 SQLite | 是 | 只保存 binding 和必要 capability |
| Codex/Claude transcript | 对应宿主 | 否 | 按需 inspect；索引需用户开启 |
| Standalone transcript | 全局 SQLite | 是 | Local Chat 自己拥有 |
| Local Chat 路线文件 | 本机 `files/objects` + 全局 SQLite 索引 | 是 | 单文件最多 50 MiB；大文件上传会话和已收分片进入 SQLite/专用上传目录，完成后原始字节按 SHA-256 存储；SQLite 保存解析状态、派生分块和 FTS5 trigram 全文索引，查询仅覆盖当前父路线；消息只存路线受限引用，不能跨 workflow/route 复用 |
| 消息自动检索计划 | 全局 SQLite | 是 | 普通请求按实时父路线生成预算化词法检索计划，保存 query、route、精确 chunk/hash、匹配词、上下文与截断状态；历史计划冻结用于重放和审计，兄弟路线不进入候选集，已引用原件不能删除或重解析 |
| Checkpoint | 全局 SQLite | 是 | 分支时记录不可变锚点、创建时快照并绑定具体 instance/revision；运行时上下文沿 parent 路线动态读取 |
| Foundation、route digest | 全局 SQLite（planned） | 尚未实现 | 未来必须绑定具体 instance/checkpoint |
| Agent run brief、状态、step、event | 全局 SQLite | 是 | Runtime v2 本机 preview；run 绑定具体 instance/revision，并保存取消、重试 lineage 与审批状态 |
| Agent frozen context snapshot | 全局 SQLite | 是 | 启动 run 时复制该刻最新 effective route、memory route、工具规格与 brief；用于审计和 revision 防护，不是分支记忆或应用层 KV cache |
| 路线压缩计划 | assistant response details / Agent frozen context | 是 | 超预算时保存具体目标路线、祖先 revision vector、来源 message/hash、摘要/hash 和计数；它是可重建的 provider-input 投影，不删除或替换原始 transcript，也不能跨兄弟路线共享私有摘要 |
| Model step usage | 全局 SQLite | 是 | 只持久化 allowlist token/cache 字段；provider 未报告时保持不可用，不保存未知原始 usage 对象 |
| Local Chat response details | 全局 SQLite | 是 | 按 assistant message 保存实际用时、模型标识和 allowlist token/cache 字段；旧消息不回填伪造值 |
| Tool call 参数与 tool result | 全局 SQLite | 是 | `safe_calculator` 无副作用；`propose_patch` 审批后只生成 Artifact；可选工作区读取工具必须显式配置根目录 |
| Run lease 与 tool effect journal | 全局 SQLite | 是 | lease 记录当前执行者、心跳和执行阶段；effect journal 按 root-run lineage + 工具版本 + 规范参数防止已完成副作用被重试执行，并阻止结果未知的自动重放 |
| Agent final answer | 全局 SQLite | 是 | 同时写入节点 assistant message；run 内另存不可变副本，后续重新生成聊天不改写它 |
| Agent model snapshot | 全局 SQLite | 是 | 仅 allowlist 的 provider/model/base URL/建连超时/无响应时限/重试次数/system prompt 等非凭据字段 |
| 临时任务摘要 | 宿主或派生缓存 | 可选 | 不能混入兄弟路线记忆 |
| 项目文件、数据集、实验输出 | 项目文件系统 | 否 | 数据库仅存路径、hash、版本、provenance |
| API key、OAuth token | 默认当前进程内存；可来自环境变量；用户显式同意时由 Windows 当前用户 DPAPI 加密到独立凭据文件 | 仅显式选择时 | 禁止回显，禁止写 SQLite、工作流、日志或 `model-settings.json`；加密文件不能被其他 Windows 账户解密 |
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
- 超预算时该路线的确定性 compaction plan；原始 effective messages 仍保留在冻结审计快照和规范 transcript 中；
- `objective`、`constraints`、`deliverables`、`acceptanceChecks`；
- 当次自动检索计划及精确证据文本；对外 detail 隐藏完整证据正文，只显示可审计来源和 hash。

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

## 自动压缩的所有权

自动压缩不拥有消息，也不把消息从一个节点“搬到”某条支线。原始 A、B、C 等本地消息仍由各自 `ConversationInstance` 的 transcript 拥有；运行时沿 parent 链组装选定路线。

例如 `A-B-C-D` 和 `A-B-E`：

- A、B 原始消息各保存一份，两条路线动态读取相同的 A-B；
- D 的压缩计划绑定 A-B-C-D 及其 revision vector；E 的计划绑定 A-B-E；
- D 的摘要不能被 E 直接采用，因为它可能含 C 私有内容；
- B 更新后，两个旧计划只作为历史审计记录存在，下一次请求分别从最新 B 重新派生；
- 删除旧压缩计划不能删除 A/B 原始消息，归档 D 也不能改变 E 的有效路线。

## 本地数据库与备份

默认位置：

- Windows：`%LOCALAPPDATA%\WeavePath\data\workspace.db`
- macOS/Linux（无 XDG 时）：`~/.local/share/weavepath/data/workspace.db`
- Linux（设置 XDG 时）：`$XDG_DATA_HOME/weavepath/data/workspace.db`

为避免改名造成数据丢失，当新路径不存在而旧版 `CoThinker Workspace` / `co-thinker-workspace` 数据库存在时，WeavePath 会原地复用旧数据库；不会自动复制、重命名或删除。

优先使用 `WEAVEPATH_DATA_DIR` 覆盖数据目录，或用 `WEAVEPATH_DB` 直接指定数据库文件；`COTHINKER_DATA_DIR` 与 `COTHINKER_WORKFLOW_DB` 仅为兼容入口。当前 graph schema version 为 7，runtime auxiliary schema version 为 3；SQLite 已启用 WAL、外键和迁移表前向迁移。受管理启动会在真实迁移前使用 SQLite backup API 创建并校验 verified snapshot，失败自动恢复；设置页可查看备份、执行保留清理并生成要求停机和精确确认短语的离线 restore plan。项目不提供反向 SQL migration。schema v7 迁移把所有既有标题视为用户所有，宁可保留旧的 `新分支 N`，也不猜测并覆盖历史名称。备份不能直接复制正在写入的 WAL 组合。

仓库中的 `backend/workflow.db` 是早期测试产物，不是长期真源；`*.db` 已被忽略。删除是独立的人工确认操作，不由启动或文档脚本自动执行。
