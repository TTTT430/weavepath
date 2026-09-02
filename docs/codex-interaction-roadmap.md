# Codex 对话交互借鉴路线

更新日期：2026-08-31

本清单用于借鉴 Codex 的交互组织方式，不把未公开记录的界面行为当作稳定产品契约。

## 官方依据

- [Projects and chats](https://learn.chatgpt.com/docs/projects)：固定、重命名、搜索、归档与恢复；建议为不同 outcome 使用独立 chat。
- [Commands](https://learn.chatgpt.com/docs/reference/commands)：前后切换、聊天内查找、恢复上一条 composer prompt、复制 conversation path/deep link/session ID、Undo/Redo，以及 thread deep link。
- 官方 Commands 文档明确：`prompt=` deep link 只预填 composer，不会自动发送。因此本项目的节点导航继续使用受信任的 activate/adapter 接口，绝不把导航编码成 follow-up 消息。

## 已实现

- 每个节点对应一个独立 conversation instance，不同 outcome 通过 fork 建立兄弟路线。
- 双击顶层节点进入其 Turn Canvas；只有显式“继续对话”才切换具体路线，任何动作都不向 composer 写入导航指令。
- 节点标题、revision-safe 重命名、路线、级联归档与 tombstone。
- 卡片 `＋` 可直接创建无标题、无首条内容的分支；系统先生成 `新分支 N`，第一条本地用户消息可生成摘要标题，用户重命名后不再自动覆盖。
- 编辑当前节点最后一次本地用户提问并重新生成；已有子节点 checkpoint 锚点和审计快照保持不变，但子节点有效上下文仍沿 parent 路线动态读取最新消息。
- 复制最近一次用户提问。

“编辑最近提问并重新生成”是本产品按明确路线语义实现的能力；上述官方页面没有把它定义为 Codex 跨平台稳定契约，因此不标注为官方 parity。

## 推荐顺序

### P0：完成生成闭环

1. SSE 流式输出与停止生成。
2. 不修改提问、仅重试最后一次回答，并保留 request id 以避免网络重试重复消耗模型调用。
3. 失败后的明确恢复入口和离线/超时状态。

### P1：高频导航与整理

1. 搜索工作流、节点标题和消息；当前节点内查找。
2. 上一个/下一个节点、最近节点快捷切换和键盘快捷键面板。
3. 固定/取消固定节点，归档列表与恢复入口；节点重命名已经实现，后续补批量整理与快捷键。
4. 复制节点链接、instance ID、完整记忆路线；Standalone 使用 Web URL，宿主 adapter 可映射真实 task deep link。
5. composer 为空时按 `↑` 恢复上一次输入草稿。

### P2：可逆工作流操作

1. 对“新建分支、重命名、激活、归档”等本项目可逆动作提供有限 Undo/Redo。
2. 级联归档仍必须确认并保留 tombstone；Undo 不是事务安全或永久删除的替代品。
3. 只读分享某个具体路线的 snapshot，默认不暴露兄弟路线。

## 不直接照搬

- 不把 Git branch 搜索误称为 conversation branch。
- 不用 `codex://...prompt=` 代替真正的节点切换。
- 不允许编辑任意历史片段后静默改写已有子节点。
- 不自动合并兄弟路线 transcript；跨路线引用必须显式并记录 provenance。
