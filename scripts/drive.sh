#!/usr/bin/env bash
# Drive helper for nw-media-engine.
#   drive.sh setup                 -> install rclone + write config from $RCLONE_CONF (if set)
#   drive.sh fetch <FILE_ID> <OUT> -> download a Drive file by ID
#   drive.sh upload <FILE> [NAME]  -> upload a file into RESULTS ($RESULTS_FOLDER_ID)
#
# Auth: secret RCLONE_CONF = full rclone.conf text with a remote named "gdrive".
#   Option A: OAuth token of Anh Hoang's Google account (type=drive, token=...).
#   Option B: service account (service_account_credentials=...) + Shared Drive (team_drive=...).
# Without RCLONE_CONF: fetch falls back to the public "anyone with link" URL,
# upload is skipped (results are still kept as GitHub Actions artifacts).
set -euo pipefail

REMOTE="${RCLONE_REMOTE:-gdrive}"
CONF="${HOME}/.config/rclone/rclone.conf"

has_conf() { [[ -s "$CONF" ]]; }

cmd_setup() {
  if [[ -z "${RCLONE_CONF:-}" ]]; then
    echo "::warning::RCLONE_CONF secret not set -> Drive upload disabled (artifact only)."
    return 0
  fi
  if ! command -v rclone >/dev/null; then
    curl -fsSL https://rclone.org/install.sh | sudo bash >/dev/null
  fi
  mkdir -p "$(dirname "$CONF")"
  printf '%s\n' "$RCLONE_CONF" > "$CONF"
  chmod 600 "$CONF"
  rclone about "${REMOTE}:" >/dev/null 2>&1 && echo "rclone: remote '${REMOTE}' OK" \
    || echo "::warning::rclone remote '${REMOTE}' did not answer 'about' (may still work for folder-scoped access)."
}

cmd_fetch() {
  local id="$1" out="$2"
  mkdir -p "$(dirname "$out")"
  if has_conf; then
    local tmpd; tmpd="$(mktemp -d)"
    rclone backend copyid "${REMOTE}:" "$id" "$tmpd/" 
    local f; f="$(find "$tmpd" -type f | head -n1)"
    [[ -n "$f" ]] || { echo "::error::rclone copyid returned nothing for $id"; exit 1; }
    mv "$f" "$out"
  else
    curl -fsSL -o "$out" "https://drive.usercontent.google.com/download?id=${id}&export=download&confirm=t"
  fi
  local size; size=$(stat -c %s "$out")
  if [[ "$size" -eq 0 ]] || head -c 200 "$out" | grep -qi '<html'; then
    echo "::error::Download of $id failed (empty or HTML page). Share file 'anyone with link' or set RCLONE_CONF."
    exit 1
  fi
  echo "fetched $id -> $out ($size bytes)"
}

cmd_upload() {
  local file="$1" name="${2:-$(basename "$1")}"
  if ! has_conf; then
    echo "::notice::Skip Drive upload of $name (no RCLONE_CONF)."
    return 0
  fi
  : "${RESULTS_FOLDER_ID:?RESULTS_FOLDER_ID not set}"
  rclone copyto "$file" "${REMOTE}:${name}" --drive-root-folder-id "$RESULTS_FOLDER_ID" \
    --retries 5 --low-level-retries 10
  echo "uploaded $name -> RESULTS ($RESULTS_FOLDER_ID)"
}

case "${1:-}" in
  setup)  cmd_setup ;;
  fetch)  shift; cmd_fetch "$@" ;;
  upload) shift; cmd_upload "$@" ;;
  *) echo "usage: $0 setup|fetch|upload" >&2; exit 2 ;;
esac
