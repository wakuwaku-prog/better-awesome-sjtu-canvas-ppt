#!/usr/bin/env python
"""Speaker diarization for classroom transcripts produced by transcribe_class.py.

Embeds every whisper segment with a pretrained speaker-verification model
(`microsoft/wavlm-base-plus-sv` through transformers, which does not need
torchaudio) and clusters the embeddings with agglomerative cosine clustering.
Falls back to MFCC statistics clustering when the neural model is unavailable.

The teacher/student role is *inferred*, not proven: the speaker who accumulates the
most speaking time (and, in this course, mostly speaks English) is labelled teacher.
Always spot-check the labels against the timestamps before quoting anyone.

Usage
-----
python diarize_transcript.py <transcript.json> [--wav audio.wav] [--speakers 2]
                             [--backend xvector|mfcc] [--outdir dir]
"""
import argparse
import json
import os
import re
import sys

import numpy as np

CJK = re.compile(r"[\u4e00-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")
XMODEL = "microsoft/wavlm-base-plus-sv"


def fmt_ts(sec):
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:d}:{m:02d}:{s:05.2f}" if h else f"{m:02d}:{s:05.2f}"


def _windows(start, end, sr, max_len=12.0, min_len=0.7):
    """Yield (i0, i1) sample windows for a segment, skipping very short ones."""
    if end - start < min_len:
        return []
    if end - start <= max_len:
        return [(int(start * sr), int(end * sr))]
    mid = (start + end) / 2
    return [(int(max(0, mid - max_len / 2) * sr), int((mid + max_len / 2) * sr))]


def embed_xvector(wav_path, spans, batch_size=8):
    import soundfile as sf
    import torch
    from transformers import AutoFeatureExtractor, AutoModelForAudioXVector

    device = "cuda" if torch.cuda.is_available() else "cpu"
    fe = AutoFeatureExtractor.from_pretrained(XMODEL)
    model = AutoModelForAudioXVector.from_pretrained(XMODEL).to(device).eval()
    data, sr = sf.read(wav_path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        import scipy.signal as sps
        data = sps.resample(data, int(round(len(data) * 16000 / sr))).astype("float32")
        sr = 16000
    total = len(data)

    tasks = []
    out = [None] * len(spans)
    for i, (a, b) in enumerate(spans):
        for (i0, i1) in _windows(a, b, sr):
            i0, i1 = max(0, i0), min(total, i1)
            if i1 - i0 >= int(0.7 * sr):
                clip = data[i0:i1].astype("float32")
                clip = clip / (np.abs(clip).max() + 1e-6)
                tasks.append((i, clip))
    print(f"      {len(tasks)} embeddable windows out of {len(spans)} segments")

    for start in range(0, len(tasks), batch_size):
        chunk = tasks[start:start + batch_size]
        batch = [c for _, c in chunk]
        with torch.no_grad():
            inp = fe(batch, sampling_rate=sr, return_tensors="pt", padding=True)
            inp = {k: v.to(device) for k, v in inp.items()}
            emb = model(**inp).embeddings.detach().cpu().numpy()
        for k, (i, _) in enumerate(chunk):
            v = emb[k]
            v = v / (np.linalg.norm(v) + 1e-9)
            out[i] = v if out[i] is None else (out[i] + v) / 2
        if start % (batch_size * 20) == 0:
            print(f"      embedded {min(start + batch_size, len(tasks))}/{len(tasks)}")
    return out, sr, device


def mfcc_stats(x, sr, n_mfcc=20):
    import scipy.fftpack as fftpack
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    if len(x) < int(0.3 * sr):
        return None
    pre = np.append(x[0], x[1:] - 0.97 * x[:-1])
    win, hop = int(0.025 * sr), int(0.010 * sr)
    n_frames = 1 + (len(pre) - win) // hop
    if n_frames < 5:
        return None
    frames = np.lib.stride_tricks.as_strided(
        pre, shape=(n_frames, win), strides=(pre.strides[0] * hop, pre.strides[0])).copy()
    frames *= np.hamming(win)
    spec = np.abs(np.fft.rfft(frames, 512)) ** 2
    n_filt = 26
    mel_low, mel_high = 2595 * np.log10(1 + 80 / 700), 2595 * np.log10(1 + min(7600, sr / 2 - 100) / 700)
    mel_pts = np.linspace(mel_low, mel_high, n_filt + 2)
    hz = 700 * (10 ** (mel_pts / 2595) - 1)
    bins = np.clip(np.floor(513 * hz / sr).astype(int), 0, 256)
    fbanks = np.zeros((n_filt, 257))
    for m in range(1, n_filt + 1):
        l, c, r = bins[m - 1], bins[m], bins[m + 1]
        r = max(r, l + 1); c = min(max(c, l + 1), r)
        for k in range(l, min(c, 257)):
            fbanks[m - 1, k] = (k - l) / (c - l)
        for k in range(c, min(r, 257)):
            fbanks[m - 1, k] = (r - k) / (r - c)
    fb = np.log(spec @ fbanks.T + 1e-10)
    mfcc = fftpack.dct(fb, type=2, axis=1, norm="ortho")[:, :n_mfcc]
    energy = fb.mean(axis=1)
    keep = energy > np.percentile(energy, 45)
    if keep.sum() < 5:
        return None
    m = mfcc[keep]
    d = np.diff(m, axis=0)
    return np.concatenate([m.mean(0), m.std(0), np.median(m, 0),
                           np.percentile(m, 90, 0) - np.percentile(m, 10, 0),
                           d.std(0) if len(d) >= 3 else np.zeros(n_mfcc)])


def embed_mfcc(wav_path, spans):
    import soundfile as sf
    data, sr = sf.read(wav_path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    out = [None] * len(spans)
    for i, (a, b) in enumerate(spans):
        w = _windows(a, b, sr)
        if not w:
            continue
        i0, i1 = w[0]
        e = mfcc_stats(data[max(0, i0):min(len(data), i1)], sr)
        if e is not None:
            out[i] = e / (np.linalg.norm(e) + 1e-9)
    return out, sr, "cpu"


def cluster(embs, k):
    from sklearn.cluster import AgglomerativeClustering
    idx = [i for i, e in enumerate(embs) if e is not None]
    labels = np.zeros(len(embs), dtype=int)
    if len(idx) < 3:
        return labels
    X = np.vstack([embs[i] for i in idx])
    k = max(2, min(k, len(X)))
    cl = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average").fit(X)
    for pos, i in enumerate(idx):
        labels[i] = cl.labels_[pos]
    # neighbours inherit the label for very short segments and singletons
    if len(idx) >= 5:
        sizes = np.bincount([int(labels[i]) for i in idx])
        for i in range(len(labels)):
            if embs[i] is None or sizes[labels[i]] <= 2:
                lo = [labels[j] for j in range(max(0, i - 3), i) if embs[j] is not None]
                hi = [labels[j] for j in range(i + 1, min(len(labels), i + 4)) if embs[j] is not None]
                if lo and hi:
                    labels[i] = int(np.bincount(lo + hi).argmax())
    return labels


def fill_unembedded(labels, embedded):
    """Nearest-embedded label propagation for segments that were skipped by --stride."""
    out = labels.copy()
    idx = [i for i, e in enumerate(embedded) if e]
    if not idx:
        return out
    for i in range(len(out)):
        if embedded[i]:
            continue
        nearest = min(idx, key=lambda j: abs(j - i))
        out[i] = labels[nearest]
    return out


def smooth(labels, rows, short=2.0):
    out = labels.copy()
    for i in range(1, len(rows) - 1):
        if (rows[i]["end"] - rows[i]["start"]) < short and labels[i - 1] == labels[i + 1]:
            out[i] = labels[i - 1]
    med = out.copy()
    for i in range(1, len(out) - 1):
        med[i] = int(np.median(out[i - 1:i + 2]))
    return med


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript_json")
    ap.add_argument("--wav", default=None)
    ap.add_argument("--speakers", type=int, default=2)
    ap.add_argument("--backend", default="xvector", choices=["xvector", "mfcc"])
    ap.add_argument("--stride", type=int, default=1,
                    help="embed only every Nth segment (faster); skipped segments inherit the nearest label")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--teacher-label", default="老师 Teacher")
    ap.add_argument("--student-label", default="学生 Student")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    with open(args.transcript_json, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    rows = data["segments"]
    wav = args.wav or data.get("wav")
    if not os.path.isabs(wav):
        wav = os.path.join(os.path.dirname(os.path.abspath(args.transcript_json)), os.path.basename(wav))
    print(f"transcript={args.transcript_json}  segments={len(rows)}  wav={wav}")

    spans = [(r["start"], r["end"]) for r in rows]
    embedded = [True] * len(rows)
    if args.stride > 1:
        spans = [(a, b) if (i % args.stride == 0) else (a, a) for i, (a, b) in enumerate(spans)]
        embedded = [i % args.stride == 0 for i in range(len(rows))]
        print(f"      stride={args.stride}: embedding {sum(embedded)}/{len(rows)} segments")
    if args.backend == "xvector":
        try:
            embs, sr, device = embed_xvector(wav, spans)
        except Exception as exc:
            print(f"      xvector backend failed ({exc}); falling back to MFCC")
            embs, sr, device = embed_mfcc(wav, spans)
            args.backend = "mfcc"
    else:
        embs, sr, device = embed_mfcc(wav, spans)
    print(f"      backend={args.backend} device={device} "
          f"embeddings={sum(e is not None for e in embs)}/{len(embs)}")

    labels = smooth(cluster(embs, args.speakers), rows)
    labels = fill_unembedded(labels, embedded)

    stats = {}
    for lab in sorted(set(int(v) for v in labels)):
        sel = [r for r, l in zip(rows, labels) if int(l) == lab]
        text = " ".join(r["text"] for r in sel)
        n_cjk, n_lat = len(CJK.findall(text)), len(LATIN.findall(text))
        stats[lab] = {
            "segments": len(sel),
            "seconds": round(sum(r["end"] - r["start"] for r in sel), 1),
            "zh_share": round(n_cjk / max(1, n_cjk + n_lat), 3),
            "sample": text[:160],
        }
    order = sorted(stats, key=lambda k: -stats[k]["seconds"])
    teacher = order[0]
    # Classroom recordings have one dominant voice (the lecturer) plus several
    # student voices. With more than two clusters the students are grouped under one
    # label so the transcript stays readable.
    role = {lab: args.student_label for lab in stats}
    role[teacher] = args.teacher_label
    for lab in order:
        print(f"      cluster {lab}: {stats[lab]['segments']} segs, {stats[lab]['seconds']}s, "
              f"zh_share={stats[lab]['zh_share']} | {stats[lab]['sample'][:90]}")

    for r, l in zip(rows, labels):
        r["speaker_cluster"] = int(l)
        r["speaker"] = role[int(l)]

    turns = []
    for r in rows:
        if (turns and turns[-1]["speaker"] == r["speaker"] and turns[-1]["lang"] == r["lang"]
                and r["start"] - turns[-1]["end"] < 3.0):
            turns[-1]["end"] = r["end"]
            turns[-1]["text"] += " " + r["text"]
            turns[-1]["n"] += 1
        else:
            turns.append({"speaker": r["speaker"], "lang": r["lang"], "start": r["start"],
                          "end": r["end"], "text": r["text"], "n": 1})

    data["segments"] = rows
    data["turns"] = turns
    data["diarization"] = {"backend": args.backend, "model": XMODEL if args.backend == "xvector" else "mfcc-stats",
                           "device": device, "clusters": {str(k): stats[k] for k in stats},
                           "role_map": {str(k): v for k, v in role.items()},
                           "teacher_cluster": int(teacher)}

    outdir = args.outdir or os.path.dirname(os.path.abspath(args.transcript_json))
    stem = os.path.splitext(os.path.basename(args.transcript_json))[0]
    jp = os.path.join(outdir, f"{stem}（说话人）.json")
    mp = os.path.join(outdir, f"{stem}（说话人）.md")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    with open(mp, "w", encoding="utf-8") as f:
        f.write(f"# {data.get('label', stem)} — 课堂转写（已区分说话人）\n\n")
        f.write(f"- 音源 Audio：`{os.path.basename(wav)}`\n")
        f.write(f"- 时长 Duration：{fmt_ts(data.get('duration', 0))}\n")
        f.write(f"- 转写 Model：faster-whisper `{data.get('model')}`（逐段自动判定中/英）\n")
        f.write(f"- 说话人分离 Diarization：`{data['diarization']['model']}`（{device}）\n")
        f.write(f"- 角色推断 Role inference：发言时长最长者 = cluster {teacher}\n\n")
        f.write("> [!warning] 自动说话人分离与角色推断仅供定位参考，不能替代人工确认；"
                "引用具体观点前请核对时间戳与录像。\n\n")
        for k in order:
            f.write(f"## cluster {k} → {role[k]}（{stats[k]['segments']} 段 / {stats[k]['seconds']} 秒 / "
                    f"中文占比 {stats[k]['zh_share']}）\n\n")
        f.write("\n---\n\n")
        for t in turns:
            f.write(f"**[{fmt_ts(t['start'])} → {fmt_ts(t['end'])}] {t['speaker']} · {t['lang']}**\n\n")
            f.write(t["text"] + "\n\n")
    print(f"wrote {jp}")
    print(f"wrote {mp}")


if __name__ == "__main__":
    main()
