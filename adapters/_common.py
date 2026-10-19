# -*- coding: utf-8 -*-
"""lecture-to-obsidian 采集层共用工具（adapters/ 内部模块，不是 CLI）。

只做四件事，供 canvas.py / chaoxing.py / pdf.py 复用，避免三份实现漂移：

1. 定位本 skill 与 ``better-awesome-sjtu-canvas-ppt``（环境变量 ``CANVAS_PPT_SKILL`` 可覆盖）。
2. 原子写 JSON / 文本（UTF-8，``ensure_ascii=False``，便于人工 diff）。
3. **凭据护栏**：扫描待落盘对象里的签名 URL / cookie / token，命中即抛
   :class:`SecretLeak`——铁律要求「不把签名 URL、cookie、token 写进任何归档文件」。
4. 时间码工具：一律以**秒**为内部单位；``startMmss`` 由秒重算，**不采信**上游给的字符串
   （实测 ``extract_ppt_scenes.py`` 写出的 ``start_mmss`` 是 ``"0448"`` 这种缺冒号的紧凑格式）。

时间码口径（重要，防止单位混淆）：
- ``createSec``      旧课件轨接口，单位**毫秒**
- ``imageSeekTime``  新课件轨清单，单位**秒**
- ``decode-scene``   场景抽取的**解码时间码**（秒），不是容器时间戳
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------

# adapters/_common.py -> parents[1] == skill 根
SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = SKILL_ROOT / "schemas"
MANIFEST_SCHEMA = SCHEMAS_DIR / "manifest.schema.json"

DEFAULT_CANVAS_PPT_SKILL = Path.home() / ".dsh" / "skills" / "better-awesome-sjtu-canvas-ppt"


def canvas_ppt_skill() -> Path:
    """``better-awesome-sjtu-canvas-ppt`` 的根目录。**只调用它的脚本，不复制。**"""
    raw = os.environ.get("CANVAS_PPT_SKILL")
    return Path(raw).expanduser().resolve() if raw else DEFAULT_CANVAS_PPT_SKILL


def canvas_ppt_script(name: str) -> Path:
    """返回那个 skill 里某个脚本的绝对路径，并校验它真实存在。"""
    p = canvas_ppt_skill() / "scripts" / name
    if not p.exists():
        raise FileNotFoundError(
            f"找不到 {p}\n"
            f"请确认 better-awesome-sjtu-canvas-ppt 已安装，或用环境变量 CANVAS_PPT_SKILL 指定其根目录。"
        )
    return p


# --------------------------------------------------------------------------
# 时间 / 文本
# --------------------------------------------------------------------------

def now_iso() -> str:
    """本地时区的 ISO 8601（带偏移），用作 ``capturedAt`` / ``date``。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sec_to_mmss(sec: float | None) -> str | None:
    """秒 -> ``MM:SS``（分钟两位补零，与知识库既有页图命名 ``(第7节 2215)`` 口径一致）。

    schema 的 pattern 是 ``^[0-9]{1,2}:[0-9]{2}$``：分钟最多两位。
    超过 99 分钟（长视频）时**返回 None**（schema 允许 null），由调用方记 warning，
    不硬塞一个违反 pattern 的三位分钟数。
    """
    if sec is None:
        return None
    total = int(sec)
    m, s = divmod(total, 60)
    if m > 99:
        return None
    return f"{m:02d}:{s:02d}"


def parse_compact_mmss(text: str) -> float | None:
    """解析 ``"0448"`` / ``"10005"`` 这种紧凑格式为秒。

    ``extract_ppt_scenes.py`` 用 ``"%02d%02d" % (mm, ss)`` 写 ``start_mmss``，
    所以它**没有冒号**；后两位是秒，前面都是分钟。仅用于读历史产物做交叉校验，
    权威时间码仍取 ``start`` / ``imageSeekTime`` 等数值字段。
    """
    if not text:
        return None
    digits = re.sub(r"\D", "", str(text))
    if len(digits) < 3:
        return None
    return int(digits[:-2]) * 60 + int(digits[-2:])


def ms_to_sec(value) -> float | None:
    """毫秒 -> 秒（``createSec`` 口径）。"""
    if value is None:
        return None
    return round(float(value) / 1000.0, 3)


def to_sec(value) -> float | None:
    """把 ``imageSeekTime``（已是秒）或数值字符串转成秒。"""
    if value is None:
        return None
    return round(float(value), 3)


def slugify(label: str, fallback: str = "session") -> str:
    """节次标签 -> 目录名。保留中文，只清掉文件系统非法字符。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", label or "").strip(" ._")
    name = re.sub(r"\s+", " ", name)
    return name[:60] or fallback


def safe_filename(name: str, fallback: str = "file") -> str:
    """文件名清洗。**不使用方括号**——会破坏 Obsidian 的 wikilink / 嵌入语法。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t\[\]]', "_", name or "").strip(" ._")
    return name[:80] or fallback


# --------------------------------------------------------------------------
# 原子写
# --------------------------------------------------------------------------

def write_json(path: Path, obj) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)
    return path


def read_json(path: Path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def write_text(path: Path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------
# 凭据护栏
# --------------------------------------------------------------------------

class SecretLeak(RuntimeError):
    """待落盘内容里出现了签名 URL / cookie / token。"""


# 命中的话**几乎不可能是误报**的模式
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS 签名 URL", re.compile(r"X-Amz-(Signature|Credential|Expires|Security-Token)", re.I)),
    ("带 key= 的签名直链", re.compile(r"[?&]key=[A-Za-z0-9%_\-\.~]{16,}")),
    ("token 参数", re.compile(r"[?&](token|access_token|user_token|accessToken)=[^&\s]{8,}", re.I)),
    ("cookie 头", re.compile(r"(^|[\s\"'])(cookie|set-cookie)\s*:", re.I)),
    ("会话 id", re.compile(r"\b(JSESSIONID|JACCOUNT|SESSION|PHPSESSID)\s*[=:]", re.I)),
    ("authorization 头", re.compile(r"\bauthorization\s*:\s*(bearer|basic)\b", re.I)),
    ("明文口令参数", re.compile(r"[?&](password|passwd|pwd)=[^&\s]{4,}", re.I)),
    ("jaccount 票据", re.compile(r"\b(ticket|execution|lt|_eventId)\s*=\s*[A-Za-z0-9\-_]{12,}")),
]

# 这些键承载的是平台直链，**规范化时必须丢弃**（值本身可能就是签名 URL）
URL_BEARING_KEYS = {
    "u", "url", "pptimgurl", "ppt_img_url", "downloadurl", "download_url",
    "pdfurl", "pdf_url", "http", "download", "srcurl", "src_url", "videourl",
    "video_url", "audiourl", "audio_url", "coverurl", "cover_url", "signatureurl",
}


def scan_secrets(obj, _path: str = "$") -> list[str]:
    """递归扫描 JSON 可序列化对象里的凭据痕迹，返回人类可读的命中清单。"""
    hits: list[str] = []

    def visit(node, path: str):
        if isinstance(node, dict):
            for k, v in node.items():
                key = str(k)
                child = f"{path}.{key}"
                if key.lower() in URL_BEARING_KEYS and isinstance(v, str) and v.strip():
                    # URL 承载字段只要不是空就报——规范化阶段本该把它丢掉
                    hits.append(f"{child}: URL 承载字段（值已被丢弃，不该出现在产物里）")
                    continue
                visit(v, child)
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                visit(v, f"{path}[{i}]")
        elif isinstance(node, str):
            for label, pat in _SECRET_PATTERNS:
                m = pat.search(node)
                if m:
                    hits.append(f"{path}: {label}（片段：…{node[max(0, m.start() - 8):m.end()][:60]}…）")
                    break

    visit(obj, _path)
    return hits


def assert_no_secrets(obj, where: str = "产物") -> None:
    """命中即抛 :class:`SecretLeak`。**写盘前必须调用。**"""
    hits = scan_secrets(obj)
    if hits:
        raise SecretLeak(
            f"{where} 检出 {len(hits)} 处凭据痕迹，已拒绝写盘（铁律：签名 URL / cookie / token 不入档）：\n  - "
            + "\n  - ".join(hits[:10])
        )


def drop_url_fields(obj):
    """递归删除 URL 承载键。平台原始 JSON 直接过一遍再落盘。"""
    if isinstance(obj, dict):
        return {k: drop_url_fields(v) for k, v in obj.items()
                if str(k).lower() not in URL_BEARING_KEYS}
    if isinstance(obj, list):
        return [drop_url_fields(v) for v in obj]
    return obj


# --------------------------------------------------------------------------
# 子进程
# --------------------------------------------------------------------------

def run(cmd: list, dry_run: bool = False, cwd: Path | None = None,
        check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess | None:
    """跑一个子进程。``dry_run`` 只打印命令，不执行——用来在没登录平台时验证编排。"""
    printable = " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd)
    if dry_run:
        print(f"[dry-run] {printable}")
        return None
    print(f"[run] {printable}", flush=True)
    proc = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        capture_output=quiet,
        text=quiet,
        encoding="utf-8" if quiet else None,
        errors="replace" if quiet else None,
    )
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    return proc


def python_exe() -> str:
    return sys.executable or "python"


def have(binary: str) -> str | None:
    return shutil.which(binary)


# --------------------------------------------------------------------------
# 下载（前台、串行、断点续传）
# --------------------------------------------------------------------------

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")


def download_file(url: str, dest: Path, referer: str | None = None,
                  extra_headers: dict | None = None, timeout: int = 60,
                  max_retry: int = 3) -> tuple[bool, int | None, str]:
    """带 Referer 的断点续传下载，返回 ``(ok, bytes, msg)``。

    **只做 GET**；大文件必须前台调用（本环境后台 job 里 curl 会卡在 0 字节）。
    直链**不写日志、不落盘**——只有 ``dest`` 与返回值进调用方逻辑。
    """
    import urllib.request

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    done = tmp.stat().st_size if tmp.exists() else 0

    for attempt in range(max_retry):
        try:
            headers = {"User-Agent": DEFAULT_UA}
            if referer:
                headers["Referer"] = referer
            headers.update(extra_headers or {})
            if done:
                headers["Range"] = f"bytes={done}-"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                total = None
                cr = resp.headers.get("Content-Range")
                cl = resp.headers.get("Content-Length")
                if cr and "/" in cr:
                    total = int(cr.rsplit("/", 1)[1])
                elif cl and not done:
                    total = int(cl)
                mode = "ab" if done else "wb"
                with open(tmp, mode) as f:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
            size = tmp.stat().st_size
            if total and size < total:
                done = size
                continue
            os.replace(tmp, dest)
            return True, size, "ok"
        except Exception as exc:                                  # noqa: BLE001
            done = tmp.stat().st_size if tmp.exists() else 0
            if attempt == max_retry - 1:
                return False, None, f"{type(exc).__name__}: {exc}"
            import time
            time.sleep(2 * (attempt + 1))
    return False, None, "unreachable"


# --------------------------------------------------------------------------
# manifest 契约枚举（与 schemas/manifest.schema.json 逐字对齐）
# --------------------------------------------------------------------------

SOURCES = ("platform-ppt-track", "scene-extract", "keyframe-extract", "pdf-render")
CONTENT_TYPES = ("slide", "video", "activity", "desktop", "unknown")
TIMECODE_SOURCES = ("platform-imageSeekTime-sec", "platform-createSec-ms",
                    "decode-scene", "keyframe", "manual")
PAGE_NO_SCOPES = ("section", "page", "none")
PAGE_NO_STATUSES = ("verified", "anomalous", "mismatch", "missing", "not-applicable")
ARTIFACT_KINDS = ("ai-summary", "platform-asr", "ppt-track-manifest", "other")

_PAGE_DIGITS = re.compile(r"(\d{1,3})")


def normalize_page_no(value) -> str | None:
    """页脚读数 -> 纯数字字符串。

    页脚可能带版式标注（实测 L08 p10 读作「实验心理学 11」、p12 读作「实验心理学 06」），
    所以 ``pageNoFullOcr`` / ``pageNoFooterOcr`` 是**字符串**，比较时用本函数的结果。
    """
    if value is None:
        return None
    m = _PAGE_DIGITS.search(str(value))
    return m.group(1) if m else None


def make_projection(session_index: int, start_sec: float | None,
                    timecode_source: str | None = None, raw_timecode: str | None = None,
                    end_sec: float | None = None, is_replay: bool = False) -> dict:
    """构造 ``slides[].projections[]`` 的一项（一次放映记录）。

    ``sessionIndex`` 是 1-based，对应 ``source.sessions`` 下标 +1。
    ``isReplay`` **总是写出**（基准样本里非重放项也显式写 ``false``）。
    """
    proj = {"sessionIndex": int(session_index)}
    if start_sec is not None:
        proj["startSec"] = round(float(start_sec), 3)
        mmss = sec_to_mmss(start_sec)
        if mmss:
            proj["startMmss"] = mmss
    else:
        proj["startSec"] = 0.0          # schema 必填；未知时由调用方改走「无投影」
    if end_sec is not None:
        proj["endSec"] = round(float(end_sec), 3)
    proj["isReplay"] = bool(is_replay)
    if timecode_source:
        if timecode_source not in TIMECODE_SOURCES:
            raise ValueError(f"未知 timecodeSource：{timecode_source}")
        proj["timecodeSource"] = timecode_source
    if raw_timecode is not None:
        proj["rawTimecode"] = str(raw_timecode)
    return proj


def make_slide(index: int, file: str, source: str, projections: list | None = None,
               **fields) -> dict:
    """构造 ``slides[]`` 的一项，并校验枚举与必填。

    未提供的可选字段**不写**（不塞 null），保持产物可读。
    """
    if source not in SOURCES:
        raise ValueError(f"未知 source：{source}（允许：{', '.join(SOURCES)}）")
    slide = {"index": int(index), "file": file, "source": source}
    if projections is not None:
        slide["projections"] = projections
    ct = fields.get("contentType")
    if ct is not None and ct not in CONTENT_TYPES:
        raise ValueError(f"未知 contentType：{ct}（允许：{', '.join(CONTENT_TYPES)}）")
    for key in ("pdfPage", "title", "contentType", "isPureImagePage", "textExtractionIssue",
                "duplicateOf", "ocrText", "footerUnit", "pageNoScope", "pageNoExpected",
                "pageNoFullOcr", "pageNoFooterOcr", "pageNoNormalized", "pageNoStatus",
                "pageNoNote"):
        if key in fields and fields[key] is not None:
            slide[key] = fields[key]
    return slide


# --------------------------------------------------------------------------
# raw/ 契约（见 references/01-acquisition.md）
# --------------------------------------------------------------------------

def raw_dir(work: Path) -> Path:
    return Path(work) / "raw"


def sessions_dir(work: Path) -> Path:
    return raw_dir(work) / "sessions"


def session_dir(work: Path, label: str) -> Path:
    return sessions_dir(work) / slugify(label)


def native_dir(work: Path, label: str) -> Path:
    return session_dir(work, label) / "native"


def init_session(work: Path, label: str, **fields) -> Path:
    """建一个节次目录并写 ``session.json``。**每个平台节次一个目录，不合并。**"""
    d = session_dir(work, label)
    (d / "native").mkdir(parents=True, exist_ok=True)
    payload = {"label": label}
    payload.update(fields)
    write_json(d / "session.json", payload)
    return d


def add_session_slides(work: Path, label: str, slides: list) -> Path:
    return write_json(session_dir(work, label) / "slides.json", slides)


def merge_source(work: Path, platform: str, course: dict, sessions_meta: list | None = None,
                 platform_subtitles: str | None = None, extra: dict | None = None) -> Path:
    """写/更新 ``raw/source.json``。多次运行（如 canvas + pdf）可合并。"""
    path = raw_dir(work) / "source.json"
    if path.exists():
        payload = read_json(path)
    else:
        payload = {}
    payload["platform"] = platform if platform != "pdf" or not payload.get("platform") else payload["platform"]
    payload["course"] = course
    payload["capturedAt"] = now_iso()
    if platform_subtitles:
        payload["platformSubtitles"] = platform_subtitles
    if sessions_meta:
        payload.setdefault("sessionsMeta", []).extend(sessions_meta)
    for k, v in (extra or {}).items():
        payload[k] = v
    assert_no_secrets(payload, "raw/source.json")
    return write_json(path, payload)


def add_warnings(work: Path, messages: list[str]) -> Path:
    """追加「需要人工判断」的事实。"""
    path = raw_dir(work) / "warnings.json"
    existing = read_json(path) if path.exists() else []
    if not isinstance(existing, list):
        existing = []
    for m in messages:
        if m and m not in existing:
            existing.append(m)
    return write_json(path, existing)


def add_unarchived(work: Path, items: list[dict]) -> Path:
    """登记「课堂用到但尚未归档」的材料（what / evidence / impact）。

    L08 实例：第15节前 35 分钟用的教师端 `第9次课.pptx` 尚未归档——
    不记这一条，后续复核会以为课件是完整的。
    """
    path = raw_dir(work) / "unarchived.json"
    existing = read_json(path) if path.exists() else []
    if not isinstance(existing, list):
        existing = []
    for it in items:
        if it and it.get("what") and it not in existing:
            existing.append(it)
    assert_no_secrets(existing, "raw/unarchived.json")
    return write_json(path, existing)


def write_pdf_block(work: Path, block: dict) -> Path:
    """写 PDF 元数据块（``raw/pdf.json``），对应 manifest 顶层 ``pdf``。"""
    assert_no_secrets(block, "raw/pdf.json")
    return write_json(raw_dir(work) / "pdf.json", block)


def write_pdf_slides(work: Path, slides: list) -> Path:
    """写 PDF 渲染页图清单（``raw/pdf/slides.json``），与节次快照轨**分开**。"""
    assert_no_secrets(slides, "raw/pdf/slides.json")
    return write_json(raw_dir(work) / "pdf" / "slides.json", slides)


def write_source_register(work: Path, rows: list[dict]) -> Path:
    """写待写入目标库 Source Register 的行（``id`` 留空——**本 skill 不擅自编号**）。"""
    assert_no_secrets(rows, "raw/source_register.json")
    return write_json(raw_dir(work) / "source_register.json", rows)


def banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)
