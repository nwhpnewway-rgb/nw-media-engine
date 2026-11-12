#!/usr/bin/env bash
# gha_upload_to_results.sh — dùng trong GitHub Actions để gửi 1 file về RESULTS qua ResultsUploader
# Cách dùng:  ./gha_upload_to_results.sh <đường_dẫn_file> <mimeType> [tên_trên_Drive]
# Cần biến môi trường (từ GitHub Secrets): RESULTS_UPLOAD_URL, RESULTS_UPLOAD_SECRET
set -euo pipefail

FILE="$1"; MIME="$2"; NAME="${3:-$(basename "$FILE")}"
: "${RESULTS_UPLOAD_URL:?thiếu RESULTS_UPLOAD_URL}"
: "${RESULTS_UPLOAD_SECRET:?thiếu RESULTS_UPLOAD_SECRET}"

case "$NAME" in GHA_*) ;; *) NAME="GHA_${NAME}";; esac

SIZE=$(stat -c %s "$FILE")
if [ "$SIZE" -gt $((35*1024*1024)) ]; then
  echo "FILE QUÁ LỚN ($SIZE byte > 35 MB): $FILE — hãy nén/chia nhỏ hoặc dùng GitHub Artifact" >&2; exit 2
fi

PAYLOAD="$(mktemp)"
python3 - "$FILE" "$MIME" "$NAME" > "$PAYLOAD" <<'PY'
import base64, hashlib, json, os, sys
path, mime, name = sys.argv[1:4]
data = open(path, 'rb').read()
print(json.dumps({
  "secret": os.environ["RESULTS_UPLOAD_SECRET"],
  "filename": name,
  "mimeType": mime,
  "sha256": hashlib.sha256(data).hexdigest(),
  "data": base64.b64encode(data).decode()
}))
PY

# Apps Script trả 302 sau khi doPost chạy xong; -L theo redirect để lấy JSON kết quả.
RESP="$(curl -sS -L --max-time 300 -H 'Content-Type: application/json' --data-binary @"$PAYLOAD" "$RESULTS_UPLOAD_URL")"
rm -f "$PAYLOAD"
python3 - "$RESP" "$NAME" <<'PY'
import json, sys
r = json.loads(sys.argv[1])
if r.get("code") == 200:
    print(f"UPLOAD_OK name={r.get('name', sys.argv[2])} size={r.get('size')} sha256={r.get('sha256')}")
    sys.exit(0)
print(f"UPLOAD_FAIL name={sys.argv[2]} code={r.get('code')} msg={r.get('message')}", file=sys.stderr)
sys.exit(1)
PY
