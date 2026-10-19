---
{#-
  模板作者注（不会出现在产物里）：
  ⚠️ V## **不是默认产物**。目标库 AGENTS.md 的规则是「仅在学习者明确需要回放复核时才建立」，
     核对完成后按库规则删除或保留。gen_notes.py 只有在 `--with-timeline` 时才生成；默认不建。
     三库实测：心理与精神健康 = 不默认创建；细胞生物学和组织学 = 不默认创建；
     有机化学 = AGENTS.md 无此条款（未找到，不代它决定）。
-#}
{{note_type_key}}: replay-review
course_code: {{course_code}}
lecture: {{lecture_id}}
video_id: {{timeline_id}}
class_date: {{class_date}}
status: {{status}}
source:
{{source_lines}}
tags:
{{tag_lines}}
---

# {{timeline_id}} — 回放复核：{{title_zh}} {{title_en}}

> [!warning] 这一页是复核工具，不是学习主线
> 按目标库 AGENTS.md，`V##` 只用于**原话核对**，不把时间线当默认学习主线。
> 正式内容请读 [[{{lecture_link}}|{{lecture_id}} 课堂笔记]]。
> 核对完成后按该库规则处理（有的库要求删除）。
>
> **证据边界：** 时间码来自 `slides[].projections`（平台课件轨的换算后秒数）。
> 转写为机器转写时，说话人标签是**推断**、不是身份；引用前需核对原始音频。

## 节次与时间基准 Sessions and time base

| # | 节次 | 平台 id | 课堂日期 | 时长 | 实际使用课件 | 覆盖说明 |
|---|---|---|---|---|---|---|
{{session_rows}}

> [!note] 一节课拆成多段时
> 各段**分别登记**（Source Register 一行一节），但合成同一篇 L 笔记——
> 这是目标库 AGENTS.md 的要求，不要合并成一份材料，也不要把平台节次号当 L 编号。

## 课件放映时间线 Projection timeline

> 同一页可能出现**多行**：跨节次放映，或同节次重放（`isReplay`）。
> `projections` 为空的页**不在这张表里**——它们没有对应讲解。

| 时间码 | 节次 | PDF 阅读页 | 页主题 | 内容类型 | 重放 | 时间码口径 | 平台原始读数 |
|---|---|---|---|---|---|---|---|
{{timeline_rows}}

## 未见对应讲解的页 Slides with no projection

{{no_projection_note}}

## 原始媒体与转写 Media and transcript

| 节次 | 原始视频 | 音轨 | 归档 | 转写文件 | 模型 | 分段 | 段数 | 说话人 | 备注 |
|---|---|---|---|---|---|---|---|---|---|
{{media_rows}}

> [!warning] 引用纪律
> - 只在**实际播放/听取音频**后写「已听/已核对音频」；只读转写时写「按转写核对」。
> - 已知误听词（`transcript.knownMishearings`）**不能作为逐字教师引语**。
> - 自动说话人标签恒为推断，不是身份。
> {{media_policy_line}}

## 未归档材料 Unarchived materials

> 课堂用到但尚未归档的材料。**不记这一条，复核者会以为课件是完整的。**

{{unarchived_rows}}

## 待核实清单 To verify

{{to_verify}}

## 相关链接 Connections

{{connections}}
