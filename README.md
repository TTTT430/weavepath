# WeavePath

> 面向 Agent 开发的可视化、路线感知工作台：把对话、分支记忆、模型运行、工具、文件、实验和宿主任务放在同一张可追溯的图里。

WeavePath（织径）是一个本地优先的 Agent 工程项目。它不是 Codex 或 Claude Code 的替代品，而是连接多个 AI 宿主、模型服务和实验资产的工作台。项目当前是 `0.x` 单用户本机预览版，不提供公网身份认证或生产级多租户隔离，请不要直接暴露到互联网。

## 为什么需要 WeavePath

普通聊天把所有尝试堆在一条时间线上，分支之间容易互相污染，也很难回答“这个答案来自哪条路线”。WeavePath 把每个对话作为图节点，把父子关系作为记忆路线：

```text
A ── B ── C ── D
      └── E
```

切到 E 时，模型读取 `A-B-E`；切到 D 时，模型读取 `A-B-C-D`。C 创建以后，B 新增的消息会在下一次请求中动态进入 C；C 和 E 的私有消息不会互相进入上下文。checkpoint 保存创建分支时的审计快照，但不是运行时冻结的上下文。

## 当前完成度

当前本机切片已完成：

- 路线感知对话图与双层画布；
- OpenAI-compatible JSON/SSE Chat、模型切换、推理强度、连接诊断和自动重连；
- 50 MiB 文件上传、分片续传、路线隔离检索和自动上下文压缩；
- Runtime v2 的安全工具、审批、取消、重试、lease/heartbeat、effect journal 与 KV-cache-aware 上下文装配；
- 运行时间线、Token、费用、工具记录和 OpenAI/DeepSeek cache usage 展示；
- 分支摘要对比、路线差异研判、受控知识合并和版本化 Artifact；
- 真实 Codex companion、Claude Code companion、跨宿主 Saga、verified backup 与停机恢复；
- 214 项后端测试、155 项前端测试、TypeScript、production build、真实宿主和 SSE 断线恢复验收。

当前仍是本机单用户切片：多宿主并发 registry、多用户授权、公网部署、自动 evaluator/scorer、多 Agent 协作和永久删除尚未实现。

## 核心概念

| 概念 | 说明 |
|---|---|
| Workflow | 一组相关对话、运行和实验资产的容器。 |
| ConversationInstance | 图中的一个具体对话节点，有且只有一个父节点。 |
| Topic | 逻辑主题。同一主题可以有多条互相隔离的路线实例。 |
| Memory route | 从根节点到当前节点的完整父链，例如 `A-B-C`。 |
| Checkpoint | 创建分支时保存的 cursor、revision 和快照，用于审计；不阻止父节点后续消息进入子路线。 |
| Turn Tree | 某个顶层对话内部的轮次/内部分支画布，不会把内部节点混到第一层工作流图。 |
| Artifact | 绑定到路线或运行的版本化产物，带 MIME、SHA-256 和来源。 |

## 功能详解

### 1. Chat 对话

- 左侧显示工作流和对话列表，中间显示当前路线消息，切换工作流节点后 Chat 自动跟随具体 `activeRouteInstanceId`。
- 普通消息支持 Markdown/GFM、安全代码块、复制和最近一次提问编辑。
- 编辑最近一次用户问题后可“保存并重新生成”；模型失败时不会写入半截 assistant 消息。
- 失败回答可以单独重试，不重复写入 user 消息；请求带幂等键。
- 生成过程中显示连接中、等待模型、接收回答、自动重连和已处理时长，可随时停止。
- 首次连接或流传输中断时自动重试最多 3 次；没有固定的模型生成超时。
- 回复详情可展开查看耗时、输入/输出 Token、缓存 Token、未缓存 Token、复用率、覆盖率、检索来源和压缩计划。供应商不返回 cache usage 时显示“不可用”。

### 2. 模型设置与输入框控制

点击左下角“设置”可配置：

- OpenAI、DeepSeek、LM Studio、Ollama 或自定义 OpenAI-compatible 服务；
- Base URL、模型 ID、API Key、系统提示词；
- 网络方式：自动、系统代理、直连。自动模式先直连，连接无法建立时才尝试系统代理；
- 界面语言：中文/英文；界面主题：浅色/深色；
- 是否保存非敏感设置，以及是否用当前 Windows 账户 DPAPI 安全保存 API Key。

保存后，输入框发送按钮旁可以直接切换已发现的模型，并选择低、中、高、极高推理强度。完整连接测试会展示实际尝试的网络路线、错误类别和耗时。远程服务必须使用 HTTPS，HTTP 仅允许 loopback 地址。

### 3. 第一层 Workflow 画布

工作流页的第一层只显示工作流级对话节点。每张卡片包含：

- 对话名称；
- 最近本地问答的 extractive 摘要（不额外调用模型）；
- 父子连线和当前路线状态；
- 详情、轮次画布和快捷 `＋` 分支入口。

单击节点只选择，双击进入该对话的第二层 Turn Tree；选择或双击成功后，回到 Chat 会显示同一个具体对话。标题为空时系统会生成“新分支 N”，收到首条消息后可自动生成摘要，用户显式重命名后不再被覆盖。

### 4. 第二层 Turn Tree 画布

双击第一层节点后进入该对话内部的轮次画布：

- 一个框代表一轮用户提问及其 assistant/tool/failure 事件；
- 内部分支仍有自己的路线和记忆，不会泄漏到第一层；
- 可在卡片上直接点 `＋` 创建分支，暂时不填名称和首条内容也可以；
- 可从具体 user turn 创建分支，带上 `anchorMessageId` 和当前 revision；
- 选择内部路线后直接在画布输入框继续发送，消息写回同一 SQLite 真源；
- 右侧 inspector 显示完整 memory path、继承消息数量、轮次详情和路线选择。

画布布局借鉴 Synapse 式节点、连线、缩放、适应视图和右侧检查器，但不复制第三方项目的代码或数据。

### 5. 分支、路线与归档

从任意节点创建 sibling 分支：

1. 点击节点右侧 `＋` 或轮次卡片的分支入口；
2. 名称和首条问题都可以留空；
3. 系统创建独立 ConversationInstance，记录父节点、topic、checkpoint cursor 和 revision；
4. 选择新节点后自动切换 Chat；
5. 若配置了模型并填写首条问题，系统会生成回答。

级联归档不是永久删除：先生成带 revision 的 leaf-first 计划，确认后归档后代并保留 manifest tombstone。revision 变化、节点/宿主不匹配或部分远端归档失败时停止并保留可恢复状态。

### 6. 文件、检索与上下文压缩

输入框左侧 `＋` 支持每条消息最多 5 个文件，单文件最大 50 MiB：UTF-8 文本/代码/结构化数据、PDF、DOCX、XLSX、PPTX。超过 4 MiB 自动采用 4 MiB 分片上传、重试和跨页面/服务重启续传。

- 原始字节按 SHA-256 保存到本机 content-addressed object store；
- SQLite 保存文件元数据、解析状态、消息引用和派生分块；
- PDF/Office 分块带页码、工作表或幻灯片定位；
- FTS5 trigram 索引只搜索当前路线及其父路线，不搜索兄弟路线；
- 显式附件按问题确定性固化上下文；未选文件时自动生成最多 6 个分块、约 24,000 字符的检索计划；
- 路线超过默认 240,000 字符时自动压缩较早消息，最近 8 条消息保持完整；原始消息永不被删除或改写；
- 压缩计划绑定具体路线和 revision vector，A-B-C 与 A-B-E 各自维护计划；
- 图片可以保存，但 OCR 和 embedding/向量召回尚未启用，会明确显示不可用于模型上下文。

### 7. Agent Runtime v2

在 Agent 运行入口填写 execution brief 并确认后，运行时固定按以下顺序装配上下文：

```text
System Policy
→ 稳定排序的 Tools
→ 当前路线 A → B → C 的动态消息
→ accepted knowledge
→ 当前请求
```

运行时不会把 runId、时间戳、UI 状态、随机请求键放入稳定前缀，也不在应用层自建 KV cache。每个 model step 记录供应商返回的 cache usage。

当前安全工具：

- `safe_calculator`：无副作用；
- `propose_patch`：必须审批，只生成可审查 Artifact，不直接修改工作区；
- `read_file`、`workspace_search`：只有显式设置 `WEAVEPATH_WORKSPACE_ROOT` 才开放。

每次运行都保存时间线、运行阶段、工具调用、输入/输出 Token、费用、缓存统计、路线和上下文 hash。支持取消、失败重试、审批恢复、运行 lease/heartbeat、未知模型/工具结果保护和 root-run effect 幂等。

### 8. 工作流研判

“分支对比”入口服务于对话工作流本身：

- 选择 2–4 条路线，对比每条路线的本地对话摘要、共同记忆前缀和分支独有路径；
- 并列查看消息规模、模型、最近的 Agent 结论和已保存成果；
- 显式勾选需要沿用的结论或 Artifact，并写入指定目标路线；
- 在成果库保存带名称、版本、MIME、SHA-256 和来源路线/Run 的版本化产物。

知识合并只携带用户明确选中的结论、事实、决策、约束或 Artifact 引用。合并后的知识只沿目标路线及其后代可见，并保留 provenance；兄弟路线的 transcript 不会被拼接。旧版小型数据集/实验快照 API 暂时保留兼容，但不再作为主界面能力。GB 级数据处理将在后续通过本地路径、对象存储或外部训练运行器引用，而不是上传进浏览器内存。

### 9. Codex / Claude Code 宿主桥

#### Codex

个人插件 `weavepath-codex-companion` 通过 Codex 原生 app-tools pipe 建立随机端口、每进程 token 的 loopback bridge：

- `list_threads` / `read_thread`：枚举和读取任务；
- `fork_thread`：任务头分支；
- `navigate_to_codex_page`：直接激活任务，不向 composer 写文本；
- `set_thread_title` / `set_thread_archived`：重命名和归档。

#### Claude Code

`integrations/claude_code_companion` 读取本地 JSONL 会话，并使用 `claude --resume <session> --fork-session` 创建任务头分支。Claude CLI 没有可靠公开支持的历史 turn 分支、重命名和归档时，接口会明确返回 unsupported，而不是伪造能力。可参考 [Claude Code Sessions](https://code.claude.com/docs/en/sessions)。

WeavePath API 提供：

```text
GET  /api/v1/host/capabilities
GET  /api/v1/host/conversations
POST /api/v1/host/conversations/import
GET  /api/v1/workflows/{workflowId}/instances/{instanceId}/host-transcript
GET  /api/v1/host/operations
```

导入只保存宿主类型和会话 ID；transcript 仍归宿主持有。

### 10. 数据库备份与恢复

启动迁移前会进行只读版本预检、SQLite online backup、SHA-256 和 `PRAGMA integrity_check`，然后才执行迁移。失败时自动恢复升级前快照，并把 manifest 标记为 `restored`。

设置页可以查看：

- 数据库路径、graph/runtime schema 版本和完整性；
- verified backup 列表、状态和可恢复性；
- 保留策略预览与确认清理；
- 停机 restore plan。

恢复必须先停止 API，再执行 CLI 给出的命令，并输入精确确认短语 `RESTORE <database filename>`。不提供反向 SQL migration，也不会在 API 运行中覆盖数据库。

## 安装与启动

### 环境要求

- Windows 推荐 PowerShell 7；macOS/Linux 可分别启动两个进程；
- Python 3.12+；
- Node.js 22+；
- npm 10+。

### 安装依赖

```powershell
git clone https://github.com/TTTT430/weavepath.git
cd weavepath

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".\backend[test]"

Push-Location .\apps\web
npm ci
Pop-Location
```

### 启动 Web + API

```powershell
.\scripts\dev.ps1
```

启动后自动打开浏览器：

```powershell
.\scripts\dev.ps1 -OpenBrowser
```

默认地址：

- Web：<http://127.0.0.1:5173>
- API health：<http://127.0.0.1:8000/api/v1/health>

指定 Web 端口：

```powershell
.\scripts\dev.ps1 -WebPort 5174
```

如需分开启动：

```powershell
# PowerShell 1
cd backend
..\.venv\Scripts\python.exe -m uvicorn api.app:create_app --factory --host 127.0.0.1 --port 8000

# PowerShell 2
cd apps\web
npm run dev
```

不要使用 `uvicorn --workers`：当前数据库锁、聊天恢复和 Runtime lease 针对单 API 进程设计。

## 配置模型

推荐在界面中配置：设置 → 选择服务商 → 填 Base URL、模型和 API Key → “测试并获取模型” → 保存。

也可以在启动前设置环境变量：

```powershell
$env:WEAVEPATH_LLM_BASE_URL = "http://127.0.0.1:1234/v1"
$env:WEAVEPATH_LLM_MODEL = "your-model-id"
$env:WEAVEPATH_LLM_API_KEY = "optional"
$env:WEAVEPATH_LLM_NETWORK_MODE = "auto"       # auto / system / direct
$env:WEAVEPATH_CONTEXT_BUDGET_CHARS = "240000"
.\scripts\dev.ps1
```

只设置 `OPENAI_API_KEY` 时默认使用 `https://api.openai.com/v1`，但仍需设置 `WEAVEPATH_LLM_MODEL`。`WEAVEPATH_LLM_CONNECT_TIMEOUT` 只影响建连和写入，不限制模型生成时长；界面不提供固定响应超时设置。

API Key 默认只存在后端进程内存；启用安全保存后，Windows 使用当前账户 DPAPI 加密。密钥不会写入 SQLite、JSON、日志、工作流或 Git。

## Codex 插件安装

仓库包含个人 marketplace 和插件：

```powershell
codex plugin marketplace add C:\path\to\weavepath
codex plugin add weavepath-codex-companion@weavepath-local
```

安装后新建一个 Codex task，使插件获得原生 app-tools pipe；然后重启 WeavePath API。更新插件时重新安装对应 marketplace 版本，并在新 task 中测试，避免旧缓存继续加载旧代码。

## Claude Code companion 启动

```powershell
.\scripts\start-claude-companion.ps1
```

companion 发布发现文件后，重启 WeavePath API。它只监听 `127.0.0.1`，每次进程生成随机 token。

## 验证与测试

统一自动化检查：

```powershell
.\scripts\check.ps1
```

真实本机连接与恢复检查：

```powershell
.\scripts\check-live.ps1
```

它会验证真实 Codex app-tools pipe、Claude Code 本机会话和 HTTP/SSE socket 断线恢复。SSE 故障注入使用本地可控 OpenAI-compatible provider，不代表任意公网服务当前可用；公网 provider 仍需使用用户自己的 URL、模型和密钥人工验证。

详细验收记录见 [P3/P4 真实连接与恢复验收](docs/p3-p4-live-acceptance.md)，架构和逐项状态见 [总体架构](docs/architecture.md) 与 [开发状态](docs/development-status.md)。

## 数据目录

默认数据库：`%LOCALAPPDATA%\WeavePath\data\workspace.db`。

可通过以下变量覆盖：

```powershell
$env:WEAVEPATH_DATA_DIR = "C:\path\to\data"
# 或
$env:WEAVEPATH_DB = "C:\path\to\workspace.db"
```

数据库旁的 `backups/` 保存版本化 verified backup；文件对象位于数据库数据目录下的 `files/objects`。旧 `COTHINKER_*` 环境变量仍兼容。仓库中的旧 `backend/workflow.db` 只是历史测试遗留物，不是默认数据库。

## 安全与边界

- 默认只绑定本机 loopback；不要把 API 或 companion 直接暴露到公网；
- 不执行任意 shell、默认不写工作区、不自动合并兄弟路线；
- 宿主返回的 workflow、instance、thread、provider identity 必须通过校验；
- 外部操作失败或结果未知时不盲目重放副作用；
- 归档保留 tombstone，不执行账户级永久删除；
- 请不要在 Issue、日志、测试快照或提交中粘贴 API Key、宿主 token、数据库和私密 transcript。

## 目录结构

```text
apps/web/                         React WorkspaceShell、Chat、Workflow/Turn Canvas、Lab
backend/graph_core/               SQLite 图模型、路线、checkpoint、迁移与附件
backend/agent_runtime/            Runtime、运行事件、工具注册表、审批与 effect journal
backend/api/                      FastAPI 路由、模型设置、Chat SSE、宿主 Saga
backend/host_adapters/            Standalone、Codex、Claude contract 与 transport
integrations/claude_code_companion/ Claude Code 本机 companion
plugins/weavepath-codex-companion/  Codex 个人插件
scripts/                          dev、check、live recovery 和 companion 启动脚本
docs/                             架构、ADR、验收和路线图
```

## 后续方向

下一阶段重点是多宿主 registry、完整可视化宿主导入器、真实 provider 长上下文浏览器验收、分支总结与研判、Artifact diff、多 Agent 交接和签名桌面打包。大型数据或模型训练能力通过外部运行器接入。所有新能力都必须保持路线记忆隔离、稳定前缀、幂等副作用和可恢复数据边界。

## 许可证与贡献

本项目采用 [Apache License 2.0](LICENSE)。贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)；安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。
