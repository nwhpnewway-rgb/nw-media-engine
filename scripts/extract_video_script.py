#!/usr/bin/env python3
"""Extract the 8-slide voiceover script for V01..V24 from nw/template-data.json."""
import argparse
import json
import re


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template-json", required=True)
    ap.add_argument("--video-code", default="V01")
    ap.add_argument("--lang", default="vi")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    m = re.search(r"(\d+)", a.video_code.strip())
    if not m:
        raise SystemExit(f"Invalid video_code: {a.video_code}")
    code = f"V{int(m.group(1)):02d}"

    data = json.load(open(a.template_json, encoding="utf-8"))
    vid = next((v for v in data["videos"] if v.get("code", "").upper() == code), None)
    if not vid:
        raise SystemExit(f"Video {code} not found in {a.template_json}")

    lines = []
    for s in vid["slides"]:
        txt = s["karaokeSubtitle"].strip()
        if a.lang.startswith("vi"):
            txt = re.sub(r"\bCO[2₂]\b", "CO hai", txt)
            txt = txt.replace("CO₂", "CO hai")
        lines.append(txt)

    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Extracted {len(lines)} lines for {code} -> {a.out}")


if __name__ == "__main__":
    main()
