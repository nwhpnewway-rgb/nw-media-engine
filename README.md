# nw-media-engine

Engine nặng của MUSE chạy trên GitHub Actions (theo MUSE STUDY #02): Muse chỉ điều phối, GitHub dựng video và giọng đọc, kết quả gom về **RESULTS** (qua ResultsUploader).

> **OPSEC:** Tuyệt đối không commit token/secret hay Drive ID vào repo; Secret giữ kín trong GitHub Secrets; 0% tên tàu trong kịch bản & media.

## Workflows

| Workflow | Đầu vào chính | Đầu ra (tiền tố `GHA_<HF\|OV\|REEL>_<run_id>_<TASK_ID>_`) |
|---|---|---|
| `nwl-reel-e2e` | `task_id`, `video_code` (`V01`..`V24`), `voice`, `num_step` | `reel.mp4` (**có tiếng AAC 192k -14 LUFS + chữ Karaoke khớp từng câu**), `voice.wav`, `reel_report.md`, `qa.json` |
| `hyperframes-render` | `task_id`, `video_code`, `voice_run_id` (tuỳ chọn để ghép tiếng từ lượt TTS sẵn có), `start`, `end`, `fps` | `video.mp4` (h264 1080x1920, kèm AAC 192k nếu truyền `voice_run_id`), `media_report.md`, `qa.json` |
| `omnivoice-tts` | `task_id`, `voice`, `script_text` hoặc `script_file_id` (.txt trên Drive), `speed`, `num_step` | `voice.wav` (24 kHz mono PCM16, −14 LUFS), `voice_report.md`, `qa.json`, `tts_meta.json` |

Giọng chuẩn (`nwl_voices/`, lấy từ repo OmniVoice của NWL, sha256 khớp Drive, kèm sẵn lời mẫu `.txt` để bỏ qua Whisper): `vi_male_nhat_phong` (mặc định), `vi_female_mai`, `en_male_adam`, `en_female_claire`.

Mỗi lượt chạy luôn lưu kết quả thành **artifact** 14 ngày, đồng thời đẩy lên RESULTS qua **ResultsUploader** (Apps Script, phương án B'). Upload lỗi thì job fail.

## Cách gọi

```bash
# 1) Trọn gói 1 lệnh: tự đọc kịch bản V01..V24 bằng giọng Nhất Phong -> đồng bộ Karaoke -> dựng MP4 có tiếng
gh workflow run nwl-reel-e2e -R nwhpnewway-rgb/nw-media-engine \
  -f task_id=REEL_20261113_V01 -f video_code=V01 -f voice=vi_male_nhat_phong

# 2) Ghép nhanh (~3 phút) từ lượt omnivoice-tts đã chạy sẵn (voice_run_id)
gh workflow run hyperframes-render -R nwhpnewway-rgb/nw-media-engine \
  -f task_id=REEL_V01_NHATPHONG -f video_code=V01 -f voice_run_id=43184416292 -f fps=30

# 3) Tách lẻ từng engine
gh workflow run omnivoice-tts -R nwhpnewway-rgb/nw-media-engine \
  -f task_id=VOICE_20261112_01 -f voice=vi_male_nhat_phong -f script_file_id=<DRIVE_TXT_ID>

# 4) Từ Muse/Hook (repository_dispatch)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/nwhpnewway-rgb/nw-media-engine/dispatches \
  -d '{"event_type":"nwl-reel-e2e","client_payload":{"task_id":"REEL_20261113_V02","video_code":"V02","voice":"vi_male_nhat_phong"}}'
```

## Đẩy kết quả về RESULTS: ResultsUploader

- Secrets của repo (Anh Hoàng tự dán, không ghi vào repo hay chat): `RESULTS_UPLOAD_URL`, `RESULTS_UPLOAD_SECRET`.
- Script: `scripts/gha_upload_to_results.sh <file> <mime> GHA_<HF|OV>_<run_id>_<mô tả>.<đuôi>`.
- Giới hạn: tên bắt đầu bằng `GHA_`, tối đa 35 MB/file, MIME: mp4/wav/mpeg/png/jpeg/md/txt/json.
- Kiểm tra đường upload: chạy workflow `upload-smoke-test` (tạo `GHA_SMOKE_<run_id>.txt`).
- `template_file_id` / `script_file_id` trên Drive phải chia sẻ "bất kỳ ai có link" (lấy bằng `scripts/drive.sh fetch`).

## Ghi chú kỹ thuật
- HyperFrames `0.9.37` cài từ npm, Chrome lấy bằng `hyperframes browser ensure` (có cache).
- OmniVoice `0.2.1` chạy CPU (torch 2.8.0 CPU wheel), trọng số `k2-fsa/OmniVoice` cache trong Actions cache (`omnivoice-hf-v1`).
- Báo cáo ghi thời gian cài đặt, thời gian dựng, peak RAM (`/usr/bin/time -v`) để so sánh với Newway (MUSE STUDY #03).

## Giao việc qua GSheet Pipeline: Newway viết kịch bản → GitHub dựng video (nw-reel-v2)

```
Anh Hoàng ─ 02_TASKS: REELSCRIPT_<...> | Newway | <chủ đề> | QUEUED
   │  (Bridge Apps Script, 1 phút/nhịp)  → INBOX/<TASK>_input.json + MAILBOX_Newway QUEUED
   ▼
Newway (Hook, nhánh REELSCRIPT_) → nghiên cứu ≥3 nguồn → RESULTS/<TASK>__reel.json + <TASK>.json DONE
   │  (Bridge)  → repository_dispatch nw-reel-v2 (kịch bản base64)
   ▼
GitHub nw-reel-v2 → reel_spec.py kiểm định → OmniVoice Nhất Phong ×4 → HyperFrames → QA → RESULTS (+ _status.json)
   │  (Bridge)  → 02_TASKS: <TASK>_GHA = DONE (link MP4, thời lượng, LUFS) · <TASK>_QA = WAITING_APPROVAL · email
   ▼
Anh Hoàng duyệt QA_GATE_APPROVAL = APPROVED
```

| Thành phần | File |
|---|---|
| Bridge (Apps Script, cài 1 lần) | `bridge/NW_GHA_Bridge.gs` |
| Prompt Newway V6.2 (thêm nhánh `REELSCRIPT_`) | `docs/NEWWAY_PROMPT_V6_2_REELSCRIPT.txt` |
| Định dạng kịch bản + mẫu (Bridge tự chép vào INBOX) | `docs/NW_REEL_V2_SCHEMA.md`, `docs/NW_REEL_V2_EXAMPLE.json` |
| Kiểm định kịch bản (dữ liệu ngoài, lọc trắng) | `scripts/reel_spec.py` |
| Workflow | `.github/workflows/nw-reel-v2.yml` (`repository_dispatch: nw-reel-v2`, payload `reel_id`, `task_id`, `spec_b64`) |

Cột `LINK_INPUT_NGUON` (tuỳ chọn): `platform=youtube_shorts; seconds=65`. Mặc định **không giới hạn số video/ngày** (`DAILY_DISPATCH_LIMIT = 0`, tối ưu cho runner GitHub Actions 4 vCPU / 16 GB RAM trên repo Public).
Kiểm tra & hồi phục skill `agent-reach` của Newway **0% mở chat**: thêm dòng `PROBE_<...>` hoặc `SKILLSYNC_<...>` (`install agent-reach`) vào Tab `02_TASKS`.
