# Security Policy

## Supported versions

WeavePath 目前处于 `0.x` 早期开发阶段，只为默认分支的最新版本提供安全修复。

## Reporting a vulnerability

请不要在公开 Issue 中披露漏洞、密钥、对话数据或可复现的敏感日志。请使用 GitHub 仓库的 **Security → Report a vulnerability** 私下提交报告，并包含：

- 受影响的版本或 commit；
- 最小复现步骤；
- 可能影响的数据或权限；
- 已知缓解方式。

维护者会尽快确认报告，并在修复可用后协调公开披露。

## Deployment warning

- 当前服务面向单用户本机开发，默认仅绑定 `127.0.0.1`。
- 当前版本没有公网身份认证、租户隔离或生产级密钥库；不要直接绑定 `0.0.0.0` 暴露到互联网。
- SQLite 数据库、checkpoint 和导出内容可能包含完整对话与敏感项目上下文。
- API key 默认只保存在进程内存；通过环境变量提供的密钥仍由启动环境负责保护。
- 提交 Issue 或日志前，请移除路径、令牌、对话内容和个人信息。
