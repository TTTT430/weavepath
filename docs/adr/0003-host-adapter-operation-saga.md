# ADR-0003：HostAdapter 与 operation saga

- 状态：Accepted
- 日期：2026-08-31

## 背景

Codex、Claude Code 和 Standalone 的 fork、navigate、inspect、archive 能力不同。宿主 API 调用不能加入 SQLite 事务：宿主可能已经创建任务，而本地注册失败；也可能部分 descendant 已归档后网络中断。

## 决策

所有宿主能力通过 `HostAdapter` port 暴露，并进行 capability negotiation。跨数据库与宿主的修改通过持久化 operation saga 协调：

```text
validate → persist pending operation → host call → bind/commit → emit event
```

- 每个外部 mutation 使用稳定 `operation_id` 和 `idempotency_key`。
- fork 失败时回滚 pending；若宿主资源已产生则尝试 archive，否则标记 orphaned。
- prune 先生成带 revision 的 leaf-first plan；全部宿主 archive 成功后才提交 tombstone。
- navigate 成功才通知 UI 关闭；失败保持窗口和重试入口。
- capability 缺失时返回结构化降级结果，不通过 composer 文本模拟。

## 后果

优点：

- graph-core 与宿主解耦；
- 可测试部分失败、重试和重启恢复；
- 同一 UI 可服务不同能力的宿主。

代价：

- operation 有 pending、committed、failed、orphaned 等中间状态；
- 需要恢复 worker 和用户可见的错误处理；
- Codex legacy bridge 需要逐步改造成 adapter，而非继续独立写 manifest。

## Legacy 处理

`conversation-workflow-bridge-v4` 冻结为 legacy Codex adapter。迁移期间只修复安全、兼容和导入所需问题；新的跨模块能力进入 Core Service 和正式 CodexAdapter。
