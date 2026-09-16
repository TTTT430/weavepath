# WeavePath

> 面向 Agent 开发的可视化、路线感知工作台

WeavePath 把对话、分支记忆、模型运行、工具、文件、Artifact 和宿主任务组织成一张可追溯的工作流图。它适合需要反复探索、比较方案、保留上下文来源的 Agent 开发者、研究者和个人用户。

当前版本是 `0.x` 单用户本机预览版。WeavePath 不是 Codex、Claude Code 或模型服务的替代品，而是连接这些宿主与模型的本地工作台。

## 它解决什么问题

线性聊天很难管理并行思路：

```text
A ── B ── C ── D
      └── E
```

WeavePath 将每个对话作为节点，将父子关系作为记忆路线：切换到 D 时读取 `A-B-C-D`，切换到 E 时读取 `A-B-E`。创建 C 后，B 新增的消息会动态进入 C；C 和 E 的私有消息不会互相泄漏。Checkpoint 只保存分支创建时的审计快照，不冻结运行时记忆。

## 主要能力

| 能力 | 说明 |
| --- | --- |
| 工作流画布 | 可缩放、拖拽、选择和连接对话节点；第一层展示工作流级对话。 |
| 双层画布 | 双击顶层节点进入对话内部的 Turn Tree，内部分支不会污染第一层。 |
| 路线感知 Chat | 每条路线拥有独立上下文，切换画布节点后 Chat 自动跟随。 |
| 模型接入 | 支持 OpenAI-compatible、OpenAI、DeepSeek、LM Studio、Ollama 和自定义服务。 |
| Runtime v2 | 提供安全工具、审批、取消、重试、运行 lease、事件时间线和 KV-cache-aware 上下文装配。 |
| 文件与检索 | 支持常见文本、代码、PDF、Office 文件，路线隔离的 FTS5 检索和上下文压缩。 |
| 工作流研判 | 对比 2–4 条路线的摘要、共同前缀、独有内容、运行指标和 Artifact，并受控合并知识。 |
| 宿主桥接 | 提供 Codex companion 与 Claude Code companion 的任务枚举、读取、分支和导航能力。 |
| 本地优先 | SQLite、附件对象和 API Key 默认保留在本机，不依赖云端账户。 |

## 快速开始

### Windows 一键安装（推荐）

从仓库的 [Releases](https://github.com/TTTT430/weavepath/releases) 下载最新的 `WeavePath-Setup-*.exe`，运行安装程序并按提示完成安装。安装程序会创建桌面和开始菜单快捷方式；以后直接点击快捷方式即可启动 WeavePath。首次打开后，在“设置”中填写自己的模型服务地址、模型 ID 和 API Key。

> 当前仓库的 Windows Preview 安装包也可由 GitHub Actions 手动构建或通过 `v*` tag 生成。

### 从源码运行

需要 Windows PowerShell 7（macOS/Linux 可分别启动进程）、Python 3.12+、Node.js 22+ 和 npm 10+。

```powershell
git clone https://github.com/TTTT430/weavepath.git
cd weavepath

python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install -e ".\\backend[test]"

Push-Location .\\apps\\web
npm ci
Pop-Location

.\\scripts\\dev.ps1 -OpenBrowser
```

执行最后一条命令后，脚本会自动启动本机服务并打开 WeavePath 页面。源码方式适合开发者；普通用户直接使用 Windows 安装包即可。

## 使用指南

### 1. 创建工作流和对话

进入“对话”，创建工作流后发送第一条消息。左侧列表显示顶层对话，工作流画布显示节点及其父子连线。节点名称是用户可见的对话名称，不会因为界面语言切换而翻译。

### 2. 创建和切换分支

点击节点或卡片上的 `＋` 创建 sibling 分支。分支名称和首条问题可以留空，系统会自动生成名称；之后可通过双击名称重命名。选择节点会同步 Chat，继续发送的消息只写入当前路线。

双击顶层节点会打开第二层 Turn Tree。这里可以查看该对话的轮次、从具体 user turn 创建分支，并直接在选中的内部路线继续对话。

### 3. 选择模型和推理强度

在“设置”中填写服务商、Base URL、模型 ID 和 API Key，点击“测试并获取模型”。保存后，可在输入框发送按钮旁切换模型，并选择低、中、高、极高推理强度。连接失败会显示诊断路线，并自动重试可恢复的连接故障。

### 4. 查看回答与运行详情

回答支持 Markdown/GFM、代码复制、编辑最近一次提问并重新生成。生成期间会显示连接、等待模型、接收回答、自动重连和已处理时长。展开详情可查看耗时、Token、费用、工具记录、检索来源、上下文压缩计划，以及供应商返回的缓存 Token、未缓存 Token、复用率和覆盖率；供应商未提供缓存数据时显示“不可用”。

### 5. 文件、检索和压缩

输入框左侧 `＋` 支持每条消息最多 5 个文件，单文件最大 50 MiB；大于 4 MiB 时自动分片并支持失败重试和跨重启续传。文件按 SHA-256 存入本机对象存储，解析后的文本按路线建立索引。检索只访问当前路线及其父路线，不读取兄弟路线。上下文超过预算时，系统按具体路线生成压缩计划，原始消息不会删除。

### 6. 工作流研判（实验室）

实验室用于比较工作流分支，而不是训练大模型：

1. 选择 2–4 条路线；
2. 查看共同记忆前缀、分支独有消息、摘要、模型和运行指标；
3. 对比 Artifact、已接纳知识和最近 Agent 结论；
4. 明确勾选要合并的结论或 Artifact；
5. 将结果写入指定目标路线并保留 provenance。

它不会把 GB 级数据集全部上传到浏览器，也不会假装提供 PyTorch 训练。真正的大规模训练应由本机路径、对象存储或外部 Runner 执行。

### 7. Codex / Claude Code 桥接

Codex companion 通过宿主 app-tools 读取和导航任务，Claude Code companion 读取本地 JSONL 会话并使用 `--resume --fork-session` 创建任务头分支。宿主不支持的历史 turn 分支、重命名或归档会明确返回 unsupported，不伪造能力。

## 模型配置

也可以在启动前设置环境变量：

```powershell
$env:WEAVEPATH_LLM_BASE_URL = "http://127.0.0.1:1234/v1"
$env:WEAVEPATH_LLM_MODEL = "your-model-id"
$env:WEAVEPATH_LLM_API_KEY = "optional"
$env:WEAVEPATH_LLM_NETWORK_MODE = "auto"       # auto / system / direct
$env:WEAVEPATH_CONTEXT_BUDGET_CHARS = "240000"
.\\scripts\\dev.ps1
```

自动网络模式优先直连，失败后才尝试系统代理；HTTP 仅允许 loopback，远程服务应使用 HTTPS。API Key 默认只存在后端进程内存，启用安全保存后 Windows 使用当前账户 DPAPI 加密。

## Windows 打包

```powershell
.\\scripts\\package-windows.ps1
```

脚本会构建 Vite、用 PyInstaller 打包本地 API，再生成 Electron x64 NSIS 安装包到 `release/`。如果下载 Electron 依赖时网络不稳定：

```powershell
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
$env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"
.\\scripts\\package-windows.ps1 -SkipTests
```

## 开发与验证

```powershell
.\\scripts\\check.ps1
```

当前本机切片已验证后端测试、前端测试、TypeScript、production build、SSE 断线恢复、Codex/Claude companion、数据库备份恢复和 Windows 桌面启动。详细边界见：

- [总体架构](docs/architecture.md)
- [开发状态](docs/development-status.md)
- [Windows 桌面预览](docs/desktop-preview.md)
- [P3/P4 真实连接与恢复验收](docs/p3-p4-live-acceptance.md)

开发者排障时，源码启动的 Web 页面默认监听 `http://127.0.0.1:5173`，API 健康检查为 `http://127.0.0.1:8000/api/v1/health`。这些是启动脚本在用户自己电脑上临时使用的本地地址，不是供他人访问的公共网址。

## 数据、安全与限制

- 默认只监听 `127.0.0.1`，不要直接暴露到互联网；
- SQLite 是当前单 API 进程的真源，数据库旁保存 verified backup；
- `read_file` 和 `workspace_search` 只有显式设置 `WEAVEPATH_WORKSPACE_ROOT` 才开放；
- `propose_patch` 只生成待审批 Artifact，不直接修改工作区；
- 当前不提供云端登录、多租户授权、生产级公网部署、自动 evaluator/scorer、多 Agent 协作或永久删除；
- 图片 OCR、embedding/向量召回、自动更新和签名发布仍是后续计划。

## 许可证

Apache-2.0
# Agent 验收

项目提供独立的 [Agent 验收入口](evals/README.md)：24 项无费用规则回归，以及显式开启的真实模型冒烟测评。报告区分规则通过与人工质量评审，不以 Token 或回答长度代替正确性。
