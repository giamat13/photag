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
const size = () => { const r = stage.getBoundingClientRect(); return { w: r.width, h: r.height }; };
const rotated = () => st.rot % 180 !== 0;
function fitScale() {
  const { w, h } = size(), iw = rotated() ? nat.h : nat.w, ih = rotated() ? nat.w : nat.h;
  if (!iw || !ih) return 1;
  return Math.min(w / iw, h / ih, 1);               // small pictures stay at their real size
}
function clamp() {
  const { w, h } = size(), iw = (rotated() ? nat.h : nat.w) * st.s, ih = (rotated() ? nat.w : nat.h) * st.s;
  const mx = Math.max(0, (iw - w) / 2), my = Math.max(0, (ih - h) / 2);
  st.x = Math.min(mx, Math.max(-mx, st.x)); st.y = Math.min(my, Math.max(-my, st.y));
}
function apply() {
  if (st.fit) { st.s = fitScale(); st.x = 0; st.y = 0; } else clamp();
  pic.style.transform = `translate(-50%,-50%) translate(${st.x}px,${st.y}px) rotate(${st.rot}deg) scale(${st.s})`;
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
  if (token && !cache[token]) { const i = new Image(); i.src = `/api/viewer/${token}/image`; cache[token] = i; }
}
async function show(token) {
  try {
    const d = await api(`/api/viewer/${token}/info`);
    tok = token; cur = d;
    st = { s: 1, x: 0, y: 0, rot: 0, fit: true };
    await new Promise((ok, bad) => { pic.onload = ok; pic.onerror = () => bad(new Error('This file is not a picture photag can show')); pic.src = `/api/viewer/${token}/image`; });
    nat = { w: pic.naturalWidth, h: pic.naturalHeight };
    apply();
    $('#name').textContent = d.name;
    $('#meta').textContent = [t('{n} of {total}', { n: d.index, total: d.count }), nat.w && `${nat.w} × ${nat.h}`, sizeText(d.bytes)].filter(Boolean).join('  ·  ');
    document.title = `${d.name} - photag`;
    $('#b-prev').disabled = !d.prev; $('#b-next').disabled = !d.next;
    $('#msg').classList.add('hidden');
    preload(d.next); preload(d.prev);
    history.replaceState(null, '', `?t=${token}`);
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
stage.addEventListener('wheel', e => { e.preventDefault(); zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - stage.getBoundingClientRect().left, e.clientY - stage.getBoundingClientRect().top); }, { passive: false });
stage.addEventListener('dblclick', e => { if (st.fit && fitScale() < 1) actual(); else fit(); });
let drag = null;
stage.addEventListener('pointerdown', e => { drag = { x: e.clientX - st.x, y: e.clientY - st.y }; stage.classList.add('drag'); stage.setPointerCapture(e.pointerId); });
stage.addEventListener('pointermove', e => { if (!drag) return; st.fit = false; st.x = e.clientX - drag.x; st.y = e.clientY - drag.y; apply(); });
stage.addEventListener('pointerup', () => { drag = null; stage.classList.remove('drag'); });
window.addEventListener('resize', apply);
window.addEventListener('keydown', e => {
  const k = e.key;
  if (k === 'ArrowLeft' || k === 'PageUp') go('prev');
  else if (k === 'ArrowRight' || k === 'PageDown' || k === ' ') go('next');
  else if (k === 'Home') go('first'); else if (k === 'End') go('last');
  else if (k === '+' || k === '=') zoomCenter(1.25); else if (k === '-') zoomCenter(0.8);
  else if (k === '0') fit(); else if (k === '1') actual();
  else if (k === 'r' || k === 'R') rotate(e.shiftKey ? -90 : 90);
  else if (k === 'f' || k === 'F' || k === 'F11') full();
  else return;
  e.preventDefault();
});
setInterval(() => fetch('/api/viewer/ping').catch(() => {}), 4000);       // tells the program that a viewer window is still open
if (tok) show(tok); else say(t('This file is not a picture photag can show'), 0);
