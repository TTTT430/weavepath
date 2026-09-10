# P3：正式 HostAdapter 与数据库发布恢复

更新日期：2026-09-10

## 状态

P3 的当前本机单用户切片已经完成，包含三部分：

1. Codex 与 Claude Code 的真实 companion transport；
2. 跨宿主写操作的持久化 Saga、幂等重放和补偿；
3. 用户可见的 verified backup、保留策略与停机恢复入口。

这里的“完成”不代表多设备、多用户或公网生产部署完成。当前一次只发现一个本机 companion；Codex 与 Claude Code 各自只声明宿主真实提供的能力。

## 1. 真实宿主 companion

### Codex

`plugins/weavepath-codex-companion` 是个人 Codex 插件。它通过 Codex 提供的原生 app-tools pipe 调用任务能力，不向 composer 写入控制文本，也不使用自定义深链。

- 枚举与读取：`list_threads`、`read_thread`；
- 分支：`fork_thread`，当前只支持任务头；
- 导航：`navigate_to_codex_page`；
- 重命名与归档：`set_thread_title`、`set_thread_archived`。

插件启动后在 `127.0.0.1` 随机端口发布 contract-v1 loopback endpoint，并生成每进程随机 token。发现文件不包含用户 transcript。

### Claude Code

`integrations/claude_code_companion` 读取 Claude Code 本地 JSONL 会话，并按 Claude Code 的公开 CLI 语义调用 `--resume ... --fork-session`。历史 turn 精确分支、宿主重命名和宿主归档没有可靠公开能力，因此明确返回 unsupported，不进行伪实现。

启动：

```powershell
.\scripts\start-claude-companion.ps1
```

companion 启动后需重启 WeavePath API，使 API 读取新的带 token 发现文件。

### 宿主数据入口

```text
GET  /api/v1/host/capabilities
GET  /api/v1/host/conversations
POST /api/v1/host/conversations/import
GET  /api/v1/workflows/{workflowId}/instances/{instanceId}/host-transcript
```

导入只在 WeavePath 中保存 provider 与 conversation ID 绑定。提交前会用 companion 验证该任务可读；重复导入按 provider + conversation ID 幂等。外部 transcript 继续归宿主持有，不复制到 SQLite。

## 2. Host operation Saga

`host_operation_sagas` 在执行外部写操作前先保存规范请求与幂等键。fork、navigate、rename、archive 均记录 `started → host_succeeded → completed`，进程崩溃后可从 `host_succeeded` 继续本地提交，而不会重复调用宿主。

- fork 本地注册失败时尝试归档远端子任务作为补偿；
- rename 本地提交失败时尝试恢复宿主原标题；
- prune 先验证 graph revision，再按 leaf-first 调宿主归档，全部成功后提交本地 tombstone；
- 无法确定宿主结果的 `started` 操作在启动恢复时标记为 `orphaned`，不盲目重放副作用；
- workflow、instance、thread、provider identity 不匹配时拒绝结果。

诊断接口：

```text
GET /api/v1/host/operations
GET /api/v1/host/operations/{operationId}
```

## 3. 数据库备份、保留与恢复

受管理启动在真实 schema 升级前执行：只读版本预检 → SQLite online backup → SHA-256 与 `PRAGMA integrity_check` → manifest → 前向迁移 → 目标版本复检。迁移失败时，在进程独占锁内自动恢复升级前快照。

当前 graph schema 为 7，runtime auxiliary schema 为 3。runtime v3 新增宿主 Saga 与 Chat SSE durable event journal。

```text
GET  /api/v1/system/database
GET  /api/v1/system/database/backups
POST /api/v1/system/database/backups/retention
POST /api/v1/system/database/restore-plan
```

设置页显示数据库路径、完整性、版本、可恢复备份和保留清理。恢复必须先停止 API，再执行后端给出的命令；CLI 要求精确短语 `RESTORE <database filename>`，并再次校验 manifest、hash、完整性和独占锁。项目不提供反向 SQL migration，也不会在 API 运行时覆盖唯一数据库。

## P4 收尾验收

`scripts/check-live.ps1` 执行三个真实进程边界：

1. 通过当前 Codex app-tools pipe 枚举并读取真实可见任务；
2. 通过真实 Claude Code 本机会话目录与 CLI 握手、枚举并读取会话；
3. 启动真实 Uvicorn HTTP socket 与可控 OpenAI-compatible SSE provider socket，在首个流中途断开后验证自动重连、`message.reset`、durable replay、同一幂等键、单条 user message 和单条完整 assistant message。

第三项是可重复的协议级故障注入，不使用用户付费模型，也不宣称某个公网供应商当前可用。公网模型仍需用用户自己的 URL、模型和密钥做一次人工验收。

完整证据见 [P3/P4 真实连接验收](p3-p4-live-acceptance.md)。

## 已知边界

- companion 发现文件当前代表一个活动宿主，尚未实现 Codex 与 Claude Code 同时在线的 registry；
- Codex 仅对当前任务头提供分支；Claude Code 需要首条 prompt 才能创建 fork；
- 未提供永久删除，只提供可恢复的归档语义；
- 没有多租户授权、跨设备同步、签名安装包或自动更新服务；
- 前端已具备宿主枚举/导入 API client，完整的可视化宿主导入选择器留给后续产品切片。
