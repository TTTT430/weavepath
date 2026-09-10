# Runtime v2 P2：可靠执行与路线级自动压缩

更新日期：2026-09-09

## 本轮目标

P2 不重复开发已经存在的时间线、Token、费用、缓存 usage 和工具记录。本轮补足两个运行时缺口：

1. 在进程中断、重试和审批恢复时，不把未知结果当成失败前的安全状态，也不重复执行已经完成的副作用。
2. 当当前路线超过上下文预算时，自动生成可审计、可重建、严格隔离兄弟路线的模型输入投影。

应用仍不实现推理引擎 KV cache。自动压缩只改变当次发送给模型的历史投影，原始 transcript 始终是权威数据。

## 路线压缩语义

假设存在：

```text
A → B → C → D
      └→ E
```

当 D 的上下文需要压缩时：

- GraphStore 中 A、B、C、D 的原始消息保持不变；
- compactor 只接收动态读取出的 A-B-C-D 有效路线；
- 较早消息被转换为一个确定性、带来源与 hash 的审计摘要；
- 最近 8 条路线消息逐字保留；
- 计划绑定 `targetInstanceId=D` 和 A、B、C、D 的 `routeRevisionVector`；
- E 的私有消息不可能进入 D 的输入或压缩计划。

之后如果 B 新增消息：

- C、D、E 的下一次请求都会先沿 parent 链读取 B 的最新内容；
- 旧压缩计划仍作为历史回答/run 的审计记录保留，不被改写；
- 新请求因为 B 的 revision 和 source-message hash 已变化，会生成新的压缩计划；
- 不存在“把 A/B 永久压缩到第一条支线”的全局状态。

因此，A/B 是一份规范原始数据，多条路线只是按各自目标和当前 revision 派生模型输入。两条路线可以拥有相同的 A-B 原始前缀，但不能复用包含 C 或 E 私有内容的摘要。

## 压缩计划合同

自动压缩计划随普通 assistant message 的 `responseDetails.compactionPlan` 或 Agent run 的冻结 context 保存。关键字段包括：

- `compactorVersion`：确定性压缩格式版本；
- `targetInstanceId`、`routeInstanceIds`：唯一目标路线；
- `routeRevisionVector`：当次读取的每个祖先版本；
- `sourceMessageIds`、`sourceMessageSetSha256`：被压缩输入的审计来源；
- `summarySha256`、`contextSha256`：摘要与最终路线投影 hash；
- `originalMessages`、`compactedMessages`、`retainedMessages`：消息数量；
- `originalCharacters`、`resultCharacters`、`compressionRatio`：预算结果；
- `budgetExceededByProtectedTail`：近期完整消息本身已超过预算时的显式告警。

摘要不包含 run ID、时间戳、幂等键、UI 状态或随机 nonce。它只包含原消息角色、来源 instance、message ID、内容 hash 与确定性摘录。

默认字符预算为 240,000，可在启动 API 前设置：

```powershell
$env:WEAVEPATH_CONTEXT_BUDGET_CHARS = "240000"
```

配置范围被限制在 16,000 到 2,000,000 字符。当前以字符数作为跨 provider 的保守触发器；未来可以由具体模型 adapter 提供 token-window 能力，但不能因此改变路线隔离规则。

## 执行所有权与心跳

`agent_runs` 的辅助 runtime migration v2 增加：

- `lease_owner`
- `lease_expires_at`
- `last_heartbeat_at`
- `execution_phase`

执行者启动 run 时原子获得 lease；provider 或工具调用阻塞期间按固定间隔续约。进入模型请求、步骤间隔或工具执行前会重新验证所有权。所有权丢失时必须停止进入新的外部边界。

当前产品仍由数据库旁的 OS 锁限制为单 API 进程。数据库 lease 是可靠执行协议和未来 worker 拆分的基础，不表示当前已经支持多个 Uvicorn worker 竞争接管。

## 副作用 effect journal

审批工具的幂等单位不是单个 attempt，而是整个 `rootRunId` 重试 lineage：

```text
effectKey = SHA256(rootRunId + toolName + toolVersion + canonicalArguments)
```

`tool_effects` 状态为：

```text
prepared → executing → completed
                     └→ failed
          └→ interrupted
```

- 完全相同且已经 `completed` 的 effect 在 retry 中只复制已记录结果并发出 `tool.reused`，不再次调用工具，也不创建第二份 Artifact。
- `executing` 时进程中断，外部结果不可证明，effect 进入 `interrupted`；相同 effect 的自动重放被拒绝并返回 `toolOutcomeUnknown`。
- 模型请求中断使用 `modelOutcomeUnknown`，避免把迟到模型回答当成可安全重试的“尚未调用”。
- 尚未进入外部边界的 queued run 仍可以通过原有冻结 context 完整性校验后恢复。

当前副作用执行器仍只允许 `propose_patch / 1.0.0`，且结果只是可审查 Artifact，不直接修改工作区。未来接入真实写文件、网络或 shell 工具前，必须先为每种 effect 定义可查询结果、幂等键或人工协调策略。

## 已验证

自动化覆盖：

- 压缩输出确定、近期消息完整保留、审计 hash 稳定；
- A-B-C-D 与 A-B-E 兄弟隔离；
- B 在分支创建后更新会进入下一次子路线输入并改变派生计划；
- 压缩不改变 system policy / sorted tools 稳定前缀；
- Chat 和 Agent run 均持久化并显示压缩计划；
- runtime auxiliary migration v1→v2；
- 单 owner lease、心跳阶段与错误 owner 拒绝；
- 同 lineage 完成 effect 只执行一次；
- 中断且结果未知的 effect 禁止自动重放；
- model/tool 未知边界恢复使用不同稳定错误码。

P2 完成时的统一基线为后端 187 项、前端 153 项、Python compileall、TypeScript typecheck 和 production build 通过；后续 P3 测试在此基础上继续累加。

## 后续边界

- 用真实 provider 的长路线验证质量、延迟、token 与缓存命中率变化，再调整默认预算和近期完整消息数量。
- 增加模型能力表，以 token-window 而非单一字符估算提供 adapter-specific 预算。
- 为真实外部副作用增加 provider idempotency key、result lookup 和人工 reconciliation UI。
- 在取消 OS 单进程锁之前，完成多进程 lease takeover、fencing token 和 SQLite/队列压力验证。
- 不在本轮实现自动跨兄弟摘要合并、全局 transcript 覆盖或应用层 KV cache。
