# manifest v1 迁移

## 目标

现有 `.codex/conversation-workflows/*.json` 是当前 Codex 工作流的 schema-v1 真源。长期模式改为全局 SQLite，但必须保留可验证、可回滚的导入路径。

## 当前资产定位

- `conversation-workflow-bridge-v4`：冻结为 legacy Codex adapter。
- `conversation-workflow-skill-v4`：保留 schema v1 校验规则和兼容说明。
- `workflow_graph.py`：后续拆为 import/export/validate 工具，不再作为第二个在线写引擎。
- demo HTML：仅作视觉规格，不参与迁移。

## 单真源模式

每个 workflow 必须明确处于一种模式：

```text
legacy_manifest   JSON 是真源，Core Service 只读或执行一次性导入
core_database     SQLite 是真源，插件通过 Core Service 修改
```

不提供隐式双向同步。进入 `core_database` 后，旧 JSON 只能显式导出，不能被 watcher 自动回写数据库。

## 字段映射

| manifest v1 | Core Service |
|---|---|
| `workflow_id` | `workflows.id` |
| `workflow_name` | `workflows.title` |
| `root_node_id` | `workflows.root_instance_id` |
| `revision` | 初始 `workflows.graph_revision` |
| node `id` | `conversation_instances.id` 或 legacy key |
| node `parent_id` | `conversation_instances.parent_instance_id` |
| node `topic_id` | `topics.id` + instance `topic_id` |
| node `label` | `conversation_instances.explicit_title` |
| node `thread_id` / `host_id` | `host_bindings` |
| node `status` / `pruned_at` | instance status + `tombstones` |
| `active_node_id` | 仅导入为 `last_opened` 偏好，不作为全局 current |

manifest 不含 transcript，因此导入也不得推断或复制 transcript。

## 导入流程

1. 以原 schema-v1 规则校验 canonical manifest。
2. 计算文件 hash，并保存导入来源、路径、revision、hash。
3. 在单个 SQLite 事务中创建 workflow、topics、instances、bindings 和 tombstones。
4. 验证 root、唯一 host thread binding、无环、pruned ancestor 等不变量。
5. 标记 workflow 为 `core_database`。
6. 生成导入报告；原文件保持不变并继续可恢复。

相同 `workflow_id + source hash` 的重复导入必须幂等。相同 workflow ID 但 hash/revision 不同则停止并要求用户选择覆盖、另存或取消。

## 导出与回滚

- 导出生成新的 schema-v1 文件，不覆盖原文件，除非用户明确指定。
- 无法表达的未来字段放入独立导出报告，不静默丢失。
- 回滚到 legacy 前必须停止 Core Service 对该 workflow 的写入，并导出一个已验证 manifest。
- Codex 插件完成 graph-core 接入前，legacy v4 仍继续读取原 manifest；迁移版本不得同时由两端修改。
