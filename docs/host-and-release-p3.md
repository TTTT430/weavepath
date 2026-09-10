# P3：HostAdapter 合同与数据库发布硬化

更新日期：2026-09-10

## 范围

本轮 P3 指近期优先级中的“正式 HostAdapter 与迁移/发布硬化”，不是长期路线图中同名的 Route Memory Phase 3。本轮解决两类发布风险：

1. 版本升级不能在没有可验证恢复点的情况下修改用户数据库；旧版应用也不能静默打开新版本数据库。
2. Codex、Claude Code 和 standalone 的会话能力不同，工作流核心不能把某一个宿主的操作当成所有宿主都有的能力。

## 数据库升级协议

受管理的 API 启动按以下顺序执行：

1. 获取数据库对应的进程独占锁；
2. 只读检查 graph/runtime migration 历史是否连续且不高于当前程序；
3. 如果需要升级，使用 SQLite online backup API 创建一致性快照；
4. 对备份执行 `PRAGMA integrity_check`，再写入版本化 JSON manifest；
5. 打开数据库并执行前向迁移；
6. 验证目标 graph/runtime 版本和迁移后完整性；
7. 将 manifest 标记为 `completed`。

备份默认位于数据库旁的 `backups/` 目录，名称包含来源版本、目标版本、UTC 时间和随机后缀。不会在每次正常启动时重复备份；只有存在实际版本迁移时才创建。

如果迁移或迁移后验证失败：

- 先关闭失败的 SQLite 连接；
- 在仍持有数据库独占锁时校验备份；
- 清除该数据库的精确 `-wal/-shm` sidecar；
- 原子替换为升级前备份；
- manifest 标记为 `restored` 并保存失败类型；
- 启动以稳定错误码 `databaseMigrationFailed` 失败，不会转入一个空的临时工作区。

如果必要备份本身无法生成，启动以 `databaseBackupFailed` 失败，也不会继续迁移或隐式切换数据库。

## 版本兼容与降级

- 当前 graph schema 为 7，runtime auxiliary schema 为 2。
- migration marker 必须从 1 连续排列；断裂历史返回 `databaseSchemaHistoryInvalid`。
- 高于当前程序支持范围的 marker 返回 `databaseSchemaTooNew`，检查发生在 WAL 和 DDL 之前。
- 不提供反向 SQL migration。程序版本回退时，必须停止 API，使用对应发布生成的 verified pre-migration backup 恢复，再启动旧版程序。
- 自动清理旧备份暂未实现，避免未经用户确认删除唯一恢复点。

只读诊断接口：

```text
GET /api/v1/system/database
```

返回当前路径、graph/runtime 版本、目标版本、完整性状态、最近一次启动迁移的备份与 manifest 路径，以及明确的 downgrade/rollback 策略。

## HostAdapter contract v1

`HostDescriptor` 现在显式返回：

- `adapterId`、`hostKind`、`displayName`；
- `contractVersion=1`；
- 独立的 capability flags；
- 连接状态与宿主限制。

新增 capability-aware bridge adapter：

- `CodexHostAdapter`
- `ClaudeCodeHostAdapter`
- 共用 `HostBridgeTransport.invoke(operation, payload, operationId)` 边界。

适配器在调用 companion 前检查 fork/navigation/transcript/archive/rename 能力，也会检查 checkpoint cursor 类型。所有 companion 返回的 workflow、instance、thread 和 provider identity 都必须通过验证；跨工作流或跨 provider 绑定会以 `hostBindingMismatch` 拒绝。

这并不宣称当前 standalone Web 已经接通 Codex 或 Claude Code。真实接入仍需要各自受信任 companion/插件提供 transport，并在握手时给出真实能力。核心合同已经避免后续用 composer 文本、深链或虚假的统一能力模拟宿主导航。

兼容接口：

```text
GET /api/v1/host/capabilities
```

保留原有 `adapter` 和 `capabilities` 字段，同时增加完整 `descriptor`。

## 已验证

- v1 数据库在迁移前产生可打开、完整性为 `ok` 的备份与 completed manifest；
- 故障注入破坏迁移目标后，原 v1 数据库自动恢复，manifest 记录为 restored；
- future schema 和非连续 migration history 在写入前拒绝；
- 当前版本数据库正常启动不会产生冗余备份；
- 必需备份失败不会被临时空数据库 fallback 隐藏；
- standalone descriptor 和 contract version；
- Codex bridge 的 operation ID 透传、identity validation、checkpoint/capability 拒绝；
- 当前统一后端套件 197 项通过。

## 下一切片

- 给备份增加用户可见的列表、显式恢复命令和保留策略，恢复必须要求停机和二次确认；
- 为真实 Codex 插件和 Claude Code companion 分别实现 transport 与握手 E2E；
- 增加 host operation saga journal，处理“宿主 fork 已成功但本地注册失败”等部分成功；
- 把数据库诊断加入设置页，而不是要求普通用户直接访问 JSON API；
- 发布前在真实旧数据库副本上执行升级/恢复矩阵，不修改唯一用户原件。
