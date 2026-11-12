#!/usr/bin/env python3
"""Strict validator / sanitizer for nw-reel-v2 scripts written by a Muse (Newway).

The spec arrives from outside the repo (Drive -> Apps Script bridge -> repository_dispatch), so it is
treated as untrusted DATA: only whitelisted keys survive, every string is length-capped and stripped of
control characters, enums are forced to known values and unknown backgrounds fall back to the library.

Usage:  python3 scripts/reel_spec.py IN.json OUT.json [--reel-id ID]
Exit 0 = OK (OUT written, warnings on stderr) · exit 1 = rejected (reasons on stderr).
"""
import json
import re
import sys
from pathlib import Path

BG_LIBRARY = [
    "port_aerial", "fertilizer_granules", "bulk_deck_bags", "ship_unloading_crane", "container_terminal",
    "barge_tug", "trucks_highway", "port_cranes_sunset", "barges_river", "barge_bridge",
]
SLIDE_TYPES = {"hero", "bullets", "price_table", "stats", "steps", "cta"}
TRENDS = {"flat", "up", "down"}
VOICES = {"vi_male_nhat_phong", "vi_female_mai", "en_male_adam", "en_female_claire"}

MIN_SLIDES, MAX_SLIDES = 4, 10
MAX_PARTS_PER_SLIDE = 3
MIN_TTS_CHARS, MAX_TTS_CHARS = 700, 2600      # ~19 chars/s  ->  ~40 s .. ~140 s of speech
MAX_PART_CHARS = 260

# OPSEC: no vessel names in media. Reject the common vessel markers outright.
OPSEC_PATTERNS = [r"\bM\s*/\s*V\b", r"\bM\.V\.", r"\bMV\s+[A-Z]", r"\bm/v\b", r"\btàu\s+[A-Z]{2,}"]

CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


class Reject(Exception):
    pass


def s(v, cap, field, warn, required=False):
    if v is None or v == "":
        if required:
            raise Reject(f"thiếu trường bắt buộc: {field}")
        return ""
    if not isinstance(v, (str, int, float)):
        raise Reject(f"{field} phải là chuỗi")
    t = CTRL.sub("", str(v)).replace("\n", " ").strip()
    t = re.sub(r"\s{2,}", " ", t)
    if len(t) > cap:
        warn.append(f"{field}: cắt còn {cap} ký tự")
        t = t[:cap].rstrip()
    return t


def enum(v, allowed, default):
    return v if v in allowed else default


def lst(v, cap_n, field, warn):
    if v is None:
        return []
    if not isinstance(v, list):
        raise Reject(f"{field} phải là danh sách")
    if len(v) > cap_n:
        warn.append(f"{field}: chỉ giữ {cap_n} mục đầu")
    return [x for x in v[:cap_n] if isinstance(x, dict)]


def sanitize(raw, reel_id=None):
    warn = []
    if not isinstance(raw, dict):
        raise Reject("reel.json phải là một object JSON")
    if raw.get("schema") != "nw-reel-v2":
        raise Reject('schema phải là "nw-reel-v2"')
    rid = reel_id or raw.get("reel_id", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", str(rid)):
        raise Reject("reel_id chỉ gồm A-Z a-z 0-9 _ - (3-64 ký tự)")

    out = {
        "schema": "nw-reel-v2",
        "reel_id": rid,
        "title": s(raw.get("title"), 90, "title", warn, required=True),
        "handle": "newwaylogisticsjsc",     # fixed brand handle, not taken from the script
        "voice": enum(raw.get("voice"), VOICES, "vi_male_nhat_phong"),
        "source_note": s(raw.get("source_note"), 110, "source_note", warn),
        "sources": [],
        "slides": [],
    }
    for i, src in enumerate(lst(raw.get("sources"), 12, "sources", warn)):
        url = s(src.get("url"), 300, f"sources[{i}].url", warn)
        if url and not re.match(r"https?://", url):
            url = ""
        out["sources"].append({"name": s(src.get("name"), 140, f"sources[{i}].name", warn), "url": url})
    if len(out["sources"]) < 2:
        warn.append("ít hơn 2 nguồn — nên có >= 3 nguồn kiểm chứng")

    slides = raw.get("slides")
    if not isinstance(slides, list) or not (MIN_SLIDES <= len(slides) <= MAX_SLIDES):
        raise Reject(f"cần {MIN_SLIDES}-{MAX_SLIDES} slide")

    used_bg = []
    total_tts = 0
    for i, sl in enumerate(slides, 1):
        if not isinstance(sl, dict):
            raise Reject(f"slide {i} không hợp lệ")
        f = f"slides[{i}]"
        typ = sl.get("type")
        if typ not in SLIDE_TYPES:
            raise Reject(f"{f}.type phải thuộc {sorted(SLIDE_TYPES)}")
        bg = sl.get("bg")
        if bg not in BG_LIBRARY:
            bg = next((b for b in BG_LIBRARY if b not in used_bg), BG_LIBRARY[(i - 1) % len(BG_LIBRARY)])
            warn.append(f"{f}.bg không có trong thư viện -> dùng {bg}")
        used_bg.append(bg)
        o = {"type": typ, "bg": bg}
        if sl.get("align") == "top":
            o["align"] = "top"
        if typ != "cta":
            o["kicker"] = s(sl.get("kicker"), 48, f"{f}.kicker", warn)
            o["headline"] = s(sl.get("headline"), 60, f"{f}.headline", warn, required=True)
            o["highlight"] = s(sl.get("highlight"), 48, f"{f}.highlight", warn)
        if typ == "hero":
            o["sub"] = s(sl.get("sub"), 110, f"{f}.sub", warn)
            o["chips"] = [{"name": s(c.get("name"), 16, f"{f}.chips.name", warn),
                           "trend": enum(c.get("trend"), TRENDS, ""),
                           "label": s(c.get("label"), 24, f"{f}.chips.label", warn)}
                          for c in lst(sl.get("chips"), 4, f"{f}.chips", warn)]
        elif typ == "bullets":
            b = sl.get("badge")
            if isinstance(b, dict):
                o["badge"] = {"label": s(b.get("label"), 40, f"{f}.badge.label", warn),
                              "value": s(b.get("value"), 22, f"{f}.badge.value", warn)}
            o["bullets"] = [{"icon": s(x.get("icon"), 2, f"{f}.icon", warn) or "•",
                             "text": s(x.get("text"), 90, f"{f}.bullets.text", warn, required=True),
                             "note": s(x.get("note"), 70, f"{f}.bullets.note", warn)}
                            for x in lst(sl.get("bullets"), 4, f"{f}.bullets", warn)]
        elif typ == "price_table":
            o["table_title"] = s(sl.get("table_title"), 40, f"{f}.table_title", warn)
            o["unit"] = s(sl.get("unit"), 30, f"{f}.unit", warn)
            rows = []
            for r in lst(sl.get("rows"), 6, f"{f}.rows", warn):
                row = {"name": s(r.get("name"), 32, f"{f}.rows.name", warn, required=True),
                       "note": s(r.get("note"), 40, f"{f}.rows.note", warn),
                       "price": s(r.get("price"), 16, f"{f}.rows.price", warn, required=True),
                       "trend": enum(r.get("trend"), TRENDS, ""),
                       "label": s(r.get("label"), 20, f"{f}.rows.label", warn)}
                if isinstance(r.get("part"), int) and 0 <= r["part"] < MAX_PARTS_PER_SLIDE:
                    row["part"] = r["part"]
                rows.append(row)
            if not rows:
                raise Reject(f"{f}: price_table cần ít nhất 1 dòng giá")
            o["rows"] = rows
        elif typ == "stats":
            o["stats"] = [{"label": s(x.get("label"), 28, f"{f}.stats.label", warn, required=True),
                           "note": s(x.get("note"), 40, f"{f}.stats.note", warn),
                           "value": s(x.get("value"), 10, f"{f}.stats.value", warn, required=True),
                           "unit": s(x.get("unit"), 14, f"{f}.stats.unit", warn),
                           "delta": s(x.get("delta"), 10, f"{f}.stats.delta", warn),
                           "dir": enum(x.get("dir"), TRENDS, "flat")}
                          for x in lst(sl.get("stats"), 3, f"{f}.stats", warn)]
        elif typ == "steps":
            o["steps"] = [{"title": s(x.get("title"), 70, f"{f}.steps.title", warn, required=True),
                           "note": s(x.get("note"), 70, f"{f}.steps.note", warn)}
                          for x in lst(sl.get("steps"), 4, f"{f}.steps", warn)]
        elif typ == "cta":
            o["tagline"] = s(sl.get("tagline"), 110, f"{f}.tagline", warn)
            o["note"] = s(sl.get("note"), 90, f"{f}.note", warn)

        parts = sl.get("parts")
        if not isinstance(parts, list) or not (1 <= len(parts) <= MAX_PARTS_PER_SLIDE):
            raise Reject(f"{f}.parts cần 1-{MAX_PARTS_PER_SLIDE} câu")
        o["parts"] = []
        for j, p in enumerate(parts):
            if not isinstance(p, dict):
                raise Reject(f"{f}.parts[{j}] không hợp lệ")
            d = s(p.get("display"), MAX_PART_CHARS, f"{f}.parts[{j}].display", warn, required=True)
            t = s(p.get("tts") or p.get("display"), MAX_PART_CHARS, f"{f}.parts[{j}].tts", warn, required=True)
            total_tts += len(t)
            o["parts"].append({"display": d, "tts": t})
        out["slides"].append(o)

    if out["slides"][-1]["type"] != "cta":
        warn.append("slide cuối nên là cta")
    if not (MIN_TTS_CHARS <= total_tts <= MAX_TTS_CHARS):
        raise Reject(f"tổng lời đọc {total_tts} ký tự, cần {MIN_TTS_CHARS}-{MAX_TTS_CHARS} (~60-130 giây)")

    blob = json.dumps(out, ensure_ascii=False)
    for pat in OPSEC_PATTERNS:
        m = re.search(pat, blob)
        if m:
            raise Reject(f"OPSEC: phát hiện dấu hiệu tên tàu ({m.group(0)!r}) — xoá khỏi kịch bản")
    out["_meta"] = {"tts_chars": total_tts, "est_seconds": round(total_tts / 19 + 0.5 * len(out["slides"]) + 1.6, 1)}
    return out, warn


def main():
    args = sys.argv[1:]
    rid = None
    if "--reel-id" in args:
        k = args.index("--reel-id")
        rid = args[k + 1]
        del args[k:k + 2]
    src, dst = args
    try:
        raw = json.loads(Path(src).read_text(encoding="utf-8-sig"))
        out, warn = sanitize(raw, rid)
    except (Reject, json.JSONDecodeError, ValueError) as e:
        print(f"SPEC_REJECTED: {e}", file=sys.stderr)
        sys.exit(1)
    for w in warn:
        print(f"SPEC_WARN: {w}", file=sys.stderr)
    Path(dst).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"SPEC_OK slides={len(out['slides'])} tts_chars={out['_meta']['tts_chars']} est_s={out['_meta']['est_seconds']}")


if __name__ == "__main__":
    main()
