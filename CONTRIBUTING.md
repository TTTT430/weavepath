# Contributing to WeavePath

感谢你参与 WeavePath。当前项目仍处于早期 Agent 工程工作台阶段；提交代码前请先确认变更符合路线记忆隔离和本地优先原则。

## 开发环境

- Python 3.12+
- Node.js 22+
- npm 10+

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".\backend[test]"
Push-Location .\apps\web
npm ci
Pop-Location
.\scripts\check.ps1
```

macOS/Linux 可分别在 `backend/` 运行 `pytest`，并在 `apps/web/` 运行：

```bash
npm ci
npm test
npm run build
```

## 提交流程

1. 为一个明确结果建立独立分支。
2. 保持提交小而完整，提交信息说明“为什么改变”。
3. 行为变化必须增加或更新测试。
4. 架构边界变化先增加 ADR，再修改实现。
5. Pull Request 中写明验证命令、兼容性和数据迁移影响。

## 安全与数据

禁止提交以下内容：

- API key、访问令牌、密码或私钥；
- `.env`、本地模型配置或凭据文件；
- `*.db`、conversation transcript、checkpoint 导出或用户产物；
- `node_modules`、虚拟环境、缓存、日志和构建输出。

发现安全问题时不要创建公开 Issue，请按照 [SECURITY.md](SECURITY.md) 私下报告。

## 领域不变量

- 一个 conversation instance 只有一条祖先路线。
- 同 topic 的不同路线实例不得共享可变 transcript。
- 已创建子节点的 checkpoint 不得被父节点后续编辑回写。
- 跨路线读取必须由用户显式授权并记录 provenance。
- 级联归档保留 tombstone，不伪装成永久删除。

提交贡献即表示该贡献按仓库的 Apache License 2.0 提供。
