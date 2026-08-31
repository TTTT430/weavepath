# ADR-0002：同 topic 使用多个路线实例

- 状态：Accepted
- 日期：2026-08-31

## 背景

用户可能从不同祖先路线到达同一逻辑主题 D，例如 `A-B-C-D` 和 `A-E-D`。若两条路线共用一个可变 conversation/task，模型会混合 sibling transcript，无法保证路径记忆。

## 决策

- `Topic` 是逻辑分组和显示名称。
- `ConversationInstance` 是具体、可继续的对话实例。
- 每个实例只有一个 `parent_instance_id` 和一个冻结的 `parent_checkpoint_id`。
- 相同 topic 可有任意多个实例；它们拥有独立 transcript、host binding、checkpoint 和 content revision。
- UI 可以聚合显示同 topic，但继续前必须选择具体路线实例。
- 路线从 parent 链推导，不存可变路径数组。

## 后果

优点：

- sibling memory 严格隔离；
- 同一主题可对比不同路线；
- 归档、检查和宿主导航都有唯一实例目标。

代价：

- UI 必须提供 route chooser；
- 逻辑节点数与具体实例数不同；
- 跨路线转移不能隐式发生，需要显式 provenance。

## 不变量

- 一个 host thread/session binding 只能属于一个 active instance。
- 同 topic 不代表共享 transcript。
- 子节点只继承 fork 时 checkpoint 之前的祖先状态。
- pruned ancestor 下不能存在 active descendant。
