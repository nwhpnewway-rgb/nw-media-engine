#!/usr/bin/env python3
"""NEWWAY Reel v2 builder — script spec (reel.json) -> TTS shards -> voice timings -> HyperFrames project.

Sub-commands
  shards      : split the spec's karaoke parts into N TTS shard files (one part per line) + parts.json
  assemble    : join per-part WAVs (chunk_XX.wav) with natural pauses -> raw voice WAV + timings.json
  fake-timings: estimate timings from text length (local preview without TTS)
  build       : copy templates/nw_reel_v2, inject reel-data.js (spec + timings) and composition duration
"""
import argparse
import json
import math
import shutil
import wave
from pathlib import Path

SR = 24000
FPS = 30
LEAD_IN = 0.20     # silence before first sentence
INTRA = 0.28       # pause between the 2 parts of one slide
INTER = 0.50       # pause at slide change
TAIL = 1.40        # hold on the CTA after the last word


def load_spec(path):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    assert spec.get("slides"), "spec has no slides"
    for i, s in enumerate(spec["slides"], 1):
        assert s.get("parts"), f"slide {i} has no parts"
        for p in s["parts"]:
            assert p.get("display") and p.get("tts"), f"slide {i}: part needs display + tts"
            assert "\n" not in p["tts"], "tts must be single-line"
    return spec


def flat_parts(spec):
    out = []
    for si, s in enumerate(spec["slides"]):
        for pi, p in enumerate(s["parts"]):
            out.append({"index": len(out), "slide": si, "part": pi, "display": p["display"].strip(), "tts": p["tts"].strip()})
    return out


def cmd_shards(a):
    spec = load_spec(a.spec)
    parts = flat_parts(spec)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n = max(1, min(a.shards, len(parts)))
    # balance by characters (greedy, contiguous blocks keep order simple)
    total = sum(len(p["tts"]) for p in parts)
    target = total / n
    shards, cur, acc = [], [], 0
    for p in parts:
        if cur and acc + len(p["tts"]) / 2 > target * (len(shards) + 1) and len(shards) < n - 1:
            shards.append(cur)
            cur = []
        cur.append(p)
        acc += len(p["tts"])
    shards.append(cur)
    manifest = []
    for k, sh in enumerate(shards):
        (out / f"shard_{k}.txt").write_text("\n".join(p["tts"] for p in sh) + "\n", encoding="utf-8")
        manifest.append({"shard": k, "offset": sh[0]["index"], "count": len(sh), "chars": sum(len(p["tts"]) for p in sh)})
    (out / "parts.json").write_text(json.dumps({"parts": parts, "shards": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    # matrix helper for GitHub Actions
    print("MATRIX=" + json.dumps([m["shard"] for m in manifest]))


def read_pcm16(path):
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2, f"{path}: need 24k mono s16"
        return w.readframes(w.getnframes())


def build_timings(spec, durations):
    """durations[i] = speech seconds of flat part i -> timings dict (all seconds)."""
    parts = flat_parts(spec)
    t = LEAD_IN
    slides = []
    k = 0
    n_sl = len(spec["slides"])
    for si, s in enumerate(spec["slides"]):
        st = 0.0 if si == 0 else t
        plist = []
        for pi, _ in enumerate(s["parts"]):
            d = durations[k]
            p_start = t
            speech_end = t + d
            last_in_slide = pi == len(s["parts"]) - 1
            pause = (TAIL if si == n_sl - 1 else INTER) if last_in_slide else INTRA
            t = speech_end + pause
            plist.append({"start_s": round(p_start, 3), "speech_end_s": round(speech_end, 3), "end_s": round(t, 3),
                          "display": parts[k]["display"]})
            k += 1
        slides.append({"start_s": round(st, 3), "end_s": round(t, 3), "parts": plist})
    total = math.ceil(t * FPS) / FPS
    slides[-1]["end_s"] = round(total, 3)
    slides[-1]["parts"][-1]["end_s"] = round(total, 3)
    return {"fps": FPS, "total_s": round(total, 3), "lead_in": LEAD_IN, "intra": INTRA, "inter": INTER, "tail": TAIL,
            "slides": slides}


def cmd_assemble(a):
    spec = load_spec(a.spec)
    parts = flat_parts(spec)
    cdir = Path(a.chunks_dir)
    pcms = [read_pcm16(cdir / f"chunk_{p['index']:02d}.wav") for p in parts]
    durs = [len(x) / 2 / SR for x in pcms]
    tm = build_timings(spec, durs)
    sil = lambda sec: b"\x00\x00" * int(round(sec * SR))
    buf = bytearray()
    k = 0
    for s in tm["slides"]:
        for p in s["parts"]:
            pos = int(round(p["start_s"] * SR)) * 2
            if len(buf) < pos:
                buf.extend(b"\x00" * (pos - len(buf)))
            buf.extend(pcms[k])
            k += 1
    end = int(round(tm["total_s"] * SR)) * 2
    buf.extend(b"\x00" * max(0, end - len(buf)))
    del buf[end:]
    with wave.open(a.out_wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(bytes(buf))
    tm["speech_s"] = round(sum(durs), 3)
    tm["part_durations_s"] = [round(d, 3) for d in durs]
    Path(a.out_timings).write_text(json.dumps(tm, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"total_s": tm["total_s"], "speech_s": tm["speech_s"], "parts": len(durs)}))


def cmd_fake(a):
    spec = load_spec(a.spec)
    durs = [max(2.0, len(p["tts"]) / a.cps) for p in flat_parts(spec)]
    tm = build_timings(spec, durs)
    tm["estimated"] = True
    Path(a.out_timings).write_text(json.dumps(tm, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"total_s": tm["total_s"]}))


def cmd_build(a):
    spec = load_spec(a.spec)
    tm = json.loads(Path(a.timings).read_text(encoding="utf-8"))
    assert len(tm["slides"]) == len(spec["slides"]), "timings/spec slide count mismatch"
    tpl, out = Path(a.template), Path(a.out_dir)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(tpl, out, ignore=shutil.ignore_patterns("bg"))
    (out / "assets" / "bg").mkdir(parents=True, exist_ok=True)
    for s in spec["slides"]:
        src = tpl / "assets" / "bg" / f"{s['bg']}.jpg"
        assert src.exists(), f"missing background {src}"
        shutil.copy2(src, out / "assets" / "bg" / src.name)
    html = (out / "index.html").read_text(encoding="utf-8")
    html = html.replace("__DURATION__", f"{tm['total_s']}")
    (out / "index.html").write_text(html, encoding="utf-8")
    data = {"spec": spec, "timings": tm}
    (out / "reel-data.js").write_text("window.__REEL = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "duration": tm["total_s"]}))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("shards"); p.add_argument("--spec", required=True); p.add_argument("--shards", type=int, default=4); p.add_argument("--out-dir", required=True)
    p = sp.add_parser("assemble"); p.add_argument("--spec", required=True); p.add_argument("--chunks-dir", required=True); p.add_argument("--out-wav", required=True); p.add_argument("--out-timings", required=True)
    p = sp.add_parser("fake-timings"); p.add_argument("--spec", required=True); p.add_argument("--cps", type=float, default=15.0); p.add_argument("--out-timings", required=True)
    p = sp.add_parser("build"); p.add_argument("--spec", required=True); p.add_argument("--timings", required=True); p.add_argument("--template", default="templates/nw_reel_v2"); p.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    {"shards": cmd_shards, "assemble": cmd_assemble, "fake-timings": cmd_fake, "build": cmd_build}[a.cmd](a)


if __name__ == "__main__":
    main()
