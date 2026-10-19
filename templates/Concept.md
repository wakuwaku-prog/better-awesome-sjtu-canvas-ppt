---
{#-
  模板作者注（不会出现在产物里）：概念页是**跨课次复用**的——一个术语只建一个主概念笔记，
  同义词在正文里说明并链接过来。{{note_type_key}} 由 gen_notes.py 按画像替换。
-#}
{{note_type_key}}: concept
course_code: {{course_code}}
term: "{{term}}"
status: {{status}}
source:
{{source_lines}}
tags:
{{tag_lines}}
---

# Concept — {{term}} {{term_zh}}

## 一句话定义 One-sentence definition

> {{definition_zh}}（{{term}} / {{definition_en}}）

## 为什么重要 Why it matters

{{why_it_matters}}

## 构成或机制 Components / mechanism

{{mechanism}}

## 操作化与测量 Operationalization and measurement

> [!important] 概念页的价值主要在这一节
> 一个概念在课程里被**怎么测**，决定了关于它的论断能走多远。
> 写清构念、可观察指标、方法与主要效度问题，别只写定义。

- **构念 Construct：** {{construct}}
- **可观察指标 Observable indicator：** {{indicator}}
- **方法 Method：** {{method}}
- **主要效度问题 Main validity concern：** {{validity_concern}}

## 例子 Example

{{example}}

## 边界与常见混淆 Boundary and common confusion

{{boundary}}

## 证据与推断 Evidence and inference

> 区分「课件称 / 课件引用」与「独立核实的事实」。
> 课件里的论文数值或研究总结若未核对原始论文，写「课件称」，不要写成已独立验证。

{{evidence_notes}}

## 连接 Connection map

{{connection_map}}

## 主动回忆 Active recall

{{active_recall}}

## 资料来源 Sources

> 来源位置写清课件页码 / PDF 阅读页 / 时间码；**不要写本机绝对路径**。

{{sources}}

## 待核实 To verify

{{to_verify}}
