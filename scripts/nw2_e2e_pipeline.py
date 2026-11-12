#!/usr/bin/env python3
"""
nw2_e2e_pipeline.py — Dây chuyền dựng Reels E2E ≥ 60s chuẩn HTML Template NW2 + OmniVoice Nhất Phong
100% Zero-VPS (chạy trên GitHub Actions runner chuẩn, tương thích cả repo Private lẫn Public).

4 lệnh con:
  1. prepare-chunks  : Xuất danh sách 16 câu đọc (chunks.json) từ nw2_e2e_scripts.json (hoặc tự tách từ template-data.json cho V03..V24).
  2. assemble-audio  : Ghép 16 file WAV từng câu + nhịp thở phát thanh viên, đảm bảo >= 61.0s, chuẩn hoá 2-pass loudnorm -14.0 LUFS (24kHz PCM16 Mono) và xuất timings.json khớp tới từng mili-giây.
  3. build-html      : Cắt đúng 8 slide của Video (V01..V24) từ gói NW2 offline, gắn window.__NW2_TIMINGS (2 nhịp Karaoke/slide + chuyển slide khớp giọng đọc + thanh tiến trình 0->100%).
  4. mux-qa          : Ghép video HyperFrames + WAV Nhất Phong -> MP4 H.264 High@L4.2 / AAC 192k (-14 LUFS), trích bảng 8 frame giữa mỗi slide, chạy cổng QA nghiêm ngặt (<= 35 MB, >= 60s).
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import wave
import zipfile
from pathlib import Path


def run_cmd(cmd, check=True):
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def load_video_script(scripts_json_path: Path, base_zip_path: Path, video_code: str):
    vcode = video_code.upper().strip()
    if not re.match(r"^V(0[1-9]|1[0-9]|2[0-4])$", vcode):
        raise ValueError(f"video_code không hợp lệ (cần V01..V24): {video_code}")
    vid_num = int(vcode[1:])
    vid_id = f"v{vid_num}"

    if scripts_json_path and scripts_json_path.exists():
        sdata = json.loads(scripts_json_path.read_text(encoding="utf-8"))
        if vcode in sdata.get("videos", {}):
            return sdata["videos"][vcode], float(sdata.get("target_min_duration_s", 61.5))

    # Fallback tự động cho V03..V24 từ template-data.json trong gói NW2
    with zipfile.ZipFile(base_zip_path, "r") as zf:
        tdata = json.loads(zf.read("nw/template-data.json").decode("utf-8"))
    vobj = tdata["videos"][vid_num - 1]
    slides_out = []
    for idx, sl in enumerate(vobj["slides"], 1):
        sub = sl.get("karaokeSubtitle", "").strip()
        fields = [re.sub(r"<[^>]+>", "", f).strip() for f in sl.get("editableFields", [])]
        extra = next((f for f in fields if len(f) > 38 and f != sub and "•" not in f), "")
        if not extra:
            extra = "Giải pháp vận tải đa phương thức an toàn, chuẩn tiến độ cùng New Way Logistics."
        tts_a = sub.replace("NEWWAY", "New Way").replace("newwaylogisticsjsc", "New Way Logistics").replace("CO2", "Cê Ô hai").replace("CO₂", "Cê Ô hai")
        tts_b = extra.replace("NEWWAY", "New Way").replace("newwaylogisticsjsc", "New Way Logistics").replace("CO2", "Cê Ô hai").replace("CO₂", "Cê Ô hai")
        slides_out.append({
            "slide": idx,
            "parts": [
                {"display": sub, "tts": tts_a},
                {"display": extra, "tts": tts_b},
            ],
        })
    return {"video_id": vid_id, "title": vobj.get("title", vcode), "slides": slides_out}, 61.5


def cmd_prepare_chunks(args):
    vspec, min_dur = load_video_script(
        Path(args.scripts_json) if args.scripts_json else None,
        Path(args.base_zip),
        args.video_code,
    )
    chunks = []
    idx = 0
    for sl in vspec["slides"]:
        for p_idx, part in enumerate(sl["parts"]):
            chunks.append({
                "chunk_index": idx,
                "slide": sl["slide"],
                "part": p_idx + 1,
                "display": part["display"].strip(),
                "words": part["display"].strip().split(),
                "tts": part["tts"].strip(),
            })
            idx += 1
    out = {
        "video_code": args.video_code.upper(),
        "video_id": vspec["video_id"],
        "title": vspec["title"],
        "target_min_duration_s": min_dur,
        "total_chunks": len(chunks),
        "total_tts_chars": sum(len(c["tts"]) for c in chunks),
        "chunks": chunks,
    }
    Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[prepare-chunks] {out['video_code']} -> {out['total_chunks']} chunks, {out['total_tts_chars']} chars")


def read_wav_mono_24k(path: Path) -> bytes:
    """Chuyển bất kỳ WAV đầu vào nào về chuẩn 24000 Hz, mono, pcm_s16le bằng ffmpeg để ghép chính xác tuyệt đối."""
    tmp = path.with_suffix(".norm24k.wav")
    run_cmd(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(tmp)])
    with wave.open(str(tmp), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
    tmp.unlink(missing_ok=True)
    return frames


def cmd_assemble_audio(args):
    meta = json.loads(Path(args.chunks_json).read_text(encoding="utf-8"))
    chunks_dir = Path(args.chunks_dir)
    sr = 24000
    bytes_per_sample = 2

    raw_pcm = {}
    speech_total_s = 0.0
    for c in meta["chunks"]:
        i = c["chunk_index"]
        wav_path = chunks_dir / f"chunk_{i:02d}.wav"
        if not wav_path.exists():
            raise FileNotFoundError(f"Thiếu file WAV của chunk {i}: {wav_path}")
        pcm = read_wav_mono_24k(wav_path)
        raw_pcm[i] = pcm
        speech_total_s += (len(pcm) // bytes_per_sample) / sr

    intra_pause = 0.20   # nghỉ giữa câu A và câu B trong cùng 1 slide
    inter_pause = 0.34   # nghỉ chuyển slide
    lead_in = 0.12       # đầu video
    tail_out = 0.48      # ngân cuối slide 08 CTA

    n_slides = 8
    base_total = lead_in + speech_total_s + n_slides * intra_pause + (n_slides - 1) * inter_pause + tail_out
    min_dur = max(61.0, float(meta.get("target_min_duration_s", 61.5)))
    if base_total < min_dur:
        extra = min_dur - base_total
        # Phân bổ thêm tối đa nhịp thở tự nhiên giữa các slide nếu thiếu vài giây
        add_per_gap = extra / (n_slides * 2)
        intra_pause += add_per_gap
        inter_pause += add_per_gap

    def silence(sec: float) -> bytes:
        return b"\x00\x00" * int(round(sec * sr))

    # Căn tổng thời lượng khớp bội số frame 30fps để video/audio khớp 100%
    assembled = bytearray()
    slides_timing = []
    cur_samples = 0

    by_slide = {}
    for c in meta["chunks"]:
        by_slide.setdefault(c["slide"], []).append(c)

    for s_idx in range(1, n_slides + 1):
        parts_c = by_slide[s_idx]
        slide_start_sample = cur_samples
        part_entries = []

        if s_idx == 1:
            lead = silence(lead_in)
            assembled.extend(lead)
            cur_samples += len(lead) // 2

        for p_pos, c in enumerate(parts_c):
            p_start = cur_samples
            pcm = raw_pcm[c["chunk_index"]]
            assembled.extend(pcm)
            cur_samples += len(pcm) // 2
            if p_pos < len(parts_c) - 1:
                pad = silence(intra_pause)
            elif s_idx < n_slides:
                pad = silence(inter_pause)
            else:
                pad = silence(tail_out)
            assembled.extend(pad)
            cur_samples += len(pad) // 2
            p_end = cur_samples
            part_entries.append({
                "part": p_pos + 1,
                "words": c["words"],
                "display": c["display"],
                "start_s": round(p_start / sr, 4),
                "end_s": round(p_end / sr, 4),
                "dur_s": round((p_end - p_start) / sr, 4),
            })

        slide_end_sample = cur_samples
        s_start_s = slide_start_sample / sr
        s_end_s = slide_end_sample / sr
        s_dur = max(0.001, s_end_s - s_start_s)
        # Chuẩn hoá ratio nội bộ trong slide [0..1] cho Karaoke 2 nhịp
        acc = 0.0
        for pe in part_entries:
            # Slide 1 bao gồm lead_in vào part 1
            r0 = acc
            acc = min(1.0, (pe["end_s"] - s_start_s) / s_dur)
            pe["ratio_start"] = round(r0, 5)
            pe["ratio_end"] = round(acc, 5)
        part_entries[-1]["ratio_end"] = 1.0

        slides_timing.append({
            "slide": s_idx,
            "start": round(s_start_s, 4),
            "end": round(s_end_s, 4),
            "duration": round(s_dur, 4),
            "parts": part_entries,
        })

    # Snap tổng mẫu lên khung 30fps chẵn (sr=24000 -> 1 frame 30fps = 800 samples)
    rem = cur_samples % 800
    if rem != 0:
        add_samples = 800 - rem
        assembled.extend(b"\x00\x00" * add_samples)
        cur_samples += add_samples
        slides_timing[-1]["end"] = round(cur_samples / sr, 4)
        slides_timing[-1]["duration"] = round(slides_timing[-1]["end"] - slides_timing[-1]["start"], 4)

    total_duration_s = round(cur_samples / sr, 4)
    out_wav = Path(args.out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    raw_concat = out_wav.with_suffix(".raw_concat.wav")
    with wave.open(str(raw_concat), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(assembled))

    # Chuẩn hoá âm lượng phát sóng 2-pass EBU R128: -14.0 LUFS, True Peak <= -1.5 dBTP
    p1 = run_cmd([
        "ffmpeg", "-hide_banner", "-i", str(raw_concat),
        "-af", "loudnorm=I=-14.0:TP=-1.5:LRA=11.0:print_format=json",
        "-f", "null", "-"
    ])
    m = re.search(r"\{\s*\"input_i\".*?\}", p1.stderr, re.S)
    if m:
        ln = json.loads(m.group(0))
        af = (
            f"loudnorm=I=-14.0:TP=-1.5:LRA=11.0:"
            f"measured_I={ln['input_i']}:measured_TP={ln['input_tp']}:"
            f"measured_LRA={ln['input_lra']}:measured_thresh={ln['input_thresh']}:"
            f"offset={ln['target_offset']}:linear=true"
        )
    else:
        af = "loudnorm=I=-14.0:TP=-1.5:LRA=11.0"

    run_cmd([
        "ffmpeg", "-y", "-v", "error", "-i", str(raw_concat),
        "-af", af, "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)
    ])
    raw_concat.unlink(missing_ok=True)

    timings = {
        "video_code": meta["video_code"],
        "video_id": meta["video_id"],
        "title": meta["title"],
        "fps": 30,
        "total_frames": int(round(total_duration_s * 30)),
        "total_duration": total_duration_s,
        "speech_duration": round(speech_total_s, 4),
        "slides": slides_timing,
    }
    Path(args.out_timings).write_text(json.dumps(timings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[assemble-audio] total={total_duration_s:.2f}s ({timings['total_frames']} frames @ 30fps) -> {out_wav}")


JS_SYNC_REPLACEMENT = r"""      const __TIMINGS = window.__NW2_TIMINGS;
      const slides = Array.from(document.querySelectorAll('.slide'));
      const SLIDE_DURATION = 3;
      const TOTAL_SLIDES = slides.length || 8;
      const TOTAL_DURATION = __TIMINGS ? __TIMINGS.total_duration : (TOTAL_SLIDES * SLIDE_DURATION);"""

JS_KARAOKE_AND_STAGE = r"""      let currentRenderedKey = '';
      let activeBg = 'v1';

      function findSlideAtTime(clamped) {
        if (!__TIMINGS || !__TIMINGS.slides) {
          const idx = Math.min(TOTAL_SLIDES - 1, Math.floor(clamped / SLIDE_DURATION));
          return { idx, localProgress: (clamped - idx * SLIDE_DURATION) / SLIDE_DURATION, sMeta: null };
        }
        const arr = __TIMINGS.slides;
        for (let i = 0; i < arr.length; i++) {
          if (clamped < arr[i].end || i === arr.length - 1) {
            const dur = Math.max(0.001, arr[i].end - arr[i].start);
            const lp = Math.max(0, Math.min(0.9999, (clamped - arr[i].start) / dur));
            return { idx: i, localProgress: lp, sMeta: arr[i] };
          }
        }
        return { idx: 0, localProgress: 0, sMeta: arr[0] };
      }

      function renderKaraokeWords(slideIdx, localProgress, sMeta) {
        let words = SLIDE_SUBTITLES[slideIdx] || SLIDE_SUBTITLES[0] || [];
        let partProgress = localProgress;
        let partIdx = 0;
        if (sMeta && Array.isArray(sMeta.parts) && sMeta.parts.length > 0) {
          let chosen = sMeta.parts[sMeta.parts.length - 1];
          for (let p = 0; p < sMeta.parts.length; p++) {
            if (localProgress <= sMeta.parts[p].ratio_end || p === sMeta.parts.length - 1) {
              chosen = sMeta.parts[p];
              partIdx = p;
              break;
            }
          }
          words = chosen.words;
          const spanRatio = Math.max(0.001, chosen.ratio_end - chosen.ratio_start);
          partProgress = Math.max(0, Math.min(0.9999, (localProgress - chosen.ratio_start) / spanRatio));
        }
        const key = `${slideIdx}:${partIdx}`;
        if (currentRenderedKey !== key) {
          currentRenderedKey = key;
          karaokeBox.innerHTML = words
            .map((w, i) => `<span class="k-word" data-widx="${i}">${w}</span>`)
            .join('');
        }
        const activeWordIndex = Math.min(words.length - 1, Math.floor(partProgress * words.length));
        const wordSpans = karaokeBox.querySelectorAll('.k-word');
        wordSpans.forEach((span, idx) => {
          span.classList.toggle('passed', idx < activeWordIndex);
          span.classList.toggle('active', idx === activeWordIndex);
        });
      }

      function syncStageAtTime(t) {
        const clamped = Math.max(0, Math.min(TOTAL_DURATION - 0.001, t));
        const found = findSlideAtTime(clamped);
        const slideIdx = found.idx;
        const localProgress = found.localProgress;"""


def cmd_build_html(args):
    timings = json.loads(Path(args.timings_json).read_text(encoding="utf-8"))
    target_vid = timings["video_id"]
    total_dur = timings["total_duration"]

    out_dir = Path(args.out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(args.base_zip, "r") as zf:
        for member in zf.namelist():
            if ".hyperframes/" in member:
                continue
            rel = member[len("nw/"):] if member.startswith("nw/") else member
            if not rel:
                continue
            dest = out_dir / rel
            if member.endswith("/"):
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))

    index_path = out_dir / "index.html"
    html = index_path.read_text(encoding="utf-8")

    # 1. Cắt giữ đúng 8 <section class="slide ..."> của target_vid trong <main id="slides-stage">
    stage_re = re.compile(r'(<main id="slides-stage">)(.*?)(</main>)', re.S)
    m_stage = stage_re.search(html)
    if not m_stage:
        raise RuntimeError("Không tìm thấy <main id=\"slides-stage\"> trong NW2 index.html")

    sec_re = re.compile(r'(<section class="slide[^"]*"[^>]*>.*?</section>)', re.S)
    kept = []
    for sec in sec_re.findall(m_stage.group(2)):
        if f'data-video-id="{target_vid}"' in sec:
            new_idx = len(kept)
            sec = re.sub(r'data-slide-index="\d+"', f'data-slide-index="{new_idx}"', sec, count=1)
            if new_idx == 0:
                sec = re.sub(r'class="slide(?:\s+is-active)?"', 'class="slide is-active"', sec, count=1)
            else:
                sec = re.sub(r'class="slide\s+is-active"', 'class="slide"', sec, count=1)
            # Đồng bộ nhãn SLIDE xx/96 trên bìa về SLIDE 01/08
            sec = re.sub(r'SLIDE\s+\d+/\d+', f'SLIDE {new_idx+1:02d}/08', sec)
            kept.append(sec)

    if len(kept) != 8:
        raise RuntimeError(f"Kỳ vọng 8 slide cho {target_vid}, tìm thấy {len(kept)}")

    html = html[:m_stage.start()] + m_stage.group(1) + "\n" + "\n".join(kept) + "\n" + m_stage.group(3) + html[m_stage.end():]

    # 2. Cập nhật #root[data-duration] và bộ đếm ban đầu SLIDE 01 / 08
    html = re.sub(r'data-duration="[^"]+"', f'data-duration="{total_dur}"', html, count=1)
    html = html.replace("SLIDE 01 / 192", "SLIDE 01 / 08")

    # 3. Nhúng window.__NW2_TIMINGS trước window.__timelines
    timings_js = f"      window.__NW2_TIMINGS = {json.dumps(timings, ensure_ascii=False)};\n"
    html = html.replace("      window.__timelines = window.__timelines || {};", timings_js + "      window.__timelines = window.__timelines || {};", 1)

    # 4. Nâng cấp biến thời lượng & hàm Karaoke 2 nhịp + syncStageAtTime + GSAP startT
    old_consts = """      const slides = Array.from(document.querySelectorAll('.slide'));
      const SLIDE_DURATION = 3;
      const TOTAL_SLIDES = slides.length || 192;
      const TOTAL_DURATION = TOTAL_SLIDES * SLIDE_DURATION;"""
    if old_consts not in html:
        raise RuntimeError("Không khớp khối hằng số SLIDE_DURATION trong index.html")
    html = html.replace(old_consts, JS_SYNC_REPLACEMENT, 1)

    old_karaoke_block_re = re.compile(
        r"      let currentRenderedSlide = -1;\s*let activeBg = 'v1';.*?function syncStageAtTime\(t\) \{\s*const clamped = Math\.max\(0, Math\.min\(TOTAL_DURATION - 0\.001, t\)\);\s*const slideIdx = Math\.min\(TOTAL_SLIDES - 1, Math\.floor\(clamped / SLIDE_DURATION\)\);\s*const localProgress = \(clamped - slideIdx \* SLIDE_DURATION\) / SLIDE_DURATION;",
        re.S,
    )
    if not old_karaoke_block_re.search(html):
        raise RuntimeError("Không khớp khối renderKaraokeWords/syncStageAtTime trong index.html")
    html = old_karaoke_block_re.sub(lambda _: JS_KARAOKE_AND_STAGE, html, count=1)

    html = html.replace("renderKaraokeWords(slideIdx, localProgress);", "renderKaraokeWords(slideIdx, localProgress, found.sMeta);", 1)
    html = html.replace("const startT = idx * SLIDE_DURATION;", "const startT = (__TIMINGS && __TIMINGS.slides[idx]) ? __TIMINGS.slides[idx].start : (idx * SLIDE_DURATION);", 1)

    # Kiểm tra an toàn: 0 link ngoài
    ext = re.findall(r'https?://[^\s"\'<>)]+', html)
    if ext:
        raise RuntimeError(f"Phát hiện URL ngoài trong index.html: {ext}")

    index_path.write_text(html, encoding="utf-8")
    print(f"[build-html] Đã xuất gói NW2 {timings['video_code']} (8 slides, {total_dur}s, 0 external URL) tại {out_dir}")


def measure_loudnorm(media_path: Path):
    p = run_cmd(["ffmpeg", "-hide_banner", "-i", str(media_path), "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    m = re.search(r"\{\s*\"input_i\".*?\}", p.stderr, re.S)
    if not m:
        return {"lufs": None, "tp": None, "lra": None}
    d = json.loads(m.group(0))
    return {"lufs": float(d["input_i"]), "tp": float(d["input_tp"]), "lra": float(d["input_lra"])}


def cmd_mux_qa(args):
    timings = json.loads(Path(args.timings_json).read_text(encoding="utf-8"))
    out_mp4 = Path(args.out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)

    # Ghép H.264 High@L4.2 (1080x1920, 30fps, yuv420p, <=5Mbps để <35MB) + AAC 192k 48kHz
    run_cmd([
        "ffmpeg", "-y", "-v", "error",
        "-i", str(args.raw_mp4),
        "-i", str(args.wav),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.2", "-pix_fmt", "yuv420p",
        "-r", "30", "-crf", "18", "-maxrate", "4500k", "-bufsize", "9000k",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "1",
        "-shortest", "-movflags", "+faststart",
        str(out_mp4),
    ])

    # Trích 8 khung hình ở đúng điểm giữa từng slide ghép thành bảng đối chứng 4x2
    frames_png = Path(args.out_frames_png)
    tmp_fdir = out_mp4.parent / "_qa_slide_frames"
    tmp_fdir.mkdir(parents=True, exist_ok=True)
    frame_inputs = []
    for sl in timings["slides"]:
        mid_t = round((sl["start"] + sl["end"]) / 2.0, 3)
        fp = tmp_fdir / f"s{sl['slide']:02d}.png"
        run_cmd(["ffmpeg", "-y", "-v", "error", "-ss", str(mid_t), "-i", str(out_mp4), "-frames:v", "1", "-vf", "scale=270:480", str(fp)])
        frame_inputs.extend(["-i", str(fp)])
    run_cmd([
        "ffmpeg", "-y", "-v", "error", *frame_inputs,
        "-filter_complex", "xstack=inputs=8:layout=0_0|w0_0|w0+w1_0|w0+w1+w2_0|0_h0|w0_h0|w0+w1_h0|w0+w1+w2_h0",
        str(frames_png),
    ])
    shutil.rmtree(tmp_fdir, ignore_errors=True)

    # Kiểm định QA bằng ffprobe + loudnorm
    pr = json.loads(run_cmd([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size,bit_rate:stream=codec_type,codec_name,profile,level,width,height,r_frame_rate,pix_fmt,sample_rate,channels",
        "-of", "json", str(out_mp4),
    ]).stdout)

    vstream = next((s for s in pr["streams"] if s.get("codec_type") == "video"), {})
    astream = next((s for s in pr["streams"] if s.get("codec_type") == "audio"), {})
    fmt = pr["format"]
    dur = float(fmt["duration"])
    size_b = int(fmt["size"])
    ln = measure_loudnorm(out_mp4)

    fps_parts = vstream.get("r_frame_rate", "0/1").split("/")
    fps_val = round(float(fps_parts[0]) / max(1.0, float(fps_parts[1])), 2)

    checks = [
        {"check": "video_codec", "ok": vstream.get("codec_name") == "h264", "actual": vstream.get("codec_name"), "expected": "h264"},
        {"check": "video_profile", "ok": vstream.get("profile") == "High", "actual": vstream.get("profile"), "expected": "High"},
        {"check": "resolution", "ok": f"{vstream.get('width')}x{vstream.get('height')}" == "1080x1920", "actual": f"{vstream.get('width')}x{vstream.get('height')}", "expected": "1080x1920"},
        {"check": "fps", "ok": abs(fps_val - 30.0) < 0.05, "actual": fps_val, "expected": 30.0},
        {"check": "pix_fmt", "ok": vstream.get("pix_fmt") == "yuv420p", "actual": vstream.get("pix_fmt"), "expected": "yuv420p"},
        {"check": "audio_codec", "ok": astream.get("codec_name") == "aac", "actual": astream.get("codec_name"), "expected": "aac"},
        {"check": "duration_min_60s", "ok": dur >= 60.0, "actual": round(dur, 2), "expected": ">= 60.0s"},
        {"check": "loudness_lufs", "ok": ln["lufs"] is not None and abs(ln["lufs"] - (-14.0)) <= 1.2, "actual": ln["lufs"], "expected": "-14.0±1.2 LUFS"},
        {"check": "true_peak_dbtp", "ok": ln["tp"] is not None and ln["tp"] <= -0.8, "actual": ln["tp"], "expected": "<= -0.8 dBTP"},
        {"check": "size_under_35mb", "ok": 0 < size_b <= 35 * 1024 * 1024, "actual": size_b, "expected": "<= 36700160"},
    ]
    qa = {
        "pass": all(c["ok"] for c in checks),
        "video_code": timings["video_code"],
        "title": timings["title"],
        "duration_s": round(dur, 3),
        "size_bytes": size_b,
        "size_mb": round(size_b / (1024 * 1024), 2),
        "lufs": ln["lufs"],
        "true_peak_dbtp": ln["tp"],
        "checks": checks,
    }
    Path(args.out_qa).write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Báo cáo Reels E2E — NW2 {timings['video_code']} ({timings['title']})",
        "",
        f"- **QA tổng thể:** `{'PASS' if qa['pass'] else 'FAIL'}`",
        f"- **Thời lượng:** `{qa['duration_s']} giây` (chuẩn ≥ 60s)",
        f"- **Dung lượng MP4:** `{qa['size_mb']} MB` ({qa['size_bytes']} byte, trần cổng B' 35 MB)",
        f"- **Âm lượng tích hợp:** `{qa['lufs']} LUFS` · **True Peak:** `{qa['true_peak_dbtp']} dBTP` (giọng Nhất Phong OmniVoice)",
        f"- **Hình ảnh:** `1080x1920`, `30fps`, `H.264 High@L4.2`, `8 slides NW2 khớp từng nhịp câu`",
        "",
        "## Bảng mốc thời gian 8 Slide",
        "| Slide | Bắt đầu (s) | Kết thúc (s) | Thời lượng (s) |",
        "|---|---|---|---|",
    ]
    for sl in timings["slides"]:
        lines.append(f"| Slide {sl['slide']:02d} | {sl['start']:.2f} | {sl['end']:.2f} | {sl['duration']:.2f} |")
    lines += ["", "## Chi tiết kiểm định QA", "```json", json.dumps(qa, ensure_ascii=False, indent=2), "```", ""]
    Path(args.out_report).write_text("\n".join(lines), encoding="utf-8")
    print(f"[mux-qa] pass={qa['pass']} dur={qa['duration_s']}s size={qa['size_mb']}MB lufs={qa['lufs']}")
    if not qa["pass"]:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("prepare-chunks")
    p1.add_argument("--base-zip", required=True)
    p1.add_argument("--scripts-json", default="")
    p1.add_argument("--video-code", default="V02")
    p1.add_argument("--out-json", required=True)
    p1.set_defaults(func=cmd_prepare_chunks)

    p2 = sub.add_parser("assemble-audio")
    p2.add_argument("--chunks-json", required=True)
    p2.add_argument("--chunks-dir", required=True)
    p2.add_argument("--out-wav", required=True)
    p2.add_argument("--out-timings", required=True)
    p2.set_defaults(func=cmd_assemble_audio)

    p3 = sub.add_parser("build-html")
    p3.add_argument("--base-zip", required=True)
    p3.add_argument("--timings-json", required=True)
    p3.add_argument("--out-dir", required=True)
    p3.set_defaults(func=cmd_build_html)

    p4 = sub.add_parser("mux-qa")
    p4.add_argument("--raw-mp4", required=True)
    p4.add_argument("--wav", required=True)
    p4.add_argument("--timings-json", required=True)
    p4.add_argument("--out-mp4", required=True)
    p4.add_argument("--out-frames-png", required=True)
    p4.add_argument("--out-qa", required=True)
    p4.add_argument("--out-report", required=True)
    p4.set_defaults(func=cmd_mux_qa)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
