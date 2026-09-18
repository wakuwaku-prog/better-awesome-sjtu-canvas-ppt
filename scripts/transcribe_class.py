#!/usr/bin/env python
"""Transcribe a classroom recording: language tagging (zh / en) + speaker diarization.

Pipeline
--------
1. faster-whisper single-pass transcription of the whole 16 kHz mono wav
   (VAD-filtered, word timestamps, language auto-detected *per segment*).
2. Language label per segment from the script of the text (zh / en / mixed),
   decided by Unicode ranges, with a word-level fallback.
3. Speaker embedding per whisper segment: MFCC(+delta) statistics -> standardise ->
   PCA -> AgglomerativeClustering (ward). Labels are then smoothed so that a very
   short segment sandwiched between two same-label neighbours inherits that label.
4. Turns = consecutive same-speaker, same-language segments merged.

Outputs <outdir>/<label>.json, .md, .srt

Usage
-----
python transcribe_class.py <wav> <outdir> <label> [--model large-v3] [--device cuda] [--speakers 2]
"""
import argparse
import json
import os
import re
import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore")

CJK = re.compile(r"[\u4e00-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")


def fmt_ts(sec):
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:d}:{m:02d}:{s:05.2f}" if h else f"{m:02d}:{s:05.2f}"


def fmt_srt(sec):
    sec = max(0.0, float(sec))
    h, m, s = int(sec // 3600), int((sec % 3600) // 60), int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def mfcc_stats(x, sr, n_mfcc=20):
    """Log-mel cepstral statistics over voiced frames — a cheap speaker embedding."""
    import scipy.fftpack as fftpack

    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    if len(x) < int(0.25 * sr):
        return None
    pre = np.append(x[0], x[1:] - 0.97 * x[:-1])
    win, hop = int(0.025 * sr), int(0.010 * sr)
    if len(pre) < win:
        return None
    n_frames = 1 + (len(pre) - win) // hop
    frames = np.lib.stride_tricks.as_strided(
        pre, shape=(n_frames, win), strides=(pre.strides[0] * hop, pre.strides[0])
    ).copy()
    frames *= np.hamming(win)
    nfft = 512
    spec = np.abs(np.fft.rfft(frames, nfft)) ** 2
    n_filt = 26
    low, high = 80.0, min(7600.0, sr / 2 - 100)
    mel_low, mel_high = 2595 * np.log10(1 + low / 700), 2595 * np.log10(1 + high / 700)
    mel_pts = np.linspace(mel_low, mel_high, n_filt + 2)
    hz = 700 * (10 ** (mel_pts / 2595) - 1)
    bins = np.clip(np.floor((nfft + 1) * hz / sr).astype(int), 0, nfft // 2)
    fbanks = np.zeros((n_filt, nfft // 2 + 1))
    for m in range(1, n_filt + 1):
        l, c, r = bins[m - 1], bins[m], bins[m + 1]
        if r <= l:
            r = l + 1
        if c <= l:
            c = l + 1
        for k in range(l, min(c, fbanks.shape[1])):
            fbanks[m - 1, k] = (k - l) / (c - l)
        for k in range(c, min(r, fbanks.shape[1])):
            fbanks[m - 1, k] = (r - k) / (r - c)
    fb = np.log(spec @ fbanks.T + 1e-10)
    mfcc = fftpack.dct(fb, type=2, axis=1, norm="ortho")[:, :n_mfcc]
    energy = fb.mean(axis=1)
    keep = energy > np.percentile(energy, 45)
    if keep.sum() < 5:
        return None
    m = mfcc[keep]
    d = np.diff(m, axis=0)
    feats = [m.mean(axis=0), m.std(axis=0), np.median(m, axis=0),
             np.percentile(m, 90, axis=0) - np.percentile(m, 10, axis=0)]
    if len(d) >= 3:
        feats.append(d.std(axis=0))
        feats.append(np.abs(d).mean(axis=0))
    else:
        feats.append(np.zeros(n_mfcc))
        feats.append(np.zeros(n_mfcc))
    return np.concatenate(feats)


def embed_segment(audio, sr, start, end, max_len=12.0):
    """Embed a segment; for long segments average two windows, skip the first 0.3 s."""
    a = start + 0.30
    b = end
    if b - a < 0.35:
        a, b = start, end
    if b <= a:
        return None
    if b - a > 3 * max_len:
        mid = (a + b) / 2
        parts = [mfcc_stats(audio[int(a * sr):int((a + max_len) * sr)], sr),
                 mfcc_stats(audio[int((mid - max_len / 2) * sr):int((mid + max_len / 2) * sr)], sr),
                 mfcc_stats(audio[int((b - max_len) * sr):int(b * sr)], sr)]
        parts = [p for p in parts if p is not None]
        return np.mean(parts, axis=0) if parts else None
    return mfcc_stats(audio[int(a * sr):int(min(b, a + 3 * max_len) * sr)], sr)


def run_whisper(model, audio, sr, chunk_sec, language, label=""):
    """Transcribe audio in independent chunks and return rows with absolute timestamps.

    Chunking matters: a single 55-minute pass lets Whisper drift into repeated or
    translated text, so each chunk is decoded with its own context.
    """
    total = len(audio) / sr
    rows = []
    for c0 in range(0, max(1, int(np.ceil(total / chunk_sec)))):
        start = c0 * chunk_sec
        end = min(total, start + chunk_sec)
        clip = audio[int(start * sr):int(end * sr)]
        if len(clip) < sr:
            continue
        seg_iter, info = model.transcribe(
            clip, beam_size=5, language=language, word_timestamps=True,
            vad_filter=True, vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=200),
            condition_on_previous_text=False, no_speech_threshold=0.6, log_prob_threshold=-1.0,
        )
        n_here = 0
        for s in seg_iter:
            text = (s.text or "").strip()
            if not text:
                continue
            n_cjk, n_lat = len(CJK.findall(text)), len(LATIN.findall(text))
            if n_cjk and n_cjk >= max(2, n_lat * 0.35):
                lang = "zh" if n_cjk > n_lat * 0.6 else "mixed"
            else:
                lang = "en"
            rows.append({
                "i": len(rows), "start": round(start + float(s.start), 2),
                "end": round(start + float(s.end), 2), "lang": lang,
                "whisper_lang": getattr(s, "language", None) or getattr(info, "language", None),
                "text": text,
                "words": [{"w": w.word, "s": round(start + float(w.start), 2),
                           "e": round(start + float(w.end), 2),
                           "p": round(float(w.probability or 0), 3)} for w in (s.words or [])],
            })
            n_here += 1
        print(f"      chunk {c0 + 1} [{fmt_ts(start)}–{fmt_ts(end)}] "
              f"lang={getattr(info, 'language', '?')} segments={n_here}")
    rows = [r for r in rows if (r["end"] - r["start"]) >= 0.35]
    for i, r in enumerate(rows):
        r["i"] = i
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("outdir")
    ap.add_argument("label")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--speakers", type=int, default=2)
    ap.add_argument("--language", default=None)
    ap.add_argument("--chunk", type=float, default=600.0, help="seconds per transcription chunk")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    import soundfile as sf
    from faster_whisper import WhisperModel

    print(f"[1/4] loading {args.wav}")
    audio, sr = sf.read(args.wav, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    dur = len(audio) / sr
    print(f"      sr={sr} duration={dur:.1f}s ({dur/60:.1f} min)")

    print(f"[2/4] whisper {args.model} on {args.device} "
          f"(chunks of {args.chunk:.0f}s, language={args.language or 'auto'})")
    model = WhisperModel(args.model, device=args.device,
                         compute_type="float16" if args.device == "cuda" else "int8")
    rows = run_whisper(model, audio, sr, args.chunk, args.language, args.label)
    print(f"      {len(rows)} segments")
    for r in rows[:3]:
        print(f"      e.g. {fmt_ts(r['start'])} [{r['lang']}] {r['text'][:70]}")

    print("[3/4] merging segments into turns (speaker labels come from diarize_transcript.py)")
    rows = [dict(r, speaker=0) for r in rows]
    turns = []
    for r in rows:
        if (turns and turns[-1]["lang"] == r["lang"] and r["start"] - turns[-1]["end"] < 2.0):
            turns[-1]["end"] = r["end"]
            turns[-1]["text"] += " " + r["text"]
            turns[-1]["segs"].append(r["i"])
        else:
            turns.append({"speaker": 0, "lang": r["lang"], "start": r["start"],
                          "end": r["end"], "text": r["text"], "segs": [r["i"]]})
    labels = np.zeros(len(rows), dtype=int)

    os.makedirs(args.outdir, exist_ok=True)
    stem = args.label
    payload = {"label": args.label, "wav": os.path.basename(args.wav), "duration": dur,
               "model": args.model, "n_segments": len(rows), "n_turns": len(turns),
               "speaker_clusters": int(labels.max()) + 1 if len(rows) else 0,
               "segments": rows, "turns": turns}
    with open(os.path.join(args.outdir, f"{stem}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.outdir, f"{stem}.md"), "w", encoding="utf-8") as f:
        f.write(f"# {args.label} — 课堂转写 Transcript\n\n")
        f.write(f"- 音源 Audio: `{os.path.basename(args.wav)}`\n")
        f.write(f"- 时长 Duration: {fmt_ts(dur)}\n")
        f.write(f"- 模型 Model: faster-whisper `{args.model}` ({args.device})\n")
        f.write(f"- 分段 Chunking: {args.chunk:.0f}s 独立分段，语言逐段自动判定（zh / en / mixed）\n")
        f.write("- 说话人 Speaker: 本文件未区分；见 `（说话人）.md` 的分离结果\n\n")
        for t in turns:
            f.write(f"**[{fmt_ts(t['start'])} → {fmt_ts(t['end'])}] {t['lang']}**\n\n")
            f.write(t["text"] + "\n\n")
    with open(os.path.join(args.outdir, f"{stem}.srt"), "w", encoding="utf-8") as f:
        for n, r in enumerate(rows, 1):
            f.write(f"{n}\n{fmt_srt(r['start'])} --> {fmt_srt(r['end'])}\n")
            f.write(f"[{r['lang']}] {r['text']}\n\n")
    print(f"      -> {os.path.join(args.outdir, stem)}.json/.md/.srt  "
          f"segments={len(rows)} turns={len(turns)}")


if __name__ == "__main__":
    main()
