#!/usr/bin/env python3
"""OmniVoice TTS for nw-media-engine (CPU-friendly, long-text safe).

Splits the script into sentence groups, builds the voice-clone prompt ONCE from the
reference WAV (e.g. nwl_voices/vi_male_nhat_phong_24kHz.wav), generates each chunk,
then joins them with short pauses into a single 24 kHz mono float WAV.
Loudness normalisation (-14 LUFS) is done afterwards with ffmpeg (see workflow).
"""
import argparse
import gc
import json
import os
import re
import time

import numpy as np
import soundfile as sf
import torch

SR = 24000


def split_text(text: str, max_chars: int):
    text = re.sub(r"[ \t]+", " ", text.replace("\r", "")).strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    chunks = []
    for para in paragraphs:
        sentences = re.split(r"(?<=[.!?…;:])\s+", para)
        buf = ""
        for s in sentences:
            if not s:
                continue
            if buf and len(buf) + 1 + len(s) > max_chars:
                chunks.append(buf)
                buf = s
            else:
                buf = f"{buf} {s}".strip()
        if buf:
            chunks.append(buf)
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-file", required=True)
    ap.add_argument("--ref-audio", required=True)
    ap.add_argument("--ref-text", default="")
    ap.add_argument("--language", default="vi")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--num-step", type=int, default=32)
    ap.add_argument("--max-chars", type=int, default=220)
    ap.add_argument("--pause-ms", type=int, default=220)
    ap.add_argument("--model", default="k2-fsa/OmniVoice")
    ap.add_argument("--out", required=True)
    ap.add_argument("--meta", default="tts_meta.json")
    ap.add_argument("--chunks-dir", default="", help="also save each chunk as chunk_XX.wav (PCM16)")
    ap.add_argument("--index-offset", type=int, default=0, help="first chunk index for chunk_XX naming")
    ap.add_argument("--one-per-line", action="store_true", help="each non-empty line is exactly one chunk")
    a = ap.parse_args()

    torch.set_num_threads(max(1, torch.get_num_threads()))
    text = open(a.text_file, encoding="utf-8").read()
    if a.one_per_line:
        chunks = [ln.strip() for ln in text.splitlines() if ln.strip()]
    else:
        chunks = split_text(text, a.max_chars)
    if not chunks:
        raise SystemExit("empty script text")

    from omnivoice import OmniVoice

    t0 = time.time()
    model = OmniVoice.from_pretrained(a.model, device_map="cpu", dtype=torch.float32,
                                      asr_device="cpu")
    t_load = time.time() - t0

    t1 = time.time()
    prompt = model.create_voice_clone_prompt(ref_audio=a.ref_audio,
                                             ref_text=a.ref_text.strip() or None)
    t_prompt = time.time() - t1

    pause = np.zeros(int(SR * a.pause_ms / 1000), dtype=np.float32)
    pieces, per_chunk = [], []
    t2 = time.time()
    for i, chunk in enumerate(chunks, 1):
        tc = time.time()
        audio = model.generate(text=chunk, language=a.language or None,
                               voice_clone_prompt=prompt, speed=a.speed,
                               num_step=a.num_step)[0].astype(np.float32)
        dt = time.time() - tc
        per_chunk.append({"i": i, "chars": len(chunk), "audio_s": round(len(audio) / SR, 2),
                          "gen_s": round(dt, 2)})
        if a.chunks_dir:
            os.makedirs(a.chunks_dir, exist_ok=True)
            pk = float(np.max(np.abs(audio))) or 1.0
            sf.write(os.path.join(a.chunks_dir, f"chunk_{a.index_offset + i - 1:02d}.wav"),
                     audio / pk * 0.95 if pk > 0.95 else audio, SR, subtype="PCM_16")
        print(f"[{i}/{len(chunks)}] {len(chunk)} chars -> {len(audio)/SR:.2f}s audio in {dt:.1f}s", flush=True)
        if pieces:
            pieces.append(pause)
        pieces.append(audio)
    t_gen = time.time() - t2

    wav = np.concatenate(pieces)
    peak = float(np.max(np.abs(wav))) or 1.0
    if peak > 0.99:
        wav = wav / peak * 0.99
    sf.write(a.out, wav, SR, subtype="FLOAT")

    dur = len(wav) / SR
    meta = {
        "model": a.model, "device": "cpu", "language": a.language, "speed": a.speed,
        "num_step": a.num_step, "pause_ms": a.pause_ms,
        "chunks": len(chunks), "chars": sum(len(c) for c in chunks),
        "ref_audio": a.ref_audio, "ref_text_used": getattr(prompt, "ref_text", None),
        "audio_duration_s": round(dur, 2), "load_s": round(t_load, 1),
        "prompt_s": round(t_prompt, 1), "generate_s": round(t_gen, 1),
        "rtf": round(t_gen / dur, 3) if dur else None, "per_chunk": per_chunk,
    }
    with open(a.meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in meta.items() if k != "per_chunk"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
