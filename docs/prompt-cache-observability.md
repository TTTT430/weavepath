# Prompt cache 与 KV cache 策略

## 决策摘要

WeavePath 现在**不需要实现自己的推理引擎 KV cache**；Agent Runtime v2 已加入
**prompt cache 可观测性和缓存友好的上下文装配**。

原因是这两个概念属于不同层级：

- **推理引擎 KV cache**：由 vLLM、SGLang、llama.cpp 等自托管推理服务管理，保存模型注意力层的
  key/value 张量。WeavePath 当前调用远程或本地 OpenAI-compatible HTTP API，无法直接管理供应商内部的
  KV cache。
- **API prompt caching**：供应商在相同 token 前缀重复出现时复用前缀计算，并可能在响应 usage 中返回
  cached token 数量。WeavePath 能做的是保持前缀稳定、读取供应商返回的 usage，并展示可靠的统计。

因此，本轮 P0 的顺序是：

1. 先固定 cache-aware 上下文合同、动态路线语义和兄弟隔离；
2. 同时记录每个 Runtime model step 的真实 usage 和缓存字段；
3. 完成上述验收后，再继续其他 Runtime v2 能力和命中率调优；
4. 只有未来内置自托管推理服务时，才增加推理引擎 KV cache 配置。

不应为了显示漂亮的命中率而估算 `cached_tokens`，供应商没有返回时必须显示“不可用”，而不是 `0%`。

## 当前实现边界

2026-09-08 的 Runtime v2 P0 已实现：

- `backend/agent_runtime/context.py` 负责确定性的 policy、tool、路线、知识与当前请求装配；
- `ModelTurn.usage` 接收规范化后的 token/cache usage，`model_step_usage` 按模型调用持久化；
- OpenAI Chat/Responses 风格的嵌套 `cached_tokens` 与 DeepSeek 顶层 hit/miss 字段均可解析；
- Run metrics 与 Web 面板显示缓存 token、复用率、统计覆盖率和不可用状态；
- 普通 Local Chat 回复同样保存单次模型调用的用时与 allowlist usage，并在回答下方提供可展开详情；
- Tool Registry 规格按 `name + version` 排序，JSON Schema 递归规范化；
- context snapshot 保存审计所需的完整 envelope，但模型输入只使用白名单投影。
- 超过路线预算时，Chat 与 Agent 对较早路线消息生成确定性 compaction projection；计划保存目标路线、revision vector、来源 hash 和压缩统计，原始 transcript 不变。

普通 Local Chat 的 usage 不混入 Agent Runtime 的逐 step journal，而是按 assistant message 存入独立元数据表。
一条普通回答对应一次模型调用：返回有效缓存字段时覆盖率为 `100%`；未返回、不支持或字段矛盾时，
缓存 token、未缓存 token、复用率和覆盖率均显示“不可用”。旧消息没有可追溯用时，因此不会回填伪造详情。
即使 Runtime 已能显示统计，也不能把“相同对话路线”直接等同于“命中缓存”：供应商是否缓存和返回 usage
仍由其服务决定。

## 数据合同

### 单次模型调用 usage

每个 model step 应保存独立 usage，而不是只在 run 结束时保存汇总：

```json
{
  "inputTokens": 12400,
  "outputTokens": 630,
  "cachedInputTokens": 9400,
  "uncachedInputTokens": 3000,
  "cacheStatus": "reported"
}
```

字段语义：

- `inputTokens`：优先读取 `usage.prompt_tokens`，Responses 风格适配器可映射 `usage.input_tokens`。
- `outputTokens`：优先读取 `usage.completion_tokens`，Responses 风格适配器可映射
  `usage.output_tokens`。
- `cachedInputTokens`：优先读取 `usage.prompt_tokens_details.cached_tokens`；Responses 风格可读取
  `usage.input_tokens_details.cached_tokens`。
- `uncachedInputTokens`：DeepSeek 直接采用 miss 字段；OpenAI 只有总输入和 cached 数量时以两者差值形成
  规范化派生值。界面不把该派生值描述成供应商原始字段。
- `cacheStatus`：只能为 `reported`、`not_reported`、`unsupported` 或 `invalid`。

DeepSeek 官方 Chat Completions schema 使用的是 usage 顶层字段，而不是 OpenAI 的嵌套字段：

- `usage.prompt_cache_hit_tokens` → `cachedInputTokens`
- `usage.prompt_cache_miss_tokens` → `uncachedInputTokens`
- `usage.prompt_tokens` → `inputTokens`

DeepSeek 官方定义 `prompt_tokens = prompt_cache_hit_tokens + prompt_cache_miss_tokens`。normalizer 在两个
DeepSeek cache 字段都存在时应验证这个等式；不成立则标记为 `invalid`。若 DeepSeek 顶层字段和 OpenAI
嵌套字段同时出现且互相矛盾，adapter 标记为 `invalid`，不能悄悄选择更高的数字。参考 [DeepSeek 对话补全 API](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion)
和 [DeepSeek 上下文硬盘缓存说明](https://api-docs.deepseek.com/zh-cn/guides/kv_cache/)。

不要原样持久化未知 provider usage 对象。adapter 应提取白名单数值字段，并拒绝负数、布尔值、非有限值
和明显矛盾的数据（例如 `cachedInputTokens > inputTokens`）。原始响应可能包含供应商扩展或将来出现的
敏感元数据。

### 流式响应

OpenAI-compatible streaming 可在请求支持时增加：

```json
"stream_options": {"include_usage": true}
```

usage 通常出现在最后一个 chunk，但第三方网关可能忽略该参数、拒绝该参数，或只返回部分字段。建议通过
adapter capability 决定是否发送该选项；首轮兼容策略可以先尝试，若服务明确返回参数不支持，再以不带
该字段的请求重试一次，并将 capability 缓存在当前 provider 配置会话中。不得因此重复提交已经产生副作用
的 Agent tool step。

### 汇总指标

Run 层从 model steps 聚合：

```text
inputTokens        = sum(reported inputTokens)
outputTokens       = sum(reported outputTokens)
cachedInputTokens  = sum(reported cachedInputTokens)
uncachedInputTokens = sum(reported uncachedInputTokens when available)
cacheReuseRatio    = sum(cachedInputTokens) / sum(inputTokens)
cacheCoverage      = calls(with a complete cached/input pair) / all model calls
```

其中 `cacheReuseRatio` 是“具有同调用完整分子/分母的已报告输入 token 缓存复用比例”，不是供应商内部
缓存系统的真实命中率。字段不完整的调用仍保留其已报告 token，但绝不和另一调用交叉配对。只有
`cacheCoverage = 100%` 时才适合直接比较不同运行；否则 UI 必须同时显示覆盖率。

当没有任何 `reported` 调用时：

- `cachedInputTokens = null`
- `cacheReuseRatio = null`
- UI 显示“供应商未报告缓存数据”

## 与路线动态记忆的关系

WeavePath 的缓存策略必须服从当前产品语义，而不是反过来改变记忆：

- A-B-C 与 A-B-E 共享的 A-B token 前缀具备潜在复用价值，但 C 和 E 的本地内容仍然隔离；
- B 在创建 C 后追加消息时，C 的有效上下文按产品约定读取 B 的最新消息。缓存最多复用新增位置之前的
  旧前缀，不能为了保留更高命中率继续发送旧 checkpoint；
- 编辑 A 或 B 的早期消息会使修改点之后的 token 前缀失效，这是正确性所需的正常失效；
- 只在 C 末尾追加消息通常最有利于前缀复用；
- checkpoint 仍用于审计，不应被误用为 provider cache key，也不应恢复“冻结父路线”的旧语义。
- 自动压缩按具体目标路线派生。A-B 公共原始前缀可以相同，但包含 C 的压缩摘要不能发送给 E；B 更新后两条路线分别重建自己的计划。

因此分支越多不代表应用需要复制 KV cache。远程 provider 会自行判断公共前缀是否可复用；WeavePath 只需
保证输入确定、路线正确，并记录 provider 实际报告的数据。

## 缓存友好的 prompt 装配

模型输入按以下合同装配：

1. 版本化 system policy；
2. 稳定、按 `name + version` 排序的工具定义；
3. 当前具体 memory route 的历史消息，严格按路线和消息序号排序；
4. 按稳定键排序的 accepted knowledge；
5. 本次 objective、constraints、deliverables、acceptance checks；自动路线检索证据属于这个 current request，不进入 system/tool 稳定前缀；
6. 当前工具调用和结果。

未显式选择文件时，检索器只查询当前 instance 的实时祖先链，并把候选数、入选分块、字符预算、路线、
chunk hash 和截断状态冻结为可检查计划。Chat 把计划正文附在对应用户消息内；Agent Run 把它附在最终
current request 内。更换证据会改变完整 request hash，但不会改变只由 system policy 与规范化 tools
组成的 `stablePrefixSha256`。应用仍不创建或保存推理引擎 KV cache。

要求：

- 不把 run ID、workflow ID、实例 UUID、时间戳、idempotency key、UI 语言或随机 nonce 放进模型前缀；
- 不因 UI 切换、节点坐标、折叠状态或重命名而改写历史消息内容；
- 工具 schema 使用确定性顺序和规范 JSON，工具版本变化应有意使相关前缀失效；
- system policy 使用显式 `promptLayoutVersion`，版本改变会出现在 Run 审计信息中；
- execution brief 使用固定字段顺序和稳定序列化；
- 保持兄弟路线隔离，不能为了提高缓存命中把不同路线拼接成共享 prompt；
- 不在应用内保存完整 prompt 副本作为缓存。已有 context snapshot 和 hash 是审计边界，模型供应商的
  缓存生命周期由其自身政策决定。

注意：Chat Completions 的工具定义不属于 `messages`，但仍参与模型请求的 token 序列。不同供应商如何
选择可缓存前缀并不完全一致，所以“稳定排序”是优化条件，不是命中保证。

## 集成状态

本轮已经完成：

1. `ModelTurn` 可接收规范化 usage；mock adapter 可注入同一合同。
2. 一对一 `model_step_usage` 表保存每次模型调用，不把多步 usage 塞进 run 单行。
3. `record_model` 写入 allowlist usage；event 不保存完整 prompt 或 provider 原始对象。
4. Run metrics 和前端显示缓存 token、复用率、数据覆盖率与不可用状态。
5. 稳定 prompt builder 通过 snapshot/hash、动态父路线和兄弟隔离测试。

同一个 usage normalizer 已接入 Local Chat JSON/SSE；普通回复与 Agent run 都能在各自详情中显示真实缓存字段或“不可用”。后续优化应以真实 provider 的长路线样本为依据，同时比较压缩比例、输入 token、缓存 coverage、延迟和回答质量。

## 验收标准

### 合同测试

- 能解析 `prompt_tokens_details.cached_tokens`。
- 能解析 `input_tokens_details.cached_tokens`。
- 能解析 DeepSeek 官方 `prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens`，并校验两者之和等于
  `prompt_tokens`。
- usage 缺失时返回 `not_reported` 和 `null`，而不是零。
- 第三方只返回总 token 或使用未知扩展字段时安全降级。
- 负数、字符串、布尔值、NaN、cached 大于 input 时标记 `invalid`，不污染汇总。
- Local Chat SSE 最终 usage chunk 使用同一 normalizer，并在普通回复详情中独立持久化和展示。

### 前缀稳定性测试

- 同一 context snapshot 和同一 prompt template 产生完全一致的规范请求前缀 hash。
- 修改 run ID、节点坐标、界面语言不会改变前缀 hash。
- 修改 system policy 或工具版本会改变稳定基座 hash；修改路线历史会改变完整 model request hash。
- A-B-C 与 A-B-E 仍严格隔离，即使降低了潜在缓存复用。

### 可观测性验收

- 每个模型步骤的 usage 独立持久化；Run 面板显示可靠的聚合结果与 coverage。
- Run 汇总同时显示缓存复用比例和 coverage。
- provider 未报告缓存字段时，UI 明确显示“不可用”。
- 指标不包含失败前没有 usage 的调用；部分上报时 coverage 会下降。

## P0 决策

**cache-aware 上下文装配、动态父路线正确性和真实 usage 埋点是本轮 P0，完成以后才继续其他 Runtime v2 功能。**

usage normalizer、model-step 存储、稳定 prompt builder、路线隔离测试和最小缓存仪表已纳入 Runtime v2。
针对具体供应商的命中率调参仍应基于真实样本再做；现在投入自建 KV cache，则会在尚未拥有推理引擎的
层级过度设计。

只有满足以下任一条件，KV/prompt cache 才应升级为高优先级性能专项：

- 重复长上下文占模型成本的显著比例；
- Agent 多步调用反复发送同一长前缀；
- 延迟或输入 token 成本成为实测主要瓶颈；
- 计划提供自托管 vLLM/SGLang 部署。

当前安全工具、审批、持久化和可恢复状态仍被保留；P0 改变的是开发先后顺序，不是删除这些安全边界。

## 风险

- 第三方 OpenAI-compatible 服务可能声称兼容但完全不返回 cached token。
- 不同供应商最小可缓存长度、缓存生命周期和计费规则不同，不能硬编码为产品事实。
- prompt 内容完全相同也不保证命中；供应商可能按账户、区域、模型版本或负载隔离缓存。
- 为追求命中率而保留过多历史会增加隐私、成本和上下文污染风险。
- 自动重试 usage 请求可能导致重复模型调用或重复工具副作用，必须与 idempotency/attempt 模型结合。
- 缓存指标可能被误解为回答质量指标；它只能用于成本和延迟分析。
