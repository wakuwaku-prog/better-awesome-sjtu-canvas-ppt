---
name: better-awesome-sjtu-canvas-ppt
description: >
  上海交通大学课堂实录回放（Canvas / 视频空间）→ Obsidian 知识库的**前半程**：
  ① 提取课件、音轨、字幕（平台 PPT 轨 / 从视频重建两条证据链）；
  ② 把素材整理成可复习的笔记（知识库画像 → manifest 契约 → L## 课堂笔记 /
  P## 课件逐页索引 / V## 时间线 / Concept 概念页）。
  AI 讲解与课后考题属后半程，见 skill `lecture-to-obsidian`。
  当用户提到"交大Canvas PPT"、"上海交大课程回放"、"SJTU视频课件"、
  "交大视频PPT提取"、"导出PPT"、"下载课件"、"课堂实录"、"回放字幕"、
  "整理到 Obsidian"、"生成课堂笔记"、"课件逐页索引"、"知识库画像"、
  "/ivsModules/index"、"play-center"、sjtu.edu.cn / v.sjtu.edu.cn 回放页面时使用。
  使用前提醒用户：1) 提前在浏览器登录 SJTU（oc.sjtu.edu.cn / v.sjtu.edu.cn）；
  2) opencli doctor 全绿，扩展未连接时先 opencli daemon restart。
---

# SJTU 课堂实录 → Obsidian（提取 + 笔记）

本 skill 负责**前半程**：从课堂实录回放提取**课件图片 / 音轨 / 字幕**（已有能力），
再把素材整理成**能在 Obsidian 里复习的笔记**。后半程——AI 讲解（md 真源 + 单文件 HTML）、
课后考题（三形态同源）、跨层质量门——在同机的 skill `lecture-to-obsidian`。

```
平台回放 ──► adapters/ ──► raw/ ──► build_manifest.py ──► manifest.json   ← 冻结契约
                                                              │
库根 AGENTS.md ──► build_vault_profile.py ──► vault-profile.json ← 冻结契约
                                                              ▼
                                    gen_notes.py ──► L## / P## / V## / Concept
                                                     + 索引 + Source Register 行
```

**两份契约**：`schemas/manifest.schema.json`（素材清单）、`schemas/vault-profile.schema.json`
（知识库画像）。层间只通过这些文件传递，下游（含 `lecture-to-obsidian`）只认契约。

一节完整的课在平台上通常被拆成**两个节次**（例如 `第1周 - 周四 第9节` 15:55、
`第1周 - 周四 第10节` 16:50）。**两节要一起抓，并分别标注，不要合并成一份材料。**

## ⚠️ 开始之前

1. **登录 SJTU**：Chrome 中访问 `https://oc.sjtu.edu.cn` 并用 jAccount 登录。
   未登录会让 API 返回 `token 缺失`。视频空间在 `https://v.sjtu.edu.cn`。
2. **确认 opencli 可用**：`opencli doctor` 应显示 Daemon + Extension + Connectivity 全绿。
   扩展未连接时先 `opencli daemon restart`，再 `opencli doctor` 复查（扩展通常会自动重连，等几秒）。
3. **本机依赖**：`ffmpeg` / `ffprobe`（必需）、`python` + `Pillow`（抽帧分组）、
   `faster-whisper`（转写）、`easyocr`（OCR，可选，仅第二条路径需要）。

## 选择路径：先看平台有没有 PPT 轨

**不要默认 PPT 轨一定存在。** 先探测：

```bash
opencli browser <session> eval "fetch('/jy-application-resourcemanage/v1/course/ai/getAiFunctionShow',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({courseId:<ID>})}).then(r=>r.text())"
```

- 返回 `"pptShow": 1` → 走 **路径 A：平台 PPT 轨**（最省事，图片是干净的原图）
- 返回 `"pptShow": 0`，或 `/v1/course/ai/ppt?courseId=<ID>` 返回 `data: null` → 走 **路径 B：从视频重建**

> 实测：同一门课不同节次可能不一样。HLT3202G 的 9/15 两节有 PPT 轨（`pptShow` 有效），
> 9/17 两节没有（`pptShow = 0`）。**每节课都要重新探测。**

---

## 第 0 步（两条路径共用）：定位课程与节次

如果用户已经打开回放页，直接 `opencli browser sjtu bind` 接管。

否则从课程页进入「课堂视频新版」LTI 工具（本课的 external tool id 为 8329）：

```bash
# 1. 找到课程 id
opencli browser sjtu eval "fetch('/api/v1/courses?per_page=50',{credentials:'include'}).then(r=>r.json()).then(d=>JSON.stringify(d.map(c=>({id:c.id,name:c.name}))))"

# 2. 打开课堂视频
opencli browser sjtu open "https://oc.sjtu.edu.cn/courses/<COURSE_ID>/external_tools/8329?display=borderless"
```

页面会跳到 `https://v.sjtu.edu.cn/jy-application-resourcemanage-ui/#/play-center?<token>`。
在页面内拿到 `teclId` 与节次表：

```bash
opencli browser sjtu eval "fetch('/jy-application-resourcemanage/v1/subject_vod_list_new?page.pageIndex=1&page.pageSize=100&teclIds=<TECL_ID>&page.orders%5B0%5D.asc=false&page.orders%5B0%5D.field=courBeginTime&schoolOpenStatusFlag=false',{credentials:'include'}).then(r=>r.json()).then(d=>JSON.stringify(d.data.records.map(r=>({courseId:r.id,leti:r.letiNumber,begin:r.courBeginTime}))))"
```

`teclId` 可以从 `performance.getEntriesByType('resource')` 里的
`/v1/teachingClass/<id>/t-1` 找到，或从 `list-item` 文本与课表对照。
`records[].id` 就是每节课的 **`courseId`**，后面所有接口都用它。

> `letiNumber` = 节次号（第7/8/9/10节），`begin` = 上课时间。用这两个字段确认抓的是哪一节。

---

## 路径 A：平台 PPT 轨

### A1. 注入双重拦截器

API 端点需要页面内鉴权，无法从外部调用。**同时劫持 `XMLHttpRequest` 和 `fetch`**，
不同课程可能用不同请求方式。在页面打开后**立即**注入：

```javascript
window.__pptCapture = [];
var origOpen = XMLHttpRequest.prototype.open, origSend = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open = function(method, url) { this.__pptUrl = url; return origOpen.apply(this, arguments); };
XMLHttpRequest.prototype.send = function() {
  var self = this;
  this.addEventListener('load', function() {
    if (self.__pptUrl && self.__pptUrl.indexOf('ppt') > -1) window.__pptCapture.push({ url: self.__pptUrl, body: self.responseText });
  });
  return origSend.apply(this, arguments);
};
var origFetch = window.fetch;
window.fetch = function(input, init) {
  var url = typeof input === 'string' ? input : (input && input.url ? input.url : '');
  return origFetch.apply(this, arguments).then(function(resp) {
    if (url.indexOf('ppt') > -1) { var c = resp.clone(); c.text().then(function(b){ window.__pptCapture.push({ url: url, body: b }); }); }
    return resp;
  });
};
```

### A2. 切换节次并验证

**不能**直接 click 外层 `list-item` div（Vue 事件委托在子元素上）。
新版页面里节次是纯文本行，用文本匹配点击；旧版 `ivsModules` 页面用
`document.querySelector('#list-item-<N> .title').click()`：

```bash
opencli browser sjtu eval "(()=>{var e=Array.from(document.querySelectorAll('*')).filter(x=>x.children.length===0&&/第1周 - 周四 第9节/.test(x.textContent.trim()));if(!e.length)return 'notfound';e[0].click();return 'clicked'})()"
opencli browser sjtu wait time 6
```

**必须验证**切换成功再继续：

```bash
opencli browser sjtu eval "JSON.stringify({url:location.href,captured:(window.__pptCapture||[]).length,slides:document.querySelectorAll('[id^=ppt-horizontal-]').length,head:document.body.innerText.slice(0,200)})"
```

### A3. 提取数据（textarea 中转）

`eval` 直接返回大字符串会让 opencli 崩溃。写 textarea → `get value` 读回：

```javascript
var body = JSON.parse(window.__pptCapture[N].body), slides = [];
for (var i = 0; i < body.data.length; i++) slides.push({ s: body.data[i].createSec, u: body.data[i].pptImgUrl });
var ta = document.getElementById('__pptDataDump') || document.body.appendChild(Object.assign(document.createElement('textarea'), {id:'__pptDataDump'}));
ta.value = JSON.stringify(slides);
```

```bash
opencli browser sjtu get value "#__pptDataDump"    # 存成 ppt_L<N>.json
python scripts/download_ppt.py ppt_L<N>.json <输出目录>
```

`createSec` 是毫秒（`"343551"` = 5:43.551）→ `int(ms)//1000` 得到秒。
`pptImgUrl` 是公开 S3 直链，**无需额外鉴权**；去掉 `:443` 端口后下载，失败再回退原 URL。

### A4. 换取视频直链（两条路径都需要）

```bash
opencli browser sjtu eval "fetch('/jy-application-resourcemanage/v1/course_vod_urls_new?courseId=<ID>',{credentials:'include'}).then(r=>r.json()).then(d=>JSON.stringify(d.data.courseVodViewList.map(v=>v.url)))"
```

每节课有 **2 路机位**：`record2`（大，~850 MB）与 `record5`（小，~440 MB），**课堂收音完全相同**。
屏幕共享画质两路都是 1920×1080，所以**只下载小文件**即可。

> ⚠️ 签名 URL 的 `key=` 里带过期时间（约 1 小时），但实测**过期后仍能下载**。
> 无论如何拿到后立即下载，失败再回页面接口重新取。

---

## 路径 B：平台没有 PPT 轨 → 从视频重建

这是本 skill 相对旧版的**主要新增能力**。触发条件：`pptShow = 0`、
`ai/ppt` 返回 `data: null`，或播放器已改版（新版页面里
`pptHorizontalWrapper`、`[id^=ppt-horizontal-]` **根本不存在**，DOM 回退方案也失效）。

### B0. 下载屏幕轨

```bash
curl.exe -L --retry 3 -o "L0X 第N节 stream2 (record5).mp4" "<直链>"
ffmpeg -v error -i "….mp4" -vn -c:a copy "L0X 第N节 原始音轨.aac"
ffmpeg -v error -i "….mp4" -vn -ac 1 -ar 16000 -c:a pcm_s16le "L0X 第N节 16k mono.wav"
```

**不要用后台任务下载大文件**：本环境里 `curl` 在 background job 中会卡在 0 字节（前台正常，~5 MB/s）。
400+ MB 的前台调用记得给足超时（本轮实测约 90 秒）。

### B1. ⚠️ 先检查容器时间戳是否可靠

部分录播的容器时间戳有**重复 DTS**，此时 `ffmpeg -ss <T>` **会落到别的画面**——
实测 HLT3202G 第9节 `-ss 43:25` 取到的是相邻页，与图像阈值分组的结果整体错位约 1 分钟。

**判定方法**：抽一帧已知内容，看 OCR 是否与预期一致：

```bash
ffmpeg -v error -ss <T> -i "<video>" -frames:v 1 -y probe.jpg
python scripts/ocr_slides.py --single probe.jpg    # 或直接看画面
```

**结论：一律用脚本 B2 的「解码时间码」方案，不要用 `-ss` 定位课件画面。**

### B2. 场景抽取（推荐方案）

`scripts/extract_ppt_scenes.py` 不解码定位，而是：

1. 第一遍 `select='gt(scene,TH)' + showinfo` 记录**每个场景切换的解码时间戳**；
2. 第二遍用**同一滤镜图**把每个场景首帧写成 `slide_###.jpg`。

两遍走同样的解码顺序，所以「第 n 张图 ↔ 第 n 个时间戳」严格对应，不受容器时间戳影响。

```bash
python scripts/extract_ppt_scenes.py "<video>" <outdir> "L0X 第N节" --thresh 0.02
# -> outdir/slide_001.jpg … + outdir/L0X 第N节.json (含 start / start_mmss)
```

> `--thresh 0.02` 对「PPT 白底深字」效果好。阈值越低场景越多（含 PowerPoint 动画构建帧）。
> 备选：`extract_ppt_frames.py`（固定间隔采样 + 图像差分分组），只在场景检测不可用时使用。

### B3. 逐页 OCR + 页码读取

```bash
python scripts/ocr_slides.py "<manifest.json>"                 # 逐页文字（ch_sim+en）
python scripts/read_slide_pageno.py "<manifest.json>"          # 页脚页码（裁切+放大+数字白名单）
```

`ocr_slides.py` 会把 EasyOCR 读到的文字连同归一化坐标写进 `_ocr.json` / `_ocr.md`，
并按「底部 12% 区域的最后一个纯数字」给出 `page_guess`。
`read_slide_pageno.py` 专门裁**右下角页脚条**（默认底部 10% × 右侧 30%），
以 3 倍放大 + `allowlist="0123456789"` 重读，比整帧 OCR 可靠得多。

> ⚠️ EasyOCR 底层 `cv2.imread` **无法读取非 ASCII 路径**（中文目录会报
> `can't open/read file`）。两个脚本都改为先把 PIL 解码成 ndarray 再交给 EasyOCR，绕开该问题。

### B4. 去重（必须）

场景检测会为 **PowerPoint 动画构建**也触发一次切换，所以同一页会有多帧。
`scripts/dedupe_slides.py` 按文本 Jaccard 相似度合并连续近重复帧，**保留信息最全的一帧**：

```bash
python scripts/dedupe_slides.py "<label>_ocr.json" --thresh 0.82
# -> <label>_kept.json，并打印每页的保留帧、时间码、页码、标题
```

> 只按 OCR 文本去重会漏掉「同一页的动画推进」（后一帧文字更多、相似度高但不等同），
> 所以脚本用 0.82 的较高阈值 + 「组内取行数最多的一帧」。

### B5. 两路页码交叉校验

**页码必须两路校验后才能写进笔记**：
`page_full_ocr`（整帧）与 `page_cut_ocr`（页脚裁切）。一致才采信；不一致就用
`verify_pageno.py` 式的多参数重试，或改用**页脚字形互相关**判断两页是否同一页码。

```bash
# 页脚字形比较：相关 ≈1.00 说明两页打印的是同一个数字（用于识别重复页码）
python scripts/compare_footer.py <img1> <img2> <img3> …
```

> 实测收获：课件页脚可能**重复**（本课「俄国客观心理学」页脚也是 35，与 p35 冲突）、
> 也可能**缺失**（「巴甫洛夫经典条件作用」页没有页脚条）→ 这类页在笔记里必须标「页码待核实」。

### B6. 归档命名

```
09 参考资料/附件/L0X 课件 PPT/第N节/PPT p<页码> — <英文主题> (第N节 MMSS).jpg
                                   素材 — <说明> (MMSS).jpg          # 非课件画面
                                    PPT pNA — <主题> (第N节 MMSS).jpg  # 页脚缺失
                                    PPT p##dup — <主题> …              # 页脚重复
```

**文件名不要使用方括号 `[ ]`**：会破坏 Obsidian 的 wikilink 与嵌入语法，统一用圆括号。

### B7. 转写 + 说话人分离

```bash
python scripts/transcribe_class.py "<16k wav>" <outdir> "L0X 第N节 转写" --model large-v3 --chunk 600
python scripts/diarize_transcript.py "<label>.json" --wav "<绝对路径 wav>" --speakers 2
```

- **必须分段转写**（`--chunk 600`）。整段 55 分钟一次性解码会让 Whisper 漂移成重复文本或错误语言。
- 语言按 Unicode 字符集逐段判定为 `zh` / `en` / `mixed`，不做翻译，中英混讲保留原样。
- 说话人用 `microsoft/wavlm-base-plus-sv` 嵌入 + 余弦层次聚类，由「累计发言时长最长者」推断为老师。
- **说话人标签是推断**：引用具体观点前必须核对时间戳，笔记中标注这是自动分离结果。
- ⚠️ `diarize_transcript.py` 的 `--wav` 若给相对路径，在中文目录下 `soundfile` 会报
  `System error` → **传绝对路径**。
- ⚠️ 课堂单麦混音下，**小组讨论阶段的学生发言通常分不出来**（本轮两节各只分出 1–2 段噪声类）。
  这种情况笔记里只记课件参考答案，明确标注「学生发言未被清晰收录」。

### B8. 平台自带 AI 材料（交叉校验用）

平台通常已生成 AI 概要与语音转写，可作**交叉校验**（不要直接引用，中英混排识别质量差）：

```bash
# 知识点思维导图 + 分段概要
opencli browser sjtu eval "fetch('/jy-application-resourcemanage/v1/course/ai/textSummary/<ID>',{credentials:'include'}).then(r=>r.text())"
# 逐句 ASR（含 bg/ed 毫秒时间码）
opencli browser sjtu eval "fetch('/jy-application-resourcemanage/v1/course/ai/translate/<ID>?useOriginal=true',{credentials:'include'}).then(r=>r.text())"
```

两者都很大（150–190 KB），**必须走 textarea 中转**。

---

## 关键坑位速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `ai/ppt` 返回 `data: null`，播放器没有 PPT 轨 | 该节次 `pptShow = 0` | 走路径 B，从屏幕轨抽取 |
| `[id^=ppt-horizontal-]`、`pptHorizontalWrapper` 都不存在 | 播放器已换新版 | DOM 回退方案失效，走路径 B |
| `ffmpeg -ss <T>` 抽帧与预期不符 | 容器时间戳有重复 DTS | 用 `extract_ppt_scenes.py` 的解码时间码 |
| 课件图比实际页数多很多 | PPT 动画构建帧 | 跑 `dedupe_slides.py` |
| 整帧 OCR 页码与页脚裁切不一致 | 页码数字小、易被误读 | 以页脚裁切为准；不一致就标「待核实」 |
| EasyOCR 报 `can't open/read file` | 中文路径，`cv2.imread` 不支持 | 脚本已改用 PIL→ndarray |
| `soundfile.LibsndfileError: System error` | `--wav` 是相对路径 + 中文目录 | 传绝对路径 |
| 后台 `curl` 下载卡在 0 字节 | 本环境 background job 下的已知问题 | 改前台下载并给足超时 |
| 两节内容重复/错位 | 把两节当一节处理 | 每节独立抓取、独立命名、独立转写 |

## 从素材到笔记（Obsidian 整理）

抓取只完成前半程。这一段把「素材已落在附件目录」变成「库里出现能用的笔记」。
**完整规范见 [`docs/OBSIDIAN-NOTES.md`](docs/OBSIDIAN-NOTES.md)**；素材清单契约见
[`schemas/manifest.schema.json`](schemas/manifest.schema.json)，画像契约见
[`schemas/vault-profile.schema.json`](schemas/vault-profile.schema.json)。

### 第 ① 步（永远第一步）：知识库画像

```bash
python scripts/build_vault_profile.py "<vault root>" \
       -o "<vault root>/_scripts/vault-profile.json" --validate
```

画像把目标库的差异（frontmatter 键名、必填字段、图片宽度、callout 形式、目录映射、
Git 严格度、讲解/题库落点）变成一份 JSON。**画像没生成成功就不要往下走。**
产出恒为 `reviewedByHuman: false`——人工复核前 `gen_notes.py --apply` 会自动降级为 dry-run。

### 第 ② 步：素材清单

```bash
python scripts/build_manifest.py "<work dir>"        # raw/ → manifest.json
```

`manifest.json` 是采集层的**唯一出口**，下游只认它。raw/ 契约见
[`docs/ACQUISITION.md`](docs/ACQUISITION.md)。

### 第 ③ 步：生成笔记

```bash
python scripts/gen_notes.py --manifest <manifest.json> --vault "<vault root>" \
       [--profile <画像>] [--out-dir <tmp>] [--with-timeline] [--with-concept TERM]
# 默认 dry-run；确认形状后加 --apply（会先跑 Git 门）
```

| 笔记 | 回答什么 | 默认建 |
|---|---|---|
| **P0X 课件总览** | 这一页讲了什么？ | 是（一页一节，含放映时间与页码校验） |
| **L0X 课堂笔记** | 我该记住什么？ | 是（按主题重排，含回忆题、待核实） |
| **V0X 录像时间线** | 什么时候讲的什么？ | **否**（只有 `--with-timeline` 才建） |
| **Concept — 术语** | 这个概念是什么？ | 按需（`--with-concept TERM`） |

> **V## 默认不建**：原版主张每课都建三件套，但心理库 `AGENTS.md` §9 明确
> 「`V##` 仅在需要回放复核时建立，不把时间线当默认学习主线」。三库实测都要求按需。

### 笔记层的五条硬规则

1. **页图块必须展开**：用 `[!example]+`，**永远不要 `[!example]-`**——
   折叠会让图默认不可见（有机化学库实测 72/72 篇打开时可见图数为 0）。
2. **表格内嵌图必须转义竖线**：写成 `![[…jpg\|300]]`，否则表格结构会被破坏。
   > 这一条很容易漏——已生成的 `V01` 就有 17 行表格图片未转义，表格渲染是坏的，
   > 后来用 `scripts/check_note_structure.py` 查出来才修好。
3. **默认内嵌页图，不用 `#page=`**：心理库实测该链接点击无效（96 处文本链接不在此列）。
4. **页码读不出来就标「待核实」**，文件名用 `pNA` / `p##dup` / `C01`，不猜。
5. **页脚不一定是页码**：心理库 L08 的 p26–p31 与 p33 页脚都是 10（那是小节号）。
   引用一律以 **PDF 阅读页**为准；`pageNoScope` 记录到底是 `section` 还是 `page`。

### 证据分层：以目标库为准

原版给的是三段式：

```
**资料事实 Source facts：**        ← 只写课件真实出现的文字/图示/页脚结论
**课堂补充 Teacher's additions（第N节 MM:SS）：**   ← 老师口头内容，带时间码
**我的理解 My interpretation：**   ← 自己的推断，明确标为理解
```

**但不是所有库都这么写。** 心理库 `AGENTS.md` §4 要的是六类标记：
`[PPT原文]` `[课堂口述]` `[机器转写]` `[我的推断]` `[背景补充，非课件原文]` `[待核实]`。
**以画像 `evidenceTags` 为准**；抽不到时回调三段式并写 warning。

### 归档与登记

- 课件图片：`09 参考资料/附件/L0X 课件 PPT/第N节/`
- 视频 / 音轨 / 字幕：`09 参考资料/附件/L0X 课堂视频|音轨|字幕/`
- 大体积 mp4/wav **加进 `.gitignore`**（GitHub 单文件 100 MB 上限，且可由平台重新获取）；
  转写文本与课件 jpg 属于知识库内容，照常入库。
- 提供 **provenance 文件**：场景抽取清单（时间码 + 页码读数）与画面 OCR 文本一并归档。
- 登录 `09 参考资料/Source Register.md`（每类材料一行，**页码异常也要登记**）。
- 文件名**不要用方括号 `[ ]`**——会破坏 Obsidian 的 wikilink 与嵌入语法，统一用圆括号。

### 收尾自查

```bash
python scripts/check_vault_links.py "<vault root>"       # wikilink / 嵌入能否解析
python scripts/check_note_structure.py "<vault root>"    # 笔记结构是否符合库约定
python scripts/check_note_frontmatter.py --profile <画像> --vault "<vault root>"  # 画像驱动必填与结构
python scripts/check_vault_profile.py <画像>             # 画像声明的路径是否真实存在
python scripts/check_manifest.py <manifest.json>         # 素材清单自洽
```

> ⚠️ `check_vault_links.py` 与 `check_note_structure.py` **永远 `exit 0`**，只能解析 stdout；
> 前者对**不存在的库路径**会静默报 OK（`checked 0` + `OK: every wikilink…`），
> 后者把 frontmatter 字段名硬编码为 `type`（用 `note_type` 的库会全库误报）。
> 真正的必填校验靠**画像驱动**的 `check_note_frontmatter.py`。

### 下一步

AI 讲解（md 真源 + 单文件 HTML）与课后考题（对话卷子 / md 折叠答案 / 题库 JSON v1）
属 skill `lecture-to-obsidian`——它读本 skill 产出的 `manifest.json` 与
`vault-profile.json`，不重复采集与笔记逻辑。

## 脚本清单

| 脚本 | 用途 |
|---|---|
| `download_ppt.py` | 路径 A：按 slide JSON 下载平台 PPT 图，毫秒时间码转 `MMSS` |
| `extract_ppt_scenes.py` | **路径 B 主方案**：解码精确的场景抽取（图↔时间戳严格对应） |
| `extract_ppt_frames.py` | 备选：固定间隔采样 + 图像差分分组 |
| `ocr_slides.py` | EasyOCR 逐页文字 + 页脚页码初判（`_ocr.json` / `_ocr.md`） |
| `read_slide_pageno.py` | 页脚条裁切 + 放大 + 数字白名单，专读课件页码 |
| `dedupe_slides.py` | 按文本相似度合并动画帧，保留信息最全的一帧 |
| `scene_times.py` | 输出场景切换时间码与分数（诊断用） |
| `transcribe_class.py` | 分段转写 + 逐段判定 zh/en/mixed（`.md/.json/.srt`） |
| `diarize_transcript.py` | WavLM 声纹嵌入 + 聚类，输出说话人版本 |
| `check_vault_links.py` | 校验 Obsidian 库中 wikilink 与 `![[…]]` 嵌入是否都能解析 |
| `check_note_structure.py` | 校验笔记结构是否符合库约定（frontmatter / 内嵌 / 转义 / 回忆题 / 待核实） |

适配器在 [`adapters/`](adapters/)：`canvas.py`（Canvas 双路径）、`chaoxing.py`（超星学习通）、
`pdf.py`（教师 PDF 渲染页图）、`_common.py`（三方共用的凭据护栏 / 契约枚举 / 原子写 / 下载器）。

### 笔记层（本 skill 的后半程）

| 脚本 | 用途 |
|---|---|
| `build_vault_profile.py` | 读库根 `AGENTS.md` + 扫目录实况 → `vault-profile.json`（画像契约） |
| `build_manifest.py` | `raw/`（采集产物）→ `manifest.json`（素材清单契约），两层校验 |
| `gen_notes.py` | manifest + 画像 → L## / P## / V## / Concept + 索引 + Source Register 待插入行 |
| `check_manifest.py` | 校验 manifest 自洽性（页序、时间码、页码三态、两条证据链不猜对齐） |
| `check_note_frontmatter.py` | **画像驱动**的必填字段与结构要求检查（推断值只报 WARN） |
| `check_vault_profile.py` | 校验画像声明的路径真实存在 |

## 耗时参考（RTX 4060）

| 步骤 | 55 分钟一节的耗时 |
|---|---|
| 下载屏幕轨（440 MB） | ~90 秒 |
| 场景抽取（两遍解码） | ~2–4 分钟 |
| 逐页 OCR（100 帧左右） | ~4–6 分钟（CPU EasyOCR） |
| 页脚页码 OCR | ~2–3 分钟 |
| Whisper `large-v3` 转写（600 秒分段） | ~8–10 分钟 |
| 说话人分离 | ~3–5 分钟 |
