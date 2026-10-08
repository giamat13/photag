/* photag picture viewer: shows the picture(s) of "Open with photag" without adding anything to the catalog.
   viewer.html?t=<token>: the token comes from /api/viewer/open (see app/viewer.py). Left / Right: previous / next picture of the folder,
   mouse wheel or + / -: zoom, drag: move, double click: fit <-> actual size, R: rotate, F: full screen. */
'use strict';
const $ = s => document.querySelector(s);
const stage = $('#stage'), pic = $('#pic');
let tok = new URLSearchParams(location.search).get('t');
let cur = null;                         // info of the picture on screen
let st = { s: 1, x: 0, y: 0, rot: 0, fit: true };
let nat = { w: 0, h: 0 };
const NEUTRAL = () => ({ exposure: 0, highlights: 0, shadows: 0, contrast: 100, brightness: 100, saturation: 100, temperature: 0, tint: 0,
  vibrance: 0, sharpness: 0, vignette: 0, bw: false, angle: 0, crop: [0, 0, 1, 1], cropOn: false, cropAuto: true, aspect: 'free' });
const edit = { on: false, ...NEUTRAL() };
const TONE = ['exposure', 'highlights', 'shadows', 'temperature', 'tint', 'vibrance', 'sharpness', 'vignette'];   // drawn by the server (CSS cannot)
let stripDirty = true, stripKey = null;
const cache = {};                       // token -> preloaded Image

function say(text, ms = 1800) {
  const m = $('#msg'); m.textContent = text; m.classList.remove('hidden');
  clearTimeout(say.t); if (ms) say.t = setTimeout(() => m.classList.add('hidden'), ms);
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let key = 'This file is not a picture photag can show';
    try { const d = (await r.json()).detail; if (d && d.key) key = d.key; } catch {}
    throw new Error(key);
  }
  return r.json();
}
const pane1 = $('#pane1'), pic2 = $('#pic2');
let cmp = null;                         // compare mode: { tok, info, nat } of the picture on the right
const size = () => { const r = pane1.getBoundingClientRect(); return { w: r.width, h: r.height }; };
const ang = () => st.rot + (edit.on ? edit.angle : 0);                 // the turn the picture is shown with: 90-degree steps + the straightening
function bbox() {                                                       // the size of the turned picture (what the crop is measured on)
  const a = ang() * Math.PI / 180, c = Math.abs(Math.cos(a)), sn = Math.abs(Math.sin(a));
  return { w: nat.w * c + nat.h * sn, h: nat.w * sn + nat.h * c };
}
function fitScale() {
  const { w, h } = size(), b = bbox();
  if (!b.w || !b.h) return 1;
  return Math.min(w / b.w, h / b.h, 1);               // small pictures stay at their real size
}
function clamp() {
  const { w, h } = size(), b = bbox(), iw = b.w * st.s, ih = b.h * st.s;
  const mx = Math.max(0, (iw - w) / 2), my = Math.max(0, (ih - h) / 2);
  st.x = Math.min(mx, Math.max(-mx, st.x)); st.y = Math.min(my, Math.max(-my, st.y));
}
function apply() {
  if (cur && cur.video) return;
  if (st.fit || edit.cropOn) { st.s = fitScale(); st.x = 0; st.y = 0; } else clamp();
  pic.style.filter = edit.on ? filterCss() : '';
  pic.style.transform = `translate(-50%,-50%) translate(${st.x}px,${st.y}px) rotate(${ang()}deg) scale(${st.s})`;
  if (cmp && cmp.nat.w) {                       // the other picture: the same zoom (so details can be compared) and the same move; fitted on its own while the left one is fitted
    const { w, h } = size(), s2 = st.fit ? Math.min(w / cmp.nat.w, h / cmp.nat.h, 1) : st.s;
    pic2.style.transform = `translate(-50%,-50%) translate(${st.x}px,${st.y}px) scale(${s2})`;
  }
  drawCrop();
}
function zoomAt(factor, cx, cy) {
  const { w, h } = size(), c = { x: cx - w / 2, y: cy - h / 2 };
  const ns = Math.min(32, Math.max(0.05, st.s * factor)), k = ns / st.s;
  st.fit = false; st.x = c.x - k * (c.x - st.x); st.y = c.y - k * (c.y - st.y); st.s = ns; apply();
}
function zoomCenter(f) { const { w, h } = size(); zoomAt(f, w / 2, h / 2); }
function fit() { st.fit = true; apply(); }
function actual() { const { w, h } = size(); st.fit = false; st.s = 1; st.x = 0; st.y = 0; apply(); }

function preload(token) {
  if (token && !cache[token] && !(cur && cur.video)) { const i = new Image(); i.src = `/api/viewer/${token}/image`; cache[token] = i; }
}
async function show(token) {
  try {
    const d = await api(`/api/viewer/${token}/info`);
    tok = token; cur = d;
    st = { s: 1, x: 0, y: 0, rot: 0, fit: true };
    const vid = $('#vid');
    document.body.classList.toggle('video', !!d.video);
    if (d.video) compareStop();
    if (d.video) {
      pic.removeAttribute('src'); closePanel();
      vid.onerror = () => say(t('This video cannot be played here'), 0);
      vid.onloadedmetadata = () => { $('#meta').textContent = [t('{n} of {total}', { n: d.index, total: d.count }), vid.videoWidth && `${vid.videoWidth} × ${vid.videoHeight}`, sizeText(d.bytes)].filter(Boolean).join('  ·  '); };
      vid.src = `/api/viewer/${token}/image`;
      nat = { w: 0, h: 0 };
    } else {
      vid.pause(); vid.removeAttribute('src'); vid.load();
      await new Promise((ok, bad) => { pic.onload = ok; pic.onerror = () => bad(new Error('This file is not a picture photag can show')); pic.src = `/api/viewer/${token}/image`; });
      nat = { w: pic.naturalWidth, h: pic.naturalHeight };
      pic.style.width = nat.w + 'px'; pic.style.height = nat.h + 'px';        // the live preview of the tone sliders is a smaller picture: keep the real size
      resetEdit();
      apply();
    }
    $('#name').textContent = d.name;
    if (cmp) $('#lab1').textContent = `${d.name}  ·  ${nat.w} × ${nat.h}  ·  ${sizeText(d.bytes)}`;
    $('#meta').textContent = [t('{n} of {total}', { n: d.index, total: d.count }), nat.w && `${nat.w} × ${nat.h}`, sizeText(d.bytes)].filter(Boolean).join('  ·  ');
    document.title = `${d.name} - photag`;
    $('#b-prev').disabled = !d.prev; $('#b-next').disabled = !d.next;
    ['#b-edit', '#b-info', '#b-map', '#b-rot'].forEach(b => { $(b).disabled = !!d.video; });
    $('#msg').classList.add('hidden');
    preload(d.next); preload(d.prev);
    history.replaceState(null, '', `?t=${token}`);
    if (panelKind === 'info') loadInfo(); else if (panelKind === 'map') loadMap();
    stripUpdate();
  } catch (e) { say(t(e.message), 0); }
}
function sizeText(b) { return b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`; }
const go = k => { if (cur && cur[k]) show(cur[k]); };
function rotate(d) { st.rot = (st.rot + d + 360) % 360; apply(); }
function full() { if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen().catch(() => {}); }
document.addEventListener('fullscreenchange', () => { document.body.classList.toggle('fs', !!document.fullscreenElement); setTimeout(apply, 50); });

$('#b-prev').onclick = () => go('prev'); $('#b-next').onclick = () => go('next');
$('#b-in').onclick = () => zoomCenter(1.25); $('#b-out').onclick = () => zoomCenter(0.8);
$('#b-fit').onclick = fit; $('#b-rot').onclick = () => rotate(90); $('#b-full').onclick = full;
$('#b-dir').onclick = () => api(`/api/viewer/${tok}/reveal`, { method: 'POST' }).catch(e => say(t(e.message)));
$('#b-add').onclick = async () => {
  try { const r = await api(`/api/viewer/${tok}/import`, { method: 'POST' }); say(r.added ? t('Added to the library') : t('Already in the library')); }
  catch (e) { say(t(e.message)); }
};

// ---- side panel: information (EXIF), location (map), edit ----
let vmap = null, panelKind = null;
const esc = x => String(x).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function filterCss() {
  return `brightness(${edit.brightness / 100}) contrast(${edit.contrast / 100}) saturate(${edit.saturation / 100})${edit.bw ? ' grayscale(1)' : ''}`;
}
// ---- the editor ----
const SLIDERS = [   // [key, label, min, max, step, group]
  ['exposure', t('Exposure'), -3, 3, 0.05, 'light'], ['highlights', t('Highlights'), -100, 100, 1, 'light'], ['shadows', t('Shadows'), -100, 100, 1, 'light'],
  ['contrast', t('Contrast'), 50, 150, 1, 'light'], ['brightness', t('Brightness'), 50, 150, 1, 'light'],
  ['temperature', t('Temperature'), -100, 100, 1, 'colour'], ['tint', t('Tint'), -100, 100, 1, 'colour'], ['vibrance', t('Vibrance'), -100, 100, 1, 'colour'],
  ['saturation', t('Saturation'), 0, 200, 1, 'colour'], ['sharpness', t('Sharpness'), 0, 100, 1, 'detail'], ['vignette', t('Vignette'), -100, 100, 1, 'detail'],
];
const NEUTRAL_OF = k => ({ contrast: 100, brightness: 100, saturation: 100 }[k] ?? 0);
const ASPECTS = [['free', t('Free')], ['orig', t('Original')], ['1:1', '1 : 1'], ['4:3', '4 : 3'], ['3:2', '3 : 2'], ['16:9', '16 : 9'], ['5:4', '5 : 4'], ['10x15', '10 × 15 cm'], ['13x18', '13 × 18 cm'], ['35x45', t('Passport 35 × 45 mm')]];
const ASPECT_VAL = { '1:1': 1, '4:3': 4 / 3, '3:2': 3 / 2, '16:9': 16 / 9, '5:4': 5 / 4, '10x15': 3 / 2, '13x18': 18 / 13, '35x45': 45 / 35 };
function buildEdit() {
  const grp = (g, title) => `<h4>${title}</h4>` + SLIDERS.filter(x => x[5] === g).map(([k, l, mn, mx, stp]) =>
    `<label class="sl"><span>${l}<i id="v-${k}"></i></span><input type="range" data-k="${k}" id="${{ brightness: 'e-bri', contrast: 'e-con', saturation: 'e-sat' }[k] || 'e-' + k}" min="${mn}" max="${mx}" step="${stp}" value="${NEUTRAL_OF(k)}"></label>`).join('');
  $('#p-edit').innerHTML = `<div class="row top"><button id="e-auto">${t('Improve automatically')}</button></div>
    ${grp('light', t('Light'))}${grp('colour', t('Colour'))}
    <label class="chk"><input type="checkbox" id="e-bw"> <span>${t('Black and white')}</span></label>
    ${grp('detail', t('Detail'))}
    <h4>${t('Straighten and crop')}</h4>
    <label class="sl"><span>${t('Straighten')}<i id="v-angle"></i></span><input type="range" id="e-angle" min="-20" max="20" step="0.1" value="0"></label>
    <div class="row"><button id="e-cropon">${t('Crop')}</button><select id="e-aspect">${ASPECTS.map(a => `<option value="${a[0]}">${a[1]}</option>`).join('')}</select><button id="e-swap" title="${t('Swap width and height')}">⇄</button><button id="e-cropreset">${t('Reset Crop')}</button></div>
    <div class="row"><button id="e-save" class="primary">${t('Save a copy')}</button><button id="e-reset">${t('Reset')}</button></div>`;
  $('#p-edit').querySelectorAll('input[type=range][data-k]').forEach(i => i.oninput = () => { edit[i.dataset.k] = +i.value; editChanged(i.dataset.k); });
  $('#e-bw').onchange = e => { edit.bw = e.target.checked; editChanged('bw'); };
  $('#e-angle').oninput = e => { edit.angle = +e.target.value; if (edit.cropAuto) edit.crop = inscribed(); editChanged('angle'); };
  $('#e-auto').onclick = autoImprove; $('#e-save').onclick = saveCopy; $('#e-reset').onclick = resetEdit;
  $('#e-cropon').onclick = () => { edit.cropOn = !edit.cropOn; if (edit.cropOn) applyAspect(); editChanged('crop'); };
  $('#e-aspect').onchange = e => { edit.aspect = e.target.value; edit.cropOn = true; applyAspect(); editChanged('crop'); };
  $('#e-swap').onclick = () => { swapAspect(); editChanged('crop'); };
  $('#e-cropreset').onclick = () => { edit.crop = edit.angle ? inscribed() : [0, 0, 1, 1]; edit.cropAuto = true; edit.cropOn = false; editChanged('crop'); };
  editSync();
}
function editSync() {
  SLIDERS.forEach(([k]) => { const i = $(`#p-edit [data-k="${k}"]`); if (i) { i.value = edit[k]; const v = $('#v-' + k); if (v) v.textContent = edit[k] === NEUTRAL_OF(k) ? '' : (k === 'exposure' ? (edit[k] > 0 ? '+' : '') + (+edit[k]).toFixed(2) : (+edit[k] - NEUTRAL_OF(k) > 0 ? '+' : '') + (+edit[k] - NEUTRAL_OF(k))); } });
  $('#e-bw').checked = edit.bw; $('#e-angle').value = edit.angle; $('#v-angle').textContent = edit.angle ? (edit.angle > 0 ? '+' : '') + (+edit.angle).toFixed(1) + '°' : '';
  $('#e-aspect').value = edit.aspect; $('#e-cropon').classList.toggle('on', edit.cropOn);
}
// the largest rectangle with the picture's own proportions that fits inside the straightened picture (no empty corners)
function inscribed() {
  const a = Math.abs(edit.angle) * Math.PI / 180;
  if (!a || !nat.w) return [0, 0, 1, 1];
  const turn = st.rot % 180 !== 0, w = turn ? nat.h : nat.w, h = turn ? nat.w : nat.h;
  const k = 1 / (Math.cos(a) + Math.sin(a) * Math.max(w / h, h / w));
  const W = w * Math.cos(a) + h * Math.sin(a), H = w * Math.sin(a) + h * Math.cos(a);
  const fx = w * k / W, fy = h * k / H;
  return [0.5 - fx / 2, 0.5 - fy / 2, 0.5 + fx / 2, 0.5 + fy / 2];
}
function cropAspect() {
  if (edit.aspect === 'free') return null;
  const b = bbox();
  if (edit.aspect === 'orig') return nat.w / nat.h;
  const r = ASPECT_VAL[edit.aspect];
  return b.w >= b.h ? Math.max(r, 1 / r) : Math.min(r, 1 / r);        // landscape pictures get landscape crops
}
function applyAspect() {      // the biggest crop of that shape, in the middle of what is there now
  const r = cropAspect(), b = bbox();
  edit.cropAuto = false;
  if (!r) return;
  const [x1, y1, x2, y2] = edit.crop, cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
  let w = (x2 - x1) * b.w, h = (y2 - y1) * b.h;
  if (w / h > r) w = h * r; else h = w / r;
  let fx = w / b.w, fy = h / b.h;
  const sx = Math.min(1, 2 * Math.min(cx, 1 - cx) / fx || 1), sy = Math.min(1, 2 * Math.min(cy, 1 - cy) / fy || 1), m = Math.min(sx, sy);
  fx *= m; fy *= m;
  edit.crop = [cx - fx / 2, cy - fy / 2, cx + fx / 2, cy + fy / 2];
}
function swapAspect() {
  const b = bbox(), [x1, y1, x2, y2] = edit.crop, cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
  let w = (y2 - y1) * b.h, h = (x2 - x1) * b.w;                      // turn the frame by 90 degrees (same area), then keep it inside
  const m = Math.min(1, b.w / w, b.h / h); w *= m; h *= m;
  let fx = w / b.w, fy = h / b.h;
  const nx = Math.min(Math.max(cx, fx / 2), 1 - fx / 2), ny = Math.min(Math.max(cy, fy / 2), 1 - fy / 2);
  edit.crop = [nx - fx / 2, ny - fy / 2, nx + fx / 2, ny + fy / 2]; edit.cropAuto = false;
}
let previewTimer = null, previewSeq = 0, previewUrl = null;
function editChanged(key) {
  editSync(); apply();
  if (TONE.includes(key) || key === 'all') { clearTimeout(previewTimer); previewTimer = setTimeout(tonePreview, 140); }
}
function opsBody() {
  const o = { brightness: edit.brightness / 100, contrast: edit.contrast / 100, saturation: edit.saturation / 100, grayscale: edit.bw, rotate: st.rot + edit.angle };
  TONE.forEach(k => { o[k] = +edit[k]; });
  const c = edit.crop;
  if (c.some((v, i) => Math.abs(v - [0, 0, 1, 1][i]) > 1e-3)) o.crop = c.map(v => Math.round(v * 10000) / 10000);
  return o;
}
async function tonePreview() {                 // the sliders CSS cannot draw (exposure, highlights, shadows, colour balance...): the server draws a preview
  if (!edit.on || !cur || cur.video) return;
  const seq = ++previewSeq;
  if (!TONE.some(k => +edit[k])) { if (previewUrl) { pic.src = `/api/viewer/${tok}/image`; URL.revokeObjectURL(previewUrl); previewUrl = null; } return; }
  try {
    const r = await fetch(`/api/viewer/${tok}/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(opsBody()) });
    if (!r.ok || seq !== previewSeq) return;
    const url = URL.createObjectURL(await r.blob());
    if (seq !== previewSeq) { URL.revokeObjectURL(url); return; }
    pic.src = url; if (previewUrl) URL.revokeObjectURL(previewUrl); previewUrl = url;
  } catch {}
}
async function autoImprove() {
  try {
    const o = await api(`/api/viewer/${tok}/auto`, { method: 'POST' });
    Object.assign(edit, NEUTRAL(), { on: true, crop: edit.crop, cropOn: edit.cropOn, cropAuto: edit.cropAuto, angle: edit.angle, aspect: edit.aspect });
    TONE.forEach(k => { if (o[k] != null) edit[k] = o[k]; });
    if (o.contrast != null) edit.contrast = Math.round(o.contrast * 100);
    editChanged('all');
  } catch (e) { say(t(e.message)); }
}
function resetEdit() {
  Object.assign(edit, NEUTRAL());
  previewSeq++; if (previewUrl && cur && !cur.video) { pic.src = `/api/viewer/${tok}/image`; URL.revokeObjectURL(previewUrl); previewUrl = null; }
  if ($('#e-angle')) editSync();
  apply();
}
// ---- the crop frame: drag it, or its handles ----
function drawCrop() {
  const el = $('#crop');
  if (!el) return;
  const show = edit.on && !(cur && cur.video) && (edit.cropOn || edit.crop.some((v, i) => Math.abs(v - [0, 0, 1, 1][i]) > 1e-3));
  el.classList.toggle('hidden', !show);
  if (!show) return;
  const { w, h } = size(), b = bbox(), W = b.w * st.s, H = b.h * st.s, left = w / 2 + st.x - W / 2, top = h / 2 + st.y - H / 2, c = edit.crop;
  Object.assign(el.style, { left: left + c[0] * W + 'px', top: top + c[1] * H + 'px', width: (c[2] - c[0]) * W + 'px', height: (c[3] - c[1]) * H + 'px' });
  el.classList.toggle('on', edit.cropOn);
  el._box = { W, H };
}
(function cropDrag() {
  const el = $('#crop');
  el.innerHTML = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'].map(h => `<i data-h="${h}"></i>`).join('');
  el.addEventListener('pointerdown', e => {
    if (!edit.cropOn) return;
    e.stopPropagation(); e.preventDefault();
    const hnd = e.target.dataset.h || 'move', start = [...edit.crop], sx = e.clientX, sy = e.clientY, { W, H } = el._box, r = cropAspect();
    el.setPointerCapture(e.pointerId);
    const mv = ev => {
      const dx = (ev.clientX - sx) / W, dy = (ev.clientY - sy) / H;
      let [x1, y1, x2, y2] = start;
      if (hnd === 'move') { const w = x2 - x1, h = y2 - y1; x1 = Math.min(Math.max(0, x1 + dx), 1 - w); y1 = Math.min(Math.max(0, y1 + dy), 1 - h); x2 = x1 + w; y2 = y1 + h; }
      else {
        if (hnd.includes('w')) x1 = Math.min(Math.max(0, x1 + dx), x2 - 0.03); if (hnd.includes('e')) x2 = Math.max(Math.min(1, x2 + dx), x1 + 0.03);
        if (hnd.includes('n')) y1 = Math.min(Math.max(0, y1 + dy), y2 - 0.03); if (hnd.includes('s')) y2 = Math.max(Math.min(1, y2 + dy), y1 + 0.03);
        if (r && hnd.length === 2) {            // a corner with a fixed shape: the width decides, the height follows
          const w = (x2 - x1) * W, h = w / r, fy = h / H;
          if (hnd.includes('n')) y1 = y2 - fy; else y2 = y1 + fy;
          if (y1 < 0 || y2 > 1) { const k = hnd.includes('n') ? y2 / fy : (1 - y1) / fy; const fx = ((x2 - x1) * k); if (hnd.includes('w')) x1 = x2 - fx; else x2 = x1 + fx; if (hnd.includes('n')) y1 = y2 - fy * k; else y2 = y1 + fy * k; }
        }
      }
      edit.crop = [x1, y1, x2, y2]; edit.cropAuto = false; drawCrop();
    };
    const up = () => { el.removeEventListener('pointermove', mv); el.removeEventListener('pointerup', up); };
    el.addEventListener('pointermove', mv); el.addEventListener('pointerup', up);
  });
})();
function closePanel() {
  const wasEdit = panelKind === 'edit';
  panelKind = null; edit.on = false; edit.cropOn = false;
  if (wasEdit && previewUrl && cur && !cur.video) { pic.src = `/api/viewer/${tok}/image`; URL.revokeObjectURL(previewUrl); previewUrl = null; } $('#panel').classList.add('hidden'); apply();
  ['#b-edit', '#b-info', '#b-map'].forEach(b => $(b).classList.remove('on'));
  setTimeout(apply, 30);
}
async function openPanel(kind) {
  if (panelKind === kind) { closePanel(); return; }
  compareStop();
  panelKind = kind; edit.on = kind === 'edit'; if (edit.on) { if (!$('#e-angle')) buildEdit(); editSync(); }
  const P = $('#panel'); P.dataset.k = kind; P.classList.remove('hidden');
  $('#ptitle').textContent = { info: t('All EXIF tags'), map: t('Location'), edit: t('Edit') }[kind];
  $('#b-edit').classList.toggle('on', kind === 'edit'); $('#b-info').classList.toggle('on', kind === 'info'); $('#b-map').classList.toggle('on', kind === 'map');
  if (kind === 'info') await loadInfo(); else if (kind === 'map') await loadMap();
  apply(); setTimeout(apply, 30);
}
let exifCache = { tok: null, data: null };
async function exifOf() {
  if (exifCache.tok !== tok) exifCache = { tok, data: await api(`/api/viewer/${tok}/exif`) };
  return exifCache.data;
}
async function loadInfo() {
  const box = $('#p-info'); box.textContent = '';
  try {
    const d = (await exifOf()).exif || {};
    const groups = Object.keys(d);
    if (!groups.length) { box.innerHTML = `<div class="none">${esc(t('No EXIF information available'))}</div>`; return; }
    box.innerHTML = groups.map(g => `<h4>${esc(g)}</h4>` + Object.entries(d[g]).map(([k, v]) =>
      `<div class="kv"><span>${esc(k)}</span><b>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</b></div>`).join('')).join('');
  } catch (e) { box.innerHTML = `<div class="none">${esc(t(e.message))}</div>`; }
}
async function loadMap() {
  const c = $('#coords'); c.textContent = '';
  let g = null;
  try { g = (await exifOf()).gps; } catch {}
  if (vmap) { vmap.remove(); vmap = null; }
  $('#vmap').style.display = g ? '' : 'none';
  if (!g) { c.textContent = t('This picture has no location'); return; }
  vmap = L.map('vmap', { zoomControl: true }).setView([g.lat, g.lng], 14);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap' }).addTo(vmap);
  L.circleMarker([g.lat, g.lng], { radius: 9, color: '#fff', weight: 2, fillColor: '#e8590c', fillOpacity: 1 }).addTo(vmap);
  c.textContent = `${g.lat.toFixed(5)}, ${g.lng.toFixed(5)}`;
  setTimeout(() => vmap && vmap.invalidateSize(), 60);
}
async function trash() {
  if (!cur) return;
  if (!confirm(t('Move this picture to the Recycle Bin?') + '\n' + cur.name)) return;
  try {
    const r = await api(`/api/viewer/${tok}/trash`, { method: 'POST' });
    say(t('Moved to the Recycle Bin'));
    if (r.next) show(r.next); else { pic.removeAttribute('src'); $('#name').textContent = ''; $('#meta').textContent = ''; say(t('Moved to the Recycle Bin'), 0); }
  } catch (e) { say(t(e.message)); }
}
async function saveCopy() {
  try {
    const r = await api(`/api/viewer/${tok}/edit`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(opsBody()) });
    stripDirty = true; closePanel(); await show(r.token); say(t('Saved as {name}', { name: r.name }));
  } catch (e) { say(t(e.message)); }
}
$('#b-edit').onclick = () => openPanel('edit'); $('#b-info').onclick = () => openPanel('info'); $('#b-map').onclick = () => openPanel('map');
$('#b-del').onclick = trash; $('#pclose').onclick = closePanel;

// ---- compare two pictures side by side (C): the same zoom and move on both; arrows change the right one, X swaps, C ends ----
async function loadRight(token) {
  const d = await api(`/api/viewer/${token}/info`);
  if (d.video) throw new Error('This file is not a picture photag can show');
  await new Promise((ok, bad) => { pic2.onload = ok; pic2.onerror = () => bad(new Error('This file is not a picture photag can show')); pic2.src = `/api/viewer/${token}/image`; });
  cmp = { tok: token, info: d, nat: { w: pic2.naturalWidth, h: pic2.naturalHeight } };
  pic2.style.width = cmp.nat.w + 'px'; pic2.style.height = cmp.nat.h + 'px';
  $('#lab2').textContent = `${d.name}  ·  ${cmp.nat.w} × ${cmp.nat.h}  ·  ${sizeText(d.bytes)}`;
  $('#lab1').textContent = `${cur.name}  ·  ${nat.w} × ${nat.h}  ·  ${sizeText(cur.bytes)}`;
}
async function compareStart() {
  if (!cur || cur.video) return;
  const other = cur.next || cur.prev;
  if (!other) { say(t('There is no other picture in this folder')); return; }
  slideStop(); closePanel(); closeMenu();
  try {
    await loadRight(other);
    document.body.classList.add('cmp'); $('#b-cmp').classList.add('on');
    st.fit = true; apply(); setTimeout(apply, 30);
  } catch (e) { cmp = null; say(t(e.message)); }
}
function compareStop() {
  if (!cmp) return;
  cmp = null; document.body.classList.remove('cmp'); $('#b-cmp').classList.remove('on'); pic2.removeAttribute('src');
  st.fit = true; apply(); setTimeout(apply, 30);
}
const compareToggle = () => cmp ? compareStop() : compareStart();
async function compareStep(k) {                 // the right picture moves through the folder (never onto the left one)
  if (!cmp) return;
  let tk = cmp.info[k];
  if (tk === tok) tk = (await api(`/api/viewer/${tk}/info`))[k];
  if (!tk) return;
  try { await loadRight(tk); apply(); } catch (e) { say(t(e.message)); }
}
async function compareSwap() {
  if (!cmp) return;
  const right = cmp.tok, left = tok;
  await show(right);                              // show() leaves compare mode alone; the old left picture goes to the right
  try { await loadRight(left); document.body.classList.add('cmp'); st.fit = true; apply(); } catch (e) { say(t(e.message)); }
}
$('#b-cmp').onclick = e => { e.stopPropagation(); compareToggle(); };

// ---- the thumbnail strip at the bottom ----
let stripOn = false;
try { stripOn = localStorage.getItem('photag-viewer-strip') === '1'; } catch {}
async function stripUpdate() {
  document.body.classList.toggle('strip', stripOn);
  $('#b-strip').classList.toggle('on', stripOn);
  const box = $('#strip');
  box.classList.toggle('hidden', !stripOn);
  if (!stripOn || !cur) return;
  try {
    if (stripDirty || !box.querySelector(`[data-t="${tok}"]`)) {
      const d = await api(`/api/viewer/${tok}/list`);
      stripDirty = false;
      box.innerHTML = d.items.map(it => `<button data-t="${it.token}" title="${esc(it.name)}"${it.video ? ' class="vid"' : ''}>${it.video ? '▶' : ''}</button>`).join('');
      box.querySelectorAll('button').forEach(b => {
        b.onclick = () => show(b.dataset.t);
        if (!b.classList.contains('vid')) b.style.backgroundImage = `url(/api/viewer/${b.dataset.t}/thumb)`;
      });
    }
    box.querySelectorAll('button.cur').forEach(b => b.classList.remove('cur'));
    const c = box.querySelector(`[data-t="${tok}"]`);
    if (c) { c.classList.add('cur'); c.scrollIntoView({ block: 'nearest', inline: 'center' }); }
  } catch {}
  setTimeout(apply, 30);
}
function stripToggle() {
  stripOn = !stripOn;
  try { localStorage.setItem('photag-viewer-strip', stripOn ? '1' : '0'); } catch {}
  stripUpdate();
}
$('#b-strip').onclick = stripToggle;
// ---- clipboard and desktop background ----
function currentCanvas() {         // the picture as it is on screen: turned, straightened, cropped, with the sliders
  const a = ang() * Math.PI / 180, w = pic.naturalWidth, h = pic.naturalHeight, b = bbox();
  const c = edit.on ? edit.crop : [0, 0, 1, 1];
  const k = Math.min(1, 4096 / Math.max(b.w, b.h));
  const full = document.createElement('canvas'); full.width = Math.round(b.w * k); full.height = Math.round(b.h * k);
  const g = full.getContext('2d');
  if (edit.on) g.filter = filterCss();
  g.translate(full.width / 2, full.height / 2); g.rotate(a); g.scale(k * (nat.w / w), k * (nat.h / h)); g.drawImage(pic, -w / 2, -h / 2);
  const out = document.createElement('canvas'), sx = c[0] * full.width, sy = c[1] * full.height;
  out.width = Math.max(1, Math.round((c[2] - c[0]) * full.width)); out.height = Math.max(1, Math.round((c[3] - c[1]) * full.height));
  out.getContext('2d').drawImage(full, sx, sy, out.width, out.height, 0, 0, out.width, out.height);
  return out;
}
async function copyPicture() {
  if (!cur || cur.video || !pic.naturalWidth) return;
  try {
    const blob = await new Promise((ok, bad) => currentCanvas().toBlob(b => b ? ok(b) : bad(new Error('x')), 'image/png'));
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
    say(t('Copied to the clipboard'));
  } catch { say(t('Could not copy the picture')); }
}
async function setWallpaper() {
  try { await api(`/api/viewer/${tok}/wallpaper`, { method: 'POST' }); say(t('The desktop background was changed')); }
  catch (e) { say(t(e.message)); }
}

// ---- the "more" menu: rename, copy, move, print; the slideshow ----
function closeMenu() { $('#menu').classList.add('hidden'); }
function openMenu(items, head) {
  const m = $('#menu');
  m.innerHTML = (head ? `<div class="head">${head}</div>` : '') + items.map((it, i) => `<button data-i="${i}">${it[0]}</button>`).join('');
  m.querySelectorAll('button').forEach(b => b.onclick = () => { closeMenu(); items[+b.dataset.i][1](); });
  m.classList.remove('hidden');
}
function toggleMenu(items, head) { if (!$('#menu').classList.contains('hidden')) closeMenu(); else openMenu(items, head); }
function askName(title, value, ok) {
  $('#dlg-title').textContent = title; const inp = $('#dlg-input'); inp.value = value;
  $('#dlg').classList.remove('hidden'); inp.focus();
  const dot = value.lastIndexOf('.'); inp.setSelectionRange(0, dot > 0 ? dot : value.length);
  const done = v => { inp.blur(); $('#dlg').classList.add('hidden'); if (v != null) ok(v); };
  $('#dlg-ok').onclick = () => done(inp.value); $('#dlg-cancel').onclick = () => done(null);
  inp.onkeydown = e => { e.stopPropagation(); if (e.key === 'Enter') done(inp.value); else if (e.key === 'Escape') done(null); };
}
async function renameIt() {
  if (!cur) return;
  askName(t('Rename the picture'), cur.name, async name => {
    try { const r = await api(`/api/viewer/${tok}/rename`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }); await show(r.token); say(t('Renamed to {name}', { name: r.name })); }
    catch (e) { say(t(e.message)); }
  });
}
async function pickFolder(title) { const r = await api('/api/pick-file?kind=folder&title=' + encodeURIComponent(title)); return r.path || null; }
async function copyIt() {
  if (!cur) return;
  const f = await pickFolder(t('Choose the folder to copy the picture to')).catch(e => { say(t(e.message)); return null; });
  if (!f) return;
  try { const r = await api(`/api/viewer/${tok}/copy`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folder: f }) }); say(t('Copied to {folder}', { folder: r.folder })); }
  catch (e) { say(t(e.message)); }
}
async function moveIt() {
  if (!cur) return;
  const f = await pickFolder(t('Choose the folder to move the picture to')).catch(e => { say(t(e.message)); return null; });
  if (!f) return;
  try {
    const r = await api(`/api/viewer/${tok}/move`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folder: f }) });
    say(t('Moved to {folder}', { folder: r.folder }));
    if (r.next && r.next !== tok) show(r.next); else if (!r.next) { pic.removeAttribute('src'); $('#name').textContent = ''; $('#meta').textContent = ''; }
  } catch (e) { say(t(e.message)); }
}
// Print: the picture as it is on screen (turned, and with the edit sliders) drawn on a canvas, so the printed page matches what you see.
function printIt() {
  if (!cur || !pic.naturalWidth) return;
  const turn = st.rot % 180 !== 0, w = pic.naturalWidth, h = pic.naturalHeight;
  const c = document.createElement('canvas'); c.width = turn ? h : w; c.height = turn ? w : h;
  const g = c.getContext('2d');
  if (edit.on) g.filter = filterCss();
  g.translate(c.width / 2, c.height / 2); g.rotate(st.rot * Math.PI / 180); g.drawImage(pic, -w / 2, -h / 2);
  $('#printimg').src = c.toDataURL('image/jpeg', 0.95);
  setTimeout(() => window.print(), 50);
}
// Slideshow: full screen, a new picture every few seconds, around the folder again and again; Esc, S or a click on the button ends it.
let slide = null;
function slideStop() { if (slide) { clearInterval(slide); slide = null; } document.body.classList.remove('show'); $('#b-show').classList.remove('on'); if (document.fullscreenElement) document.exitFullscreen().catch(() => {}); }
function slideStart(sec) {
  slideStop(); compareStop(); closePanel(); document.body.classList.add('show'); $('#b-show').classList.add('on');
  if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
  slide = setInterval(() => { if (!cur) return; if (cur.next) show(cur.next); else if (cur.first && cur.first !== tok) show(cur.first); }, sec * 1000);
}
const slideMenu = () => toggleMenu([2, 4, 8, 15].map(n => [t('Every {n} seconds', { n }), () => slideStart(n)]), t('Slideshow'));
const moreMenu = () => {
  const it = [[t('Rename…'), renameIt], [t('Copy to folder…'), copyIt], [t('Move to folder…'), moveIt]];
  if (cur && !cur.video) it.push([t('Copy picture'), copyPicture], [t('Print…'), printIt]);
  if (cur && cur.can_wallpaper) it.push([t('Set as desktop background'), setWallpaper]);
  toggleMenu(it);
};
$('#b-more').onclick = e => { e.stopPropagation(); moreMenu(); };
$('#b-show').onclick = e => { e.stopPropagation(); if (slide) slideStop(); else slideMenu(); };
document.addEventListener('click', () => closeMenu());
document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && slide) slideStop(); });
stage.addEventListener('wheel', e => { e.preventDefault(); const r = (cmp && e.clientX > $('#pane2').getBoundingClientRect().left ? $('#pane2') : pane1).getBoundingClientRect(); zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top); }, { passive: false });
stage.addEventListener('dblclick', e => { if (st.fit && fitScale() < 1) actual(); else fit(); });
let drag = null;
stage.addEventListener('pointerdown', e => { drag = { x: e.clientX - st.x, y: e.clientY - st.y }; stage.classList.add('drag'); stage.setPointerCapture(e.pointerId); });
stage.addEventListener('pointermove', e => { if (!drag) return; st.fit = false; st.x = e.clientX - drag.x; st.y = e.clientY - drag.y; apply(); });
stage.addEventListener('pointerup', () => { drag = null; stage.classList.remove('drag'); });
window.addEventListener('resize', apply);
window.addEventListener('keydown', e => {
  const k = e.key;
  if (cmp && (k === 'ArrowLeft' || k === 'PageUp')) compareStep('prev');
  else if (cmp && (k === 'ArrowRight' || k === 'PageDown' || k === ' ')) compareStep('next');
  else if (cmp && (k === 'x' || k === 'X')) compareSwap();
  else if (cmp && k === 'Escape') compareStop();
  else if ((k === 'c' || k === 'C') && !(e.ctrlKey || e.metaKey)) compareToggle();
  else if (k === 'ArrowLeft' || k === 'PageUp') go('prev');
  else if (k === 'ArrowRight' || k === 'PageDown' || k === ' ') go('next');
  else if (k === 'Home') go('first'); else if (k === 'End') go('last');
  else if (k === '+' || k === '=') zoomCenter(1.25); else if (k === '-') zoomCenter(0.8);
  else if (k === '0') fit(); else if (k === '1') actual();
  else if (k === 'r' || k === 'R') rotate(e.shiftKey ? -90 : 90);
  else if (k === 'f' || k === 'F' || k === 'F11') full();
  else if (k === 'Delete') trash();
  else if (k === 'F2') renameIt();
  else if ((k === 'p' || k === 'P') && (e.ctrlKey || e.metaKey)) printIt();
  else if (k === 's' || k === 'S') { if (slide) slideStop(); else slideMenu(); }
  else if ((k === 'c' || k === 'C') && (e.ctrlKey || e.metaKey)) copyPicture();
  else if (k === 't' || k === 'T') stripToggle();
  else if (k === 'i' || k === 'I') openPanel('info');
  else if (k === 'Escape' && slide) slideStop();
  else if (k === 'Escape' && !$('#menu').classList.contains('hidden')) closeMenu();
  else if (k === 'Escape' && panelKind) closePanel();
  else return;
  e.preventDefault();
});
setInterval(() => fetch('/api/viewer/ping').catch(() => {}), 4000);       // tells the program that a viewer window is still open
if (tok) show(tok); else say(t('This file is not a picture photag can show'), 0);
