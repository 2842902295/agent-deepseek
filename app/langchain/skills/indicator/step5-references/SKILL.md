---
name: step5-references
description: "执行第5步（处理占位引用）时读取。包含：见表X、章节引用、附录、外部标准的处理方法，以及绝对禁止在字段中保留引用语的规则。"
license: MIT
---

# step5-references — 处理全部占位引用

第4步读取章节时记录的所有引用，在本步骤统一追踪补全。

## 处理规则

| 引用模式                                   | 处理方式                                                              |
|----------------------------------------|-------------------------------------------------------------------|
| "见表X"、"按表X"、"如表X所示"                    | `get_standard_chapters(standard_no, keyword="表X")` 读取该表所在章节，从章内 media 的表格条目解析内容（content）提取具体值回填 |
| "按X.X规定"、"符合X.X要求"                     | `get_standard_chapters(title_no_prefix="X.X")` 读取被引用章节，提取实际值回填    |
| "应符合附录X"                               | `get_standard_chapters(title_no_prefix="附录X")` 读取附录正文（规范性附录必须读）   |
| "按GB/T XXXX进行"、"符合XX XXXX"、"依据XX XXXX" | 按「标准阅读方法」读取该外部标准相关章节；若库中无此标准，保留标准号原文                              |

## 判断标准

只要能从正文中找到具体数值（数字、等级、合格/不合格判定），就必须提取，不允许以"引用"为由丢弃。

补全后若仍无具体值 → 该条丢弃。

## 图片处理

`get_standard_chapters` 返回的各章 `media` 字段条目都带 `path`：table/formula 条目自带解析文本 `content`（HTML/LaTeX），
直接读文本取值，不必看图；`{"type": "image", "path": "images/xxx.jpg"}` 的插图才用 `read_file(path)` 读取图片内容，
从中提取具体值。

## 绝对禁止

任何字段（`source_value`、`source_result`、`source_input_params`、`source_process_logic`）中出现以下表述：

- "见X.X"、"按X.X"、"同X.X"、"参见"
- "如表X"、"按表X"、"见表X"

所有值必须是追踪后得到的具体数值、文字描述或判定结论，不得保留任何指向原文的引用语。
