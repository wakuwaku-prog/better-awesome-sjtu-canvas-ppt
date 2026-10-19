---
{#-
  模板作者注（不会出现在产物里）：
  字段名按目标库画像 `frontmatter.required` 决定：
    心理与精神健康 / 有机化学  → `type`
    细胞生物学和组织学        → `note_type`
  gen_notes.py 把 {{note_type_key}} 替换成实际的键名。
-#}
{{note_type_key}}: lecture-note
course_code: {{course_code}}
lecture: {{lecture_id}}
lecture_date: {{class_date}}
material_date: {{material_date}}
status: {{status}}
source:
{{source_lines}}
tags:
{{tag_lines}}
---

# {{lecture_id}} — {{title_zh}} {{title_en}}

> [!abstract] 本节主线
> {{one_line_summary}}
>
> **怎么用这份稿：** 先看「课堂覆盖与证据边界」确认哪些内容是课堂讲过的、哪些只是课件里存在；
> 再按概念顺序读正文；最后做主动回忆题。带 ❓ 的条目是尚未核实的，不要当成结论记。

> [!info] 资料与课堂状态
> **课堂日期：** {{class_date}}（{{class_date_note}}）
> **平台节次：** {{sessions_line}}
> **内容基准：** {{deck_basis_line}}
> **证据类别约定**
> {{evidence_tags_line}}
> {{evidence_caveat_line}}

## 中心问题 Central questions

### 课件原题（章节导入页 / Checkpoint，保留原文）

{{central_questions}}

> [!note] 为什么单独列出
> 按目标库 AGENTS.md，目录页、章节/单元导入页与 Checkpoint 页提出的问题属于本课重点，
> 必须保留英文原文、中文释义与课件页码，并用来组织本笔记内容。

### 本课要回答的问题（我的整理，供复习自查）

{{self_questions}}

## 课堂覆盖与证据边界 Class coverage and evidence boundary

> [!important] 课件存在 ≠ 课堂讲过
> 下表按 manifest 的 `slides[].projections` 判定：**`projections` 为空数组 = 未见对应讲解**，
> 只说明该页存在于课件中。**不要把「有页图」当成「已讲授」。**

| 状态 | 页数 | PDF 阅读页 | 含义 |
|---|---:|---|---|
| 已讲授 | {{n_taught}} | {{pages_taught}} | 回放/口述有放映记录 |
| 未见对应讲解 | {{n_not_taught}} | {{pages_not_taught}} | 课件里有，但转写/回放中没有对应讲解 |
| 非课件内容 | {{n_nonslide}} | {{pages_nonslide}} | `contentType` 为 video / activity / desktop |

> 「非课件内容」与「已讲授」**会有重叠**：视频页/活动页也可以有放映记录。
> 这一行是内容类型的提示，不是互斥的第三类。

{{coverage_notes}}

{{unarchived_callouts}}

## 正文

<!--
写作要求（本 skill 的硬规则）：
  1. 按**概念与课堂逻辑**组织，不逐页机械抄写。
  2. 一段只讲一个重点；用短句；关键术语首次出现给英文。
  3. 图片块必须用**展开** callout，由画像 `callouts.slideBlock` 决定形式：
       有机化学库      显式展开的 example 块
       心理与精神健康  裸 example 块（Obsidian 默认即展开）
     两者都可见。**绝不允许出现折叠写法**——折叠会让图默认不可见；
     有机化学库实测：509 处嵌入写在折叠块里，72/72 篇 L 笔记打开时可见图数为 0。
     gen_notes.py 会做这一项强制替换，不依赖写作时的自觉。
  4. 默认**内嵌页图**，不要用 `#page=`。心理库学习者实测该链接点击无效。
  5. 同一页可以在多个节次放映、也可以在同一节次重放，**不要按页号去重**。
-->

{{body}}

## 本节要点

{{key_points}}

## 页脚异常与待核实 Footer anomalies and to-verify

> [!warning] 页脚不是页码，是小节编号
> 本课件 `pageNoScope: section`：页脚是小节 / 版式编号，**一个小节可跨多页**。
> 心理库 L08 实测 p26–p31 与 p33 页脚**都是 10**、p8/p9/p11 都是 03、p17/p18 都是 06——
> **重复是正常的，不是异常**。因此页脚**不能当唯一页码用**，引用一律以 PDF 阅读页为准。
> 下面只列真正需要人工判断的条目（`anomalous` / `mismatch` / `missing`）。

{{footer_anomalies}}

### 待核实清单

{{to_verify}}

## 主动回忆 Active recall

> 先自己作答，再看「相关链接」里的课件页与来源。

{{active_recall}}

## 相关链接 Connections

{{connections}}
