# Windows 桌面预览版

WeavePath 的桌面包由 Electron 外壳和 PyInstaller 打包的本地 FastAPI sidecar 组成。Electron 启动时选择空闲的 loopback 端口，启动 `WeavePathBackend.exe`，等待 `/api/v1/health` 就绪后打开同源 React 界面；退出应用时会回收 sidecar。API 只绑定 `127.0.0.1`，不会把本机数据库暴露到局域网。

## 构建

在仓库根目录执行：

```powershell
.\scripts\package-windows.ps1
```

脚本会依次运行检查、构建 Vite、用 PyInstaller 生成 `dist/WeavePathBackend`，再用 electron-builder 生成 `release/WeavePath-Setup-0.1.0.exe`。GitHub Actions 的 `Windows Preview` workflow 也会在手动触发或推送 `v*` tag 时上传同名 artifact。

## 数据和限制

安装包默认将数据库和附件放在 `%LOCALAPPDATA%\WeavePath\data`，卸载保留这些数据。当前是单用户本机 preview，不含账号、多租户、自动更新或公网部署治理。大文件/GB 级数据集不会被强行上传到浏览器；路线文件可引用本机对象存储，真正训练应由后续外部 runner 执行。

## 实验室定位

实验室现在是“工作流研判”：选择 2–4 条路线，比较共同前缀、路线独有消息、摘要、模型、运行指标、Artifact 和已接纳知识，并把用户明确勾选的结论合并到目标路线。它不是 PyTorch 训练器，也不会假装已经完成大规模数据集训练。旧的小样本数据集/实验快照 API 仅保留兼容，不再作为主导航。
