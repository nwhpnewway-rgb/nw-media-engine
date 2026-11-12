# NW_REEL_V2_SCHEMA — Định dạng kịch bản `reel.json` cho GitHub nw-reel-v2

Newway viết **1 file JSON** theo đúng định dạng dưới. GitHub đọc file này để: đọc giọng Nhất Phong từng câu → dựng
slide HTML (nền ảnh logistics Ken Burns, chữ karaoke) → MP4 1080x1920. File chỉ là DỮ LIỆU; mọi trường lạ bị bỏ.
Mẫu đầy đủ đã chạy thật: `NW_REEL_V2_EXAMPLE.json` (bản tin Giá phân bón 12/11/2026, 8 slide, 94,9 giây).

## Khung chung
```json
{
  "schema": "nw-reel-v2",
  "reel_id": "<TASK_ID>",
  "title": "Tiêu đề video (≤90 ký tự)",
  "voice": "vi_male_nhat_phong",
  "source_note": "Dòng nguồn chạy cuối màn hình (≤110 ký tự) · Giá tham khảo",
  "sources": [ { "name": "Tên nguồn", "url": "https://..." } ],
  "slides": [ { "type": "...", "bg": "...", "...": "...", "parts": [ {"display": "...", "tts": "..."} ] } ]
}
```

## Luật cứng (sai là GitHub từ chối, task FAIL)
1. `schema` = `nw-reel-v2`; `reel_id` = đúng TASK_ID (chỉ A-Z a-z 0-9 _ -).
2. **4–10 slide** (khuyên 7–8). Slide đầu `hero`, slide cuối `cta`.
3. Mỗi slide **1–3 câu** trong `parts` (khuyên 2). Mỗi câu ≤260 ký tự, 1 dòng.
4. **Tổng ký tự `tts` 700–2600** (≈19 ký tự/giây → 60–130 giây). Video ngắn TikTok/Shorts: nhắm 1100–1400 ký tự (≈65–80 giây).
5. **OPSEC: 0% tên tàu** (không "M/V…", "MV …", "tàu ABC"), không tên khách hàng, không số hợp đồng.
6. Mọi con số phải có trong `sources` (≥3 nguồn có URL). Giá ghi rõ ngày + "giá tham khảo".

## `display` và `tts`
- `display`: câu hiện chữ karaoke trên màn hình (viết chuẩn: Urê, DAP, 12/11, 620–640K).
- `tts`: cùng câu nhưng **viết theo cách đọc** cho giọng Việt: `New Way Logistics`, `U rê`, `Đê A Pê`, `Ka li`,
  `Nờ Pê Ka`, `mười hai tháng mười một`, `sáu trăm hai mươi đến sáu trăm bốn mươi nghìn đồng`, `đô la Mỹ một tấn`,
  `phần trăm`. Không viết tắt, không ký hiệu (/, %, –, K, USD) trong `tts`.

## 6 kiểu slide (giới hạn ký tự trong ngoặc)
| type | Trường riêng |
|---|---|
| `hero` | `kicker`(48) `headline`(60) `highlight`(48) `sub`(110) `chips`[≤4]: `name`(16) `trend` `label`(24) |
| `bullets` | `kicker` `headline` `highlight` · `badge`: `label`(40) `value`(22) · `bullets`[≤4]: `icon`(1 ký tự) `text`(90) `note`(70) |
| `price_table` | `kicker` `headline` `highlight` `table_title`(40) `unit`(30) · `rows`[1–6]: `name`(32) `note`(40) `price`(16) `trend` `label`(20) `part`(0/1/2: dòng sáng lên theo câu nào) |
| `stats` | `kicker` `headline` `highlight` · `stats`[≤3]: `label`(28) `note`(40) `value`(10) `unit`(14) `delta`(10) `dir` |
| `steps` | `kicker` `headline` `highlight` · `steps`[≤4]: `title`(70) `note`(70) |
| `cta` | `tagline`(110) `note`(90) — logo + handle `newwaylogisticsjsc` + nút Follow tự có |

- `trend` / `dir`: `up` | `down` | `flat`. `headline` ngắn (2–5 từ), `highlight` là cụm nhấn màu cam.
- Thêm `"align": "top"` nếu slide nhiều nội dung.

## Thư viện nền `bg` (chọn mỗi slide 1 ảnh, không lặp liền nhau)
`port_aerial` cảng nhìn từ trên · `fertilizer_granules` hạt phân bón · `bulk_deck_bags` bao hàng trên boong ·
`ship_unloading_crane` cẩu dỡ hàng · `container_terminal` bãi container · `barge_tug` sà lan + tàu kéo ·
`trucks_highway` xe tải trên cao tốc · `port_cranes_sunset` cẩu cảng hoàng hôn · `barges_river` sà lan trên sông ·
`barge_bridge` sà lan qua cầu. (Tên khác → GitHub tự thay ảnh trong thư viện.)

## Gợi ý cấu trúc 8 slide (bản tin 65–95 giây)
1 `hero` móc câu + chủ đề · 2 `bullets` diễn biến chính · 3–4 `price_table` bảng giá theo nhóm ·
5 `stats` số liệu thế giới/so sánh · 6 `bullets` rủi ro / yếu tố tác động · 7 `steps` khuyến nghị cho đại lý/nhà nông ·
8 `cta` NEWWAY Logistics + mời theo dõi.
