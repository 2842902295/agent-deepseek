---
name: step6-dedup
description: "执行第6步（去重并提交）时读取。包含静态指标和动态指标的去重规则，以及提交前的最终检查要点。"
license: MIT
---

# step6-dedup — 去重并提交

## 去重规则

### 静态指标

`indicator_object` 完全相同 **且** `source_value` 相同 → 保留一条，`source_clause` 取最具体的。

### 动态指标

`experiment_name` 相同 **且** `source_input_params` 相同 → 保留一条。

### 保留全部

同名但参数不同（如不同规格的同名试验）→ 保留全部，不合并。

## 提交前检查

1. 所有字段中无"见X.X"、"按X.X"、"如表X"等引用语
2. 每条 static 指标的 `indicator_object` 和 `source_value` 均不为空
3. 每条 dynamic 指标的 `experiment_name` 和 `source_result` 均不为空
4. `source_clause` 只含条款编号，不含标题文字

检查通过后，调用结构化输出工具一次性提交全部指标。
