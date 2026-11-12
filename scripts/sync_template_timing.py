#!/usr/bin/env python3
"""Sync NW HTML template slide & Karaoke timings to OmniVoice TTS audio.

Given the 8-slide video code (V01..V24), exact WAV duration, and optional tts_meta.json
(with per_chunk spoken durations), patches nw/index.html in-place so:
  1. Each of the 8 slides of Vxx spans its spoken chunk + inter-sentence pause.
  2. Karaoke word highlighting advances across [start, speechEnd] (locked to speech)
     and holds on the final word during the pause / CTA tail hold.
  3. Slide 8 holds cleanly to an integer-second boundary (ceil(audio_dur + tail_pad)),
     printing JSON {"start": ..., "end": ..., "duration": ..., "slides": [...]} to stdout.
"""
import argparse
import json
import math
import re
import sys


def parse_video_num(code: str) -> int:
    m = re.search(r"(\d+)", str(code).strip())
    if not m:
        raise SystemExit(f"Invalid video code: {code!r} (expected V01..V24)")
    n = int(m.group(1))
    if not (1 <= n <= 24):
        raise SystemExit(f"Video number out of range 1..24: {n}")
    return n


def compute_eight_spans(audio_dur: float, meta_path: str, tail_pad: float):
    total_dur = float(math.ceil(audio_dur + tail_pad))
    if meta_path:
        meta = json.load(open(meta_path, encoding="utf-8"))
        chunks = meta.get("per_chunk") or []
        pause_s = float(meta.get("pause_ms", 220)) / 1000.0
    else:
        chunks = []
        pause_s = 0.0

    if len(chunks) == 8:
        spans = []
        cur = 0.0
        for idx, ch in enumerate(chunks):
            sp = float(ch["audio_s"])
            speech_end = round(cur + sp, 4)
            if idx < 7:
                end = round(speech_end + pause_s, 4)
            else:
                end = round(total_dur, 4)
            spans.append({"start": round(cur, 4), "speechEnd": speech_end, "end": end})
            cur = end
        return spans, total_dur

    # Fallback when per_chunk != 8: divide audio_dur evenly across 8 slides, hold tail on slide 8.
    per = audio_dur / 8.0
    spans = []
    for idx in range(8):
        st = round(idx * per, 4)
        sp_end = round((idx + 1) * per, 4)
        end = sp_end if idx < 7 else round(total_dur, 4)
        spans.append({"start": st, "speechEnd": sp_end, "end": end})
    return spans, total_dur


def patch_html(html: str, vnum: int, eight_spans: list[dict]) -> tuple[str, float, float]:
    base_idx = (vnum - 1) * 8
    all_spans = []
    cur = 0.0
    v_start = 0.0
    v_end = 0.0
    video_offset = 0.0
    for idx in range(192):
        if base_idx <= idx < base_idx + 8:
            if idx == base_idx:
                video_offset = cur
                v_start = round(video_offset + eight_spans[0]["start"], 4)
            rel = eight_spans[idx - base_idx]
            st = round(video_offset + rel["start"], 4)
            sp = round(video_offset + rel["speechEnd"], 4)
            en = round(video_offset + rel["end"], 4)
            if idx == base_idx + 7:
                v_end = en
            all_spans.append({"start": st, "speechEnd": sp, "end": en})
            cur = en
        else:
            st = round(cur, 4)
            en = round(cur + 3.0, 4)
            all_spans.append({"start": st, "speechEnd": en, "end": en})
            cur = en

    total_film = int(math.ceil(cur))
    spans_js = json.dumps(all_spans, separators=(",", ":"))

    # 1. Update #root data-duration
    html_new, n1 = re.subn(r'data-duration="\d+(?:\.\d+)?"', f'data-duration="{total_film}"', html, count=1)
    if n1 != 1:
        raise RuntimeError("Failed to patch data-duration on #root")

    # 2. Inject SLIDE_SPANS + helper right after TOTAL_DURATION declaration
    anchor_vars = (
        "      const SLIDE_DURATION = 3;\n"
        "      const TOTAL_SLIDES = slides.length || 192;\n"
        "      const TOTAL_DURATION = TOTAL_SLIDES * SLIDE_DURATION;"
    )
    if anchor_vars not in html_new:
        raise RuntimeError("Anchor SLIDE_DURATION block not found in index.html")
    replacement_vars = (
        "      const SLIDE_DURATION = 3;\n"
        "      const TOTAL_SLIDES = slides.length || 192;\n"
        f"      const SLIDE_SPANS = {spans_js};\n"
        f"      const RENDER_VIDEO_START = {v_start};\n"
        f"      const RENDER_VIDEO_END = {v_end};\n"
        "      const TOTAL_DURATION = SLIDE_SPANS[SLIDE_SPANS.length - 1].end;\n"
        "      function findSlideAtTime(sec) {\n"
        "        for (let i = 0; i < SLIDE_SPANS.length; i++) {\n"
        "          if (sec < SLIDE_SPANS[i].end || i === SLIDE_SPANS.length - 1) return i;\n"
        "        }\n"
        "        return 0;\n"
        "      }"
    )
    html_new = html_new.replace(anchor_vars, replacement_vars, 1)

    # 3. Patch syncStageAtTime(t) lookup + Karaoke speech progress + video-scoped progress bar & slide badge
    anchor_sync = (
        "        const clamped = Math.max(0, Math.min(TOTAL_DURATION - 0.001, t));\n"
        "        const slideIdx = Math.min(TOTAL_SLIDES - 1, Math.floor(clamped / SLIDE_DURATION));\n"
        "        const localProgress = (clamped - slideIdx * SLIDE_DURATION) / SLIDE_DURATION;"
    )
    if anchor_sync not in html_new:
        raise RuntimeError("Anchor syncStageAtTime lookup not found in index.html")
    replacement_sync = (
        "        const clamped = Math.max(0, Math.min(TOTAL_DURATION - 0.001, t));\n"
        "        const slideIdx = findSlideAtTime(clamped);\n"
        "        const curSpan = SLIDE_SPANS[slideIdx];\n"
        "        const spanDur = Math.max(0.001, curSpan.end - curSpan.start);\n"
        "        const speechDur = Math.max(0.001, curSpan.speechEnd - curSpan.start);\n"
        "        const localProgress = Math.min(0.9999, Math.max(0, (clamped - curSpan.start) / spanDur));\n"
        "        const karaokeProgress = Math.min(0.9999, Math.max(0, (clamped - curSpan.start) / speechDur));"
    )
    html_new = html_new.replace(anchor_sync, replacement_sync, 1)

    anchor_karaoke = (
        "        renderKaraokeWords(slideIdx, localProgress);\n"
        "        progressFill.style.width = `${(clamped / TOTAL_DURATION) * 100}%`;"
    )
    if anchor_karaoke not in html_new:
        raise RuntimeError("Anchor renderKaraokeWords block not found in index.html")
    replacement_karaoke = (
        "        renderKaraokeWords(slideIdx, karaokeProgress);\n"
        "        const vidPct = Math.min(100, Math.max(0, ((clamped - RENDER_VIDEO_START) / Math.max(0.001, RENDER_VIDEO_END - RENDER_VIDEO_START)) * 100));\n"
        "        progressFill.style.width = `${vidPct}%`;"
    )
    html_new = html_new.replace(anchor_karaoke, replacement_karaoke, 1)

    # Show SLIDE 01 / 08 .. SLIDE 08 / 08 for the 8-slide Reel
    anchor_badge = "counterBadge.textContent = `SLIDE ${String(slideIdx + 1).padStart(2, '0')} / ${String(TOTAL_SLIDES).padStart(2, '0')}`;"
    if anchor_badge in html_new:
        html_new = html_new.replace(
            anchor_badge,
            "counterBadge.textContent = `SLIDE ${String((slideIdx % 8) + 1).padStart(2, '0')} / 08`;",
            1,
        )

    # 4. Patch GSAP slide entrance startT
    anchor_gsap = "          const startT = idx * SLIDE_DURATION;"
    if anchor_gsap not in html_new:
        raise RuntimeError("Anchor GSAP startT not found in index.html")
    html_new = html_new.replace(anchor_gsap, "          const startT = SLIDE_SPANS[idx].start;", 1)

    return html_new, v_start, v_end


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True)
    ap.add_argument("--video-code", default="V01")
    ap.add_argument("--audio-duration", type=float, required=True)
    ap.add_argument("--tts-meta", default="")
    ap.add_argument("--tail-pad", type=float, default=0.15)
    a = ap.parse_args()

    vnum = parse_video_num(a.video_code)
    eight_spans, _ = compute_eight_spans(a.audio_duration, a.tts_meta, a.tail_pad)
    raw = open(a.html, encoding="utf-8").read()
    patched, v_start, v_end = patch_html(raw, vnum, eight_spans)
    with open(a.html, "w", encoding="utf-8") as f:
        f.write(patched)

    out = {
        "video_code": f"V{vnum:02d}",
        "start": int(round(v_start)) if abs(v_start - round(v_start)) < 1e-6 else v_start,
        "end": int(round(v_end)) if abs(v_end - round(v_end)) < 1e-6 else v_end,
        "duration": round(v_end - v_start, 4),
        "audio_duration": round(a.audio_duration, 4),
        "slides": eight_spans,
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
