# 完整工作流程

## 0. 定位课程与节次

**如果用户已经打开回放页**：直接 `opencli browser sjtu bind` 接管（注意 `bind` 时的当前节次不会重新触发 API）。

**否则**从课程页进入「课堂视频新版」（LTI external tool）：

```bash
# 找课程 id
opencli browser sjtu eval "fetch('/api/v1/courses?per_page=50',{credentials:'include'}).then(r=>r.json()).then(d=>JSON.stringify(d.map(c=>({id:c.id,name:c.name}))))"

# 打开课堂视频（external_tools/8329 是「课堂视频新版」）
opencli browser sjtu open "https://oc.sjtu.edu.cn/courses/<COURSE_ID>/external_tools/8329?display=borderless"
```

页面会跳转到 `https://v.sjtu.edu.cn/jy-application-resourcemanage-ui/#/play-center?<token>`。

### 拿 teclId

从已加载的资源里找：

```bash
opencli browser sjtu eval "JSON.stringify(performance.getEntriesByType('resource').map(r=>r.name).filter(n=>/teachingClass|subject_vod_list/.test(n)))"
```

`/v1/teachingClass/<teclId>/t-1` 里的数字就是 `teclId`。

### 拿节次表

```bash
opencli browser sjtu eval "fetch('/jy-application-resourcemanage/v1/subject_vod_list_new?page.pageIndex=1&page.pageSize=100&teclIds=<TECL_ID>&page.orders%5B0%5D.asc=false&page.orders%5B0%5D.field=courBeginTime&schoolOpenStatusFlag=false',{credentials:'include'}).then(r=>r.json()).then(d=>JSON.stringify(d.data.records.map(r=>({courseId:r.id,leti:r.letiNumber,begin:r.courBeginTime,end:r.courEndTime}))))"
```

`records[].id` = 每节的 **`courseId`**（后续所有接口都用它）。

### 大响应走 textarea

平台接口的响应经常有 150–190 KB，`eval` 直接返回长字符串会让 opencli 崩溃。统一用：

```javascript
var ta = document.getElementById('__d') || document.body.appendChild(Object.assign(document.createElement('textarea'), {id:'__d'}));
ta.value = <字符串>;
```

```bash
opencli browser sjtu get value "#__d"
```

> `get value` 的输出带 BOM，落盘后用 `encoding='utf-8-sig'` 读回。

---

## 1. 探测是否有 PPT 轨

```bash
opencli browser sjtu eval "fetch('/jy-application-resourcemanage/v1/course/ai/getAiFunctionShow',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({courseId:<ID>})}).then(r=>r.text())"
```

返回 `{"pptShow":1}` → 路径 A；`{"pptShow":0}` → 路径 B。

也可直接试 `/v1/course/ai/ppt?courseId=<ID>`：返回 `data: null` 就是没有。
注意 `{teclId, courseId, keyword}` 形式的 `/v1/course/ai/summary/ppt/search` **不能**当作 PPT 轨的替代——
它只返回该关键词命中的少数页，`pptHitCount: 0` 表示这一节根本没建索引。

---

## 2A. 路径 A：平台 PPT 轨

### 注入双重拦截器

**同时劫持 `XMLHttpRequest` 和 `fetch`**。在页面打开后**立即**注入：

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

### 切换节次

**不能**直接 click 外层 `list-item` div（Vue 事件委托在子元素上）。

新版页面节次是纯文本行，用文本匹配：

```bash
opencli browser sjtu eval "(()=>{var e=Array.from(document.querySelectorAll('*')).filter(x=>x.children.length===0&&/第1周 - 周四 第9节/.test(x.textContent.trim()));if(!e.length)return 'notfound';e[0].click();return 'clicked'})()"
opencli browser sjtu wait time 6
```

旧版 `ivsModules` 页面用 `document.querySelector('#list-item-<N> .title').click()`。

**必须验证**再继续：

```bash
opencli browser sjtu eval "JSON.stringify({url:location.href,captured:(window.__pptCapture||[]).length,slides:document.querySelectorAll('[id^=ppt-horizontal-]').length,head:document.body.innerText.slice(0,200)})"
```

### 提取与下载

```javascript
var body = JSON.parse(window.__pptCapture[N].body), slides = [];
for (var i = 0; i < body.data.length; i++) slides.push({ s: body.data[i].createSec, u: body.data[i].pptImgUrl });
document.getElementById('__pptDataDump').value = JSON.stringify(slides);
```

```bash
opencli browser sjtu get value "#__pptDataDump"     # 存成 ppt_L<N>.json
python scripts/download_ppt.py ppt_L<N>.json <输出目录>
```

- `createSec` 是毫秒（`"343551"` = 5:43.551）→ `int(ms)//1000` 得到秒。
- `pptImgUrl` 是公开 S3 直链，无需额外鉴权；去掉 `:443` 端口后下载，失败再回退原 URL。

---

## 2B. 路径 B：从视频重建

### 为什么不用 DOM 回退

新版播放器里 `pptHorizontalWrapper`、`[id^=ppt-horizontal-]` **根本不存在**，旧的「滚动触发懒加载」方案无效。

### 判定容器时间戳是否可靠

```bash
ffmpeg -v error -ss <已知内容的时刻> -i "<video>" -frames:v 1 -y probe.jpg
```

如果抽出的画面与预期不符 → 时间戳不可靠，必须用解码时间码方案。

### 场景抽取（主方案）

```bash
python scripts/extract_ppt_scenes.py "<video>" <outdir> "L0X 第N节" --thresh 0.02
```

- 第一遍：`select='gt(scene,TH)',showinfo` → 记录每个场景切换的 `pts_time`
- 第二遍：同一滤镜图 + `-vsync 0 -q:v 2` → 每个场景首帧写成 `slide_###.jpg`
- 输出 `L0X 第N节.json`：每页的 `file` / `start` / `start_mmss`

阈值参考：PPT 白底深字用 `0.02`；片头片尾等大面积变化场景也可用 `0.02`。
阈值越低场景越多（含动画构建帧，靠下一步去重）。

备选 `extract_ppt_frames.py`：固定间隔采样 + 缩略图差分分组（`--interval 2 --thresh 0.012 --min-dur 3`），
只在场景检测不可用时使用；它对「时间戳可靠」的录播更省算力。

### OCR 与页码

```bash
python scripts/ocr_slides.py "<manifest.json>"          # -> _ocr.json / _ocr.md
python scripts/read_slide_pageno.py "<manifest.json>"   # -> _pageno.json
```

- `ocr_slides.py`：EasyOCR（`ch_sim,en`），把文字连同归一化坐标（`top`/`left`）与置信度写入 `_ocr.json`；
  `page_guess` = 底部 12% 区域内最后一个高置信纯数字。
- `read_slide_pageno.py`：裁**右下角页脚条**（默认底部 10% × 右侧 30%），3 倍放大 + `allowlist="0123456789"` 重读。
  比整帧 OCR 可靠得多。

**两路读数必须一致才采信。** 不一致时：

```bash
# 试多组裁切参数，看读数是否稳定
python - <<'PY'
import numpy as np, easyocr
from PIL import Image
r = easyocr.Reader(['en'], gpu=False, verbose=False)
for bottom, right, scale in [(0.10,0.30,3),(0.08,0.20,5),(0.12,0.40,4),(0.06,0.15,6)]:
    im = Image.open(PATH); W,H = im.size
    crop = im.crop((int(W*(1-right)), int(H*(1-bottom)), W, H)).convert('L')
    crop = crop.resize((crop.width*scale, crop.height*scale), Image.LANCZOS)
    print(bottom, right, scale, r.readtext(np.asarray(crop.convert('RGB')), detail=0, allowlist='0123456789', text_threshold=0.4, low_text=0.2))
PY
```

**页脚字形互相关**可判断两页是否同一页码（不依赖 OCR）：把页脚数字区域归一化后算相关系数，≈1.00 即同一个数字。

**页码可能重复或缺失**：

- 重复（两页页脚同号）→ 文件名加 `dup`，笔记里标注冲突。
- 缺失（页面没有页脚条）→ 文件名用 `pNA`，标「页码待核实」。
- 独立章节（如插进来的附录课件）→ 页脚重新从 1 编号，用 `C01…` 之类前缀区分。

### 去重

```bash
python scripts/dedupe_slides.py "<label>_ocr.json" --thresh 0.82
```

按 token Jaccard 相似度合并**连续**近重复帧，组内保留**行数最多**的一帧（信息最全的动画末态）。
纯按文本去重会漏掉「同一页动画推进」，所以用较高阈值 + 组内取最大。

### 归档与 provenance

手工建立 `{file, name}` 映射后批量复制（脚本会拒绝含 `[]?:*"<>|` 的文件名）：

```json
{"src_dir": "...", "dst_dir": "...", "items": [{"file": "slide_077.jpg", "name": "PPT p12 — Plato and rational knowledge (第9节 0626).jpg"}]}
```

**provenance 一并归档**：场景抽取清单（时间码 + 两路页码读数）与画面 OCR 全文，便于回溯核对。

---

## 3. 转写与说话人分离

```bash
python scripts/transcribe_class.py "<16k wav>" <outdir> "L0X 第N节 转写" --model large-v3 --chunk 600
python scripts/diarize_transcript.py "<label>.json" --wav "<绝对路径 wav>" --speakers 2
```

- **必须分段**（`--chunk 600`）：整段 55 分钟一次性解码会让 Whisper 漂移成重复文本或错误语言。
- 语言按 Unicode 字符集逐段判定 `zh` / `en` / `mixed`，不做翻译。
- 说话人：`microsoft/wavlm-base-plus-sv` 嵌入 + 余弦层次聚类，由「累计发言时长最长者」推断为老师。
  **标签是推断**，引用前必须核对时间戳。
- 课堂单麦混音下**小组讨论的学生发言通常分不出来**；这种情况笔记里只记课件参考答案并标注。

### 平台自带 AI 材料（交叉校验）

```bash
# 知识点思维导图 + 分段概要
opencli browser sjtu eval "fetch('/jy-application-resourcemanage/v1/course/ai/textSummary/<ID>',{credentials:'include'}).then(r=>r.text())"
# 逐句 ASR（含 bg/ed 毫秒时间码）
opencli browser sjtu eval "fetch('/jy-application-resourcemanage/v1/course/ai/translate/<ID>?useOriginal=true',{credentials:'include'}).then(r=>r.text())"
```

**作为交叉校验，不作为引用依据**（中英混排识别质量差，人名术语常错）。都要走 textarea 中转。

---

## 4. 配合 Obsidian 知识库

- 归档：`09 参考资料/附件/L0X 课件 PPT/第N节/`、`…/L0X 课堂视频|音轨|字幕/`
- 大体积 mp4/wav 加进 `.gitignore`（GitHub 单文件 100 MB 上限，且可由平台重新获取）；
  转写文本与课件 jpg 属知识库内容，照常入库。
- 笔记里用 `![[路径|宽度]]` **内嵌**图片，不要只放链接；表格内嵌入需把竖线转义成 `\|`。
- 生成后校验：

```bash
python scripts/check_vault_links.py "<vault root>"
```

该脚本会把「索引页预留但尚未建立的骨架链接」「`_待整理` 这类目录级链接」也列为未解析，
需要人工区分——这两类未解析是允许的。

---

## 5. 耗时参考（RTX 4060，单节 55 分钟）

| 步骤 | 耗时 |
|---|---|
| 下载屏幕轨（440 MB） | ~90 秒 |
| 场景抽取（两遍解码） | ~2–4 分钟 |
| 逐页 OCR（约 100 帧） | ~4–6 分钟（CPU EasyOCR） |
| 页脚页码 OCR | ~2–3 分钟 |
| Whisper `large-v3` 转写（600 秒分段） | ~8–10 分钟 |
| 说话人分离 | ~3–5 分钟 |

## 6. 依赖

```bash
pip install faster-whisper soundfile transformers torch easyocr pillow numpy
# 另需系统安装 ffmpeg（含 ffprobe）
```
