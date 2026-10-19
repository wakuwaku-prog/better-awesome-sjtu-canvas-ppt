---
{#- 模板作者注：{{note_type_key}} 由 gen_notes.py 按画像替换（type / note_type）。 -#}
{{note_type_key}}: {{deck_type_value}}
course_code: {{course_code}}
lecture: {{lecture_id}}
deck_id: {{deck_id}}
deck_file: "{{deck_file}}"
material_date: {{material_date}}
status: {{status}}
source:
{{source_lines}}
tags:
{{tag_lines}}
---

# {{deck_id}} — 课件总览：{{title_zh}} {{title_en}}

> [!info] 来源与页码
> **课件文件 Deck file：** `{{deck_file}}`（共 {{page_count}} 个 PDF 阅读页）
> **来源登记：** {{source_register_line}}
> **下文统一使用 PDF 阅读页 p1–p{{page_count}}**，即 PDF 阅读器页序。
>
> **页脚不是页码，是小节编号**（`pageNoScope: section`）：页脚是小节 / 版式编号，
> **一个小节可跨多页**。实测 p26–p31 与 p33 页脚**都是 10**（7 页共享一个小节号）、
> p8/p9/p11 都是 03、p17/p18 都是 06——**页脚重复是正常的，不是异常**，
> 所以**不能当唯一页码用**。逐页对照见 [[#页脚与 PDF 阅读页对照]]——那张表带
> **所属小节**与**页脚**两列；没有这两列，读者会误以为编号是连续、唯一的。
>
> **渲染页图：** {{page_image_line}}

{{deck_scope_callouts}}

## 内容结构

{{content_structure}}

## Checkpoint 核查

{{checkpoint_note}}

## 课堂放映记录 Class projection record

> [!important] 「已讲授」的唯一机器可判依据
> 本表来自 manifest 的 `slides[].projections`。
> **`projections` 为空数组 = 未见对应讲解**——该页只存在于课件里，不能写成「已讲授」。
> 同一页可以有**多条**放映记录：既可能在两个节次都放映，也可能在同一节次重放。
> **不要按页号去重**，也**不要把平台节次号当课程 L 编号**。

{{projection_table}}

{{content_type_callouts}}

## 页脚与 PDF 阅读页对照 Footer ↔ PDF page mapping

> 同一小节的页**共享同一个页脚编号**，所以这张表里 `页脚` 一列会有重复——
> 那是小节结构，不是错误。

| PDF 阅读页 | 页脚 / 版式编号（原始读数） | 归一化 | 状态 | 所属小节 / 单元 |
|---|---|---|---|---|
{{footer_rows}}

**状态取值**（来自 manifest `pageNoStatus`）：

| 状态 | 含义 | 处理 |
|---|---|---|
| `verified` | 两路读数一致 | 正常，不需标注（**含同小节多页共享同一编号**） |
| `anomalous` | 两路一致但**明显偏离相邻页序列**（如 p24 页脚 42，前后页是 08/09） | **按原样保留读数、不静默改名**，写进待核实 |
| `mismatch` | 整帧 OCR 与页脚裁切放大两路读数不一致 | 不猜，写进待核实 |
| `missing` | 无页脚数字（含底部为「数据来源」等其它内容行） | 记录事实，引用以 PDF 阅读页为准 |
| `not-applicable` | 该页无页码体系 | 记录事实 |

## 课件逐页索引 Page-by-page（p1–p{{page_count}}）

{{page_entries}}

## 待核实清单 To verify

{{to_verify}}

## 相关链接 Connections

{{connections}}
