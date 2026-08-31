# 数据所有权

## 原则

WeavePath 保存完成 Agent 工作路线所需的最小数据。图元数据由 Core Service 拥有；宿主 transcript、项目文件和密钥继续由其原系统拥有，除非用户明确选择导入或索引。

| 数据 | 长期真源 | 默认是否复制到全局 DB | 说明 |
|---|---|---:|---|
| Workflow、topic、instance、parent、revision | 全局 SQLite | 是 | graph-core 的结构真相 |
| Host task/session ID | 全局 SQLite | 是 | 只保存 binding 和必要 capability |
| Codex/Claude transcript | 对应宿主 | 否 | 按需 inspect；索引需用户开启 |
| Standalone transcript | 全局 SQLite | 是 | Local Chat 自己拥有 |
| Foundation、checkpoint、route digest | 全局 SQLite | 是 | 必须绑定具体 instance/checkpoint |
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

## 路线隔离

Context builder 只可读取：

1. 当前实例自己的消息；
2. 每个祖先在子分支创建时冻结的 checkpoint；
3. 用户显式授权的跨路线 transfer。

禁止自动读取：

- sibling transcript；
- 相同 `topic_id` 的其他实例；
- 当前 checkpoint 之后新增的祖先消息；
- 另一个 workspace 的内容。

显式 transfer 必须保存 source instance、source checkpoint、target instance、用户确认和摘要/hash，以便审计其来源。

## 本地数据库与备份

默认位置：

- Windows：`%LOCALAPPDATA%\WeavePath\data\workspace.db`
- macOS/Linux（无 XDG 时）：`~/.local/share/weavepath/data/workspace.db`
- Linux（设置 XDG 时）：`$XDG_DATA_HOME/weavepath/data/workspace.db`

为避免改名造成数据丢失，当新路径不存在而旧版 `CoThinker Workspace` / `co-thinker-workspace` 数据库存在时，WeavePath 会原地复用旧数据库；不会自动复制、重命名或删除。

支持 `COTHINKER_DATA_DIR` 覆盖数据目录，也保留 `COTHINKER_WORKFLOW_DB` 直接指定文件的兼容方式。当前 schema version 为 1，SQLite 已使用 WAL 和外键；正式 migration runner/迁移表仍待实现。备份必须使用 SQLite backup API 或一致性快照，不直接复制正在写入的 WAL 组合。

仓库中的 `backend/workflow.db` 是早期测试产物，不是长期真源；`*.db` 已被忽略。删除是独立的人工确认操作，不由启动或文档脚本自动执行。
