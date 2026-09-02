# WeavePath

> A visual, route-aware workspace for building, evaluating, and orchestrating AI agents.

WeavePath（织径）是一个本地优先、跨 AI 宿主的 Agent 工程工作台。它把对话路线、上下文记忆、工具、实验、产物和外部 Agent 任务关联起来，让开发者可以从任意稳定 checkpoint 分支、评测、切换或交接一条执行路线。

> [!WARNING]
> 当前版本为 `0.x` 早期开发预览，面向单用户本机环境，不具备公网身份认证或生产级多租户隔离。请勿直接暴露到互联网。

当前的 Conversation Workflow 是第一个业务模块，不是整个产品本身。Codex skill、MCP App 和未来的 Claude Code 插件都是宿主适配器或交互外壳，不是领域状态的长期真源。

## 当前阶段

项目已完成 **Phase 0：边界与架构冻结**，并开始 **Phase 1：Local Graph Chat**。Conversation Workflow 是第一个可运行纵向切片；长期方向是完整的 Agent Runtime、Memory、Evaluation、Observability、Artifacts 和 Multi-Agent Orchestration。详见 [Agent 工程路线图](docs/agent-engineering-roadmap.md)。

- `conversation-workflow-bridge-v4` 冻结为 **legacy Codex adapter**。只接受缺陷修复和迁移所需变更，不再向其单体 `server.mjs`、`widget.html` 堆叠跨模块业务逻辑。
- `conversation-workflow-skill-v4` 保留为 Codex 兼容规范、manifest v1 导入导出依据和行为参考。
- `conversation-workflow-demo/public/*.html` 只是视觉与交互规格，不是长期前端实现；其布局将迁移到 React。
- 新代码的长期中心是 graph-core、本地 Core Service 和全局 SQLite。

当前仓库已有 SQLite `GraphStore`、schema v7 启动迁移、FastAPI `/api/v1` 路由和原生 React `WorkspaceShell`。默认界面可在同一页面切换“对话 / 工作流 / 实验室”：第一层画布只显示工作流级 `ConversationInstance`，双击节点进入该对话内部的 Turn Tree。两层画布采用统一的 Synapse 式卡片、连线、画布控制和右侧检查面板，并支持浅色/深色主题；这表示交互和视觉结构借鉴，不宣称与 dsh-synapse 完全一致。卡片右侧的 `＋` 可以直接创建子分支，不要求先填写名称或内容；第二层的空内部路线会立即以占位卡显示，仍不会泄漏为第一层工作流框。用户也可从任意轮次携带首条问题精确创建隔离路线并生成回答。选择内部路线并“继续对话”后，普通 Chat 读取和写入同一路线。双击仍不 activate。实验室提供分支对比、受控知识合并、版本化 Artifact、版本化数据集和实验快照。当前后端自动化套件为 108 项通过，并完成 compileall；前端仍由统一测试、typecheck 和 production build 验证。`/graph` 只保留为兼容入口。

第一版 OpenAI-compatible 同步 AI 链路和网页模型设置已经可用，并严格只向模型发送当前具体路线的有效上下文；聊天区默认只显示当前节点本地记录，继承路线记忆可按需展开。当前节点最后一次本地提问支持编辑、复制、取消和“保存并重新生成”：模型失败时零写入，并发修改时以 revision 冲突停止。分支创建时的 checkpoint 快照继续保留用于审计，但有效上下文会沿父路线动态读取，因此父节点后续新增或修改的消息会进入已有子节点；兄弟路线仍然隔离。SSE、migration rollback/发布策略、正式 host adapter 层和 failure/approval 完整事件投影仍未完成，因此 Phase 1 尚未完成。逐项状态见 [开发状态](docs/development-status.md)。

### Route-to-Agent Run v1（本机预览已验证）

仓库已完成第一个从具体对话路线启动 Agent Run 的窄纵向切片本机验证。该能力定位为 **Verified local synchronous durable preview**：用户确认 execution brief 后，系统从一个具体实例冻结路线上下文，记录持久化 event journal，并通过已配置的 OpenAI-compatible provider 调用唯一注册工具 `safe_calculator` / `1.0.0`；界面提供 run dialog 和 timeline。`ScriptedMockAgentAdapter` 只用于确定性测试，不是用户可选择的生产 provider。

该 preview 当前是本机单进程/单 Uvicorn worker 设计；不要使用 `--workers` 启动多个 API 进程。官方 app factory 已用数据库旁的 OS 单实例锁串行化 migration 和 startup recovery；跨进程 run owner/lease 仍属于后续运行时硬化范围。所有启动实例必须使用同一规范化 `WEAVEPATH_DB` 路径，不能用 hard link、映射盘与 UNC 等不同别名指向同一 SQLite 文件。

2026-09-02 的当前统一本机验证基线包括 108 项后端测试、Python compileall，以及前端统一测试、TypeScript/production build 和双层画布/Route-to-Agent Run/Engineering Lab 浏览器验收。其中 Route-to-Agent Run 路径使用受控 OpenAI-compatible 上游完成 `safe_calculator` 工具调用并返回最终消息，兄弟路线 canary 未进入模型请求。schema v5 加入 Engineering Lab preview，schema v6 将精确轮次分支收纳为顶层对话内部的 Turn Tree 路线，schema v7 为自动分支标题增加持久化来源标记；这些都不代表完整 Evaluation 或 Artifact 阶段已经完成。证据与逐项矩阵见 [Route-to-Agent Run v1](docs/route-to-agent-run-v1.md)。SSE、取消、任意 shell/文件/网络工具、自动 evaluator/scorer、多 Agent，以及正式 Codex/Claude adapter 仍未包含。

### Engineering Lab（本机预览）

- 分支对比只返回路线元数据、本地消息计数、运行结果与 Artifact 元数据，不返回或拼接 transcript。
- “知识合并”只保存用户勾选的结论/事实/决策/约束和 Artifact 引用；接纳知识只沿目标路线向后可见，并以独立 provenance 进入 Agent Run context。
- Artifact 具有逻辑名称、递增版本、MIME/type、SHA-256、所属路线和可选来源 Run；Agent 结果可显式保存为 Artifact。
- 数据集按名称版本化，实验冻结数据集版本/哈希、具体路线和所选 Run，当前不包含自动 scorer、参数矩阵或回归门禁。

## 产品边界

本产品负责：

- route-aware conversation graph；
- 稳定 checkpoint 与路线记忆隔离；
- 宿主任务绑定、切换、检查、分支和级联归档；
- 对话节点与 artifacts、执行简报、Agent 运行的关联；
- 独立 Web/Desktop 工作台以及宿主内的轻量入口。

本产品暂不负责：

- 替代 Codex、Claude Code 或 IDE；
- 自动合并兄弟路线 transcript；
- 修改宿主私有安装包或向原生聊天栏强行注入控件；
- 默认复制全部宿主 transcript；
- 初期的云同步、多人协作或账户级永久删除；
- 未经确认由 AI 自动改变图结构。

## 模块

1. **Conversation Workflow**：图拓扑、topic 多实例、路线选择、检查和级联归档。
2. **Route Memory**：checkpoint、foundation、路线摘要、上下文预算与显式跨路线转移。
3. **Local Chat**：已实现本地消息记录和可配置 OpenAI-compatible 同步回复；后续加入 SSE、取消生成、后台 metabolize、judge 和 brief，使产品不依赖外部宿主也能完成完整 AI 对话。
4. **Artifacts & Experiments**：把数据集、代码、实验输出、指标和报告绑定到具体路线实例。
5. **Handoff & Execution**：生成执行简报并跟踪外部 Agent 任务。
6. **Search & Knowledge**：搜索结构元数据和用户明确允许索引的内容。
7. **Templates & Automation**：可复用流程模板、定时任务和监控。
8. **Collaboration / Sync**：本地单用户模型稳定后再评估。

模块之间通过 graph-core 的稳定实体 ID 和事件协作；Workflow 不直接承担 LLM、文件管理或宿主 API 细节。

## 总体架构

```text
WorkspaceShell (Chat · Workflow Graph · Turn Canvas)
Compatible /graph · Desktop Shell · Codex Widget · Claude Command
                               │
                     HTTP · Browser events · MCP
                               │
                    Local Core Service (FastAPI)
       ┌───────────────────────┼───────────────────────┐
       │                       │                       │
   graph-core              Route Memory          Application Services
   纯领域规则              checkpoint/context     command/query/saga/events
       │                       │                       │
       └───────────────────────┼───────────────────────┘
                               │ ports
               ┌───────────────┴────────────────┐
               │                                │
       Global SQLite                     Host Adapters
                                  Standalone · Codex · Claude
```

graph-core 不调用 Codex/Claude、LLM、Widget 或文件系统宿主 API。外部能力只通过 ports/adapters 注入。

## 核心不变量

- 一个 `ConversationInstance` 只有一个 `parent_instance_id`，因此只有一条不可变祖先路线。
- 多个实例可以共享同一个 `topic_id`，但绝不能共享可变 transcript。
- `root_instance_id` 是图属性；`current`、`selected`、`last opened` 是不同来源的状态。
- 分支绑定父节点的稳定 `Checkpoint` 锚点；checkpoint 快照用于审计和复现创建时状态，但有效上下文沿父路线动态读取，父节点后来新增或修改的消息会进入已创建子路线。
- 默认禁止读取兄弟路线；跨路线内容必须由用户显式授权并记录 provenance。
- 图结构变更使用 `graph_revision`；消息和摘要更新使用实例级 `content_revision`。
- Turn Canvas 只投影具体实例的本地 turns；画布命令仍写入该具体实例的同一消息表，祖先路线消息作为动态继承记忆，checkpoint 快照和锚点仅用于审计摘要。
- 未填写标题的分支先获得稳定的 `新分支 N` 名称；如果它仍是系统生成标题，第一条本地用户消息会生成最多 48 字的摘要标题并增加 `graph_revision`。显式重命名会把标题标记为用户所有，之后绝不被自动命名覆盖。
- selection 与双击钻入只改变 UI metadata；只有显式“继续对话”才 activate。
- “从工作流移除”表示 leaf-first 归档并保留 tombstone，不是永久删除账户数据。

## 首个纵向切片

第一个可运行版本是 **Local Graph Chat**：

- FastAPI + React + schema v7 全局 SQLite；
- StandaloneAdapter 拥有本地 transcript；
- 原生 `WorkspaceShell` 在同一页面切换 Chat 和 Workflow surface，并保持草稿、滚动与画布状态；
- 顶层 Workflow Graph 管理具体实例路线，双击按需进入节点内部 local-only Turn Canvas；两层卡片均提供快捷 `＋` 分支入口，第二层还可继续对话或从具体 turn 创建并回答新分支；
- 只有显式“继续对话”才 activate；可以从具体 turn 精确分支；
- 创建、fork、activate、inspect、topic route choice、prune plan/commit；
- `/graph` 与 popup 仅为兼容入口，继续用 `BroadcastChannel + postMessage` 同步真实 mutation；WebSocket 尚未实现；
- 使用 checkpoint 锚点和动态父路线验证 `A-B-C-D1` 与 `A-E-D2` 的记忆隔离。

完整验收见 [Local Graph Chat](docs/local-graph-chat.md)。

## 开发启动说明

环境要求：

- Python 3.12+
- Node.js 22+
- npm 10+
- Windows 推荐 PowerShell 7；macOS/Linux 可分别运行后端与前端命令

先安装一次依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".\backend[test]"
Push-Location .\apps\web
npm ci
Pop-Location
```

推荐从项目根目录用统一脚本启动。API 固定监听 8000，Web 默认监听 5173；`Ctrl+C` 会停止两个子进程：

```powershell
.\scripts\dev.ps1

# 可选：指定 Python 或 Web 端口
.\scripts\dev.ps1 -Python C:\path\to\python.exe -WebPort 5174
```

推荐统一验证：

```powershell
.\scripts\check.ps1

# 可选：指定 Python
.\scripts\check.ps1 -Python C:\path\to\python.exe
```

`check.ps1` 依次运行后端 pytest、Python compileall、前端测试和 production build。

需要单独调试时，仍可手动启动后端：

```powershell
cd backend
uvicorn api.app:create_app --factory --reload --host 127.0.0.1 --port 8000

# 后端测试
pytest
```

新安装的默认数据库位于 `%LOCALAPPDATA%\WeavePath\data\workspace.db`；可用 `WEAVEPATH_DATA_DIR` 指定数据目录，或用 `WEAVEPATH_DB` 直接指定数据库文件。若新路径尚无数据库，程序会原地复用旧版 `CoThinker Workspace` 数据库，不复制、不重命名、不删除；旧 `COTHINKER_*` 环境变量也继续兼容。受限 sandbox 无法写用户数据目录时会降级到系统临时目录，不会在源码目录创建新数据库。当前 health/SQLite schema version 为 7；graph snapshot 与 legacy manifest 协议仍为 schema v1。`GraphStore` 启动时运行记录在 `schema_migrations` 中的前向迁移。自动 downgrade、rollback 和发布级备份恢复流程仍未实现。

在另一个 PowerShell 手动启动前端：

```powershell
cd apps\web
npm ci
npm run dev

# 另行验证
npm test
npm run build
```

浏览器打开 Vite 输出的地址；开发服务器默认将 `/api` 代理到 `http://localhost:8000`。若 API 在其他 origin，启动 Vite 前设置 `WEAVEPATH_API_TARGET`；值只写 origin，不要附加 `/api`：

```powershell
$env:WEAVEPATH_API_TARGET = "http://127.0.0.1:8010"
npm run dev
```

`WEAVEPATH_API_TARGET` 只影响 Vite 开发代理，不改变浏览器可见 API 路径，也不修改数据库或模型 provider。统一 `scripts/dev.ps1` 默认启动 API:8000，因此通常无需设置。未配置 AI 时消息只持久化到当前路线；配置兼容服务后，同一输入框会调用 chat API 并写回 assistant 回复。

### 在界面中连接 AI（推荐）

未配置模型时，页面会明确显示“仅记录模式 · 尚未连接 AI”，消息仍会保存到当前路线，但不会伪造 assistant 回复。

1. 点击聊天页左下角的 **设置**。
2. 选择 OpenAI、DeepSeek、LM Studio、Ollama 或“自定义 / OpenAI 兼容”。
3. 填写 API 密钥；LM Studio、Ollama 等本地服务通常可以留空。
4. 点击“测试并获取模型”。本地预设允许先不填模型名，获取列表后再选择；不支持 `/models` 的兼容服务也可以手动填写模型名并直接保存。
5. 可勾选“在本机保存非敏感设置”，然后点击“保存”。

基础地址和模型名可以持久化到应用数据目录的 `model-settings.json`；API 密钥只保留在当前后端进程内存，接口不会回显，也不会写入数据库、工作流或设置文件。后端重启后，需要重新输入密钥，或使用下面的环境变量兜底。

出于安全限制，远程服务必须使用 HTTPS；HTTP 只允许 `localhost`、`127.0.0.1` 或 `::1`。

### 使用环境变量连接 AI（部署兜底）

如果希望后端重启后自动读取密钥，可以在启动 `dev.ps1` 的同一个 PowerShell 中设置：

```powershell
$env:WEAVEPATH_LLM_BASE_URL = "http://127.0.0.1:1234/v1"
$env:WEAVEPATH_LLM_MODEL = "模型名称"
$env:WEAVEPATH_LLM_API_KEY = "可选；本地服务通常不需要"
$env:WEAVEPATH_LLM_TIMEOUT = "60"
.\scripts\dev.ps1
```

若只设置 `OPENAI_API_KEY`，后端会使用 `https://api.openai.com/v1`，但仍必须显式设置 `WEAVEPATH_LLM_MODEL`。旧 `COTHINKER_LLM_*` 变量继续兼容。当前请求是同步返回；编辑最近提问并重新生成已经实现，流式输出、停止生成和“不修改提问、仅重试最后回答”仍未实现。

仓库中的 `backend/workflow.db` 是早期测试遗留物，不是当前默认数据库。`*.db` 已被 `.gitignore` 忽略；应由维护者确认无保留价值后手工删除，文档更新不代替数据删除确认。

## 文档

- [总体架构与领域模型](docs/architecture.md)
- [数据所有权](docs/data-ownership.md)
- [manifest v1 迁移](docs/manifest-migration.md)
- [阶段路线与首个切片](docs/local-graph-chat.md)
- [开发状态](docs/development-status.md)
- [Agent 工程路线图](docs/agent-engineering-roadmap.md)
- [Codex 对话交互借鉴路线](docs/codex-interaction-roadmap.md)
- [Route-to-Agent Run v1 契约与本机验收记录](docs/route-to-agent-run-v1.md)
- [ADR-0001：全局 SQLite 为长期真源](docs/adr/0001-global-sqlite-source-of-truth.md)
- [ADR-0002：同 topic 使用多个路线实例](docs/adr/0002-topic-route-instances.md)
- [ADR-0003：HostAdapter 与 operation saga](docs/adr/0003-host-adapter-operation-saga.md)
- [ADR-0004：原生 WorkspaceShell 与双层对话画布](docs/adr/0004-native-workspace-double-canvas.md)

## Contributing、Security 与 License

- 贡献流程与领域不变量见 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 请按 [SECURITY.md](SECURITY.md) 私下报告安全问题，不要在公开 Issue 中粘贴对话、数据库或密钥。
- 本项目采用 [Apache License 2.0](LICENSE)。

## 目录：当前骨架与目标布局

当前实际布局是：

```text
apps/web/                     React WorkspaceShell、Workflow/Turn 双层画布与 Agent Run 面板
backend/graph_core/           SQLite GraphStore、schema v7 migrations、turn cursor checkpoint 与双层路线
backend/agent_runtime/        Agent Run、event journal、OpenAI-compatible adapter 与安全工具注册表
backend/api/                  FastAPI `api.app:create_app` factory 与 schemaVersion 7 API
backend/tests/                graph-core、API、AI 设置/路线、迁移与 Agent Runtime 测试
backend/pyproject.toml        Python 项目与测试依赖
docs/                         Phase 0 文档与 Phase 1 验收说明
scripts/dev.ps1               同时启动 API:8000 与 Web:5173
scripts/check.ps1             后端/前端统一验证入口
```

当前沿用扁平 Python 包布局，与 `pyproject.toml` 的 `include = ["graph_core*", "api*", "agent_runtime*"]` 保持一致：

```text
backend/graph_core/              纯领域实体、命令、查询、不变量
backend/api/                     FastAPI、application services、ports/adapters
backend/agent_runtime/           durable run repository/service、model port 与 tool registry
backend/tests/                   领域、API 和 adapter contract 测试
apps/web/src/                    React WorkspaceShell + Workflow/Turn Canvas + model settings + Agent Run UI
```

以下是模块增长后的**目标布局**，不是当前已存在的目录：

```text
apps/web/                     React WorkspaceShell + workflow/turn canvas
apps/desktop/                 后续 Tauri/Electron 壳
backend/graph_core/           graph-core 与未来 memory 领域包
backend/api/application/      command/query/saga/event 服务
backend/api/ports/            repository、host、transcript、credential 接口
backend/api/adapters/         SQLite、Standalone、Codex、Claude、LLM
backend/api/routers/          FastAPI routers 与 WebSocket
packages/graph-ui/            从 v4 widget 提炼的 React 图组件
plugins/codex/                legacy bridge 的演化位置
plugins/claude/               Claude Code 接入
tools/legacy-manifest/        manifest v1 导入导出
tests/domain/                 领域不变量
tests/adapter-contract/       适配器契约
tests/e2e/                    纵向验收
docs/adr/                     架构决策记录
```

如果未来决定采用 `src/cothinker/...` 包布局，必须先提交独立 ADR，并同步迁移 `pyproject.toml`、imports 和测试；不得仅修改文档制造第三种布局。
