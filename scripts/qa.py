#!/usr/bin/env python3
"""QA gate for nw-media-engine outputs (ffprobe + ffmpeg ebur128).

  qa.py video FILE --width 1080 --height 1920 --fps 30 --duration 24 --out qa.json
  qa.py audio FILE --sr 24000 --channels 1 --lufs -14 --lufs-tol 1.0 --min-duration 0 --out qa.json

Exit code 1 when a hard check fails. Writes JSON with every check.
"""
import argparse
import json
import re
import subprocess
import sys
from fractions import Fraction


def ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def measure_lufs(path):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-filter_complex", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    tail = proc.stderr[proc.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail)
    lra = re.search(r"LRA:\s+(-?[\d.]+) LU", tail)
    return (
        float(i.group(1)) if i else None,
        float(tp.group(1)) if tp else None,
        float(lra.group(1)) if lra else None,
    )


def check(checks, name, ok, actual, expected):
    checks.append({"check": name, "ok": bool(ok), "actual": actual, "expected": expected})


def qa_video(a):
    info = ffprobe(a.file)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    fps = float(Fraction(v.get("avg_frame_rate") or v["r_frame_rate"]))
    dur = float(info["format"]["duration"])
    nb = int(v.get("nb_frames") or 0)
    checks = []
    check(checks, "codec", v["codec_name"] == "h264", v["codec_name"], "h264")
    check(checks, "resolution", (v["width"], v["height"]) == (a.width, a.height),
          f'{v["width"]}x{v["height"]}', f"{a.width}x{a.height}")
    check(checks, "fps", abs(fps - a.fps) < 0.01, round(fps, 3), a.fps)
    check(checks, "pix_fmt", v.get("pix_fmt") == "yuv420p", v.get("pix_fmt"), "yuv420p")
    if a.duration:
        tol = max(1.5 / a.fps, 0.15)
        check(checks, "duration", abs(dur - a.duration) <= tol, round(dur, 3), a.duration)
    aud = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    lufs, tp = None, None
    if a.require_audio:
        check(checks, "has_audio", aud is not None, aud is not None, True)
        if aud is not None:
            check(checks, "audio_codec", aud.get("codec_name") == "aac", aud.get("codec_name"), "aac")
            lufs, tp, _ = measure_lufs(a.file)
            check(checks, "audio_lufs", lufs is not None and abs(lufs - a.lufs) <= a.lufs_tol,
                  lufs, f"{a.lufs}±{a.lufs_tol}")
    summary = {
        "type": "video", "file": a.file, "duration_s": round(dur, 3), "frames": nb,
        "profile": v.get("profile"), "level": v.get("level"),
        "has_audio": aud is not None, "audio_codec": aud.get("codec_name") if aud else None,
        "audio_lufs": lufs, "audio_true_peak_dbtp": tp,
        "size_bytes": int(info["format"]["size"]), "bit_rate": int(info["format"].get("bit_rate", 0)),
    }
    return summary, checks


def qa_audio(a):
    info = ffprobe(a.file)
    s = next(x for x in info["streams"] if x["codec_type"] == "audio")
    dur = float(info["format"]["duration"])
    lufs, tp, lra = measure_lufs(a.file)
    checks = []
    check(checks, "codec", s["codec_name"].startswith("pcm_s16"), s["codec_name"], "pcm_s16le")
    check(checks, "sample_rate", int(s["sample_rate"]) == a.sr, int(s["sample_rate"]), a.sr)
    check(checks, "channels", int(s["channels"]) == a.channels, int(s["channels"]), a.channels)
    check(checks, "integrated_lufs", lufs is not None and abs(lufs - a.lufs) <= a.lufs_tol, lufs,
          f"{a.lufs}±{a.lufs_tol}")
    check(checks, "true_peak", tp is not None and tp <= -1.0, tp, "<= -1.0 dBTP")
    if a.min_duration > 0:
        check(checks, "min_duration", dur >= a.min_duration, round(dur, 3), f">= {a.min_duration}s")
    summary = {
        "type": "audio", "file": a.file, "duration_s": round(dur, 3), "lufs": lufs,
        "true_peak_dbtp": tp, "lra": lra, "size_bytes": int(info["format"]["size"]),
    }
    return summary, checks


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="kind", required=True)
    pv = sub.add_parser("video")
    pv.add_argument("file")
    pv.add_argument("--width", type=int, default=1080)
    pv.add_argument("--height", type=int, default=1920)
    pv.add_argument("--fps", type=float, default=30)
    pv.add_argument("--duration", type=float, default=0)
    pv.add_argument("--require-audio", action="store_true")
    pv.add_argument("--lufs", type=float, default=-14.0)
    pv.add_argument("--lufs-tol", type=float, default=1.5)
    pv.add_argument("--out", default="qa.json")
    pa = sub.add_parser("audio")
    pa.add_argument("file")
    pa.add_argument("--sr", type=int, default=24000)
    pa.add_argument("--channels", type=int, default=1)
    pa.add_argument("--lufs", type=float, default=-14.0)
    pa.add_argument("--lufs-tol", type=float, default=1.0)
    pa.add_argument("--min-duration", type=float, default=0)
    pa.add_argument("--out", default="qa.json")
    a = p.parse_args()

    summary, checks = (qa_video if a.kind == "video" else qa_audio)(a)
    passed = all(c["ok"] for c in checks)
    result = {"pass": passed, **summary, "checks": checks}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    for c in checks:
        print(f'{"PASS" if c["ok"] else "FAIL"}  {c["check"]:<16} actual={c["actual"]}  expected={c["expected"]}')
    print("QA:", "PASS" if passed else "FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
