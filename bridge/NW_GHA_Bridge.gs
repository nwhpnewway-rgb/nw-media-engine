/**
 * NW_GHA_Bridge.gs — Cầu nối GSheet Pipeline ⇄ Newway ⇄ GitHub nw-media-engine (100% Zero-VPS)
 *
 * Tính năng:
 *  ① Giao việc dựng Reels: Tab 02_TASKS thêm dòng `REELSCRIPT_<...> | Bước 1 | Newway | NONE | <chủ đề> | | QUEUED`
 *     → Bridge tạo `<TASK>_input.json` trong INBOX + ghi MAILBOX_Newway → Newway nghiên cứu (agent-reach) & viết
 *     `<TASK>__reel.json` nộp về RESULTS → Bridge bắn `repository_dispatch` sang GitHub → ~20–40 phút sau MP4 về RESULTS
 *     → Bridge điền link MP4 + thông số QA về GSheet Pipeline.
 *  ② Kiểm tra Newway còn skill `agent-reach` không (0% mở chat):
 *     - Cách A (trên GSheet): thêm dòng `PROBE_<...> | Bước 1 | Newway | NONE | Kiểm tra agent-reach | | QUEUED`
 *     - Cách B (1-click trong Apps Script): chạy hàm `checkNewwaySkillsNow()`
 *     → ~90 giây sau GSheet / RESULTS báo rõ `agent-reach` còn hay mất, phiên bản mấy, danh sách skills.
 *  ③ Cài / hồi phục `agent-reach` từ xa (0% mở chat):
 *     - Cách A (trên GSheet): thêm dòng `SKILLSYNC_<...> | Bước 1 | Newway | NONE | install agent-reach | | QUEUED`
 *     - Cách B (1-click): chạy hàm `installAgentReachNow()`
 *     (Ngoài ra trong Prompt V6.2, nhánh `REELSCRIPT_` cũng tự cài lại `agent-reach` từ SKILL_DEPOT nếu thấy thiếu.)
 *
 * Script Properties cần đặt (Project Settings → Script Properties):
 *   GITHUB_TOKEN        Fine-grained PAT (repo nw-media-engine, quyền Contents: Read & write) — dùng để gọi repository_dispatch
 *   PIPELINE_SHEET_IDS  Danh sách Spreadsheet ID của các GSheet Pipeline (ngăn cách dấu phẩy)
 *   INBOX_ID            Drive folder ID của INBOX Cabin Newway
 *   SKILL_DEPOT_ID      Drive folder ID của SKILL_DEPOT Cabin Newway
 *   RESULTS_ID          Drive folder ID của RESULTS Cabin Newway
 *   MAILBOX_NEWWAY_ID   Spreadsheet ID của MAILBOX_Newway
 *   NOTIFY_EMAIL        (Tuỳ chọn) Email nhận thông báo khi video xong / lỗi
 *   DAILY_DISPATCH_LIMIT (Tuỳ chọn) Mặc định 0 = KHÔNG GIỚI HẠN (repo Public runner 4 vCPU miễn phí không giới hạn phút)
 */

const CFG = {
  GITHUB_REPO: 'nwhpnewway-rgb/nw-media-engine',
  DEFAULT_DAILY_LIMIT: 0,           // 0 = mở không giới hạn (repo Public)
  MAX_SPEC_BYTES: 60000,
  TZ: 'Asia/Ho_Chi_Minh',
  AGENT_REACH_ITEM: {
    name: 'agent-reach',
    type: 'uv_tool_zip',
    file: 'agent-reach-v1.6.1.zip',
    sha256: '51443d733bcb26064262442083a8f8abf6ba5e3e7df54beebafe98c88be04a60',
    version: '1.6.1',
    source: 'github.com/Panniantong/Agent-Reach release v1.6.1',
    skills: ['agent-reach'],
    action: 'install',
  },
  REEL_RE: /^REELSCRIPT_(?!.*__)[A-Za-z0-9_]{3,60}$/,
  PROBE_RE: /^PROBE_(?!.*__)[A-Za-z0-9_]{3,60}$/,
  SYNC_RE: /^SKILLSYNC_(?!.*__)[A-Za-z0-9_]{3,60}$/,
  C: { ID: 1, STEP: 2, MUSE: 3, DEP: 4, DESC: 5, INPUT: 6, STATUS: 7, RESULT: 8, LINK: 9, QA: 10, DONE_AT: 11 },
};

function id_(key) {
  const v = (PropertiesService.getScriptProperties().getProperty(key) || '').trim();
  if (!v) throw new Error('Thiếu Script property: ' + key);
  return v;
}

/* ============================ cài đặt & tiện ích 1-click ============================ */
function setupBridge() {
  const p = PropertiesService.getScriptProperties();
  ['GITHUB_TOKEN', 'INBOX_ID', 'SKILL_DEPOT_ID', 'RESULTS_ID', 'MAILBOX_NEWWAY_ID'].forEach(id_);
  if (!sheetIds_().length) throw new Error('Thiếu PIPELINE_SHEET_IDS hoặc PIPELINE_FOLDER_ID chưa có file [PIPELINE] nào');
  DriveApp.getFolderById(id_('INBOX_ID')).getName();
  DriveApp.getFolderById(id_('SKILL_DEPOT_ID')).getName();
  DriveApp.getFolderById(id_('RESULTS_ID')).getName();
  SpreadsheetApp.openById(id_('MAILBOX_NEWWAY_ID')).getSheets()[0].getName();
  sheetIds_().forEach((sid) => {
    if (!SpreadsheetApp.openById(sid).getSheetByName('02_TASKS')) throw new Error('Pipeline ' + sid + ' thiếu Tab 02_TASKS');
  });
  const gh = UrlFetchApp.fetch('https://api.github.com/repos/' + CFG.GITHUB_REPO, { headers: ghHeaders_(), muteHttpExceptions: true });
  if (gh.getResponseCode() !== 200) throw new Error('GitHub truy cập repo lỗi HTTP ' + gh.getResponseCode());

  syncSchema();
  ScriptApp.getProjectTriggers().filter((t) => t.getHandlerFunction() === 'bridgeTick').forEach((t) => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('bridgeTick').timeBased().everyMinutes(1).create();
  if (!p.getProperty('SINCE')) p.setProperty('SINCE', new Date(Date.now() - 3600e3).toISOString());
  Logger.log('NW GHA Bridge OK — trigger 1 phút (không giới hạn lượt/ngày). Pipeline: ' + sheetIds_().length);
}

/** Chép schema + mẫu kịch bản từ repo GitHub vào INBOX cho Newway đọc. */
function syncSchema() {
  const inbox = DriveApp.getFolderById(id_('INBOX_ID'));
  [['docs/NW_REEL_V2_SCHEMA.md', 'NW_REEL_V2_SCHEMA.md', 'text/markdown'],
   ['docs/NW_REEL_V2_EXAMPLE.json', 'NW_REEL_V2_EXAMPLE.json', 'application/json']].forEach(([path, name, mime]) => {
    const h = ghHeaders_();
    h.Accept = 'application/vnd.github.raw+json';
    const res = UrlFetchApp.fetch('https://api.github.com/repos/' + CFG.GITHUB_REPO + '/contents/' + path, { headers: h, muteHttpExceptions: true });
    if (res.getResponseCode() !== 200) throw new Error('Không tải được ' + path + ' (HTTP ' + res.getResponseCode() + ')');
    upsertTextFile_(inbox, name, res.getContentText('UTF-8'), mime);
  });
  Logger.log('Đã đồng bộ NW_REEL_V2_SCHEMA.md + NW_REEL_V2_EXAMPLE.json vào INBOX');
}

/** 1-click kiểm tra Newway còn skill agent-reach không (0% mở chat). Kết quả về RESULTS & GSheet (nếu có). */
function checkNewwaySkillsNow() {
  const taskId = 'PROBE_' + Utilities.formatDate(new Date(), CFG.TZ, 'yyyyMMdd_HHmmss');
  enqueueMailbox_(taskId);
  Logger.log('Đã gửi ' + taskId + ' vào MAILBOX_Newway — xem kết quả sau ~90s trong RESULTS.');
  return taskId;
}

/** 1-click cài lại agent-reach v1.6.1 cho Newway từ SKILL_DEPOT (0% mở chat). */
function installAgentReachNow() {
  const taskId = 'SKILLSYNC_' + Utilities.formatDate(new Date(), CFG.TZ, 'yyyyMMdd_HHmmss');
  writeAgentReachManifest_(taskId);
  enqueueMailbox_(taskId);
  Logger.log('Đã gửi ' + taskId + ' cài agent-reach — hoàn tất sau ~4 phút trong RESULTS.');
  return taskId;
}

function stopBridge() {
  ScriptApp.getProjectTriggers().filter((t) => t.getHandlerFunction() === 'bridgeTick').forEach((t) => ScriptApp.deleteTrigger(t));
}

/* ============================ nhịp chính (1 phút/lần) ============================ */
function bridgeTick() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  try {
    stepQueue_();        // ② GSheet QUEUED (REELSCRIPT_ / PROBE_ / SKILLSYNC_) → Newway
    stepNewwayDone_();   // ④ Newway xong → cập nhật GSheet (+ bắn GitHub nếu là REELSCRIPT_)
    stepGhaStatus_();    // ⑥ GitHub xong → cập nhật GSheet
  } catch (e) {
    console.error('bridgeTick: ' + (e && e.stack || e));
  } finally {
    lock.releaseLock();
  }
}

/* ② Đọc các dòng QUEUED của Newway trên GSheet Pipeline */
function stepQueue_() {
  const inbox = DriveApp.getFolderById(id_('INBOX_ID'));
  forEachTaskRow_((sh, r, row) => {
    const id = String(row[CFG.C.ID - 1]).trim();
    if (String(row[CFG.C.MUSE - 1]).trim().toLowerCase() !== 'newway') return;
    if (String(row[CFG.C.STATUS - 1]).trim().toUpperCase() !== 'QUEUED') return;

    if (CFG.REEL_RE.test(id)) {
      const topic = clean_(row[CFG.C.DESC - 1], 120);
      if (topic.length < 5) { setCells_(sh, r, { STATUS: 'FAIL', RESULT: 'Thiếu chủ đề ở cột MO_TA_NHIEM_VU_CHI_TIET' }); return; }
      const opts = parseOpts_(String(row[CFG.C.INPUT - 1] || ''));
      const input = {
        task_id: id,
        topic: topic,
        platform: opts.platform || 'tiktok_reels_shorts',
        target_seconds: opts.target_seconds || 75,
        schema_file: 'NW_REEL_V2_SCHEMA.md',
        example_file: 'NW_REEL_V2_EXAMPLE.json',
      };
      upsertTextFile_(inbox, id + '_input.json', JSON.stringify(input, null, 1), 'application/json');
      enqueueMailbox_(id);
      setCells_(sh, r, { STATUS: 'RECEIVED', RESULT: 'Đã giao Newway (' + now_() + '). Đang nghiên cứu & viết kịch bản…' });
      ensureFollowRows_(sh, r, id);
    } else if (CFG.PROBE_RE.test(id)) {
      enqueueMailbox_(id);
      setCells_(sh, r, { STATUS: 'RECEIVED', RESULT: 'Đang kiểm tra môi trường & skill agent-reach của Newway (' + now_() + ')…' });
    } else if (CFG.SYNC_RE.test(id)) {
      const desc = String(row[CFG.C.DESC - 1] || '').toLowerCase();
      if (desc.indexOf('agent-reach') >= 0) writeAgentReachManifest_(id);
      enqueueMailbox_(id);
      setCells_(sh, r, { STATUS: 'RECEIVED', RESULT: 'Đang đồng bộ skill cho Newway qua SKILL_DEPOT (' + now_() + ')…' });
    }
  });
}

/* ④ Đọc dấu hoàn tất <TASK>.json của Newway trong RESULTS */
function stepNewwayDone_() {
  const p = PropertiesService.getScriptProperties();
  const results = DriveApp.getFolderById(id_('RESULTS_ID'));
  const since = p.getProperty('SINCE') || new Date(Date.now() - 3600e3).toISOString();
  ['REELSCRIPT', 'PROBE', 'SKILLSYNC'].forEach((kw) => {
    const it = results.searchFiles("title contains '" + kw + "' and modifiedDate > '" + since + "' and trashed = false");
    while (it.hasNext()) {
      const f = it.next();
      if (f.getName().indexOf('__') >= 0) continue;
      const m = f.getName().match(/^((?:REELSCRIPT|PROBE|SKILLSYNC)_[A-Za-z0-9_]{3,60})\.json$/);
      if (!m) continue;
      const id = m[1];
      const key = 'D_' + f.getId();
      const stamp = String(f.getLastUpdated().getTime());
      if (p.getProperty(key) === stamp) continue;
      p.setProperty(key, stamp);

      let st = {};
      try { st = JSON.parse(f.getBlob().getDataAsString('UTF-8')); } catch (e) { st = { status: 'FAIL', reason: 'TASK.json lỗi định dạng' }; }
      const status = String(st.status || '').toUpperCase();

      if (CFG.PROBE_RE.test(id)) {
        handleProbeDone_(results, f, id, st, status);
      } else if (CFG.SYNC_RE.test(id)) {
        const rep = firstFile_(results, id + '__skillsync_report.md');
        const detail = summarizeSkillSync_(rep);
        updateTask_(id, { STATUS: status || 'DONE', RESULT: detail, LINK: (rep || f).getUrl(), DONE_AT: now_() });
      } else if (CFG.REEL_RE.test(id)) {
        handleReelScriptDone_(p, results, f, id, st, status, key);
      }
    }
  });
}

function handleProbeDone_(results, f, id, st, status) {
  const rep = firstFile_(results, id + '__env_probe.md');
  let msg = 'Probe ' + status;
  if (st.skills_health && typeof st.skills_health === 'object') {
    const h = st.skills_health;
    const ar = h.agent_reach_installed ? ('agent-reach ' + (h.agent_reach_version || '') + (h.agent_reach_doctor_ok === false ? ' (doctor WARN)' : ': ĐANG CÓ ✓')) : 'THIẾU agent-reach ✗';
    const sk = Array.isArray(h.installed_skills) && h.installed_skills.length ? h.installed_skills.join(', ') : 'none';
    msg = ar + ' | skills: ' + sk;
  } else if (rep) {
    const txt = rep.getBlob().getDataAsString('UTF-8');
    const hasAr = /agent-reach[^\n]*(1\.\d+\.\d+|\/agent-reach)/i.test(txt) && !/agent-reach[^\n]*không có/i.test(txt);
    msg = (hasAr ? 'agent-reach: ĐANG CÓ ✓' : 'Chưa thấy agent-reach trong báo cáo (cập nhật Prompt V6.2 hoặc chạy SKILLSYNC_)') + ' · quota: ' + clean_(st.quota_summary || '', 80);
  }
  updateTask_(id, { STATUS: status || 'DONE', RESULT: msg, LINK: (rep || f).getUrl(), DONE_AT: now_() });
}

function summarizeSkillSync_(rep) {
  if (!rep) return 'SKILLSYNC hoàn tất';
  const txt = rep.getBlob().getDataAsString('UTF-8');
  const arOk = /agent-reach[\s\S]{0,1200}?(ok=true|"ok":\s*true|agent-reach\s+v?1\.)/i.test(txt);
  const removed = /action:\s*`?remove`?/i.test(txt);
  if (arOk && !removed) return 'Đã cài & kiểm tra agent-reach OK ✓';
  if (removed) return 'Đã gỡ skill theo manifest';
  return 'SKILLSYNC hoàn tất — xem báo cáo';
}

function handleReelScriptDone_(p, results, f, id, st, status, key) {
  if (status !== 'DONE') {
    const reason = clean_(st.reason || st.block_reason || '', 200);
    updateTask_(id, { STATUS: status || 'FAIL', RESULT: 'Newway: ' + status + (reason ? ' — ' + reason : ''), LINK: f.getUrl() });
    notify_('[NW Reel] ' + id + ' — Newway ' + status, 'Newway chưa hoàn tất kịch bản: ' + reason + '\n' + f.getUrl());
    return;
  }
  const spec = firstFile_(results, id + '__reel.json');
  if (!spec) { updateTask_(id, { STATUS: 'FAIL', RESULT: 'Newway báo DONE nhưng thiếu ' + id + '__reel.json' }); return; }
  const bytes = spec.getBlob().getBytes();
  if (bytes.length > CFG.MAX_SPEC_BYTES) { updateTask_(id, { STATUS: 'FAIL', RESULT: 'reel.json quá lớn (>60KB)' }); return; }
  try { const j = JSON.parse(spec.getBlob().getDataAsString('UTF-8')); if (j.schema !== 'nw-reel-v2') throw 0; }
  catch (e) { updateTask_(id, { STATUS: 'FAIL', RESULT: 'reel.json sai schema nw-reel-v2', LINK: spec.getUrl() }); return; }

  updateTask_(id, { STATUS: 'DONE', RESULT: 'Newway đã viết xong kịch bản & kích hoạt GitHub (' + now_() + ')', LINK: spec.getUrl(), DONE_AT: now_() });

  if (!dailyQuotaOk_()) {
    updateTask_(id + '_GHA', { STATUS: 'BLOCKED', RESULT: 'Chạm trần DAILY_DISPATCH_LIMIT — đặt DAILY_DISPATCH_LIMIT=0 để mở không giới hạn' });
    return;
  }
  const res = UrlFetchApp.fetch('https://api.github.com/repos/' + CFG.GITHUB_REPO + '/dispatches', {
    method: 'post', contentType: 'application/json', headers: ghHeaders_(), muteHttpExceptions: true,
    payload: JSON.stringify({ event_type: 'nw-reel-v2', client_payload: { reel_id: id, task_id: id, spec_b64: Utilities.base64Encode(bytes) } }),
  });
  if (res.getResponseCode() === 204) {
    updateTask_(id + '_GHA', { STATUS: 'PROCESSING', RESULT: 'GitHub đang dựng (OmniVoice Nhất Phong + HyperFrames) — gửi lúc ' + now_(),
      LINK: 'https://github.com/' + CFG.GITHUB_REPO + '/actions/workflows/nw-reel-v2.yml' });
  } else {
    p.deleteProperty(key);
    updateTask_(id + '_GHA', { STATUS: 'RETRY', RESULT: 'GitHub trả HTTP ' + res.getResponseCode() + ' — Bridge tự thử lại sau 1 phút' });
  }
}

/* ⑥ GHA_R2_<run>_<TASK>_status.json → cập nhật GSheet Pipeline */
function stepGhaStatus_() {
  const p = PropertiesService.getScriptProperties();
  const results = DriveApp.getFolderById(id_('RESULTS_ID'));
  const since = p.getProperty('SINCE') || new Date(Date.now() - 3600e3).toISOString();
  const it = results.searchFiles("title contains 'GHA_R2' and modifiedDate > '" + since + "' and trashed = false");
  while (it.hasNext()) {
    const f = it.next();
    if (!/^GHA_R2_\d+_[A-Za-z0-9_-]+_status\.json$/.test(f.getName())) continue;
    const key = 'S_' + f.getId();
    if (p.getProperty(key)) continue;
    p.setProperty(key, '1');
    let st;
    try { st = JSON.parse(f.getBlob().getDataAsString('UTF-8')); } catch (e) { continue; }
    const id = String(st.task_id || '');
    if (!CFG.REEL_RE.test(id)) continue;

    if (st.status === 'DONE') {
      const mp4 = firstFile_(results, st.files[0]);
      const qaF = firstFile_(results, 'GHA_R2_' + st.run_id + '_' + st.reel_id + '_qa.json');
      let summary = 'MP4 sẵn sàng';
      if (qaF) {
        try {
          const qa = JSON.parse(qaF.getBlob().getDataAsString('UTF-8'));
          summary = 'QA ' + (qa.pass ? 'ĐẠT' : 'CHƯA ĐẠT') + ' · ' + fmt_(qa.duration_s) + 's · ' + fmt_(qa.audio_lufs) + ' LUFS';
        } catch (e) { /* giữ mặc định */ }
      }
      updateTask_(id + '_GHA', { STATUS: 'DONE', RESULT: summary + ' · run ' + st.run_id, LINK: mp4 ? mp4.getUrl() : st.run_url, DONE_AT: now_() });
      updateTask_(id + '_QA', { STATUS: 'WAITING_APPROVAL', RESULT: 'Anh Hoàng xem MP4 và đổi QA_GATE_APPROVAL → APPROVED / REJECTED', LINK: mp4 ? mp4.getUrl() : '' });
      notify_('[NW Reel] ' + id + ' — video xong', summary + '\nMP4: ' + (mp4 ? mp4.getUrl() : '(xem RESULTS)') + '\nRun: ' + st.run_url);
    } else {
      updateTask_(id + '_GHA', { STATUS: 'FAIL', RESULT: clean_(st.reason || 'GitHub lỗi', 250), LINK: st.run_url, DONE_AT: now_() });
      notify_('[NW Reel] ' + id + ' — GitHub FAIL', (st.reason || '') + '\n' + st.run_url);
    }
  }
  p.setProperty('SINCE', new Date(Date.now() - 15 * 60e3).toISOString());
}

/* ============================ tiện ích phụ ============================ */
function enqueueMailbox_(taskId) {
  SpreadsheetApp.openById(id_('MAILBOX_NEWWAY_ID')).getSheets()[0]
    .appendRow([taskId, 'REPORT_STATUS', 'NONE', 'NONE', 'QUEUED', 'PENDING', 'NONE', now_()]);
}

function writeAgentReachManifest_(taskId) {
  const depot = DriveApp.getFolderById(id_('SKILL_DEPOT_ID'));
  const mf = { manifest_version: 1, task_id: taskId, items: [CFG.AGENT_REACH_ITEM] };
  upsertTextFile_(depot, taskId + '_manifest.json', JSON.stringify(mf, null, 2), 'application/json');
}

/** Ghi đè nội dung nếu file đã có (writer không cần quyền setTrashed trên file của owner khác), tạo mới nếu chưa có. */
function upsertTextFile_(folder, name, text, mime) {
  const it = folder.getFilesByName(name);
  let updated = false;
  while (it.hasNext()) {
    const f = it.next();
    if (!updated) {
      f.setContent(text);
      updated = true;
    } else {
      try { f.setTrashed(true); } catch (e) { /* khác owner trên My Drive thì bỏ qua bản dư */ }
    }
  }
  if (!updated) folder.createFile(name, text, mime || 'text/plain');
}

function sheetIds_() {
  const p = PropertiesService.getScriptProperties();
  const explicit = String(p.getProperty('PIPELINE_SHEET_IDS') || (typeof DEFAULTS_ !== 'undefined' && DEFAULTS_.PIPELINE_SHEET_IDS) || '')
    .split(',').map((s) => s.trim()).filter((s) => /^[A-Za-z0-9_-]{20,}$/.test(s));
  const folderId = String(p.getProperty('PIPELINE_FOLDER_ID') || (typeof DEFAULTS_ !== 'undefined' && DEFAULTS_.PIPELINE_FOLDER_ID) || '').trim();
  const seen = {};
  explicit.forEach((id) => { seen[id] = true; });
  if (/^[A-Za-z0-9_-]{20,}$/.test(folderId)) {
    const it = DriveApp.getFolderById(folderId).getFilesByType(MimeType.GOOGLE_SHEETS);
    while (it.hasNext()) {
      const f = it.next();
      if (!f.isTrashed() && /^\[PIPELINE\]/i.test(f.getName())) seen[f.getId()] = true;
    }
  }
  return Object.keys(seen);
}

function forEachTaskRow_(fn) {
  sheetIds_().forEach((sid) => {
    const sh = SpreadsheetApp.openById(sid).getSheetByName('02_TASKS');
    if (!sh || sh.getLastRow() < 2) return;
    const vals = sh.getRange(2, 1, sh.getLastRow() - 1, 11).getValues();
    vals.forEach((row, i) => fn(sh, i + 2, row));
  });
}

function findRow_(taskId) {
  let hit = null;
  forEachTaskRow_((sh, r, row) => { if (!hit && String(row[0]).trim() === taskId) hit = { sh: sh, r: r }; });
  return hit;
}

function updateTask_(taskId, cells) {
  const h = findRow_(taskId);
  if (h) setCells_(h.sh, h.r, cells);
}

function setCells_(sh, r, cells) {
  Object.keys(cells).forEach((k) => sh.getRange(r, CFG.C[k]).setValue(cells[k]));
  sh.getRange(r, CFG.C.DONE_AT).setNote('Bridge cập nhật ' + now_());
}

function ensureFollowRows_(sh, r, id) {
  const ids = sh.getRange(2, 1, Math.max(1, sh.getLastRow() - 1), 1).getValues().map((x) => String(x[0]).trim());
  const add = [];
  if (ids.indexOf(id + '_GHA') < 0) add.push([id + '_GHA', 'STEP_2', 'GitHub', id,
    'GitHub nw-reel-v2: giọng Nhất Phong (OmniVoice 24 kHz) + HyperFrames nw_reel_v2 → MP4 1080x1920 30fps H.264 High@L4.2 + AAC 192k, −14 LUFS, QA Gate.',
    '', 'WAITING', '', '', 'PENDING', '']);
  if (ids.indexOf(id + '_QA') < 0) add.push([id + '_QA', 'STEP_3', 'Anh Hoàng', id + '_GHA',
    'Nghiệm thu: xem MP4, kiểm số liệu/nguồn, OPSEC 0% tên tàu → QA_GATE_APPROVAL = APPROVED / REJECTED.',
    '', 'WAITING', '', '', 'PENDING', '']);
  if (add.length) {
    sh.insertRowsAfter(r, add.length);
    sh.getRange(r + 1, 1, add.length, 11).setValues(add);
  }
}

function firstFile_(folder, name) {
  if (!name) return null;
  const it = folder.getFilesByName(name);
  return it.hasNext() ? it.next() : null;
}

function parseOpts_(s) {
  const o = {};
  const pm = s.match(/platform\s*=\s*(tiktok|youtube_shorts|reels|tiktok_reels_shorts)/i);
  if (pm) o.platform = pm[1].toLowerCase();
  const sm = s.match(/seconds\s*=\s*(\d{2,3})/i);
  if (sm) o.target_seconds = Math.min(120, Math.max(60, parseInt(sm[1], 10)));
  return o;
}

function clean_(v, cap) {
  return String(v == null ? '' : v).replace(/[\u0000-\u001f\u007f<>{}`$\\]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, cap);
}

function dailyQuotaOk_() {
  const p = PropertiesService.getScriptProperties();
  const raw = p.getProperty('DAILY_DISPATCH_LIMIT');
  const limit = raw == null || raw === '' ? CFG.DEFAULT_DAILY_LIMIT : parseInt(raw, 10);
  if (!limit || limit <= 0) return true;   // 0 = không giới hạn (repo Public)
  const day = Utilities.formatDate(new Date(), CFG.TZ, 'yyyyMMdd');
  const n = parseInt(p.getProperty('Q_' + day) || '0', 10);
  if (n >= limit) return false;
  p.setProperty('Q_' + day, String(n + 1));
  return true;
}

function ghHeaders_() {
  return {
    Authorization: 'Bearer ' + id_('GITHUB_TOKEN'),
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
  };
}

function notify_(subject, body) {
  const to = PropertiesService.getScriptProperties().getProperty('NOTIFY_EMAIL');
  if (to) MailApp.sendEmail(to, subject, body);
}

function now_() { return Utilities.formatDate(new Date(), CFG.TZ, 'yyyy-MM-dd HH:mm:ss'); }
function fmt_(x) { return x == null ? '?' : (typeof x === 'number' ? Math.round(x * 10) / 10 : x); }
