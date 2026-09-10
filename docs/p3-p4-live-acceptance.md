# P3/P4 真实连接与恢复验收

更新日期：2026-09-10

## 自动化基线

从仓库根目录运行：

```powershell
.\scripts\check.ps1
```

当前结果：

- 后端 214 项测试通过；
- 前端 155 项测试通过；
- Python compileall、TypeScript typecheck 与 production build 通过；
- Codex companion Node smoke test与官方 plugin validator 通过。

覆盖范围包括 HostAdapter 合同与身份校验、外部任务枚举/导入、Saga 幂等/补偿/崩溃续交、数据库迁移备份/恢复/保留、SSE 事件持久化、客户端 sequence 去重与重连恢复。

## 本机真实边界

```powershell
.\scripts\check-live.ps1
```

2026-09-10 本机验收结果：

- Codex：通过真实 app-tools pipe 看见 34 个任务，并成功读取当前任务；
- Claude Code：发现 9 个本机会话，CLI fork capability 可用，并成功读取会话；
- P4：真实 Uvicorn/HTTP/SSE socket 首次返回半截内容后断开，第二次连接恢复为完整回答；durable journal 含 reset/completed 事件，数据库仅有一条 user 与一条 assistant，缓存 usage 为 cached 12 / uncached 8。

脚本的 loopback 客户端显式禁用环境代理，因为两个 socket 都由验收脚本自己拥有；这避免 Clash 或系统代理错误接管 `127.0.0.1`，不影响产品的公网模型 `auto / system / direct` 路由逻辑。

## 人工验收

安装 Codex companion 后新建一个 Codex 任务，再启动 WeavePath API。检查：

1. `GET /api/v1/host/capabilities` 返回 `hostKind=codex`、`connected=true`；
2. `GET /api/v1/host/conversations` 返回标准化 `threadId/title`；
3. 导入一个任务后读取 host transcript；
4. 从该根节点 fork，确认产生新 Codex 任务并注册新节点；
5. 在 WeavePath 选择该节点，确认 Codex 导航一次且 composer 内容不变；
6. 用设置页生成 restore plan 时只显示命令，不在运行中的 API 内执行恢复。

公网 OpenAI-compatible provider 的最终人工测试应另行使用用户自己的 API key。密钥不能写进 issue、测试快照、Git 历史或此文档。
