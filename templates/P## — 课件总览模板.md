---
type: ppt-note
course_code: <课程代码，如 HLT3202G>
lecture_id: L0X
ppt_id: P0X
title: "<课程名> 课件总览 <中文主题> <English Topic>"
deck_file: "<课件文件名，含修订后缀>"
lecture_date: YYYY-MM-DD
source: <回放平台与节次，如 "SJTU 课堂实录回放（第9节 15:55 / 第10节 16:50）">
status: transcribed
tags:
  - <course-code-lowercase>
  - ppt
  - <主题标签>
---

# P0X — 课件总览 <中文主题> <English Topic>

> [!info] 资料信息 Source
> **课件文件 Deck file：** `<文件名>.pptx`（共 **N** 张幻灯片；本课放映 <页码范围>）
> **原始录播 Recordings：** 第N节（YYYY-MM-DD HH:MM–HH:MM，时长）、第N+1节（…）
> **图片来源 Image source：** <平台 PPT 轨 / 屏幕轨逐场景抽取>，原图存于 `09 参考资料/附件/L0X 课件 PPT/`
> **整理日期 Processed：** YYYY-MM-DD

> [!warning] 页码与课件版本 Page numbering and deck revision
> 页码以课件**页脚打印页码**为准。本课用图全部经两路独立校验：整帧 OCR 与「页脚条裁切 + 放大 + 数字白名单 OCR」结果一致。
> <若课件在学期中被修改过，在此写明版本差异与生效范围。>

> [!note] 本次抽取方法 Extraction method
> <走路径 A 就写平台 PPT 轨与接口；走路径 B 就写清「平台未生成 PPT 轨 → 从屏幕轨按解码时间码抽取」及原因。>
> 脚本：`~/.dsh/skills/<skill 名>/scripts/…`

## 一、这节课实际放映范围 Coverage

| 节次 | 时间 | 时长 | 放映内容 |
|---|---|---|---|
| 第N节（上半节） | HH:MM–HH:MM | MM:SS | <页码范围与主题> |
| 第N+1节（下半节） | HH:MM–HH:MM | MM:SS | <页码范围与主题> |

- 两节合起来是**一节完整的课**（第X课），内容前后衔接。
- 课程转写与时间线见 [[02 课堂笔记/V0X — …|V0X 第X课课堂录像时间线]]；要点整理见 [[02 课堂笔记/L0X — …|L0X 课堂笔记]]。

## 二、课件逐页图解 Page-by-page gallery

> 每页一个小节。标题格式：`### p## — <English topic> — <中文主题>`。
> 图片宽度统一 **760**；同一页码对应两种内容时按**时间码**区分，不要只按页码。

### p## — <English topic> — <中文主题>

![[09 参考资料/附件/L0X 课件 PPT/第N节/PPT p## — <English topic> (第N节 MMSS).jpg|760]]

- **放映时间：** 第N节 MM:SS
- **页码校验：** 整帧 OCR = ##，页脚裁切 OCR = ##
- **本页在讲什么：** <逐条列出课件上的事实：定义、模型、图示、页脚结论>
- **课堂补充：** <老师口头补充，标注时间码；机器转写内容注明「转写」>

---

<按时间码顺序重复以上小节，覆盖全部归档图片>

## 三、页面内容分组 Content grouping

### 1) <分组标题>（p##–p##）
<两三句话概括这一组在讲什么、如何衔接。>

### 2) <分组标题>（p##–p##）
<…>

## 四、页码校正 Page numbering correction

> 仅当发现历史笔记的页码与页脚不符时才保留此节；否则删除。

| 归档文件名 | 文件名中的页码 | 图片页脚实际读数 |
|---|---|---|
| `PPT p## — …` | p## | **##** |

## 五、待核实 To verify

- [ ] <页码异常页：页脚重复 / 缺失 / 独立章节重新编号，逐条列出>
- [ ] <未在本次课出现的页码范围，声明不能凭页码补写>
- [ ] <短暂闪现页、过渡页是否单独归档>
- [ ] <课件版本差异的具体新增/移动页>

## 相关链接 Related notes

- [[02 课堂笔记/L0X — <中文主题> <English Topic>|L0X 课堂笔记]]
- [[02 课堂笔记/V0X — 第X课课堂录像时间线|V0X 第X课课堂录像时间线]]
- [[09 参考资料/Source Register|资料登记表 Source Register]]
- [[03 核心概念/Concept — <关键概念>|<关键概念>]]
