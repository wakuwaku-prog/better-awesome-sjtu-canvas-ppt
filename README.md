# sjtu-canvas-ppt

提取**上海交通大学课堂实录回放**中的课件、音轨与字幕，供课程笔记、复习或归档使用。

一节完整的课在平台上通常被拆成**两个节次**（例如 `第1周 - 周四 第9节` 15:55、`第1周 - 周四 第10节` 16:50），**两节要一起抓、分别标注**。

> **必经流程（触发本 skill 整理讲次时强制执行，不得跳步）**：
> 登录 Canvas → 教师课件 PDF 归档 → PPT 轨画面核对（帧→PDF 页映射 + 课件外内容排查）
> → 课堂字幕转写（平台 ASR 缺失时本地 whisper 兜底）→ 按库边界归档登记。
> 详细步骤与实测命令见 `SKILL.md` 的「必经流程」一节。

> **opencli 语法（2.0.2 实测）**：`opencli browser <session> <cmd>` 已失效，页面内 JS 用
> `opencli browser --session <session> eval "<js>"`；新建标签页用
> `opencli browser chrome call "tabs.create" '[{...}]'`（参数必须为 JSON 数组）。

## 两条证据链

| | 路径 A：平台 PPT 轨 | 路径 B：从视频重建 |
|---|---|---|
| **适用** | 平台已为该节次生成 PPT 轨（`pptShow = 1`） | 平台没有 PPT 轨（`pptShow = 0`）、`ai/ppt` 返回 `data: null`，或播放器已改版 |
| **做法** | opencli 注入 XHR+fetch 拦截器捕获后端 API，下载 S3 课件图 + 时间码 | 从屏幕录制轨按**解码场景**抽取课件画面，逐页 OCR，页脚页码校验 |
| **产出** | 干净原图 + 精确时间码 | 屏幕截图（1080p）+ 场景时间码 |
| **脚本** | `download_ppt.py` | `extract_ppt_scenes.py` → `ocr_slides.py` → `read_slide_pageno.py` → `dedupe_slides.py` |

**先探测再选路**，不要默认 PPT 轨存在：

```bash
opencli browser <session> eval "fetch('/jy-application-resourcemanage/v1/course/ai/getAiFunctionShow',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({courseId:<ID>})}).then(r=>r.text())"
```

> 实测同一门课不同节次可能不同：某课的 9/15 两节有 PPT 轨，9/17 两节没有。**每节课都要重新探测。**

## 前置条件

1. **已登录 SJTU**：Chrome 中登录 `https://oc.sjtu.edu.cn`（视频空间为 `https://v.sjtu.edu.cn`）。
2. **opencli 可用**：`opencli doctor` 三项全绿；扩展未连接时先 `opencli daemon restart` 再复查。
3. **本机依赖**：`ffmpeg` + `ffprobe`（必需）；`python` + `Pillow`、`numpy`；`faster-whisper`（转写）；`easyocr`（路径 B 的 OCR）。

## 节次与视频直链

从课程页进入「课堂视频新版」LTI 工具，页面跳到 `v.sjtu.edu.cn/.../play-center`，然后：

```bash
# 列课（拿 courseId / letiNumber / 上课时间）
opencli browser sjtu eval "fetch('/jy-application-resourcemanage/v1/subject_vod_list_new?page.pageIndex=1&page.pageSize=100&teclIds=<TECL_ID>&page.orders%5B0%5D.asc=false&page.orders%5B0%5D.field=courBeginTime&schoolOpenStatusFlag=false',{credentials:'include'}).then(r=>r.json()).then(d=>JSON.stringify(d.data.records.map(r=>({courseId:r.id,leti:r.letiNumber,begin:r.courBeginTime}))))"

# 取视频直链（每节 2 路机位）
opencli browser sjtu eval "fetch('/jy-application-resourcemanage/v1/course_vod_urls_new?courseId=<ID>',{credentials:'include'}).then(r=>r.json()).then(d=>JSON.stringify(d.data.courseVodViewList.map(v=>v.url)))"
```

- 每节有 **2 路机位**：`record2`（大，~850 MB）与 `record5`（小，~440 MB），**收音相同、屏幕共享画质同为 1920×1080** → **只下载小文件**。
- 大文件**必须前台下载**：后台任务里 `curl` 会卡在 0 字节（前台约 5 MB/s，440 MB 约 90 秒）。

```bash
curl.exe -L --retry 3 -o "L0X 第N节 stream2 (record5).mp4" "<直链>"
ffmpeg -v error -i "….mp4" -vn -c:a copy "L0X 第N节 原始音轨.aac"
ffmpeg -v error -i "….mp4" -vn -ac 1 -ar 16000 -c:a pcm_s16le "L0X 第N节 16k mono.wav"
```

## ⚠️ 容器时间戳可能不可靠

部分录播有**重复 DTS**，此时 `ffmpeg -ss <T>` **会落到别的画面**（实测偏错约 1 分钟）。
**一律用 `extract_ppt_scenes.py` 的解码时间码定位**，不要用 `-ss`：

```bash
python scripts/extract_ppt_scenes.py "<video>" <outdir> "L0X 第N节" --thresh 0.02
python scripts/ocr_slides.py "<outdir>/L0X 第N节.json"
python scripts/read_slide_pageno.py "<outdir>/L0X 第N节.json"
python scripts/dedupe_slides.py "<outdir>/L0X 第N节_ocr.json" --thresh 0.82
```

`extract_ppt_scenes.py` 跑两遍同一滤镜图（`select='gt(scene,TH)' + showinfo`）：
第一遍记时间戳，第二遍写首帧 → **「第 n 张图 ↔ 第 n 个时间戳」严格对应**。

`dedupe_slides.py` 是**必须的**：场景检测会为 PowerPoint 动画构建也触发切换，同一页会有多帧。

## 转写与说话人

```bash
python scripts/transcribe_class.py "<16k wav>" <outdir> "L0X 第N节 转写" --model large-v3 --chunk 600
python scripts/diarize_transcript.py "<label>.json" --wav "<绝对路径 wav>" --speakers 2
```

## 坑位速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `ai/ppt` 返回 `data: null`，没有 PPT 轨 | 该节次 `pptShow = 0` | 走路径 B |
| `[id^=ppt-horizontal-]` 不存在 | 播放器已换新版 | DOM 回退方案失效，走路径 B |
| `ffmpeg -ss` 抽帧与预期不符 | 容器时间戳重复 DTS | 用解码时间码方案 |
| 课件图比实际页数多很多 | PPT 动画构建帧 | 跑 `dedupe_slides.py` |
| 整帧 OCR 页码与页脚裁切不一致 | 页码数字小易误读 | 以页脚裁切为准，不一致就标「待核实」 |
| 两页页脚数字相同 | 课件页码重复 | 文件名加 `dup`，笔记里标注 |
| 某页没有页脚条 | 课件缺少页脚 | 文件名用 `pNA`，标「页码待核实」 |
| EasyOCR 报 `can't open/read file` | 中文路径，`cv2.imread` 不支持 | 脚本已改用 PIL→ndarray |
| `soundfile.LibsndfileError` | `--wav` 相对路径 + 中文目录 | 传绝对路径 |
| 后台下载卡在 0 字节 | 本环境后台 job 的已知问题 | 改前台下载 |
| 学生发言分不出来 | 课堂单麦混音，小组讨论没进麦 | 笔记只记课件参考答案并标注 |

完整流程、命令细节与耗时参考见 **[docs/WORKFLOW.md](docs/WORKFLOW.md)**。

## 归档命名约定

```
L0X 课件 PPT/第N节/PPT p<页码> — <英文主题> (第N节 MMSS).jpg
                    PPT pNA — <主题> (第N节 MMSS).jpg      # 页脚缺失
                    PPT p##dup — <主题> …                  # 页脚重复
                    素材 — <说明> (MMSS).jpg               # 非课件画面
L0X 课堂视频|音轨|字幕/
```

**文件名不要用方括号 `[ ]`**——会破坏 Obsidian 的 wikilink 与嵌入语法，统一用圆括号。

## 从素材到笔记

抓取只完成前半程。完整整理流程见 **[docs/OBSIDIAN-NOTES.md](docs/OBSIDIAN-NOTES.md)**，
骨架模板见 [`templates/`](templates/)。

一节课产出**三件套**：

| 笔记 | 回答什么 | 图片宽度 |
|---|---|---|
| **P0X 课件总览** | 这一页讲了什么？ | 760，一页一节，含放映时间与页码校验 |
| **V0X 录像时间线** | 什么时候讲的什么？ | 300，2 列图片廊 + 逐段时间线 |
| **L0X 课堂笔记** | 我该记住什么？ | 520–560，按主题重排 + 术语表 + 回忆题 |

三条最容易漏的硬规则：

1. **图片必须内嵌**（`![[…|760]]`），不要只放链接
2. **表格内嵌图片必须转义竖线**（`![[…jpg\|300]]`），否则表格渲染会坏
3. **页码读不出来就标「待核实」**，不要猜（`pNA` / `p##dup` / 独立章节 `C01` 前缀）

收尾自查：

```bash
python scripts/check_vault_links.py "<vault root>"      # wikilink / 嵌入能否解析
python scripts/check_note_structure.py "<vault root>"   # 笔记结构是否符合库约定
```

`check_note_structure.py` 检查 frontmatter、来源位置、图片是否内嵌、表格竖线是否转义、
中文总结、回忆题数量、待核实清单、孤岛页——只检查形状，不检查内容对错。

## License

MIT
