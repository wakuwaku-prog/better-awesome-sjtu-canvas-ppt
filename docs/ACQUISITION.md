# 01 · 采集层（L0 采集 + L1 素材）

把「平台上的一节课」变成 `manifest.json`。**平台差异全部封在 `adapters/` 里，下游只认 manifest。**

```
② 发现   adapters/<platform>.py --list      → 节次清单（人工确认）
③ 采集   adapters/<platform>.py ...         → <work>/raw/（页图、音轨、平台文本）
④ 素材   scripts/build_manifest.py <work>   → <work>/manifest.json   ← 唯一出口
```

契约： [`schemas/manifest.schema.json`](../schemas/manifest.schema.json)。产出**必须**过校验。

---

## 0. 三条只读纪律（违反即停）

1. **不产生学习记录。** 只做**只读 GET**，不播放视频、不触发心跳上报、不点「任务点」。
   超星每节带 `jobid`，播放即留下**不可逆**的完成记录。适配器里**没有任何 POST 代码路径**。
2. **不并发轰炸。** 串行请求 + **200–300 ms** 间隔（超星适配器 `--min-interval-ms` 越界即报错）。
3. **签名 URL / cookie / token 不入档。** 直链只存在于**本次运行内存与临时文件**
   （`<work>/_tmp_urls/`）；`raw/` 与 manifest 落盘前都过凭据扫描，命中即**拒绝写盘**。
   cookie 只从环境变量读，不打印、不落盘、不从浏览器导出。

### 0.1 临时文件里的签名 URL：**无条件清理（硬约束，不是注意事项）**

只要临时文件里**可能**出现签名 URL，就必须**无条件**清理——成功、失败、异常、`--dry-run`
四种情况**都要删**。实现用 `try/finally` 包住整段下载，**不能**只在成功路径上 `rmtree`。

> 实测教训：早期版本只在 `if not dry_run:` 分支里清理，于是 **`--dry-run` 把
> 6 个含签名直链的 `_tmp_urls/batch_*.json` 留在了磁盘上**（`X-Amz-Signature` /
> `key=…` 明文）。这类残留是**长期凭据暴露风险**，不是「小瑕疵」。

**自检必须断言无残留**：清理后再检查一次，仍存在就重试；再失败就**让这次采集直接失败**
（`RuntimeError`），而不是继续——静默留下签名直链比一次采集失败更糟。
自测脚本里也要显式打印/断言 `_tmp_urls/` 无残留。

`<work>/raw/` 下的产物则用 `assert_no_secrets()` 兜底：命中就**拒绝写盘并退出 2**。

---

## 1. `raw/` 输入契约

`adapters/*.py` 写、`build_manifest.py` 读。中间产物**全部落工作目录，不直接进知识库**。

```
raw/
  source.json                    platform / course / capturedAt / platformSubtitles
  sessions/<slug>/session.json   每个平台节次一个目录 —— **分别登记、不合并**
                                 label / lectureId / platformSessionId / letiNumber /
                                 beginTime / classDate / partOfSplit / durationSec /
                                 usedDeck / deckCoverageNote / media / transcript /
                                 platformArtifacts[]
  sessions/<slug>/slides.json    该节次页图（raw 层扁平：startSec / timecodeSource /
                                 rawTimecode / contentType / pageNo* / ...）
  sessions/<slug>/native/        平台原始产物与上游脚本输出（OCR、页脚、快照清单）
  pdf.json                       顶层 pdf 块（pageCount / sha256 / author / createdAt /
                                 isSelection / textExtractionIssues / note）
  pdf/slides.json                PDF 渲染页图（source=pdf-render）
  projections.json               **人工**放映记录表：pdfPage → [projection, ...]
  page_no.json                   **人工**页脚对照表：pdfPage → {expected,fullOcr,footerOcr,
                                 status,note,footerUnit}
  unarchived.json                课堂用到但尚未归档的材料 [{what,evidence,impact}]
  source_register.json           待写入 Source Register 的行（**id 留空**，本 skill 不擅自编号）
  warnings.json                  需要人工判断的事实
```

**两个「人工事实」文件是刻意的设计**：放映时间码与页脚读数**不能猜**。
没有它们时，页图照样产出，但 `projections` 为空（= 未见对应讲解）、页脚判 `missing`。

---

## 2. 路径 A：平台课件轨 —— **实测是屏幕快照轨，不是干净原图**

> 依据：心理与精神健康 L08（2026-10-13）两节次的真实 `平台课件轨清单.json`。

### 2.0 选路：**每节课、每个节次都要重新探测 `pptShow`**

```
python canvas.py --probe-ppt --course <COURSE_ID>       # 只打印页面内 JS，不执行
```

- `pptShow = 1` → **路径 A**（平台课件轨；仍是屏幕快照轨，见 §2.1）
- `pptShow = 0`，或 `ai/ppt` 返回 `data: null` → **路径 B**（从屏幕轨重建：解码场景抽取 + OCR + 去重）

**不能按课程记忆选路**：同一门课不同节次可能不一样——实测 HLT3202G 的 9/15 两节有课件轨，
9/17 两节没有。探测结果只对**当次采集的那个节次**有效。

响应体位置 `data.data.docList[]`，字段
`imageId / imageSeekTime / imageSnapshotTime / imageUrl / ocrText / pptDocId / showFlag / sliceTaskId / taskId / textKeywordList`。

### 2.1 三个必须处理的事实

| # | 事实 | 实测数据 | 处理 |
|---|---|---|---|
| 1 | **冗余极重** | 第15节 68 条 + 第16节 63 条 = **131 条快照**，而课件只有 **35 个 PDF 阅读页**；动画构建帧占绝大多数 | **即使走路径 A 也必须去重**：复用 `dedupe_slides.py`（文本 Jaccard，阈值 **0.82**），**且在下载之前跑**（见 §2.4） |
| 2 | **含教师桌面与应用窗口** | 第15节最早几条 OCR 是教师桌面（回收站 / 东方财富证券 / Chrome / 微信 / VLC）；188 秒起出现 `PowerPoint 文件 开始 插入 绘图 设计 切换…`，即**把 PowerPoint 编辑窗口本身也捕获了** | 按 `ocrText` 分类 → `contentType: "desktop"`，按「素材」命名，**不是课件页** |
| 3 | **`showFlag` 不可用** | 68/68 与 63/63 条**全为 `true`** | **不能**用作「是否放映」的过滤条件；`pptDocId` / `imageId` 也全不重复，没有现成的去重键 |

分类特征词（`canvas.py` 的 `NON_SLIDE_PATTERNS`）：回收站 / 此电脑 / 文件资源管理器 /
`文件 开始 插入`（PowerPoint 功能区）/ Adobe Acrobat / 文件传输助手 / 微信 / Chrome / VLC /
东方财富·证券行情 / `上海交通大学 此电脑`。含「心理学 / PSYCHOLOGY / Chapter / UNIT / Checkpoint」
等**课件保护词**时不判桌面。`ocrText` 为空 → `contentType: "unknown"`（**保守，不猜成 slide**）。

### 2.2 时间码口径（最容易搞错的地方）

| 接口 | 字段 | 单位 | `timecodeSource` |
|---|---|---|---|
| 旧课件轨 | `createSec` | **毫秒** | `platform-createSec-ms` |
| 新课件轨清单 | `imageSeekTime` | **秒**（实测 max 2953 / 3210，两节时长 3308.6 / 3289.0 秒） | `platform-imageSeekTime-sec` |

`startSec` **一律是换算后的秒**，同时用 `timecodeSource` + `rawTimecode` 保留平台原始读数。
`startMmss` 由秒重算（`MM:SS`，分钟补零），**不采信上游给的字符串**——
实测 `extract_ppt_scenes.py` 写出的 `start_mmss` 是 `"0448"` 这种缺冒号的紧凑格式。

### 2.3 下载

调用上游 `download_ppt.py <json_file> <output_dir>`（位置参数，无 argparse）。
**默认 `--download-batch 1`**：上游按 `MM_SS.jpg` 命名，**同一秒内的多张快照会互相覆盖**
（L08 快照密集处一秒多条），所以一次只喂一条、下载后立即改成唯一名
`ppt p### (MMmSSs).jpg` / `素材 — <原因> (MMmSSs).jpg`。

### 2.4 去重：**必须在下载之前跑**，且指标不许撒谎

**顺序是硬要求**：`过滤桌面 → 清单内去重 → 只下载保留帧`。

快照轨的 `records[].ocrText` **本来就在清单里**，不需要先下载图片、也不需要跑 EasyOCR。
去重的全部意义就是**少下几十张冗余帧**；下载完再去重等于白下。
被合并的帧**不下载、不重复归档**：它的 `file` 指向保留帧的图片，`duplicateOf` 指向保留帧的 `index`。
保留帧由上游 `dedupe_slides.py` 取「`lines` 条数最多的一帧」（信息最全的那一帧）。

去重吃的是平台 `ocrText`（合成上游需要的 `_ocr.json` 形状），**不是** `_ocr.json` 的本地 OCR 结果。
该脚本是纯本地文本计算（无网络、无副作用），所以 **dry-run 下也执行**——
否则 dry-run 给不出真实的下载量预估。

#### 指标纪律：**「未执行」绝不能报成「0 条重复」**

一个说 `0` 的指标会被读成「已检查且没问题」，这比不报更危险。去重结果**三态直说**：

| 状态 | 输出 |
|---|---|
| 真跑了、有合并 | `去重已执行（阈值 0.82）：58 条待比较帧 -> 43 个保留帧，合并掉 15 条重复/动画构建帧（duplicateOf 非空 15 条）` |
| 真跑了、没合并 | `去重已执行（阈值 0.82）：比较了 N 条帧，**未发现**相邻近重复帧（相似度均 < 0.82）` |
| 没跑 | `去重未执行（原因：--no-dedupe / 只有 1 条带 ocrText / dedupe_slides.py 未产出 kept 文件）` |

**不允许出现**「重复帧 0」这种不区分「已执行」与「未执行」的写法。

**这条纪律是通用的，不只适用于去重**：任何可能被关掉或被跳过的统计口径，
**都要先报「跑没跑」，再报「跑了多少」**。同类的还有：

- `--no-ocr`：不能报「OCR 读到 0 页页码」，要报 `OCR 未执行（原因：--no-ocr）`。
- 路径 B 未提供 `--video`：不能报「抽帧 0 张」，要报 `未抽帧（原因：未提供 --video）`。
- 未提供 `--page-no-map`：不能报「页脚全部一致」，要报
  `页脚未登记（原因：未提供 --page-no-map），pageNoStatus 判为 missing`。
- 未提供 `--projection-map`：`projections` 为空**不等于**「这节课没讲这些页」，
  而是「未登记放映记录」——两者必须能区分开（前者是结论，后者是缺输入）。

判断标准很简单：**这个 `0` 会不会被读成「已检查且没问题」？会，就不许这么写。**

#### L08 第16节实测（63 条真实快照轨，可复现）

```
快照轨原始记录 63 条 -> 滤桌面后 58 条 -> 去重后 43 个保留帧（合并 15 条）
contentType: slide 58 / desktop 5
desktop 明细: 50:38 聊天工具、50:43 桌面图标、50:48 聊天工具、50:51 桌面图标、53:30 桌面图标
相邻对 difflib ratio >= 0.82 : 18 对
相邻对 token-Jaccard >= 0.82 : 15 对   <- dedupe_slides.py 用的指标
difflib 认为像、Jaccard 认为不像（故未合并）: 3 对
   14:03 vs 14:14  difflib=0.8261  jaccard=0.5000
   14:18 vs 14:22  difflib=0.8261  jaccard=0.5000
   48:28 vs 50:33  difflib=0.8340  jaccard=0.6647
上游连续分组：43 组，组大小分布 {1: 37, 2: 3, 3: 2, 9: 1}
非相邻近重复对（Jaccard>=0.82，上游刻意不合并）: 114 对
合并样例: 380s(index 3) / 386s(index 4) 都并到 386s… -> 保留 index=2 (06:03)
```

**为什么是 43 而不是 35（S84 的页数）——如实说明，不为凑数调参**：

1. 上游 `dedupe_slides.py` **只比「上一组的最后一帧」**，即**只合并相邻近重复**。
   同页的非相邻重放（114 对）**刻意不合并**——那属于「重放」语义，应由
   `projections[].isReplay` 表达，不该在图片层抹掉。
2. 指标差异：上游用 **token-Jaccard**（CJK 的 token 是最大汉字串），
   比 `difflib` 严格；上表 3 对就是差在这里。
3. **快照轨与 PDF 是两条独立证据链**：快照轨含 PowerPoint 导航中的中间态、
   阈值未捕获的动画帧，本来就不必等于 S84 的页数。
   **35 是 S84 的页数，不是标准答案。**


---

## 3. 两条证据链：快照轨 vs PDF 渲染页图

| | 平台快照轨 | PDF 渲染页图 |
|---|---|---|
| `source` | `platform-ppt-track` | `pdf-render` |
| 时间码 | 有（`imageSeekTime`，秒） | **无** |
| 页码 | 靠页脚 OCR | `pdfPage` = **PDF 阅读器页序**，确定 |
| 噪声 | 桌面 / 应用窗口 / 动画重复帧 | 无 |
| 用途 | **课堂讲没讲、什么时候讲** | **这一页是什么** |

- **绝不用快照轨的时间码去覆盖 PDF 页图的时间码。** 两条链在 manifest 里用
  `slides[].source` 区分，**跨链对齐只能由人工 `--projection-map` 显式给出**，脚本不猜。

### 3.1 `--projection-map` 为什么**必须**人工给，不能自动对齐

把 131 条快照映射到 35 个 PDF 阅读页，需要回答「这一帧是哪一页」。
可用的自动线索**全都不可靠**：

| 看似可用的线索 | 为什么不能用 |
|---|---|
| 页脚 OCR 数字 | **不是页码，是小节编号**：p26–p31 与 p33 都是 10、p8/p9/p11 都是 03 → 一个数字对多页 |
| `imageSeekTime` 顺序 | 只给「第几个」，不给「哪一页」；动画帧与重放会打断一一对应 |
| `ocrText` 文本相似 | 只能聚类出「像同一页」，给不出 PDF 阅读页号；且课件有中英混排与版式差异 |
| `showFlag` / `pptDocId` / `imageId` | `showFlag` 恒为 true；`pptDocId` / `imageId` 全不重复，都不是页号 |
| 快照顺序 ↔ PDF 顺序 | 课堂会跳页、回看、重放（p9 两节都放、p29/p31 各重放一次） |

**结论：自动对齐会产出「看起来对、实际错位」的时间码**，比空着更糟——空 `projections`
至少诚实地表达「未见对应讲解」。所以设计上把它交给人工（P08 的放映记录表），
脚本只做机械搬运与校验：

- `projections[]` **可以是 0 条**（= 未见对应讲解，只存在于课件里）；
- 可以是 1 条；也可以多条（跨节次 / 重放，用 `sessionIndex` 与 `isReplay` 区分）：
  同一页可在两个节次都放映（L08 p9：第15节 49:13 + 第16节 00:01），
  也可在同一节次重放（p29：41:12 与 42:49；p31：42:19 与 42:52 → 后者 `isReplay: true`）；
- `timecodeSource` 保留原始口径，`rawTimecode` 保留平台原始读数。
- **空数组 = 未见对应讲解**——这是判断「课件存在 ≠ 课堂讲过」的唯一依据。
- PDF 里的嵌入视频页（L08 p13，第16节 12:51 → 约 15:47，紫色区域是**未导出的视频**）
  用 `contentType: "video"` + `projections[].endSec` 表达。`video` / `activity` 这类判定
  **必须人工标记**（`--mark-content-type 13=video`），脚本不推断。

---

## 4. 页脚：**不是页码，是小节 / 版式编号**

> 依据：L08 的 P08 页脚对照表。

- `pageNoScope: "section"`（L08 实测）。一个小节可跨多页：
  **p26–p31 与 p33 页脚都是 10**；p8/p9/p11 都是 03；p17/p18 都是 06。
  → **页脚跨页重复是正常的**，不构成异常。
- 所以 `verified` **只要求两路读数一致**（整帧 OCR + 页脚裁切放大），不看它是否唯一。
- `anomalous` 只抓**真正离群**的：L08 只有 **p24 页脚读 42**，而该单元其他页是 02–13。
  判定规则：`pageNoScope=section` 时，值**超出同单元其他页的最大编号 +1** 且
  **不被任何同单元页共享**；`pageNoScope=page`（真页码）时才用相邻页单调性。
  ⚠️ 早期版本用「相邻页单调区间」判，会在 **p12（06）与 p33（10）上误报**——
  小节编号会重启（p33=10 而 p32=11、p34=12），单调假设不成立。
- `pageNoStatus` 枚举：`verified | anomalous | mismatch | missing | not-applicable`
  （**没有** `verified-offset` / `duplicate-in-unit`，它们是中途作废的设计）。
- 页脚读数**是字符串**（可能带版式标注：p10 读作「实验心理学 11」、p12 读作「实验心理学 06」），
  另有 `pageNoNormalized`（纯数字）供比较。**带版式标注的页脚来自另一份课件版式，
  不参与跨页比较**，只按「两路一致」判 verified，并把「尚未归一到一个序列」记进 `warnings`。
- `pageNoExpected` **不等于** `pdfPage`（p24 的 expected 是 "42"，pdfPage 是 24）。
- `missing`：无页脚数字（L08 的 p4/p5/p13/p14/p15；p5 底部是「数据来源」行）。
  这些页**必须**有 `pageNoNote`。

---

## 5. 三个适配器的真实 CLI

### `adapters/canvas.py` — SJTU Canvas 课堂回放

```bash
python canvas.py --list --course <COURSE_ID> [--tecl-id <TECL_ID>] [--opencli-session sjtu]
python canvas.py --probe-ppt --course <COURSE_ID>          # 只打印页面内 JS，不执行

# 路径 A：平台课件轨（屏幕快照轨）—— 每个节次跑一次
python canvas.py --course 179741 --session "L08 第15节 1355" --out <work> \
  --ppt-json <captured_ppt.json> --leti 15 --part-of-split 1 \
  --platform-session-id 179741 --class-date 2026-10-13 \
  --used-deck "第9次课.pptx" --deck-coverage-note "0:00–35:07 未使用 S84" \
  --platform-subtitles available --mishearing "declarative→decorative"

# 路径 B：从屏幕轨重建（无课件轨时）
python canvas.py --course <ID> --session "<节次标签>" --out <work> \
  --video "L08 第16节 stream2 (record5).mp4" --audio "<16k mono.wav>"
```

主要参数：`--thresh`(0.02) `--dedupe-thresh`(0.82) `--download-batch`(1) `--chunk`(600)
`--model`(large-v3) `--device`(cuda) `--speakers`(2) `--gpu` `--mishearing`(可重复)
`--mark-content-type N=TYPE` `--footer-unit` `--record-variant` `--media-archived`
`--drop-desktop` `--no-dedupe` `--no-ocr` `--no-transcribe` `--no-diarize` `--no-segments`
`--dry-run` `--force`。

- `--drop-desktop`：**不下载也不登记**教师桌面 / 应用窗口帧（默认是下载并登记为
  `contentType: "desktop"`，留作「当时屏幕上是什么」的素材证据）。
  要留证据就不要加这个开关。
- `--no-dedupe`：跳过去重。输出会明确写 **`去重未执行（原因：--no-dedupe…）`**，
  不会写成「0 条重复」。
- `--dry-run`：不执行**下载 / 转写**子进程，但**仍会**执行本地纯文本去重
  （`dedupe_slides.py`，无网络、无副作用）——否则给不出真实的下载量预估；
  并写出 `session.json` / 快照清单 / `warnings.json`
  （`slides.json` 要等真实下载，因为 `file` 还不确定）。

**本适配器不向平台发任何请求**：平台侧取数由学习者在已登录浏览器里完成
（`--list` 会打印需要执行的 JS，或经 `opencli browser <session> eval` 代跑）。

### `adapters/chaoxing.py` — 超星学习通（**脱课程化**）

`courseid` / `clazzid` / `knowledgeid` **全部参数化**，不硬编码任何课程。

```bash
python chaoxing.py --list --course <courseid> --clazz <clazzid> [--extract-json <inventory.json>]
python chaoxing.py --course <courseid> --clazz <clazzid> --chapter <knowledgeid> \
  --out <work> --extract-json <inventory.json> --label "L1.1 绪论" \
  [--video "<本地 mp4>"] [--audio "<16k mono.wav>"] [--pdf "<课件.pdf>"] \
  [--download --dest <dir>]
```

链路：`mooc2-ans/mycourse/studentcourse` → `toOld()` 的 `knowledgeid` →
`mooc-ans/knowledge/cards?…&num=N` → `ananas/status/<objectid>?k=&flag=normal` →
字幕探测 `mooc-ans/richvideo/allsubtitle`。

- **路径白名单**（`ALLOWED_PATH_PREFIXES`）：只有 `/mooc2-ans/mycourse/studentcourse`、
  `/mooc2-ans/mycourse/stu`、`/mooc-ans/knowledge/cards`、`/ananas/status/`、
  `/mooc-ans/richvideo/allsubtitle` 允许发请求。**白名单比黑名单强**——
  heartbeat / 播放上报 / 进度上报天然不在其中（实测 `/mooc-ans/heartbeat/report`
  与 `/mooc-ans/richvideo/insertlog` 被拒）。
- **无平台字幕**：`richvideo/allsubtitle` 实测返回 `[]` → 转写只能本地 Whisper。
- **无 PPT 轨**：页图只能从视频抽帧 → `source: "keyframe-extract"`，
  复用上游 `extract_ppt_frames.py`（固定间隔采样 + 图像差分分组）。
- 视频直链下载**必须带** `Referer: https://mooc1.chaoxing.com/`。
- `--live` 走平台只读 GET（cookie 从 `--cookie-env` 指定的环境变量读）；
  **未在本机实测**（没有已登录会话），默认走 `--extract-json` 离线清单。

### `adapters/pdf.py` — 只有教师 PDF

```bash
python pdf.py --pdf "<课件.pdf>" --out <work> [--dpi 150] [--no-render] [--is-selection] \
  [--mark-content-type 13=video] [--flag-page 14="文本顺序错乱：两方框标题与正文交错"] \
  [--page-no-map <page_no.json>] [--title-map <titles.json>] [--footer-unit "…"] \
  [--page-no-scope section] [--file-prefix "09 参考资料/附件/L08 课件 PDF"]
```

- `pdfPage` **一律是 PDF 阅读器页序**，不是幻灯片印刷页脚。
- **纯图页判定**（`isPureImagePage`）**不能只看单页字符数**：L08 的 p8/p11/p20 文本是
  60 字符的**页眉页脚**（`KNOWLEDGE REPRESENTATION` / `COGNITIVE PSYCHOLOGY / 认知心理学` / `03`），
  p13 只有标题 `Formation of memory`。正确做法是**先算全文档的「版式家具」**
  （出现在 `max(3, 20% 页数)` 页以上的行 + 纯数字行），去掉后若**没有剩余行**或
  **只剩一行且短于 40 字符** → 纯图页。实测该规则复现了基准的 10 个纯图页
  {p8, p11, p13, p20, p21, p28, p29, p30, p31, p33}，`len(text)<5` 会**全部漏掉**。
- `--is-selection`：这份 PDF 是**选页**而非课件全部。L08 的 S84 就是选页——
  p35 之后课堂还放了两页（第16节 46:41 `Discussion: Would an expert respond differently?`
  与 48:28 其回答页），不在 S84 内。标了它，下游才不会把「PDF 只有 35 页」当成
  「这节课只讲了 35 页」。
- 渲染后端：PyMuPDF（`fitz`）优先，回退 `pdftoppm`（Poppler）。两者本机均可用。

---

## 6. `scripts/build_manifest.py` 的真实 CLI

```bash
python build_manifest.py "<work>" [-o <out>] [--schema <path>] \
  [--projection-map <json>] [--page-no-map <json>] [--footer-unit "…"] \
  [--page-no-scope section|page|none] [--file-prefix "09 参考资料/附件/…"] \
  [--session-order "L08 第15节 1355,L08 第16节 1450"] \
  [--platform canvas|chaoxing|pdf|unknown] [--course-id/--course-name/--course-code] \
  [--no-validate] [--strict-warnings] [--print-summary]
```

- `raw/projections.json` 与 `raw/page_no.json` **存在时自动使用**（不必显式传参）。
  `--projection-map` 的 `session` 字段接受 1-based 序号**或**节次标签，
  也接受直接写 `sessionIndex`。
- `--file-prefix` = 归档目录；页图文件名沿用 `raw/` 里的 basename
  （`raw/pdf-pages/PDF p04.jpg` → `09 参考资料/附件/L08 课件 PDF/PDF p04.jpg`）。
- 节次顺序：`--session-order` 显式指定，否则按 `(partOfSplit, label)`；
  数组下标 +1 就是 `projections[].sessionIndex`。
- `slides[].index` 重排为**连续 1..N**；PDF 渲染页按阅读页在前，其余按 `(sessionIndex, startSec)`。
- 媒体政策：`media.archived=false` 时**清空 `videoPath`** 并记「未归档」——
  绝不写失效链接。
- 凭据扫描命中 → **拒绝写盘，退出 2**。

### 校验（两层，互补）

1. **jsonschema**（本机 4.26.0 可用）对 `schemas/manifest.schema.json` 校验。
2. **内置最小校验始终补跑**——schema **表达不了** `index 必须连续`（只写在 description 里），
   内置校验能抓。两层错误合并；实测 10 个故意违规样例全部被抓
   （非法枚举 / index 不连续 / 缺 `startSec` / `startMmss` 格式错 / 未知字段 /
   `sessions` 空且无 `pdf` / 已作废的 `verified-offset` 与 `per-unit`）。
3. jsonschema 不可用时降级到内置校验，并**明确打印**「已降级」。
4. 退出码：`0` 通过；`1` 校验失败（或 `--strict-warnings` 下 warnings 非空）；`2` 检出凭据；
   `3` 找不到 `raw/`。

---

## 7. 复用的上游脚本（**调用，不复制**）

`CANVAS_PPT_SKILL` 环境变量可覆盖，默认 `~/.dsh/skills/better-awesome-sjtu-canvas-ppt`。
以下 CLI 是**读源码实测**的（`argparse` / `sys.argv`），不是猜测：

| 脚本 | 真实签名 |
|---|---|
| `download_ppt.py` | `<json_file> <output_dir>`（**无 argparse**，`sys.argv`；JSON 形如 `[{"s":毫秒,"u":直链}]`） |
| `extract_ppt_scenes.py` | `<video> <outdir> <label> [--thresh 0.02] [--quality 2]` |
| `extract_ppt_frames.py` | `<video> <outdir> <label> [--interval 2.0] [--thresh 0.02] [--min-dur 4.0] [--width 480]` |
| `ocr_slides.py` | `<manifest> [--outdir DIR] [--gpu] [--lang ch_sim,en]` |
| `read_slide_pageno.py` | `<manifest> [--gpu] [--out FILE] [--bottom 0.10] [--right 0.30]` |
| `dedupe_slides.py` | `<ocr_json> [--thresh 0.80] [--out kept.json]` |
| `scene_times.py` | `<video> [--thresh 0.02] [--out FILE]` |
| `transcribe_class.py` | `<wav> <outdir> <label> [--model large-v3] [--device cuda] [--speakers 2] [--language] [--chunk 600]` |
| `diarize_transcript.py` | `<transcript_json> [--wav WAV] [--speakers 2] [--backend xvector\|mfcc] [--stride 1] [--outdir DIR] [--teacher-label L] [--student-label L]` |
| `check_vault_links.py` / `check_note_structure.py` | `<root> [--min-recall 3] [--quiet]`（质量门层复用） |

**上游产物的已知形状**（`build_manifest.py` 与 `canvas.py` 按此归并）：

- `extract_ppt_scenes.py` → `<label>.json`：`{label, video, thresh, n_slides, slides:[{index,file,start,start_mmss,end}]}`
  ⚠️ `start_mmss` 是 `"%02d%02d"` 紧凑格式（`"0448"`），**不合 schema**，一律由 `start` 重算。
- `ocr_slides.py` → `<label>_ocr.json`：`slides:[{index,file,start,start_mmss,page_guess,text,lines}]`
- `read_slide_pageno.py` → `<label>_pageno.json`：`slides:[{index,file,start_mmss,page,cands}]`
- `dedupe_slides.py` → `<label>_kept.json`：`{label,n_kept,kept:[{index,file,start,start_mmss,page_guess,n_frames_in_group,group_files,title,text}]}`
- `transcribe_class.py` → `<label>.json/.md/.srt`：`{label,wav,duration,model,n_segments,n_turns,segments:[{i,start,end,lang,text,words}],turns}`
- `diarize_transcript.py` → `<label>（说话人）.json`：在上面基础上加 `segments[].speaker` / `speaker_cluster` 与 `diarization`

---

## 8. 素材层（L1）三条硬规则

1. **时间码一律用解码时间码 / 平台时间码换算成秒**，绝不用容器时间戳推算。
   部分录播容器有**重复 DTS**，`ffmpeg -ss <T>` 会落到邻页（实测 HLT3202G 第9节
   `-ss 43:25` 取到相邻页，整体错位约 1 分钟）→ 一律用 `extract_ppt_scenes.py` 的
   「同一滤镜图两遍解码」方案，保证「第 n 张图 ↔ 第 n 个时间戳」严格对应。
2. **转写必须分段**（`--chunk 600`）。整段一次性解码会让 Whisper 漂移成重复文本或错误语言。
   语言按 Unicode 字符集逐段判定 `zh`/`en`/`mixed`，不翻译。
   **说话人标签恒为推断**（`speakerLabelsAreInferred: true`）——引用具体观点前必须核对时间戳；
   课堂单麦混音下小组讨论的学生发言通常分不出来，这种情况写进 `qualityNote`。
   已知误听（L08 实测 `declarative→decorative`、`2,500 objects→22 models`、
   `saccade→second/circuit`、`go cue→GoQ`）登记进 `knownMishearings`，
   **这些词不能作为逐字教师引语**。
3. **`warnings` 逐条都是「需要人工判断」的事实**，不是错误日志。必须清空才算干净交付。
   典型：媒体未归档、课程 id 未确认、`projections` 为空、页脚 anomalous、
   纯图页、带版式标注的页脚尚未归一。

---

## 9. 常见故障

| 症状 | 处理 |
|---|---|
| 平台接口 401 / token 缺失 | 让学习者在浏览器完成登录；**不要试图导出 cookie** |
| 探测到 WAF / 验证码 | 停下，请学习者人工完成，再复查 |
| `ai/ppt` 返回 `data: null` / `pptShow=0` | 该节次没有课件轨 → 走路径 B（`--video`） |
| `ffmpeg -ss` 抽帧与预期不符 | 容器时间戳有重复 DTS → 用 `extract_ppt_scenes.py` 的解码时间码 |
| 课件图比实际页数多很多 | PPT 动画构建帧 → 跑 `dedupe_slides.py`（路径 A 也必须跑） |
| 整帧 OCR 页码与页脚裁切不一致 | 标 `mismatch` + `pageNoNote`，**不猜** |
| EasyOCR 报 `can't open/read file` | 中文路径，`cv2.imread` 不支持 → 上游脚本已改用 PIL→ndarray |
| `soundfile.LibsndfileError` | `--wav` 传**绝对路径**（中文目录 + 相对路径会报 System error） |
| 后台 `curl` 下载卡在 0 字节 | 本环境已知问题 → **前台**下载并给足超时 |
| 两节内容重复/错位 | 把两节当一节处理了 → 每节独立抓取、独立命名、独立登记 |
| `--projection-map` 里的 session 找不到 | 用 `--session-order` 或直接写 `sessionIndex`（1-based） |

---

## 10. 未验证清单（**如实标注，不要当成已实测**）

- `chaoxing.py --live` 的平台响应解析：**未在本机实测**（没有已登录的学习通会话）。
  已实测的只有：路径白名单拒绝越界、限速器边界、离线清单解析、dry-run 编排。
- `canvas.py --list` 的 `opencli browser <session> eval` 调用：命令形式取自
  `better-awesome-sjtu-canvas-ppt/SKILL.md`，**未执行**（无已登录 SJTU 会话）。
- 路径 A 的真实下载（`download_ppt.py` 联网）**未执行**：本机无签名直链。
  已用 `--dry-run` + 手工构造的 `docList` 响应体验证了编排、分类、单位口径与凭据护栏。
- 路径 B 的 `extract_ppt_scenes.py` / `ocr_slides.py` / `read_slide_pageno.py` /
  `transcribe_class.py` / `diarize_transcript.py` **未在本轮执行**（需要真实视频/音轨）。
  只调用了 `dedupe_slides.py` 的接口形状。
- 基准 `docs/pilot/L08.manifest.json` 的 `pdf.createdAt` 是 `2026-10-12T21:29:00+08:00`，
  而 PDF 元数据实际是 `21:29:24`（本适配器保留实际秒数）。**属基准的截断，非适配器偏差。**
