# ADR-0001：全局 SQLite 为长期真源

- 状态：Accepted
- 日期：2026-08-31

## 背景

当前 schema-v1 manifest 位于具体项目目录，适合同目录 Codex fork 共享，但无法自然支持跨工作区搜索、多宿主绑定、全局设置、operation 恢复和未来 artifacts 模块。让插件、skill、独立应用分别维护状态会产生双写与冲突。

## 决策

长期模式使用每个 OS 用户一份本地 SQLite 作为 graph 和模块元数据的唯一真源：

- 开启 WAL、foreign keys 和 schema migrations；
- workflow 使用 `graph_revision`；instance 使用 `content_revision`；
- manifest v1 仅用于 legacy、导入导出和离线备份；
- 外部 transcript 默认仍由宿主拥有；Standalone transcript 才存入 SQLite；
- secrets 存系统凭据库或环境变量，不进 SQLite。

## 后果

优点：

- 单一事务边界和统一查询；
- 支持跨宿主与跨工作区索引；
- 可持久化 pending/orphaned operation 并在重启后恢复；
- 独立 Web/Desktop 窗口不再依赖某个 manifest 路径。

代价：

- 需要 Core Service 生命周期、迁移和备份机制；
- legacy 插件必须经过明确迁移；
- 不能让 JSON 和 DB 同时在线写入同一 workflow。

## 约束

- Core Service 仅监听 loopback。
- 数据目录可配置但必须 canonicalize。
- 导入/导出必须验证领域不变量。
- 运行中的数据库使用 SQLite backup API 或一致性快照备份。
