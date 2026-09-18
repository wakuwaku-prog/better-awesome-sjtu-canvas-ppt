---
type: video-note
course_code: <课程代码>
lecture_id: L0X
video_id: V0X
title: "第X课 课堂录像时间线 Lecture X Recording Timeline"
recorded_date: YYYY-MM-DD
source: <回放平台与节次>
status: transcribed
tags:
  - <course-code-lowercase>
  - video
  - timeline
  - <主题标签>
---

# V0X — 第X课 课堂录像时间线 Lecture X Recording Timeline

> [!info] 资料信息 Source
> **原始录像 Recordings：**
> - 第N节（上半节）：YYYY-MM-DD HH:MM–HH:MM，地点，时长
> - 第N+1节（下半节）：YYYY-MM-DD HH:MM–HH:MM，地点，时长
> **文件 Files：** `09 参考资料/附件/L0X 课堂视频/`、`…/L0X 音轨/`、`…/L0X 字幕/`
> **逐页图解 Page gallery：** [[01 课程资料/P0X — …|P0X 课件总览]]
> **整理日期 Processed：** YYYY-MM-DD

> [!warning] 转写说明 Transcription caveat
> 时间线来自 faster-whisper `large-v3` 机器转写（600 秒分段、逐段自动判定中/英）+ 自动说话人分离。
> **机器转写不是逐字稿**：人名、专业术语和长句可能出现偏差；引用老师原话前请按时间码回看录像核对。

> [!warning] 时间码说明 Timestamp caveat
> <若容器时间戳可靠，写「本页时间码来自场景检测的解码时间，与语音转写时间码属两条独立证据链」。>
> <若时间戳有重复 DTS，必须写明 `ffmpeg -ss` 会取到相邻画面，因此所有课件画面时间码来自解码顺序上的场景切换时刻。>

### 说话人分离结果 Speaker diarization summary

| 节次 | cluster 0（累计发言最长，推断为老师） | cluster 1 | 备注 |
|---|---|---|---|
| 第N节 | <段数 / 秒数 / 中文占比> | <…> | <学生发言是否可辨> |
| 第N+1节 | <…> | <…> | <…> |

- 模型：`microsoft/wavlm-base-plus-sv` 声纹嵌入 + 余弦层次聚类；角色由「累计发言时长最长者 = 老师」推断。
- **说话人标签是推断**，不是逐字归属证据；课堂单麦混音下小组讨论通常分不出来。
- 完整标注版：`09 参考资料/附件/L0X 字幕/L0X 第N节 转写（说话人）.md` 等。

## 一、两节的分工 Structure of the two periods

| 节次 | 时间 | 时长 | 主要内容 |
|---|---|---|---|
| 第N节（上半节） | HH:MM–HH:MM | MM:SS | <…> |
| 第N+1节（下半节） | HH:MM–HH:MM | MM:SS | <…> |

- 两节合起来是**一节完整的课**（第X课）。
- <列出不计入课程内容的时段：课前调试、课间闲聊、下课前后桌面投屏。>

## 二、第N节时间线 Period N（HH:MM–HH:MM）

### 这一段在讲哪几页（第N节课件画面）

> 图片廊：2 列，宽度统一 **300**；单元格写「**主题**<br>时间码」。
> 表格内嵌图片必须把竖线转义成 `\|`，否则会破坏表格结构。

| **<主题>**<br>MM:SS | **<主题>**<br>MM:SS |
|---|---|
| ![[09 参考资料/附件/L0X 课件 PPT/第N节/PPT p## — <English topic> (第N节 MMSS).jpg\|300]] | ![[…\|300]] |

| **<主题>**<br>MM:SS | **<主题>**<br>MM:SS |
|---|---|
| ![[…\|300]] | ![[…\|300]] |

### 逐段时间线

| 时间戳 | 内容 | 来源 |
|---|---|---|
| 00:00–MM:SS | <课前设备/闲聊，不计入课程内容> | 录像 + PPT 画面 |
| **MM:SS** | **p## <主题>** | PPT p## + 转写 MM:SS |
| MM:SS–MM:SS | <老师口头展开的内容> | 转写 |
| <…> | <…> | <…> |

## 三、第N+1节时间线 Period N+1（HH:MM–HH:MM）

<与第二节同结构：先图片廊，再逐段时间线。>

## 四、课堂口径与课件内容的差异 Differences worth noting

| 课件页 | 课件表述 | 老师课堂上的补充口径 |
|---|---|---|
| p## | <课件原话/要点> | <老师补充的口径、例子、限定> |

## 五、与心理健康、临床或 AI 的关系

- **<主题>（第N节 MM:SS）**：<课堂实际讲了什么；没有讲什么也要写明>
- **临床边界：** <凡涉及临床/诊断的内容，注明本课只给出概念框架，不构成诊断依据>

## 六、中文总结 Chinese synthesis

<按两节顺序写成一段连贯的中文总结，覆盖主线与关键转折。>

**English keywords：** <术语英文列表，逗号分隔>

## 七、主动回忆 Active recall

1. **定义 Define：** <…>
2. **解释 Explain：** <…>
3. **比较 Compare：** <…>
4. **应用 Apply：** <…>
5. **评价 Evaluate：** <…>
6. **综合 Integrate：** <…>

## 八、待核实 / 课堂讨论 To verify

- [ ] 机器转写中的人名与专业术语需回看录像核对。
- [ ] 小组讨论的学生发言是否被清晰收录；未收录则声明本页不记录学生观点。
- [ ] 页码异常页（重复 / 缺失 / 独立章节）的真实页序待用课件原件核对。
- [ ] 课后任务是否在录像尾部口头布置，待回看确认。

## 相关链接 Related notes

- [[02 课堂笔记/L0X — <中文主题> <English Topic>|L0X 课堂笔记]]
- [[01 课程资料/P0X — 课件总览 <中文主题> <English Topic>|P0X 课件总览（逐页图解）]]
- [[09 参考资料/Source Register|资料登记表 Source Register]]
- [[03 核心概念/Concept — <关键概念>|<关键概念>]]
