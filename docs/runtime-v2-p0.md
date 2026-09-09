# Agent Runtime v2：P0 验收边界

更新日期：2026-09-09

> 后续状态：P0 已保持不变；P1 路线文件检索与 P2 lease/effect journal、路线级自动压缩已继续实现。最新边界见 [Runtime v2 P2](runtime-v2-p2.md)。

## 已实现

本轮 P0 同时保留 Runtime v2 的安全闭环，并把 cache-aware 上下文装配放在其前置边界：

1. 模型请求的逻辑顺序固定为 `System Policy → Tools → route messages → accepted knowledge → current request`。
2. Tools 按 `name + version` 稳定排序，schema 递归规范化；工具版本或策略变化会有意改变前缀。
3. route messages 从 GraphStore 的 `effective` 路线实时读取。创建 C 后再向 B 添加消息，新运行会读取最新 A-B-C；checkpoint 不参与运行时读取，只用于审计。
4. A-B-C 与 A-B-E 只能共享 A-B。accepted knowledge 只有经过显式接纳才会出现在目标路线，兄弟 transcript 不会自动合并。
5. run ID、workflow/instance ID、时间戳、幂等键、UI 语言、缩放和选择状态不会进入模型输入的稳定投影。
6. 应用不保存或模拟推理引擎 KV cache。OpenAI-compatible provider 自己决定是否复用 prompt 前缀。
7. 每个 Runtime model step 独立保存规范化 usage。支持 OpenAI Chat/Responses 风格的 `cached_tokens`，以及 DeepSeek 的 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`。
8. provider 未返回缓存字段时，`cacheStatus` 为 `not_reported` 或 `unsupported`，token 与比例保持 `null`；界面显示不可用，不显示伪造的 `0%`。
9. Run 汇总同时显示缓存复用率和统计覆盖率。复用率只聚合同一次调用内可完整配对的 cached/input 数据，绝不跨调用拼接分子分母；覆盖率分母包含全部 model step。
10. `queued / running / awaiting_approval / cancelling / cancelled / completed / failed / interrupted` 状态持久化；取消、重试 lineage 和审批决定都有独立 API。
11. `propose_patch` 必须审批，执行结果只是版本化 patch Artifact，绝不写工作区。`read_file` / `workspace_search` 只在显式配置一个工作区根目录时注册，并拒绝越界、符号链接、敏感目录、二进制和超大文件。

## 关键不变量

```text
运行 N 的输入 = 启动 N 时的最新有效路线 + 已接纳知识 + 当前 execution brief
运行 N 的审计 = 不可变 context snapshot + prompt/model hash + model/tool/event journal
分支 checkpoint = 创建时锚点与审计快照，不是运行时记忆，也不是 provider cache key
```

运行开始后若目标路线 revision 改变，旧运行不能把回答写回新路线。批准 patch 后如果发生这种冲突，Artifact 仍作为已发生且可审计的结果保留，run 进入终态失败；不会谎称文件已经修改。

## API

```text
POST /api/v1/workflows/{workflowId}/instances/{instanceId}/runs
GET  /api/v1/runs/{runId}
GET  /api/v1/runs/{runId}/events
POST /api/v1/runs/{runId}/cancel
POST /api/v1/runs/{runId}/retry
POST /api/v1/runs/{runId}/approvals/{approvalId}/decision
```

审批 decision 只能为 `approved` 或 `rejected`。相同决定可安全重放；相反决定返回冲突。重试创建新的 run，并保存 `rootRunId`、`parentRunId` 和 `attemptNumber`，不会改写原运行。

## 当前限制

- 正式本机 app 已使用单进程后台 worker；关闭面板或刷新页面不影响运行。进程重启只自动恢复尚未进入模型调用的 queued run；未知上游边界中的 running run 会安全中断，等待审批保持可恢复，cancelling 收敛为 cancelled。
- 已有持久化 owner/lease、心跳和执行阶段，但当前仍由 OS 单实例锁限制为一个 Uvicorn worker；尚无多进程安全接管能力。
- 不应以多个 Uvicorn worker 连接同一个 SQLite 文件运行当前 preview。
- 缓存复用由 provider 决定；相同输入只提供命中条件，不保证命中。
- 第三方 OpenAI-compatible 网关可能不返回任何缓存字段，此时只能显示不可用。
- 同一 usage 合同已经用于 Local Chat JSON/SSE 回复详情；它与 Agent Runtime 的逐 model-step journal 分开持久化。
- 没有任意 shell、网络工具或自动写文件工具。

## 自动化验收

后端测试覆盖：

- cache-aware 前缀在工具注册顺序、run ID、时间戳和 UI 元数据变化时保持稳定；
- 工具 schema、路线和当前请求确定性序列化；
- 父节点新增消息进入既有子路线，checkpoint 不参与运行时上下文；
- A-B-C / A-B-E 仅共享 A-B，兄弟消息隔离；
- OpenAI、Responses 风格和 DeepSeek usage 解析，以及缺失、unsupported、invalid 降级；
- 多模型步骤的 reported-only 复用率与全调用 coverage；
- 审批幂等、拒绝、取消晚响应、重试 lineage、重启恢复、revision 冲突和 Artifact 保留；
- 工作区工具的显式根目录和路径安全。

当前基线：后端 187 项、前端 153 项、Python compileall、TypeScript typecheck 和 production build 通过。
