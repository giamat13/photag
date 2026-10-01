/* photag — Lightroom Classic-style Library / Develop / Slideshow.
   One page, no framework. State lives in S; the grid and filmstrip are
   virtualized so catalogs with tens of thousands of photos stay smooth. */
'use strict';

// ---------- helpers ----------
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];
async function api(u, opt){
  const r = await fetch(u, opt); const t = await r.text(); let d = {};
  try{ d = t ? JSON.parse(t) : {}; }catch{}
  if(!r.ok) throw new Error(d.detail || r.statusText);
  return d;
}
const send = (method, u, body) => api(u, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body||{})});
const esc = s => (s??'').toString().replace(/[<>&"']/g, c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));
const num = n => (n||0).toLocaleString('he-IL');
const fdate = t => t ? new Date(t*1000).toLocaleString('he-IL', {dateStyle:'medium', timeStyle:'short'}) : '—';
const fsize = b => !b ? '—' : b > 1048576 ? (b/1048576).toFixed(1)+' MB' : Math.max(1, Math.round(b/1024))+' KB';
const I = (n, cls='') => `<svg class="ic ${cls}"><use href="#i-${n}"/></svg>`;
const ext = p => (p.filename.split('.').pop()||'').toUpperCase();
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function debounce(fn, ms){ let h; return (...a)=>{ clearTimeout(h); h=setTimeout(()=>fn(...a), ms); }; }
function toast(msg, ms=2600){ const t=$('#toast'); t.innerHTML=msg; t.classList.remove('hidden'); clearTimeout(t._h); t._h=setTimeout(()=>t.classList.add('hidden'), ms); }
window.addEventListener('unhandledrejection', e=>toast('שגיאה: '+esc(e.reason?.message||e.reason)));
const pref = {
  get(k, d){ try{ const v=localStorage.getItem('pm.'+k); return v==null ? d : JSON.parse(v); }catch{ return d; } },
  set(k, v){ try{ localStorage.setItem('pm.'+k, JSON.stringify(v)); }catch{} },
};

const LABELS = [['red','אדום','6'],['yellow','צהוב','7'],['green','ירוק','8'],['blue','כחול','9'],['purple','סגול','']];
const LNAME = Object.fromEntries(LABELS.map(([k,n])=>[k,n]));
const lcol = k => `var(--${k})`;
// photo/media urls carry a version so edited photos don't come back stale from the browser cache
const VER = {};
const thumbUrl = id => `/thumb/${id}${VER[id]?'?v='+VER[id]:''}`;
const mediaUrl = id => `/media/${id}${VER[id]?'?v='+VER[id]:''}`;

// ---------- state ----------
const S = {
  mod:'library', view:'grid', prevView:'grid',
  src:{kind:'all', name:'כל התמונות'}, hist:[], histPos:-1,
  all:[], byId:new Map(), base:[], list:[], idx:new Map(),
  sel:new Set(), act:null, anchor:null,
  sort:pref.get('sort','capture'), asc:pref.get('asc',false),
  fb:'none',
  F:{on:true, q:'', qf:'any', flags:new Set(), rop:'>=', rating:0, labels:new Set(), kinds:new Set(),
     meta:{year:new Set(), month:new Set(), ext:new Set(), orient:new Set()}},
  cell:pref.get('cellStyle','compact'), cellsz:pref.get('cellsz',180), loupeInfo:true, lights:0,
  status:null, albums:[], folders:{root:'', folders:[]}, tags:[], people:[],
  recentKw:pref.get('recentKw',[]),
};
const targets = () => (S.view==='grid' || S.view==='survey') && S.sel.size ? [...S.sel] : (S.act!=null ? [S.act] : []);
const actPhoto = () => S.act!=null ? (S.byId.get(S.act) || S.base.find(p=>p.id===S.act)) : null;

// ---------- catalog + side data ----------
async function loadCatalog(){
  S.all = await api('/api/photos?limit=10000000');
  S.byId = new Map(S.all.map(p=>[p.id, p]));
}
async function loadSide(){
  const [st, al, fo, tg, pe] = await Promise.all([api('/api/status'), api('/api/albums'), api('/api/folders'), api('/api/tags'), api('/api/people')]);
  Object.assign(S, {status:st, albums:al, folders:fo, tags:tg, people:pe});
  renderCatalog(); renderFolders(); renderColls(); renderKwList();
}

// ---------- sources (what the grid shows) ----------
const MONTH_S = 30*86400;
const SMART = [
  ['red',   'תווית אדומה',          p=>p.label==='red'],
  ['five',  'חמישה כוכבים',         p=>p.rating===5],
  ['picks', 'נבחרו (דגל)',           p=>p.flag===1],
  ['month', 'החודש האחרון',          p=>(p.taken_at||0) > Date.now()/1000 - MONTH_S],
  ['video', 'קובצי וידאו',           p=>!!p.is_video],
  ['nokw',  'ללא מילות מפתח',        p=>!p.has_kw],
  ['edited','ערוכות',                p=>!!p.edited],
  ['fav',   'מועדפים מ‑Google',      p=>!!p.favorited],
];
function srcKey(s){ return s.kind + (s.id!=null ? ':'+s.id : ''); }
function srcParams(s){
  switch(s.kind){
    case 'trash': return {trashed:1};
    case 'album': return {album:s.id};
    case 'person': return {person:s.id};
    case 'cluster': return {cluster:s.id};
    case 'tag': return {tag:s.id};
    case 'folder': return {folder:s.id};
    case 'quick': return {quick:1};
    case 'prev': return {prev_import:1};
    default: return {};
  }
}
async function setSource(src, {push=true, keepSel=false}={}){
  if(push){ S.hist = S.hist.slice(0, S.histPos+1); S.hist.push(src); S.histPos = S.hist.length-1; }
  S.src = src;
  if(!keepSel){ S.sel.clear(); S.act=null; S.anchor=null; }
  markSourceRows();
  await fetchSource();
  if(!keepSel) $('#v-grid').scrollTop = 0;
  if(S.view==='people') setView('grid');
}
async function fetchSource(){
  const src = S.src, q = S.F.qf!=='name' ? S.F.q : '';
  let rows;
  if(src.kind==='all' && !q) rows = S.all;
  else {
    const p = new URLSearchParams({limit:10000000, ...srcParams(src)}); if(q) p.set(S.F.qf==='smart' ? 'smart' : 'q', q);
    rows = (await api('/api/photos?'+p)).map(r=>{ const o=S.byId.get(r.id) || r; o.score = r.score; return o; });
    if(S.src!==src) return;   // clicked elsewhere meanwhile
  }
  if(src.kind==='smart'){ const f = SMART.find(x=>x[0]===src.id); rows = rows.filter(f[2]); }
  S.base = rows;
  applyFilter();
}

// ---------- filtering + sorting ----------
const yearOf = p => p.taken_at ? String(new Date(p.taken_at*1000).getFullYear()) : 'ללא';
const monthOf = p => p.taken_at ? String(new Date(p.taken_at*1000).getMonth()+1).padStart(2,'0') : 'ללא';
const orientOf = p => !p.width||!p.height ? 'לא ידוע' : p.width>p.height*1.05 ? 'לרוחב' : p.height>p.width*1.05 ? 'לאורך' : 'ריבועי';
const flagKey = p => p.flag===1 ? 'pick' : p.flag===-1 ? 'rej' : 'none';
function passAttr(p){
  const F = S.F;
  if(F.flags.size && !F.flags.has(flagKey(p))) return false;
  if(F.rating){ const r=p.rating||0;
    if(F.rop==='>=' && r<F.rating) return false;
    if(F.rop==='<=' && r>F.rating) return false;
    if(F.rop==='=' && r!==F.rating) return false; }
  if(F.labels.size && !F.labels.has(p.label||'none')) return false;
  if(F.kinds.size && !((F.kinds.has('photo')&&!p.is_video) || (F.kinds.has('video')&&p.is_video) || (F.kinds.has('edited')&&p.edited))) return false;
  return true;
}
const META_COLS = [['year','תאריך',yearOf],['month','חודש',monthOf],['ext','סוג קובץ',ext],['orient','כיוון',orientOf]];
function passMeta(p, upto=META_COLS.length){
  for(let i=0;i<upto;i++){ const [k,,fn]=META_COLS[i]; const set=S.F.meta[k]; if(set.size && !set.has(fn(p))) return false; }
  return true;
}
function filterActive(){
  const F=S.F; return !!(F.q || F.flags.size || F.rating || F.labels.size || F.kinds.size || Object.values(F.meta).some(s=>s.size));
}
const SORTS = {
  capture:['זמן צילום', (a,b)=>(a.taken_at||0)-(b.taken_at||0) || a.id-b.id],
  import: ['סדר הוספה', (a,b)=>(a.imported_at||0)-(b.imported_at||0) || a.id-b.id],
  name:   ['שם קובץ',   (a,b)=>a.filename.localeCompare(b.filename, 'he', {numeric:true})],
  rating: ['דירוג',     (a,b)=>(a.rating||0)-(b.rating||0) || (a.taken_at||0)-(b.taken_at||0)],
  pick:   ['דגל',       (a,b)=>(a.flag||0)-(b.flag||0) || (a.taken_at||0)-(b.taken_at||0)],
  label:  ['תווית צבע', (a,b)=>lrank(a)-lrank(b) || (a.taken_at||0)-(b.taken_at||0)],
  size:   ['גודל קובץ', (a,b)=>(a.bytes||0)-(b.bytes||0)],
};
const lrank = p => p.label ? LABELS.findIndex(l=>l[0]===p.label) : 9;
function applyFilter({keepScroll=true}={}){
  let rows = S.base;
  if(S.F.on){
    if(S.F.q && S.F.qf==='name'){ const q=S.F.q.toLowerCase(); rows = rows.filter(p=>p.filename.toLowerCase().includes(q)); }
    rows = rows.filter(p=>passAttr(p) && passMeta(p));
  }
  const cmp = SORTS[S.sort][1];
  rows = S.F.q && S.F.qf==='smart' ? rows.slice().sort((a,b)=>(b.score||0)-(a.score||0))   // best matches first
       : rows.slice().sort(S.asc ? cmp : (a,b)=>cmp(b,a));
  S.list = rows;
  S.idx = new Map(rows.map((p,i)=>[p.id,i]));
  for(const id of [...S.sel]) if(!S.idx.has(id)) S.sel.delete(id);
  if(S.act!=null && !S.idx.has(S.act)) S.act = S.sel.size ? [...S.sel][0] : null;
  if(!keepScroll) $('#v-grid').scrollTop=0;
  renderAll();
}

// ---------- selection ----------
function selectClick(id, e){
  if(e && (e.ctrlKey||e.metaKey)){
    if(S.sel.has(id)){ S.sel.delete(id); if(S.act===id) S.act = S.sel.size ? [...S.sel].pop() : null; }
    else { S.sel.add(id); S.act=id; }
    S.anchor=id;
  } else if(e && e.shiftKey && S.anchor!=null && S.idx.has(S.anchor)){
    const a=S.idx.get(S.anchor), b=S.idx.get(id);
    S.sel.clear(); for(let i=Math.min(a,b); i<=Math.max(a,b); i++) S.sel.add(S.list[i].id);
    S.act=id;
  } else {
    if(!(S.sel.has(id) && S.sel.size>1 && e && e.type==='mousedown')){ S.sel.clear(); S.sel.add(id); }
    S.act=id; S.anchor=id;
  }
  onSelChange();
}
function selectOnly(id){ S.sel.clear(); if(id!=null) S.sel.add(id); S.act=id; S.anchor=id; onSelChange(); }
function selectAll(){ S.list.forEach(p=>S.sel.add(p.id)); if(S.act==null && S.list.length) S.act=S.list[0].id; onSelChange(); }
function selectNone(){ S.sel.clear(); S.act=null; onSelChange(); }
function selectInvert(){ const n=new Set(S.list.filter(p=>!S.sel.has(p.id)).map(p=>p.id)); S.sel=n; S.act=n.size?[...n][0]:null; onSelChange(); }
function selectPicks(){ S.sel=new Set(S.list.filter(p=>p.flag===1).map(p=>p.id)); S.act=S.sel.size?[...S.sel][0]:null; onSelChange(); }
function moveAct(d, extend){
  if(!S.list.length) return;
  let i = S.act!=null && S.idx.has(S.act) ? S.idx.get(S.act)+d : (d>0?0:S.list.length-1);
  i = clamp(i, 0, S.list.length-1);
  const id = S.list[i].id;
  if(extend){ S.sel.add(id); S.act=id; onSelChange(); }
  else selectOnly(id);
  scrollToAct();
}
const onSelChange = () => { refreshCells(); renderFilm(); renderPath(); renderRight(); renderToolbar(); updateNavigator();
  if(S.view==='loupe') renderLoupe(); else if(S.view==='compare') renderCompare(); else if(S.view==='survey') renderSurvey(); };

// ---------- attribute changes (flags, stars, labels, quick collection, trash) ----------
async function setAttr(fields, ids=targets(), {advance=false}={}){
  if(!ids.length) return;
  ids.forEach(id=>{ const p=S.byId.get(id) || S.base.find(x=>x.id===id); if(p) Object.assign(p, fields.label!==undefined?{...fields, label:fields.label||null}:fields); });
  await send('PATCH', '/api/photos', {ids, ...fields});
  if('trashed' in fields){ await reloadAll(); return; }
  renderCatalog(); renderColls();
  applyFilter();
  if(advance) moveAct(1);
}
const setFlag = (f, adv) => setAttr({flag:f}, undefined, {advance:adv});
function toggleFlag(){ const p=actPhoto(); if(p) setFlag(p.flag===1?0:1); }
const setRating = (r, adv) => setAttr({rating:r}, undefined, {advance:adv});
function bumpRating(d){ const p=actPhoto(); if(p) setRating(clamp((p.rating||0)+d,0,5)); }
function setLabel(l, adv){ const p=actPhoto(); if(!p) return; setAttr({label: p.label===l ? '' : l}, undefined, {advance:adv}); }
function toggleQuick(){
  const ids=targets(); if(!ids.length) return;
  const on = !(S.byId.get(ids[0])||{}).quick;
  setAttr({quick:on?1:0}, ids);
  toast(on ? `נוסף לאוסף המהיר (${num(ids.length)})` : 'הוסר מהאוסף המהיר');
}
async function trashSelected(){
  const ids=targets(); if(!ids.length) return;
  const restore = S.src.kind==='trash';
  await setAttr({trashed: restore?0:1}, ids);
  toast(restore ? `שוחזרו ${num(ids.length)} פריטים` : `הועברו לאשפה ${num(ids.length)} פריטים · נמחקים לצמיתות אחרי ${S.status?.trash_days||60} יום`);
}
async function rotateSel(deg){
  const ids = targets().filter(id=>!(S.byId.get(id)||{}).is_video); if(!ids.length) return;
  toast('מסובב…', 1200);
  for(const id of ids){
    const d = await send('POST', `/api/photo/${id}/rotate`, {degrees:deg});
    const p = S.byId.get(id); if(p) Object.assign(p, {width:d.width, height:d.height, edited:d.edited});
    VER[id] = Date.now();
  }
  layoutGrid(true); renderFilm(); renderRight(); if(S.view==='loupe') renderLoupe();
}
async function reloadAll(){
  await Promise.all([loadCatalog(), loadSide()]);
  await fetchSource();
}

// ---------- rendering: everything ----------
function renderAll(){
  layoutGrid(true); renderFilm(true); renderPath(); renderToolbar(); renderRight(); renderFilterBar(); updateNavigator();
  if(S.view==='loupe') renderLoupe();
  else if(S.view==='compare') renderCompare();
  else if(S.view==='survey') renderSurvey();
  $('#v-empty').classList.toggle('hidden', !(S.view==='grid' && !S.list.length));
  if(S.view==='grid' && !S.list.length) renderEmpty();
}
function renderEmpty(){
  const e=$('#v-empty');
  if(!S.all.length && S.src.kind==='all') e.innerHTML = `<b>הקטלוג ריק</b><div>ייבאו תמונות מתיקייה, מכרטיס זיכרון או מ‑Google Takeout.</div><button class="primary" onclick="openImport()">ייבוא...</button>`;
  else if(S.base.length) e.innerHTML = `<b>אין תמונות שתואמות למסנן</b><div>${num(S.base.length)} תמונות במקור הזה מוסתרות על ידי המסנן.</div><button onclick="clearFilters()">נקה מסנן</button>`;
  else e.innerHTML = `<b>אין כאן תמונות</b>`;
}

// ---------- grid (virtualized) ----------
const G = {cols:1, cw:180, cells:new Map(), first:-1, last:-1};
function layoutGrid(rebuild){
  const el=$('#v-grid'), inner=$('#grid-inner');
  const W = el.clientWidth - 16;
  G.cols = Math.max(1, Math.floor(W / S.cellsz));
  G.cw = Math.floor(W / G.cols);
  inner.style.setProperty('--cellsz', G.cw+'px');
  inner.style.height = (Math.ceil(S.list.length / G.cols) * G.cw + 16) + 'px';
  inner.className = 'grid-inner ' + (S.cell==='xp'?'xp':S.cell==='plain'?'plain':'');
  if(rebuild){ G.cells.forEach(c=>c.remove()); G.cells.clear(); G.first=G.last=-1; }
  drawGrid();
}
function drawGrid(){
  if(S.view!=='grid') return;
  const el=$('#v-grid'), inner=$('#grid-inner');
  const r0 = Math.max(0, Math.floor(el.scrollTop / G.cw) - 2);
  const r1 = Math.ceil((el.scrollTop + el.clientHeight) / G.cw) + 2;
  const first = r0*G.cols, last = Math.min(S.list.length-1, r1*G.cols + G.cols - 1);
  for(const [i,c] of G.cells) if(i<first || i>last){ c.remove(); G.cells.delete(i); }
  const frag=document.createDocumentFragment();
  for(let i=first;i<=last;i++){
    if(G.cells.has(i)) continue;
    const c = makeCell(S.list[i], i); G.cells.set(i, c); frag.appendChild(c);
  }
  inner.appendChild(frag);
  G.first=first; G.last=last;
}
function fitBox(p, bw, bh){
  const w=p.width||4, h=p.height||3, s=Math.min(bw/w, bh/h);
  return [Math.max(8,Math.round(w*s)), Math.max(8,Math.round(h*s))];
}
function makeCell(p, i){
  const c=document.createElement('div');
  c.className='cell'; c.dataset.id=p.id; c.dataset.i=i; c.draggable=true;
  c.style.top = (8 + Math.floor(i/G.cols)*G.cw)+'px';
  c.style.insetInlineStart = (8 + (i%G.cols)*G.cw)+'px';
  const xp = S.cell==='xp', plain = S.cell==='plain';
  const [w,h] = fitBox(p, G.cw*(plain?.84:.78), G.cw*(plain?.84:xp?.56:.65));
  c.innerHTML = `<div class="idx">${i+1}</div>
    <div class="hdr"><span class="n">${i+1}</span><span class="f">${esc(p.filename)}</span><span>${p.width&&p.height?p.width+'×'+p.height:ext(p)}</span></div>
    <div class="ph"><img loading="lazy" decoding="async" draggable="false" width="${w}" height="${h}" style="width:${w}px;height:${h}px" src="${thumbUrl(p.id)}" alt="" onerror="this.parentElement.classList.add('noimg');this.remove()"></div>
    <span class="ov"></span>`;
  c._w=w; c._h=h;
  fillCell(c, p, i);
  return c;
}
function fillCell(c, p, i){
  c.classList.toggle('sel', S.sel.has(p.id));
  c.classList.toggle('act', S.act===p.id && S.sel.size>1);
  if(S.act===p.id && S.sel.size<=1) c.classList.add('act');
  c.classList.toggle('rej', p.flag===-1);
  if(p.label){ c.dataset.l=p.label; c.style.setProperty('--lc', lcol(p.label)); } else { delete c.dataset.l; }
  const r=p.rating||0;
  const stars = [1,2,3,4,5].map(n=>`<b data-r="${n}" class="${n<=r?'':'off'}">${n<=r?'★':'•'}</b>`).join('');
  const bx = (G.cw - c._w)/2, by = S.cell==='xp' ? G.cw*.22 + (G.cw*.58 - c._h)/2 : G.cw*.15 + (G.cw*.65 - c._h)/2;
  const badges = [p.has_kw && I('kw'), p.edited && I('dev')].filter(Boolean);
  c.querySelector('.ov').innerHTML =
    `<button class="flag ${p.flag===1?'pick':p.flag===-1?'rej':''}" data-a="flag" title="דגל (P / X / U)">${I(p.flag===-1?'reject':'flag')}</button>
     <button class="qc ${p.quick?'on':''}" data-a="qc" title="אוסף מהיר (B)">${I('dot')}</button>
     <button class="rot l" data-a="rotl" title="סובב שמאלה (Ctrl+[)">${I('rotl')}</button>
     <button class="rot r" data-a="rotr" title="סובב ימינה (Ctrl+])">${I('rotr')}</button>
     <div class="stars ${r?'':'none'}">${stars}</div>
     ${badges.length?`<div class="badges" style="inset-block-start:${Math.round(by+c._h-18)}px;inset-inline-end:${Math.round(bx+4)}px">${badges.map(b=>`<i>${b}</i>`).join('')}</div>`:''}
     ${p.is_video?`<span class="dur" style="inset-block-start:${Math.round(by+c._h-18)}px;inset-inline-start:${Math.round(bx+4)}px">${I('play')}וידאו</span>`:''}`;
}
function refreshCells(){ for(const [i,c] of G.cells){ const p=S.list[i]; if(p) fillCell(c,p,i); } }
function scrollToAct(){
  if(S.act==null || !S.idx.has(S.act)) return;
  const i=S.idx.get(S.act);
  if(S.view==='grid'){
    const el=$('#v-grid'), top=8+Math.floor(i/G.cols)*G.cw;
    if(top < el.scrollTop) el.scrollTop = top-8;
    else if(top+G.cw > el.scrollTop+el.clientHeight) el.scrollTop = top+G.cw-el.clientHeight+8;
  }
  filmScrollTo(i);
}
$('#v-grid').addEventListener('scroll', ()=>requestAnimationFrame(drawGrid), {passive:true});
new ResizeObserver(debounce(()=>{ layoutGrid(true); renderFilm(true); if(S.view==='survey') renderSurvey(); if(S.mod==='develop') layoutDev(); }, 60)).observe($('#stage'));

$('#grid-inner').addEventListener('mousedown', e=>{
  const c=e.target.closest('.cell'); if(!c || e.button!==0) return;
  const id=+c.dataset.id, a=e.target.closest('[data-a]')?.dataset.a, star=e.target.closest('[data-r]');
  if(a || star){
    e.preventDefault();
    if(!S.sel.has(id)) selectOnly(id);
    if(a==='flag'){ const p=S.byId.get(id); setAttr({flag:p.flag===1?0:1}, S.sel.has(id)?targets():[id]); }
    else if(a==='qc') toggleQuick();
    else if(a==='rotl') rotateSel(-90);
    else if(a==='rotr') rotateSel(90);
    else if(star){ const n=+star.dataset.r, p=S.byId.get(id); setAttr({rating: p.rating===n?0:n}, targets()); }
    return;
  }
  selectClick(id, e);
});
$('#grid-inner').addEventListener('dblclick', e=>{ const c=e.target.closest('.cell'); if(c && !e.target.closest('[data-a],[data-r]')){ selectOnly(+c.dataset.id); setView('loupe'); } });
$('#v-grid').addEventListener('mousedown', e=>{ if(e.target===$('#v-grid') || e.target===$('#grid-inner')) selectNone(); });
$('#grid-inner').addEventListener('dragstart', e=>{
  const c=e.target.closest('.cell'); if(!c) return;
  const id=+c.dataset.id; if(!S.sel.has(id)) selectOnly(id);
  e.dataTransfer.setData('text/x-pm-ids', JSON.stringify([...S.sel]));
  e.dataTransfer.effectAllowed='copy';
});

// ---------- filmstrip (virtualized, runs right-to-left) ----------
const FW = 78;
const F_ = {cells:new Map()};
function renderFilm(rebuild){
  const strip=$('#fs-strip'), inner=$('#fs-inner');
  const total = S.list.length*FW + 8;
  inner.style.width = Math.max(total, strip.clientWidth)+'px';
  if(rebuild){ F_.cells.forEach(c=>c.remove()); F_.cells.clear(); }
  drawFilm();
  renderFsFilter();
}
function drawFilm(){
  const strip=$('#fs-strip'), innerW=$('#fs-inner').offsetWidth;
  const fromRight = innerW - (strip.scrollLeft + strip.clientWidth);
  const a = Math.max(0, Math.floor(fromRight / FW) - 4), b = Math.min(S.list.length-1, Math.ceil((fromRight+strip.clientWidth)/FW) + 4);
  for(const [i,c] of F_.cells) if(i<a || i>b){ c.remove(); F_.cells.delete(i); }
  const frag=document.createDocumentFragment();
  for(let i=a;i<=b;i++){
    let c=F_.cells.get(i);
    const p=S.list[i];
    if(!c){ c=document.createElement('div'); c.className='fc'; c.style.right=(4+i*FW)+'px'; c.dataset.i=i; c.draggable=true;
      c.innerHTML=`<img loading="lazy" draggable="false" src="${thumbUrl(p.id)}" alt="" onerror="this.remove()"><span class="ov"></span>`; F_.cells.set(i,c); frag.appendChild(c); }
    c.dataset.id=p.id;
    c.classList.toggle('sel', S.sel.has(p.id)); c.classList.toggle('act', S.act===p.id); c.classList.toggle('rej', p.flag===-1);
    if(p.label){ c.dataset.l=p.label; c.style.setProperty('--lc', lcol(p.label)); } else delete c.dataset.l;
    c.querySelector('.ov').innerHTML = (p.flag ? `<svg class="fl ${p.flag<0?'rej':''}"><use href="#i-${p.flag<0?'reject':'flag'}"/></svg>`:'') + (p.rating?`<span class="st">${'★'.repeat(p.rating)}</span>`:'');
  }
  $('#fs-inner').appendChild(frag);
}
function filmScrollTo(i){
  const strip=$('#fs-strip'), innerW=$('#fs-inner').offsetWidth;
  const left = innerW - (4+i*FW) - FW;   // left edge of cell i in LTR coords
  if(left < strip.scrollLeft || left+FW > strip.scrollLeft+strip.clientWidth) strip.scrollLeft = left - strip.clientWidth/2 + FW/2;
}
$('#fs-strip').addEventListener('scroll', ()=>requestAnimationFrame(drawFilm), {passive:true});
$('#fs-strip').addEventListener('wheel', e=>{ if(Math.abs(e.deltaY)>Math.abs(e.deltaX)){ e.preventDefault(); $('#fs-strip').scrollLeft -= e.deltaY; } }, {passive:false});
$('#fs-inner').addEventListener('mousedown', e=>{ const c=e.target.closest('.fc'); if(c && e.button===0) selectClick(+c.dataset.id, e); });
$('#fs-inner').addEventListener('dblclick', e=>{ const c=e.target.closest('.fc'); if(c){ selectOnly(+c.dataset.id); if(S.mod==='library') setView('loupe'); } });
$('#fs-inner').addEventListener('dragstart', e=>{ const c=e.target.closest('.fc'); if(!c) return; const id=+c.dataset.id; if(!S.sel.has(id)) selectOnly(id);
  e.dataTransfer.setData('text/x-pm-ids', JSON.stringify([...S.sel])); });
function renderPath(){
  const n=S.list.length, s=S.sel.size, p=actPhoto();
  $('#fs-path').innerHTML = `<b>${esc(S.src.name)}</b> : ${num(n)} תמונות${S.base.length!==n?` (מתוך ${num(S.base.length)})`:''}${s?` / ${num(s)} נבחרו`:''}${p?` / <b dir="ltr">${esc(p.filename)}</b>`:''}`;
}
$('#fs-grid').onclick = ()=>{ if(S.mod!=='library') setModule('library'); setView('grid'); };
$('#fs-back').onclick = ()=>{ if(S.histPos>0){ S.histPos--; setSource(S.hist[S.histPos], {push:false}); } };
$('#fs-fwd').onclick = ()=>{ if(S.histPos<S.hist.length-1){ S.histPos++; setSource(S.hist[S.histPos], {push:false}); } };

// quick filter in the filmstrip header (shares state with the Library Filter's attribute tab)
function attrControls(small){
  const F=S.F;
  const flag = (k, ic, t) => `<button class="tg ${F.flags.has(k)?'on':''}" data-ff="${k}" title="${t}">${ic}</button>`;
  const stars = `<span class="fstars" data-fr>${[1,2,3,4,5].map(n=>`<b data-n="${n}" class="${n<=F.rating?'on':''}">★</b>`).join('')}</span>`;
  const labs = LABELS.map(([k,n])=>`<button class="tg lab ${F.labels.has(k)?'on':''}" data-fl="${k}" title="${n}"><span class="sw" style="background:${lcol(k)}"></span></button>`).join('')
    + `<button class="tg lab ${F.labels.has('none')?'on':''}" data-fl="none" title="ללא תווית"><span class="sw" style="background:#555"></span></button>`;
  const flags = flag('pick', I('flag'), 'נבחרו') + flag('none', `<svg class="ic"><use href="#i-flag"/></svg>`.replace('ic"','ic" style="opacity:.45"'), 'ללא דגל') + flag('rej', I('reject'), 'נדחו');
  if(small) return `<span>סינון:</span>${flags}<span class="tb-sep"></span>
    <select class="op" data-fop><option ${F.rop==='>='?'selected':''} value=">=">≥</option><option ${F.rop==='<='?'selected':''} value="<=">≤</option><option ${F.rop==='='?'selected':''} value="=">=</option></select>${stars}<span class="tb-sep"></span>${labs}
    <button class="tg ${F.on?'':'on'}" data-foff title="הפעל/השבת מסננים (Ctrl+L)">${F.on?'מופעל':'כבוי'}</button>`;
  return `<div class="attr-grp"><span>דגל</span>${flags}</div>
    <div class="attr-grp"><span>דירוג</span><select class="op" data-fop><option ${F.rop==='>='?'selected':''} value=">=">≥</option><option ${F.rop==='<='?'selected':''} value="<=">≤</option><option ${F.rop==='='?'selected':''} value="=">=</option></select>${stars}</div>
    <div class="attr-grp"><span>צבע</span>${labs}</div>
    <div class="attr-grp"><span>סוג</span>
      <button class="tg ${F.kinds.has('photo')?'on':''}" data-fk="photo" title="תמונות">${I('photos')}</button>
      <button class="tg ${F.kinds.has('video')?'on':''}" data-fk="video" title="וידאו">${I('play')}</button>
      <button class="tg ${F.kinds.has('edited')?'on':''}" data-fk="edited" title="ערוכות">${I('dev')}</button></div>`;
}
function renderFsFilter(){ $('#fs-filter').innerHTML = attrControls(true); }
function bindAttrControls(root){
  root.addEventListener('click', e=>{
    const t=e.target.closest('[data-ff],[data-fl],[data-fk],[data-n],[data-foff]'); if(!t) return;
    const F=S.F, tog=(set,k)=>set.has(k)?set.delete(k):set.add(k);
    if(t.dataset.ff) tog(F.flags, t.dataset.ff);
    else if(t.dataset.fl) tog(F.labels, t.dataset.fl);
    else if(t.dataset.fk) tog(F.kinds, t.dataset.fk);
    else if(t.dataset.n){ const n=+t.dataset.n; F.rating = F.rating===n ? 0 : n; }
    else if('foff' in t.dataset){ F.on=!F.on; }
    applyFilter();
  });
  root.addEventListener('change', e=>{ if('fop' in e.target.dataset){ S.F.rop=e.target.value; applyFilter(); } });
}
bindAttrControls($('#fs-filter')); bindAttrControls($('#fb-attr'));
function clearFilters(){
  const F=S.F; const hadQ = !!F.q;
  F.q=''; F.flags.clear(); F.labels.clear(); F.kinds.clear(); F.rating=0; F.on=true; Object.values(F.meta).forEach(s=>s.clear());
  $('#ft-q').value='';
  if(hadQ) fetchSource(); else applyFilter();
}

// ---------- library filter bar ----------
$('#fb-tabs').addEventListener('click', e=>{
  const a=e.target.closest('[data-fb]'); if(!a) return;
  S.fb = a.dataset.fb;
  if(S.fb==='none') clearFilters();
  renderFilterBar();
  if(S.fb==='text') $('#ft-q').focus();
});
function renderFilterBar(){
  $$('#fb-tabs a').forEach(a=>a.classList.toggle('on', a.dataset.fb===S.fb));
  $('#fb-text').classList.toggle('hidden', S.fb!=='text');
  $('#fb-attr').classList.toggle('hidden', S.fb!=='attr');
  $('#fb-meta').classList.toggle('hidden', S.fb!=='meta');
  if(S.fb==='attr') $('#fb-attr').innerHTML = attrControls(false);
  if(S.fb==='meta') renderMetaBrowser();
  $('#fb-state').innerHTML = filterActive() ? (S.F.on ? '<b>מסנן פעיל</b>' : 'המסנן כבוי') : '';
}
$('#ft-q').addEventListener('input', debounce(()=>{ S.F.q=$('#ft-q').value.trim(); if(S.F.qf!=='name') fetchSource(); else applyFilter(); }, 300));
$('#ft-field').onchange = ()=>{ S.F.qf=$('#ft-field').value; $('#ft-q').placeholder = S.F.qf==='smart' ? 'kids playing on the beach' : 'חיפוש'; if(S.F.q) fetchSource(); };
function renderMetaBrowser(){
  const base = S.base;
  $('#fb-meta').innerHTML = META_COLS.map(([k,title,fn], ci)=>{
    const counts = new Map();
    for(const p of base) if(passMeta(p, ci)){ const v=fn(p); counts.set(v, (counts.get(v)||0)+1); }
    const vals = [...counts.entries()].sort((a,b)=> k==='year'||k==='month' ? b[0].localeCompare(a[0]) : b[1]-a[1]);
    const set=S.F.meta[k];
    const label = v => k==='month' && v!=='ללא' ? new Date(2000, +v-1, 1).toLocaleDateString('he-IL',{month:'long'}) : v;
    return `<div class="mcol"><h4>${title}</h4><div class="mlist">
      <div class="row ${set.size?'':'on'}" data-mk="${k}" data-mv=""><span class="nm">הכול (${vals.length})</span><span class="n">${num([...counts.values()].reduce((a,b)=>a+b,0))}</span></div>
      ${vals.map(([v,n])=>`<div class="row ${set.has(v)?'on':''}" data-mk="${k}" data-mv="${esc(v)}"><span class="nm">${esc(label(v))}</span><span class="n">${num(n)}</span></div>`).join('')}
    </div></div>`;
  }).join('');
}
$('#fb-meta').addEventListener('click', e=>{
  const r=e.target.closest('[data-mk]'); if(!r) return;
  const set=S.F.meta[r.dataset.mk], v=r.dataset.mv;
  if(!v) set.clear();
  else if(e.ctrlKey||e.metaKey){ set.has(v)?set.delete(v):set.add(v); }
  else { set.clear(); set.add(v); }
  // selecting in one column resets the columns after it (they cascade)
  const ci = META_COLS.findIndex(c=>c[0]===r.dataset.mk);
  META_COLS.slice(ci+1).forEach(([k])=>S.F.meta[k].clear());
  applyFilter();
});

// ---------- left panel: catalog / folders / collections ----------
function row(key, icon, name, n, extra='', cls=''){
  return `<div class="row ${cls}" data-src="${esc(key)}">${icon}<span class="nm">${esc(name)}</span>${extra}${n!=null?`<span class="n">${num(n)}</span>`:''}</div>`;
}
function renderCatalog(){
  const st=S.status; if(!st) return;
  const q=S.all.filter(p=>p.quick).length;
  const prev = st.last_import ? S.all.filter(p=>(p.imported_at||0)>=st.last_import).length : 0;
  $('#p-catalog').innerHTML =
    row('all', I('photos'), 'כל התמונות', S.all.length) +
    row('quick', I('coll'), 'אוסף מהיר +', q) +
    (st.last_import ? row('prev', I('import'), 'ייבוא קודם', prev) : '') +
    row('trash', I('trash'), 'אשפה', st.counts.trashed);
  markSourceRows();
}
function renderFolders(){
  const f=S.folders;
  $('#p-folders').innerHTML = `<div class="vol" title="${esc(f.root)}">${I('drive')}<span>ספרייה</span><span class="path">${esc(f.root)}</span></div>` +
    (f.folders.map(x=>row('folder:'+x.name, I('folder'), x.name || '(שורש)', x.n, '', 'ind')).join('') || '<div class="hint">אין תיקיות עדיין</div>');
  markSourceRows();
}
const OPEN_SETS = new Set(pref.get('openSets', ['smart','album']));
function renderColls(){
  const al=S.albums;
  const set = (key, title, items) => {
    const open=OPEN_SETS.has(key);
    return `<div class="row set" data-set="${key}"><span class="tw">${open?'▼':'◀'}</span>${I('set')}<span class="nm">${title}</span></div>` + (open ? items : '');
  };
  const coll = a => row('album:'+a.id, I('coll'), a.name, a.n, `<button class="x" data-del="${a.id}" title="מחק אוסף">${I('close')}</button>`, 'ind');
  const smart = SMART.map(([k,n,f])=>row('smart:'+k, I('smart'), n, S.all.filter(f).length, '', 'ind')).join('');
  const people = S.people.map(p=>row('person:'+p.id, I('people'), p.name, (p.face_photos||0)+(p.tag_photos||0), '', 'ind')).join('');
  $('#p-colls').innerHTML =
    set('smart', 'אוספים חכמים', smart) +
    set('album', 'אוספים', al.filter(a=>a.kind==='album').map(coll).join('') || '<div class="hint">גררו תמונות לכאן אחרי יצירת אוסף</div>') +
    (al.some(a=>a.kind==='people-share') ? set('shared', 'אלבומים משותפים', al.filter(a=>a.kind==='people-share').map(coll).join('')) : '') +
    (al.some(a=>a.kind==='year') ? set('year', 'לפי שנה (Google)', al.filter(a=>a.kind==='year').map(coll).join('')) : '') +
    (S.people.length ? set('people', 'אנשים', people) : '');
  markSourceRows();
}
function markSourceRows(){ const k=srcKey(S.src); $$('#left [data-src]').forEach(r=>r.classList.toggle('on', r.dataset.src===k)); }
function srcFromKey(key){
  const [kind, id] = key.split(/:(.*)/s);
  const name = {all:'כל התמונות', quick:'אוסף מהיר', prev:'ייבוא קודם', trash:'אשפה'}[kind]
    || (kind==='folder' ? (id||'(שורש)') : kind==='smart' ? SMART.find(s=>s[0]===id)[1]
      : kind==='album' ? S.albums.find(a=>a.id==id)?.name : kind==='person' ? S.people.find(p=>p.id==id)?.name : '');
  return {kind, id: id===undefined ? null : (['album','person','tag','cluster'].includes(kind) ? +id : id), name};
}
$('#left').addEventListener('click', async e=>{
  const del=e.target.closest('[data-del]');
  if(del){ e.stopPropagation(); const a=S.albums.find(x=>x.id==del.dataset.del);
    if(!await confirmBox(`למחוק את האוסף „${esc(a.name)}"?`, 'התמונות עצמן יישארו בקטלוג.', 'מחק')) return;
    await send('DELETE', '/api/album/'+a.id); if(S.src.kind==='album' && S.src.id===a.id) setSource(srcFromKey('all')); loadSide(); return; }
  const st=e.target.closest('[data-set]');
  if(st){ const k=st.dataset.set; OPEN_SETS.has(k)?OPEN_SETS.delete(k):OPEN_SETS.add(k); pref.set('openSets',[...OPEN_SETS]); renderColls(); return; }
  const r=e.target.closest('[data-src]'); if(r){ if(S.mod!=='library') setModule('library'); setSource(srcFromKey(r.dataset.src)); }
});
$('#left').addEventListener('dblclick', async e=>{
  const r=e.target.closest('[data-src^="album:"]'); if(!r) return;
  const a=S.albums.find(x=>'album:'+x.id===r.dataset.src);
  const n=await promptBox('שינוי שם אוסף', a.name); if(!n || n===a.name) return;
  await send('POST', `/api/album/${a.id}/rename`, {name:n}); await loadSide(); if(S.src.kind==='album'&&S.src.id===a.id){ S.src.name=n; renderPath(); }
});
// drag photos onto a collection / the Quick Collection
$('#left').addEventListener('dragover', e=>{ const r=e.target.closest('[data-src^="album:"],[data-src="quick"]'); if(r && e.dataTransfer.types.includes('text/x-pm-ids')){ e.preventDefault(); $$('#left .drop').forEach(x=>x!==r&&x.classList.remove('drop')); r.classList.add('drop'); } });
$('#left').addEventListener('dragleave', e=>{ const r=e.target.closest('.drop'); if(r && !r.contains(e.relatedTarget)) r.classList.remove('drop'); });
$('#left').addEventListener('drop', async e=>{
  const r=e.target.closest('[data-src^="album:"],[data-src="quick"]'); $$('#left .drop').forEach(x=>x.classList.remove('drop')); if(!r) return;
  e.preventDefault(); const ids=JSON.parse(e.dataTransfer.getData('text/x-pm-ids')||'[]'); if(!ids.length) return;
  if(r.dataset.src==='quick'){ await setAttr({quick:1}, ids); toast(`נוספו ${num(ids.length)} לאוסף המהיר`); return; }
  const aid=+r.dataset.src.split(':')[1];
  await send('POST', `/api/album/${aid}/add`, {ids}); toast(`נוספו ${num(ids.length)} תמונות ל„${esc(S.albums.find(a=>a.id===aid)?.name)}"`); loadSide();
});
async function newCollection(){
  const ids=[...S.sel];
  const n=await promptBox('צור אוסף', '', ids.length?`<label class="check" style="padding:0"><input type="checkbox" id="nc-sel" checked> כלול את התמונות שנבחרו (${num(ids.length)})</label>`:'');
  if(!n) return;
  const inc = ids.length && PB_CHECKED;
  const r = await send('POST', '/api/albums', {name:n, ids: inc ? ids : []});
  OPEN_SETS.add('album'); setSource({kind:'album', id:r.id, name:n}); loadSide();
}
$('#new-coll').onclick = e=>{ e.stopPropagation(); newCollection(); };

// collapse panels (click header)
$$('.pnl>h3').forEach(h=>h.addEventListener('click', e=>{
  if(e.target.closest('button,a')) return;
  const p=h.parentElement; p.classList.toggle('shut');
  const shut=pref.get('shut',{}); shut[p.dataset.p]=p.classList.contains('shut'); pref.set('shut', shut);
}));
(()=>{ const shut=pref.get('shut',{}); $$('.pnl').forEach(p=>p.classList.toggle('shut', !!shut[p.dataset.p])); })();

// ---------- navigator ----------
let navZoom='fit';
function updateNavigator(){
  const p=actPhoto(), img=$('#nav-img');
  if(!p){ img.removeAttribute('src'); $('#nav-rect').classList.add('hidden'); return; }
  const u = thumbUrl(p.id);
  if(img.getAttribute('src')!==u) img.src=u;
  updateNavRect();
}
function updateNavRect(){
  const m=$('#loupe-media'), r=$('#nav-rect'), img=$('#nav-img');
  if(S.view!=='loupe' || !m.classList.contains('zoom') || !img.naturalWidth){ r.classList.add('hidden'); return; }
  const full=m.querySelector('img'); if(!full) return;
  const W=full.naturalWidth, H=full.naturalHeight, nb=img.getBoundingClientRect(), pb=$('#navigator').getBoundingClientRect();
  const sx=nb.width/W, sy=nb.height/H;
  const x = m.scrollLeft < 0 ? W - m.clientWidth + m.scrollLeft : m.scrollLeft;  // RTL scroll origin
  r.classList.remove('hidden');
  Object.assign(r.style, {left:(nb.left-pb.left + x*sx)+'px', top:(nb.top-pb.top + m.scrollTop*sy)+'px',
    width:Math.min(nb.width, m.clientWidth*sx)+'px', height:Math.min(nb.height, m.clientHeight*sy)+'px'});
}
$('#nav-img').addEventListener('load', updateNavRect);
$('#navigator').addEventListener('click', e=>{
  if(S.mod!=='library') return;
  const img=$('#nav-img'); if(!img.naturalWidth) return;
  const b=img.getBoundingClientRect(); if(e.clientX<b.left||e.clientX>b.right||e.clientY<b.top||e.clientY>b.bottom) return;
  if(S.view!=='loupe') setView('loupe');
  zoomLoupe(true, (e.clientX-b.left)/b.width, (e.clientY-b.top)/b.height);
});
$$('.nav-modes a').forEach(a=>a.onclick=e=>{ e.stopPropagation(); if(S.view!=='loupe' && S.mod==='library') setView('loupe'); zoomLoupe(a.dataset.zoom==='1', .5, .5); });

// ---------- views: grid / loupe / compare / survey / people ----------
function setView(v){
  if(S.mod!=='library') setModule('library');
  if(v==='loupe' && S.act==null && S.list.length) selectOnly(S.list[0].id);
  if(v==='compare' && S.act==null && S.list.length) selectOnly(S.list[0].id);
  S.prevView = S.view==='people'?S.prevView:S.view; S.view=v;
  ['grid','loupe','compare','survey','people'].forEach(k=>$('#v-'+k).classList.toggle('hidden', k!==v));
  $('#v-empty').classList.add('hidden');
  if(v!=='loupe'){ $('#loupe-media').innerHTML=''; }
  if(v==='grid'){ layoutGrid(true); scrollToAct(); $('#v-grid').focus({preventScroll:true}); if(!S.list.length){ $('#v-empty').classList.remove('hidden'); renderEmpty(); } }
  if(v==='loupe') renderLoupe();
  if(v==='compare') renderCompare();
  if(v==='survey') renderSurvey();
  if(v==='people') renderPeople();
  renderToolbar(); renderRight(); updateNavigator();
}
function renderLoupe(){
  const p=actPhoto(), m=$('#loupe-media');
  if(!p){ m.innerHTML=''; $('#loupe-info').innerHTML=''; return; }
  if(m.dataset.id!=String(p.id) || m.dataset.v!=String(VER[p.id]||'')){
    m.dataset.id=p.id; m.dataset.v=VER[p.id]||''; m.classList.remove('zoom');
    m.innerHTML = p.is_video ? `<video src="${mediaUrl(p.id)}" controls autoplay></video>` : `<img src="${mediaUrl(p.id)}" alt="" draggable="false">`;
    const img=m.querySelector('img'); if(img) img.onload=()=>drawHisto(img);
  }
  const info=$('#loupe-info');
  info.classList.toggle('hidden', !S.loupeInfo);
  info.innerHTML = `<b>${esc(p.filename)}</b><span>${fdate(p.taken_at)}</span><br><span dir="ltr">${p.width&&p.height?p.width+' × '+p.height:''}  ${fsize(p.bytes)}</span>${p.flag===-1?'<br><span>נדחתה</span>':''}`;
  filmScrollTo(S.idx.get(p.id)??0);
}
function zoomLoupe(on, fx=.5, fy=.5){
  const m=$('#loupe-media'), img=m.querySelector('img'); if(!img) return;
  on = on ?? !m.classList.contains('zoom');
  m.classList.toggle('zoom', on);
  $$('.nav-modes a').forEach(a=>a.classList.toggle('on', (a.dataset.zoom==='1')===on));
  if(on){ requestAnimationFrame(()=>{ const W=img.naturalWidth, H=img.naturalHeight;
    const x = W*fx - m.clientWidth/2, y = H*fy - m.clientHeight/2;
    m.scrollTop = y; m.scrollLeft = getComputedStyle(m).direction==='rtl' ? -(W - m.clientWidth - x) : x; updateNavRect(); }); }
  else updateNavRect();
}
$('#loupe-media').addEventListener('click', e=>{
  const img=e.target.closest('img'); if(!img || $('#loupe-media')._dragged) return;
  const b=img.getBoundingClientRect();
  zoomLoupe(undefined, (e.clientX-b.left)/b.width, (e.clientY-b.top)/b.height);
});
$('#loupe-media').addEventListener('scroll', updateNavRect, {passive:true});
$('#loupe-media').addEventListener('mousedown', e=>{   // drag to pan when zoomed
  const m=$('#loupe-media'); m._dragged=false; if(!m.classList.contains('zoom')) return;
  const sx=e.clientX, sy=e.clientY, l=m.scrollLeft, t=m.scrollTop; e.preventDefault();
  const mv=ev=>{ if(Math.abs(ev.clientX-sx)+Math.abs(ev.clientY-sy)>3) m._dragged=true; m.scrollLeft=l-(ev.clientX-sx); m.scrollTop=t-(ev.clientY-sy); };
  const up=()=>{ removeEventListener('mousemove',mv); removeEventListener('mouseup',up); setTimeout(()=>m._dragged=false); };
  addEventListener('mousemove',mv); addEventListener('mouseup',up);
});
$('#v-loupe').addEventListener('dblclick', e=>{ if(!$('#loupe-media').classList.contains('zoom')) setView('grid'); });

let CMP_CAND=null;
function renderCompare(){
  const a=actPhoto(); if(!a){ $('#v-compare').innerHTML=''; return; }
  let b = CMP_CAND && CMP_CAND!==a.id && S.idx.has(CMP_CAND) ? CMP_CAND : ([...S.sel].find(id=>id!==a.id) ?? S.list[(S.idx.get(a.id)+1)%S.list.length]?.id);
  CMP_CAND=b;
  const pane=(p,lab,cls)=>p?`<div class="cmp ${cls}" data-id="${p.id}"><span class="lab">${lab} · <bdi>${esc(p.filename)}</bdi> ${p.rating?'★'.repeat(p.rating):''}</span><img src="${mediaUrl(p.id)}" alt=""></div>`:'<div class="cmp"></div>';
  $('#v-compare').innerHTML = pane(a,'בחירה','sel') + pane(S.byId.get(b)||S.base.find(x=>x.id===b),'מועמד','');
}
$('#v-compare').addEventListener('click', e=>{ const c=e.target.closest('.cmp:not(.sel)'); if(c){ const a=S.act; selectOnly(+c.dataset.id); CMP_CAND=a; renderCompare(); } });
function compareStep(d){ const i=S.idx.get(CMP_CAND??S.act); if(i==null) return; let j=i; do{ j=(j+d+S.list.length)%S.list.length; }while(S.list[j].id===S.act && S.list.length>1); CMP_CAND=S.list[j].id; renderCompare(); }
function compareSwap(){ const a=S.act, b=CMP_CAND; if(b==null) return; selectOnly(b); CMP_CAND=a; renderCompare(); }

function renderSurvey(){
  const el=$('#v-survey');
  let ids=[...S.sel].sort((a,b)=>S.idx.get(a)-S.idx.get(b)); if(!ids.length && S.act!=null) ids=[S.act];
  const n=ids.length; if(!n){ el.innerHTML=''; return; }
  const W=el.clientWidth-28, H=el.clientHeight-28;
  let best=[1,n,0];
  for(let cols=1; cols<=n; cols++){ const rows=Math.ceil(n/cols), s=Math.min((W-(cols-1)*10)/cols, (H-(rows-1)*10)/rows); if(s>best[2]) best=[cols,rows,s]; }
  const [cols, rows] = best, bw=Math.floor((W-(cols-1)*10)/cols), bh=Math.floor((H-(rows-1)*10)/rows);
  el.innerHTML = ids.map(id=>{ const p=S.byId.get(id)||S.base.find(x=>x.id===id);
    return `<div class="sv ${id===S.act?'act':''}" data-id="${id}" style="width:${bw}px;height:${bh}px"><img src="${bw>300?mediaUrl(id):thumbUrl(id)}" alt=""><button class="x" data-x title="הסר מהסקירה">${I('close')}</button></div>`; }).join('');
}
$('#v-survey').addEventListener('click', e=>{ const c=e.target.closest('.sv'); if(!c) return; const id=+c.dataset.id;
  if(e.target.closest('[data-x]')){ S.sel.delete(id); if(S.act===id) S.act=[...S.sel][0]??null; onSelChange(); return; }
  S.act=id; onSelChange(); });

async function renderPeople(){
  const el=$('#v-people'); el.innerHTML='<div class="hint">טוען…</div>';
  const [people, clusters] = await Promise.all([api('/api/people'), api('/api/clusters')]);
  S.people=people;
  const face = src => src ? `<img loading="lazy" src="${src}" alt="">` : I('people');
  el.innerHTML = `<h2>אנשים עם שם <span>${num(people.length)}</span></h2>
    <div class="pgrid">${people.map(p=>`<div class="pc" data-person="${p.id}"><div class="face">${face(p.cover_face?'/face/'+p.cover_face:p.cover_photo?thumbUrl(p.cover_photo):'')}</div>
      <div class="nm" title="לחיצה כפולה לשינוי שם">${esc(p.name)}</div><div class="ct">${num((p.face_photos||0)+(p.tag_photos||0))}</div></div>`).join('') || '<div class="hint">עוד אין אנשים עם שם.</div>'}</div>
    <h2>אנשים ללא שם <span>${num(clusters.length)}</span></h2>
    ${clusters.length ? `<div class="pgrid">${clusters.map(c=>`<div class="pc" data-cluster="${c.id}"><div class="face">${face(c.cover_face?'/face/'+c.cover_face:'')}</div>
      <input placeholder="?" data-name-cluster="${c.id}" title="הקלידו שם ולחצו Enter"><div class="ct">${num(c.n)}</div></div>`).join('')}</div>`
      : `<div class="hint">${S.status?.counts.faces ? 'כל קבוצות הפנים קיבלו שם.' : 'עוד לא הורץ זיהוי פנים. ספרייה ← זיהוי פנים.'}</div>`}`;
}
$('#v-people').addEventListener('click', e=>{
  const face=e.target.closest('.face'); if(!face) return;
  const pc=face.closest('.pc');
  if(pc.dataset.person){ const p=S.people.find(x=>x.id==pc.dataset.person); setSource({kind:'person', id:p.id, name:p.name}); setView('grid'); }
  else setSource({kind:'cluster', id:+pc.dataset.cluster, name:'אדם ללא שם'}).then(()=>setView('grid'));
});
$('#v-people').addEventListener('dblclick', async e=>{
  const nm=e.target.closest('.nm'); if(!nm) return; const id=+nm.closest('.pc').dataset.person, p=S.people.find(x=>x.id===id);
  const n=await promptBox('שינוי שם', p.name); if(!n) return;
  await send('POST', `/api/person/${id}/rename`, {name:n}); toast('השם עודכן'); await loadSide(); renderPeople();
});
$('#v-people').addEventListener('keydown', async e=>{
  const inp=e.target.closest('[data-name-cluster]'); if(!inp || e.key!=='Enter') return;
  const n=inp.value.trim(); if(!n) return;
  await send('POST', `/api/cluster/${inp.dataset.nameCluster}/name`, {name:n}); toast(`נקרא „${esc(n)}"`); await loadSide(); renderPeople();
});

// ---------- toolbar ----------
function tbViews(){
  const b=(v,ic,t)=>`<button class="tb-btn ${S.view===v?'on':''}" data-view="${v}" title="${t}">${I(ic)}</button>`;
  return `<div class="tb-grp">${b('grid','grid','תצוגת רשת (G)')}${b('loupe','loupe','זכוכית מגדלת (E)')}${b('compare','compare','השוואה (C)')}${b('survey','survey','סקירה (N)')}${b('people','face','אנשים (O)')}</div>`;
}
function tbAttrs(){
  const p=actPhoto(), r=p?.rating||0;
  return `<div class="tb-grp"><button class="tb-btn tb-flag pick ${p?.flag===1?'on':''}" data-t="pick" title="סמן כנבחרת (P)">${I('flag')}</button>
    <button class="tb-btn tb-flag rej ${p?.flag===-1?'on':''}" data-t="rej" title="סמן כנדחית (X)">${I('reject')}</button></div>
    <span class="tb-sep"></span><span class="tb-stars">${[1,2,3,4,5].map(n=>`<b data-star="${n}" class="${n<=r?'on':''}" title="${n} (מקש ${n})">★</b>`).join('')}</span>
    <span class="tb-sep"></span><span class="tb-labs">${LABELS.map(([k,n,key])=>`<button data-lab="${k}" class="${p?.label===k?'on':''}" style="background:${lcol(k)}" title="${n}${key?` (${key})`:''}"></button>`).join('')}</span>
    <span class="tb-sep"></span><div class="tb-grp"><button class="tb-btn" data-t="rotl" title="סובב שמאלה (Ctrl+[)">${I('rotl')}</button><button class="tb-btn" data-t="rotr" title="סובב ימינה (Ctrl+])">${I('rotr')}</button></div>`;
}
function renderToolbar(){
  const tb=$('#toolbar');
  if(S.mod==='develop'){ const p=actPhoto();
    tb.innerHTML = `<button class="tb-btn ${DEV.before?'on':''}" data-t="before" title="לפני/אחרי (\\)">לפני / אחרי</button><span class="tb-sep"></span>
      <button class="tb-btn ${DEV.crop?'on':''}" data-t="crop" title="חיתוך (R)">${I('crop')}</button><span class="spacer"></span>
      <span class="tb-info">${p?`<bdi>${esc(p.filename)}</bdi>`:''}${DEV.dirty?' · שינויים שלא הוחלו':''}</span>`; return; }
  let h = tbViews() + '<span class="tb-sep"></span>';
  if(S.view==='grid') h += `<div class="tb-sort"><span class="tb-lbl">מיון:</span><button class="tb-btn" data-t="asc" title="${S.asc?'סדר עולה':'סדר יורד'}" style="${S.asc?'':'transform:scaleY(-1)'}">${I('sort')}</button>
      <select data-t="sort">${Object.entries(SORTS).map(([k,[n]])=>`<option value="${k}" ${k===S.sort?'selected':''}>${n}</option>`).join('')}</select></div><span class="tb-sep"></span>` + tbAttrs() +
      `<label class="tb-size"><span>תמונות ממוזערות</span><input type="range" data-t="size" min="110" max="420" step="10" value="${S.cellsz}"></label>`;
  else if(S.view==='loupe') h += tbAttrs() + `<span class="spacer"></span><button class="tb-btn ${S.loupeInfo?'on':''}" data-t="info" title="מידע (I)">מידע</button>`;
  else if(S.view==='compare') h += tbAttrs() + `<span class="spacer"></span><button class="tb-btn" data-t="swap" title="החלף בחירה ומועמד">החלף</button><button class="tb-btn" data-t="done" title="סיום (Esc)">סיום</button>`;
  else if(S.view==='survey') h += tbAttrs() + `<span class="spacer"></span><span class="tb-info">${num(S.sel.size)} תמונות בסקירה</span>`;
  else h += `<span class="spacer"></span><span class="tb-info">הקלידו שם מתחת לפנים כדי לתת להן שם</span>`;
  tb.innerHTML = h;
}
$('#toolbar').addEventListener('click', e=>{
  const v=e.target.closest('[data-view]'); if(v){ setView(v.dataset.view); return; }
  const s=e.target.closest('[data-star]'); if(s){ const n=+s.dataset.star, p=actPhoto(); setRating(p&&p.rating===n?0:n); return; }
  const l=e.target.closest('[data-lab]'); if(l){ setLabel(l.dataset.lab); return; }
  const t=e.target.closest('[data-t]')?.dataset.t; if(!t) return;
  if(t==='pick'){ const p=actPhoto(); setFlag(p?.flag===1?0:1); }
  else if(t==='rej'){ const p=actPhoto(); setFlag(p?.flag===-1?0:-1); }
  else if(t==='rotl') rotateSel(-90); else if(t==='rotr') rotateSel(90);
  else if(t==='asc'){ S.asc=!S.asc; pref.set('asc',S.asc); applyFilter(); }
  else if(t==='info'){ S.loupeInfo=!S.loupeInfo; renderLoupe(); renderToolbar(); }
  else if(t==='swap') compareSwap(); else if(t==='done') setView('loupe');
  else if(t==='before') devBefore(); else if(t==='crop') devCropToggle();
});
$('#toolbar').addEventListener('change', e=>{ if(e.target.dataset.t==='sort'){ S.sort=e.target.value; pref.set('sort',S.sort); applyFilter(); } });
$('#toolbar').addEventListener('input', e=>{ if(e.target.dataset.t==='size'){ S.cellsz=+e.target.value; pref.set('cellsz',S.cellsz); layoutGrid(true); scrollToAct(); } });

// ---------- right panel: histogram / keywording / keyword list / metadata ----------
const HCACHE = new Map();
function drawHisto(img){
  const cv=$('#histo'), ctx=cv.getContext('2d'); ctx.clearRect(0,0,cv.width,cv.height);
  const p=actPhoto();
  $('#histo-meta').innerHTML = p ? `<span>${p.width||'?'} × ${p.height||'?'}</span><span>${fsize(p.bytes)}</span><span>${esc(ext(p))}</span>` : '';
  if(!img || !img.naturalWidth) return;
  const key=img.src; let H=HCACHE.get(key);
  if(!H){
    const w=200, h=Math.max(1, Math.round(w*img.naturalHeight/img.naturalWidth));
    const off=document.createElement('canvas'); off.width=w; off.height=h;
    const o=off.getContext('2d'); o.drawImage(img,0,0,w,h);
    let d; try{ d=o.getImageData(0,0,w,h).data; }catch{ return; }
    H=[new Uint32Array(256),new Uint32Array(256),new Uint32Array(256)];
    for(let k=0;k<d.length;k+=4){ H[0][d[k]]++; H[1][d[k+1]]++; H[2][d[k+2]]++; }
    HCACHE.set(key, H); if(HCACHE.size>200) HCACHE.delete(HCACHE.keys().next().value);
  }
  let max=1; for(const c of H) for(let k=2;k<254;k++) max=Math.max(max,c[k]);
  // grid lines like Lightroom's (quarters)
  ctx.strokeStyle='rgba(255,255,255,.06)'; for(let q=1;q<4;q++){ ctx.beginPath(); ctx.moveTo(q*cv.width/4,0); ctx.lineTo(q*cv.width/4,cv.height); ctx.stroke(); }
  ctx.globalCompositeOperation='lighter';
  ['rgba(220,60,60,.8)','rgba(60,190,80,.8)','rgba(60,110,235,.8)'].forEach((col,ci)=>{
    ctx.fillStyle=col; ctx.beginPath(); ctx.moveTo(0,cv.height);
    for(let k=0;k<256;k++) ctx.lineTo(k*cv.width/255, cv.height - Math.min(1,H[ci][k]/max)*(cv.height-6));
    ctx.lineTo(cv.width,cv.height); ctx.closePath(); ctx.fill();
  });
  ctx.globalCompositeOperation='source-over';
}
function histoFromThumb(){
  const p=actPhoto(); if(!p){ drawHisto(null); return; }
  const im=new Image(); im.onload=()=>{ if(actPhoto()?.id===p.id) drawHisto(im); }; im.onerror=()=>drawHisto(null); im.src=thumbUrl(p.id);
}

let DETAIL=null;   // full record of the active photo (albums, people, tags...)
const renderRight = debounce(async ()=>{
  if(S.mod==='develop') return;
  if(S.view!=='loupe') histoFromThumb();
  const ids=targets(), p=actPhoto();
  const [detail, kws, sug] = await Promise.all([
    p ? api('/api/photo/'+p.id) : null,
    ids.length ? send('POST','/api/keywords',{ids}) : [],
    p && ids.length===1 ? api(`/api/photo/${p.id}/suggest`).catch(()=>[]) : [],
  ]);
  if(p && actPhoto()?.id!==p.id) return;
  DETAIL=detail;
  renderKeywording(ids, kws, sug); renderKwList(kws, ids.length); renderMeta(ids, detail);
}, 90);

function renderKeywording(ids, kws, content=[]){
  const el=$('#p-kwing');
  if(!ids.length){ el.innerHTML='<div class="hint">בחרו תמונות כדי לתייג אותן.</div>'; return; }
  const txt = kws.map(k=>k.name + (k.n<ids.length?' *':'')).join(', ');
  const top = S.tags.slice(0,30).map(t=>t.name);
  const sug = [...new Set([...content, ...S.recentKw, ...top])].filter(n=>!kws.some(k=>k.name===n && k.n===ids.length)).slice(0,9);
  el.innerHTML = `<div class="lbl-sub">מילות מפתח${ids.length>1?` · ${num(ids.length)} תמונות (* = רק בחלק מהן)`:''}</div>
    <label class="kwbox"><textarea id="kw-text" spellcheck="false" placeholder="הקלידו מילות מפתח מופרדות בפסיקים">${esc(txt)}</textarea></label>
    <label class="kwadd"><input id="kw-add" placeholder="לחצו כאן כדי להוסיף מילות מפתח"></label>
    <div class="lbl-sub" style="padding-top:8px">הצעות למילות מפתח</div>
    <div class="kwsug">${sug.map(n=>`<a data-kwadd="${esc(n)}" title="${esc(n)}">${esc(n)}</a>`).join('')}</div>`;
  el._orig = kws; el._ids = ids;
}
async function commitKeywords(addNames, removeIds){
  const ids=$('#p-kwing')._ids || targets(); if(!ids.length) return;
  addNames = addNames.filter(Boolean);
  if(!addNames.length && !removeIds.length) return;
  await send('PATCH','/api/photos',{ids, add_tags:addNames, remove_tag_ids:removeIds});
  if(addNames.length){ S.recentKw=[...new Set([...addNames, ...S.recentKw])].slice(0,9); pref.set('recentKw', S.recentKw); }
  ids.forEach(id=>{ const p=S.byId.get(id); if(p) p.has_kw = 1; });   // refreshed properly by the reload below
  S.tags = await api('/api/tags');
  const fresh = await api('/api/photos?'+new URLSearchParams({limit:10000000, ...srcParams(S.src)}));
  fresh.forEach(r=>{ const p=S.byId.get(r.id); if(p) p.has_kw=r.has_kw; });
  refreshCells(); renderColls(); renderRight();
}
$('#p-kwing').addEventListener('change', e=>{
  if(e.target.id!=='kw-text') return;
  const orig=$('#p-kwing')._orig||[], n=($('#p-kwing')._ids||[]).length;
  const parts = e.target.value.split(/[,،\n]/).map(s=>s.trim()).filter(Boolean);
  const add=[], keep=new Set();
  for(const s of parts){ const partial=/\*$/.test(s); const name=s.replace(/\s*\*$/,'').trim(); keep.add(name); if(!partial) add.push(name); }
  const toAdd = add.filter(nm=>{ const o=orig.find(k=>k.name===nm); return !o || o.n<n; });
  const toRemove = orig.filter(k=>!keep.has(k.name)).map(k=>k.id);
  commitKeywords(toAdd, toRemove);
});
$('#p-kwing').addEventListener('keydown', e=>{
  if(e.target.id==='kw-text' && e.key==='Enter' && !e.shiftKey){ e.preventDefault(); e.target.blur(); }
  if(e.target.id==='kw-add' && e.key==='Enter'){ const v=e.target.value.split(',').map(s=>s.trim()).filter(Boolean); e.target.value=''; commitKeywords(v, []); }
});
$('#p-kwing').addEventListener('click', e=>{ const a=e.target.closest('[data-kwadd]'); if(a) commitKeywords([a.dataset.kwadd], []); });

let KWF='';
function renderKwList(selKws, nSel){
  const el=$('#p-kwlist'); if(!el) return;
  selKws = selKws || el._sel || []; nSel = nSel ?? el._n ?? 0; el._sel=selKws; el._n=nSel;
  const on = new Map(selKws.map(k=>[k.id,k.n]));
  const tags = S.tags.filter(t=>!KWF || t.name.toLowerCase().includes(KWF));
  el.innerHTML = `<div class="kwlist-filter"><input id="kw-filter" type="search" placeholder="סינון מילות מפתח" value="${esc(KWF)}"></div>
    <div class="kwrows">${tags.slice(0,800).map(t=>{ const c=on.get(t.id)||0;
      return `<div class="row" data-kw="${t.id}"><input type="checkbox" ${nSel&&c===nSel?'checked':''} ${nSel?'':'disabled'} data-part="${c&&c<nSel?1:0}" title="${nSel?'הוסף/הסר לתמונות שנבחרו':''}"><span class="nm">${esc(t.name)}</span>
        <button class="go" data-go="${t.id}" title="הצג תמונות עם מילת המפתח">${I('next')}</button><span class="n">${num(t.n)}</span></div>`; }).join('')
      || '<div class="hint">אין מילות מפתח עדיין. תייגו תמונות או הריצו תיוג חכם.</div>'}</div>`;
  $$('#p-kwlist [data-part="1"]').forEach(c=>c.indeterminate=true);
}
$('#p-kwlist').addEventListener('input', debounce(e=>{ if(e.target.id==='kw-filter'){ KWF=e.target.value.toLowerCase(); renderKwList(); $('#kw-filter').focus(); } }, 150));
$('#p-kwlist').addEventListener('click', e=>{
  const go=e.target.closest('[data-go]');
  if(go){ const t=S.tags.find(x=>x.id==go.dataset.go); setSource({kind:'tag', id:t.id, name:'מילת מפתח: '+t.name}); return; }
  const cb=e.target.closest('input[type=checkbox]');
  if(cb){ const t=S.tags.find(x=>x.id==cb.closest('[data-kw]').dataset.kw);
    if(cb.checked) commitKeywords([t.name], []); else commitKeywords([], [t.id]); }
});

function renderMeta(ids, d){
  const el=$('#p-meta');
  if(!ids.length || !d){ el.innerHTML='<div class="hint">לא נבחרה תמונה.</div>'; return; }
  const multi = ids.length>1;
  const sel = ids.map(id=>S.byId.get(id)).filter(Boolean);
  const same = k => sel.every(p=>(p[k]??null)===(sel[0][k]??null));
  const MIX = '<span class="mixed">&lt;מעורב&gt;</span>';
  const rating = !multi || same('rating') ? (d.rating||0) : -1;
  const label = !multi || same('label') ? (d.label||'') : '*';
  const local = d.taken_at ? new Date(d.taken_at*1000 - new Date().getTimezoneOffset()*60000).toISOString().slice(0,16) : '';
  const links = (arr, kind) => arr.length ? arr.map(a=>`<a data-src-link="${kind}:${a.id}">${esc(a.name)}</a>`).join(', ') : '—';
  el.innerHTML = `
    ${multi?`<div class="lbl-sub">${num(ids.length)} תמונות נבחרו — שינויים יחולו על כולן</div>`:''}
    <div class="kv"><span>שם קובץ</span>${multi?MIX:`<span dir="ltr" title="${esc(d.filename)}">${esc(d.filename)}</span>`}</div>
    <div class="kv"><span>תיקייה</span>${multi&&!same('folder')?MIX:`<a data-src-link="folder:${esc(sel[0]?.folder??'')}">${esc(sel[0]?.folder||'(שורש)')}</a>`}</div>
    <div class="kv"><span>דירוג</span><span class="stars-in" id="m-stars">${[1,2,3,4,5].map(n=>`<b data-mr="${n}" class="${rating>=n?'on':''}">★</b>`).join('')}${rating<0?' '+MIX:''}</span></div>
    <div class="kv"><span>תווית</span><select id="m-label"><option value="">ללא</option>${LABELS.map(([k,n])=>`<option value="${k}" ${label===k?'selected':''}>${n}</option>`).join('')}${label==='*'?'<option selected disabled>&lt;מעורב&gt;</option>':''}</select></div>
    <div class="meta-sub">תוכן</div>
    <div class="kv tall"><span>כיתוב</span><textarea id="m-desc" placeholder="${multi?'<מעורב>':''}">${multi?'':esc(d.description||'')}</textarea></div>
    <div class="meta-sub">צילום</div>
    <div class="kv"><span>זמן צילום</span>${multi?MIX:`<input id="m-date" type="datetime-local" value="${local}">`}</div>
    <div class="kv"><span>מידות</span>${multi?MIX:`<span dir="ltr">${d.width||'?'} × ${d.height||'?'}</span>`}</div>
    <div class="kv"><span>גודל קובץ</span>${multi?MIX:fsize(d.bytes)}</div>
    <div class="kv"><span>סוג</span>${multi&&!sel.every(p=>ext(p)===ext(sel[0]))?MIX:esc(ext(d))}${d.edited&&!multi?' · נערך':''}</div>
    <div class="meta-sub">מיקום</div>
    ${multi?`<div class="kv"><span>GPS</span>${MIX}</div>`:`
    <div class="kv"><span>קו רוחב</span><input id="m-lat" type="number" step="any" dir="ltr" value="${d.lat??''}"></div>
    <div class="kv"><span>קו אורך</span><input id="m-lng" type="number" step="any" dir="ltr" value="${d.lng??''}"></div>
    ${d.lat!=null?`<div class="kv"><span></span><a href="https://www.google.com/maps?q=${d.lat},${d.lng}" target="_blank">${I('pin','')} הצג במפה</a></div>`:''}
    <div class="meta-sub">קטלוג</div>
    <div class="kv tall"><span>אוספים</span><span style="white-space:normal">${links(d.albums,'album')}</span></div>
    <div class="kv tall"><span>אנשים</span><span style="white-space:normal">${links(d.people,'person')}</span></div>
    <div class="kv"><span>מועדף Google</span><label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="m-fav" ${d.favorited?'checked':''}></label></div>
    <div class="kv"><span>יובא</span>${fdate(d.imported_at)}</div>
    ${d.gphotos_url?`<div class="kv"><span></span><a href="${esc(d.gphotos_url)}" target="_blank">פתח ב‑Google Photos ${I('external')}</a></div>`:''}
    <div class="btnrow"><button id="m-exif" title="כתוב תיאור, תאריך ומיקום לתוך קובץ ה‑JPG (Ctrl+S)">שמור מטא-נתונים לקובץ</button><button id="m-reveal" title="Ctrl+R">הצג בסייר</button></div>`}`;
}
$('#p-meta').addEventListener('click', e=>{
  const s=e.target.closest('[data-mr]'); if(s){ const n=+s.dataset.mr, p=actPhoto(); setRating(p&&p.rating===n&&targets().length===1?0:n); setTimeout(renderRight, 50); return; }
  const l=e.target.closest('[data-src-link]'); if(l){ setSource(srcFromKey(l.dataset.srcLink)); return; }
  if(e.target.id==='m-exif') saveMetaToFile();
  if(e.target.id==='m-reveal') reveal();
});
$('#p-meta').addEventListener('change', async e=>{
  const ids=targets(), t=e.target;
  if(t.id==='m-label') setAttr({label:t.value}, ids);
  else if(t.id==='m-desc'){ await send('PATCH','/api/photos',{ids, description:t.value}); toast('הכיתוב נשמר', 1200); }
  else if(t.id==='m-fav'){ setAttr({favorited:t.checked?1:0}, ids); }
  else if(t.id==='m-date' && t.value){ const ts=Math.floor(new Date(t.value).getTime()/1000);
    await send('PATCH','/api/photo/'+ids[0],{taken_at:ts}); const p=S.byId.get(ids[0]); if(p) p.taken_at=ts; applyFilter(); toast('זמן הצילום עודכן', 1200); }
  else if(t.id==='m-lat' || t.id==='m-lng'){ const lat=$('#m-lat').value, lng=$('#m-lng').value;
    if((lat==='')!==(lng==='')) return;
    await send('PATCH','/api/photo/'+ids[0],{lat: lat===''?null:+lat, lng: lng===''?null:+lng}); toast('המיקום נשמר', 1200); renderRight(); }
});
$('#p-meta').addEventListener('keydown', e=>{ if(e.target.id==='m-desc' && e.key==='Enter' && !e.shiftKey){ e.preventDefault(); e.target.blur(); } });
async function saveMetaToFile(){
  const d=DETAIL; if(!d || targets().length!==1) return toast('בחרו תמונה אחת');
  await send('PATCH','/api/photo/'+d.id,{description:d.description, taken_at:d.taken_at, lat:d.lat, lng:d.lng, write_exif:true});
  toast(/\.jpe?g$/i.test(d.filename) ? 'המטא-נתונים נכתבו לקובץ' : 'כתיבה לקובץ נתמכת רק ב‑JPG');
}
async function reveal(){ const p=actPhoto(); if(p) await send('POST', `/api/photo/${p.id}/reveal`); }
$('#btn-sync-meta').onclick = async ()=>{
  const ids=targets(), d=DETAIL; if(ids.length<2 || !d) return toast('בחרו כמה תמונות; הערכים יועתקו מהתמונה הפעילה');
  await send('PATCH','/api/photos',{ids, description:d.description||'', rating:d.rating||0, label:d.label||''});
  ids.forEach(id=>{ const p=S.byId.get(id); if(p){ p.rating=d.rating||0; p.label=d.label||null; } });
  toast(`סונכרנו ${num(ids.length)} תמונות`); applyFilter();
};

// ---------- modules ----------
$('#modules').addEventListener('click', e=>{ const b=e.target.closest('[data-mod]'); if(b) setModule(b.dataset.mod); });
async function setModule(m){
  if(m==='slideshow'){ ssStart(); return; }
  if(m===S.mod) return;
  if(S.mod==='develop') await devLeave();
  S.mod=m; document.body.classList.toggle('mod-library', m==='library'); document.body.classList.toggle('mod-develop', m==='develop');
  $$('#modules [data-mod]').forEach(b=>b.classList.toggle('on', b.dataset.mod===m));
  if(m==='develop'){
    if(S.act==null && S.list.length) selectOnly(S.list[0].id);
    ['grid','loupe','compare','survey','people','empty'].forEach(k=>$('#v-'+k).classList.add('hidden'));
    $('#v-develop').classList.remove('hidden');
    devOpen();
  } else {
    $('#v-develop').classList.add('hidden');
    setView(S.view==='people'?'grid':S.view);
  }
  renderToolbar();
}

// ---------- develop ----------
const DEV = {id:null, ops:null, saved:null, hist:[], crop:false, before:false, dirty:false, last:pref.get('lastDev', null)};
const NEUTRAL = () => ({bri:0, con:0, sat:0, gray:false, rot:0, crop:[0,0,1,1]});
const fac = (v, lo) => v>=0 ? 1+v/100 : 1+(v/100)*(1-lo);
const unfac = (f, lo) => f==null ? 0 : Math.round(f>=1 ? (f-1)*100 : (f-1)/(1-lo)*100);
const PRESETS = [
  ['ללא (איפוס טונים)', {bri:0,con:0,sat:0,gray:false}],
  ['שחור-לבן', {gray:true, con:10}],
  ['שחור-לבן בניגודיות גבוהה', {gray:true, con:45, bri:5}],
  ['חי וצבעוני', {sat:40, con:15}],
  ['מושתק', {sat:-40, con:-10}],
  ['בהיר ואוורירי', {bri:20, con:-15, sat:-10}],
  ['כהה ודרמטי', {bri:-15, con:35, sat:-15}],
];
async function devOpen(){
  const p=actPhoto();
  DEV.crop=false; DEV.before=false; DEV.dirty=false;
  if(!p){ DEV.id=null; $('#dev-img').removeAttribute('src'); renderDevPanels(); return; }
  if(p.is_video){ DEV.id=null; $('#dev-img').removeAttribute('src'); renderDevPanels(); toast('עריכה זמינה לתמונות בלבד'); return; }
  DEV.id=p.id;
  const d=await api('/api/photo/'+p.id); if(DEV.id!==p.id) return;
  const o = d.edit_ops ? JSON.parse(d.edit_ops) : {};
  DEV.ops = {bri:unfac(o.brightness,.3), con:unfac(o.contrast,.3), sat:unfac(o.saturation,0), gray:!!o.grayscale,
             rot:o.rotate||0, crop:o.crop&&o.crop.length===4?o.crop:[0,0,1,1]};
  DEV.saved = JSON.stringify(DEV.ops);
  DEV.hist=[{t:d.edited?'הגדרות שמורות':'ייבוא', ops:{...DEV.ops}}];
  const img=$('#dev-img'); img.onload=()=>{ layoutDev(); drawHisto(img); };
  img.src = `/original/${p.id}${VER[p.id]?'?v='+VER[p.id]:''}`;
  renderDevPanels(); renderToolbar(); updateNavigator();
}
async function devLeave(){ if(DEV.id!=null && DEV.dirty) await devApply(true); }
function layoutDev(){
  const img=$('#dev-img'), cv=$('#dev-canvas'), st=$('#v-develop');
  if(!img.naturalWidth || DEV.id==null) return;
  const pad=24, SW=st.clientWidth-2*pad, SH=st.clientHeight-2*pad;
  const o=DEV.ops, w=img.naturalWidth, h=img.naturalHeight;
  const th = (DEV.before?0:o.rot)*Math.PI/180, C=Math.abs(Math.cos(th)), Sn=Math.abs(Math.sin(th));
  const BW=w*C+h*Sn, BH=w*Sn+h*C;
  const c = (DEV.crop||DEV.before) ? [0,0,1,1] : o.crop;
  const s = Math.min(SW/((c[2]-c[0])*BW), SH/((c[3]-c[1])*BH));
  const cw=BW*s, ch=BH*s;
  const left = pad + SW/2 - (c[0]+c[2])/2*cw, top = pad + SH/2 - (c[1]+c[3])/2*ch;
  Object.assign(cv.style, {left:left+'px', top:top+'px', width:cw+'px', height:ch+'px',
    clipPath:`inset(${c[1]*100}% ${(1-c[2])*100}% ${(1-c[3])*100}% ${c[0]*100}%)`});
  Object.assign(img.style, {width:w*s+'px', height:h*s+'px', left:(cw-w*s)/2+'px', top:(ch-h*s)/2+'px',
    transform:`rotate(${DEV.before?0:o.rot}deg)`,
    filter: DEV.before ? '' : `brightness(${fac(o.bri,.3)}) contrast(${fac(o.con,.3)}) saturate(${fac(o.sat,0)})${o.gray?' grayscale(1)':''}`});
  $('#dev-badge').classList.toggle('hidden', !DEV.before);
  const ov=$('#crop-ov'); ov.classList.toggle('hidden', !DEV.crop);
  if(DEV.crop){ const k=o.crop; Object.assign(ov.style, {left:left+k[0]*cw+'px', top:top+k[1]*ch+'px', width:(k[2]-k[0])*cw+'px', height:(k[3]-k[1])*ch+'px'});
    ov._box={left, top, cw, ch}; if(!ov.children.length) ov.innerHTML=['nw','n','ne','e','se','s','sw','w'].map(h=>`<i data-h="${h}"></i>`).join(''); }
}
function devSet(changes, label){
  if(DEV.id==null) return;
  Object.assign(DEV.ops, changes);
  DEV.dirty = JSON.stringify(DEV.ops)!==DEV.saved;
  if(label) DEV.hist.push({t:label, ops:{...DEV.ops, crop:[...DEV.ops.crop]}});
  layoutDev(); renderDevPanels(); renderToolbar();
}
function renderDevPanels(){
  const o=DEV.ops || NEUTRAL(), dis = DEV.id==null ? 'disabled' : '';
  const sl=(k,label,cls,min=-100,max=100)=>`<div class="dsl ${cls}"><label for="d-${k}">${label}</label><input id="d-${k}" data-k="${k}" type="range" min="${min}" max="${max}" step="1" value="${o[k]}" ${dis} title="לחיצה כפולה לאיפוס"><output>${o[k]>0?'+':''}${o[k]}</output></div>`;
  $('#p-basic').innerHTML = `
    <div class="dsec">טיפול</div>
    <div class="treat"><a data-gray="0" class="${o.gray?'':'on'}">צבע</a><a data-gray="1" class="${o.gray?'on':''}">שחור-לבן</a></div>
    <div class="dsec">טון</div>
    ${sl('bri','חשיפה','exp')}${sl('con','ניגודיות','con')}
    <div class="dsec">נוכחות</div>
    ${sl('sat','רוויה','sat')}`;
  const fine = Math.round((o.rot - Math.round(o.rot/90)*90)*10)/10;
  $('#p-transform').innerHTML = `
    <div class="dsl"><label for="d-straight">יישור</label><input id="d-straight" data-k="straight" type="range" min="-45" max="45" step="0.5" value="${fine}" ${dis}><output>${fine>0?'+':''}${fine}°</output></div>
    <div class="btnrow90"><button data-rot="-90" ${dis}>${I('rotl')} 90°</button><button data-rot="90" ${dis}>90° ${I('rotr')}</button></div>
    <div class="btnrow90"><button data-t="crop" ${dis}>${I('crop')} ${DEV.crop?'סיום חיתוך (Enter)':'חיתוך (R)'}</button><button data-t="cropreset" ${dis}>אפס חיתוך</button></div>`;
  $('#p-presets').innerHTML = PRESETS.map(([n],i)=>`<div class="row" data-preset="${i}">${I('dev')}<span class="nm">${n}</span></div>`).join('');
  $('#p-history').innerHTML = DEV.hist.map((h,i)=>`<div class="row ${i===DEV.hist.length-1?'':''}" data-hist="${i}"><span class="nm">${esc(h.t)}</span></div>`).reverse().join('') || '<div class="hint">—</div>';
  $$('#dev-tools [data-tool]').forEach(b=>b.classList.toggle('on', DEV.crop));
}
const DLABEL = {bri:'חשיפה', con:'ניגודיות', sat:'רוויה'};
$('#right').addEventListener('input', e=>{
  const k=e.target.dataset.k; if(!k || DEV.id==null) return;
  const v=+e.target.value;
  if(k==='straight'){ DEV.ops.rot = Math.round(DEV.ops.rot/90)*90 + v; }
  else DEV.ops[k]=v;
  e.target.nextElementSibling.textContent = (v>0?'+':'')+v+(k==='straight'?'°':'');
  DEV.dirty = true; layoutDev();
});
$('#right').addEventListener('change', e=>{
  const k=e.target.dataset.k; if(!k || DEV.id==null) return;
  const v=+e.target.value;
  devSet({}, k==='straight' ? `יישור ${v>0?'+':''}${v}°` : `${DLABEL[k]} ${v>0?'+':''}${v}`);
});
$('#right').addEventListener('dblclick', e=>{
  const k=e.target.dataset?.k; if(!k || DEV.id==null) return;
  if(k==='straight') devSet({rot:Math.round(DEV.ops.rot/90)*90}, 'יישור 0°'); else devSet({[k]:0}, `${DLABEL[k]} 0`);
});
$('#right').addEventListener('click', e=>{
  if(S.mod!=='develop' || DEV.id==null) return;
  const g=e.target.closest('[data-gray]'); if(g){ const on=g.dataset.gray==='1'; if(on!==DEV.ops.gray) devSet({gray:on}, on?'שחור-לבן':'צבע'); return; }
  const r=e.target.closest('[data-rot]'); if(r){ const d=+r.dataset.rot; const k=DEV.ops.crop, c = d>0 ? [1-k[3],k[0],1-k[1],k[2]] : [k[1],1-k[2],k[3],1-k[0]];
    let rot=DEV.ops.rot+d; if(rot>180) rot-=360; if(rot<=-180) rot+=360; devSet({rot, crop:c}, d>0?'סיבוב ימינה':'סיבוב שמאלה'); return; }
  const t=e.target.closest('[data-t]')?.dataset.t;
  if(t==='crop') devCropToggle();
  if(t==='cropreset') devSet({crop:[0,0,1,1]}, 'איפוס חיתוך');
  if(e.target.closest('[data-tool="crop"]')) devCropToggle();
});
$('#left').addEventListener('click', e=>{
  if(S.mod!=='develop' || DEV.id==null) return;
  const pr=e.target.closest('[data-preset]'); if(pr){ const [n,o]=PRESETS[+pr.dataset.preset]; devSet({...NEUTRAL(), rot:DEV.ops.rot, crop:DEV.ops.crop, ...o}, 'הגדרה קבועה: '+n); return; }
  const h=e.target.closest('[data-hist]'); if(h){ const s=DEV.hist[+h.dataset.hist]; devSet({...s.ops, crop:[...s.ops.crop]}); }
});
function devCropToggle(){ if(DEV.id==null) return; DEV.crop=!DEV.crop; if(!DEV.crop) devSet({}, 'חיתוך'); else { layoutDev(); renderDevPanels(); renderToolbar(); } }
function devBefore(){ if(DEV.id==null) return; DEV.before=!DEV.before; layoutDev(); renderToolbar(); }
$('#crop-ov').addEventListener('mousedown', e=>{
  e.preventDefault();
  const ov=$('#crop-ov'), b=ov._box, h=e.target.dataset.h || 'move', start=[...DEV.ops.crop], sx=e.clientX, sy=e.clientY;
  const mv=ev=>{
    const dx=(ev.clientX-sx)/b.cw, dy=(ev.clientY-sy)/b.ch; let [x1,y1,x2,y2]=start; const MIN=.04;
    if(h==='move'){ const w=x2-x1, hh=y2-y1; x1=clamp(x1+dx,0,1-w); y1=clamp(y1+dy,0,1-hh); x2=x1+w; y2=y1+hh; }
    else { if(h.includes('w')) x1=clamp(x1+dx,0,x2-MIN); if(h.includes('e')) x2=clamp(x2+dx,x1+MIN,1);
           if(h.includes('n')) y1=clamp(y1+dy,0,y2-MIN); if(h.includes('s')) y2=clamp(y2+dy,y1+MIN,1); }
    DEV.ops.crop=[x1,y1,x2,y2]; DEV.dirty=true; layoutDev();
  };
  const up=()=>{ removeEventListener('mousemove',mv); removeEventListener('mouseup',up); renderToolbar(); };
  addEventListener('mousemove',mv); addEventListener('mouseup',up);
});
function devOpsToApi(o){
  const full = o.crop.every((v,i)=>Math.abs(v-[0,0,1,1][i])<1e-3);
  return {rotate:o.rot||null, crop: full?null:o.crop.map(v=>Math.round(v*10000)/10000),
    brightness:fac(o.bri,.3), contrast:fac(o.con,.3), saturation:fac(o.sat,0), grayscale:o.gray||null};
}
async function devApply(silent){
  if(DEV.id==null) return;
  const id=DEV.id, ops={...DEV.ops};
  if(!silent) toast('מחיל…', 1500);
  await send('POST', `/api/photo/${id}/edit`, devOpsToApi(ops));
  const d=await api('/api/photo/'+id);
  const p=S.byId.get(id); if(p) Object.assign(p, {width:d.width, height:d.height, edited:d.edited, bytes:d.bytes});
  VER[id]=Date.now();
  DEV.last = {bri:ops.bri, con:ops.con, sat:ops.sat, gray:ops.gray}; pref.set('lastDev', DEV.last);
  if(DEV.id===id){ DEV.saved=JSON.stringify(DEV.ops); DEV.dirty=false; renderToolbar(); }
  G.cells.forEach(c=>{ if(+c.dataset.id===id){ const img=c.querySelector('img'); if(img) img.src=thumbUrl(id); } });
  renderFilm(true); renderColls();
  if(!silent) toast('ההגדרות הוחלו · המקור נשמר');
}
$('#btn-dev-apply').onclick = ()=>devApply();
$('#btn-dev-reset').onclick = ()=>{ if(DEV.id!=null) devSet(NEUTRAL(), 'איפוס'); };
$('#btn-copy-prev').onclick = ()=>{ if(DEV.id==null) return; if(!DEV.last) return toast('עוד לא הוחלו הגדרות על תמונה אחרת'); devSet({...DEV.last}, 'הגדרות קודמות'); };
$('#btn-dev-revert').onclick = async ()=>{
  if(DEV.id==null) return; const p=actPhoto();
  if(!p?.edited){ devSet(NEUTRAL(), 'איפוס'); return; }
  await send('POST', `/api/photo/${DEV.id}/revert`); VER[DEV.id]=Date.now();
  Object.assign(p, {edited:0}); const d=await api('/api/photo/'+p.id); Object.assign(p,{width:d.width,height:d.height,bytes:d.bytes});
  toast('הוחזר לקובץ המקורי'); renderFilm(true); devOpen();
};

// ---------- slideshow ----------
const SS={list:[], i:0, t:null, playing:true, cur:'a'};
function ssStart(){
  let list = S.sel.size>1 ? S.list.filter(p=>S.sel.has(p.id)) : S.list;
  list = list.filter(p=>!p.is_video); if(!list.length) return toast('אין תמונות להצגה');
  SS.list=list; SS.i=Math.max(0, list.findIndex(p=>p.id===S.act)); SS.playing=true;
  $('#slideshow').classList.remove('hidden'); ssShow(); ssTimer();
  document.documentElement.requestFullscreen?.().catch(()=>{});
}
function ssShow(){
  const p=SS.list[SS.i], nxt=SS.cur==='a'?'b':'a', img=$('#ss-'+nxt), old=$('#ss-'+SS.cur);
  img.onload=()=>{ img.classList.add('on'); old.classList.remove('on'); };
  img.src=mediaUrl(p.id); SS.cur=nxt;
  $('#ss-count').textContent = `${num(SS.i+1)} / ${num(SS.list.length)}`;
  const pre=SS.list[(SS.i+1)%SS.list.length]; if(pre) new Image().src=mediaUrl(pre.id);
}
function ssTimer(){ clearInterval(SS.t); if(SS.playing) SS.t=setInterval(()=>ssStep(1,true), 4000);
  $('#ss-pp').innerHTML = I(SS.playing?'pause':'play'); }
window.ssStep=(d,auto)=>{ SS.i=(SS.i+d+SS.list.length)%SS.list.length; ssShow(); if(!auto) ssTimer(); };
window.ssToggle=()=>{ SS.playing=!SS.playing; ssTimer(); };
window.ssStop=()=>{ clearInterval(SS.t); $('#slideshow').classList.add('hidden'); $('#ss-a').classList.remove('on'); $('#ss-b').classList.remove('on');
  if(document.fullscreenElement) document.exitFullscreen().catch(()=>{}); };
$('#slideshow').addEventListener('mousemove', ()=>{ const s=$('#slideshow'); s.classList.add('ui'); clearTimeout(s._h); s._h=setTimeout(()=>s.classList.remove('ui'), 1800); });

// ---------- panels / lights out ----------
function togglePanel(k, force){
  const cls={left:'hide-left', right:'hide-right', top:'hide-top', film:'hide-film', tool:'hide-tool'}[k];
  document.body.classList.toggle(cls, force);
  const hid = pref.get('hidden', {}); hid[k]=document.body.classList.contains(cls); pref.set('hidden', hid);
}
$$('[data-toggle]').forEach(b=>b.onclick=()=>togglePanel(b.dataset.toggle));
(()=>{ const hid=pref.get('hidden',{}); Object.entries(hid).forEach(([k,v])=>v&&togglePanel(k,true)); })();
function cycleLights(){ S.lights=(S.lights+1)%3; document.body.classList.toggle('lights-dim', S.lights===1); document.body.classList.toggle('lights-off', S.lights===2); }

// ---------- dialogs ----------
function modal(html){ $('#modal-box').innerHTML=html; $('#modal').classList.remove('hidden'); const f=$('#modal-box input:not([type=checkbox]),#modal-box button.primary'); f?.focus(); f?.select?.(); }
function closeModal(){ $('#modal').classList.add('hidden'); $('#modal-box').innerHTML=''; }
$('#modal').addEventListener('mousedown', e=>{ if(e.target===$('#modal')) closeModal(); });
let PB_CHECKED=false;   // state of an optional checkbox passed to promptBox via `extra`
function promptBox(title, value='', extra=''){
  return new Promise(res=>{
    modal(`<h3>${esc(title)}</h3><form class="mb" id="pb-form"><input type="text" id="pb-in" value="${esc(value)}" autocomplete="off">${extra}</form>
      <div class="mf"><button id="pb-cancel">ביטול</button><button class="primary" id="pb-ok">אישור</button></div>`);
    const done=v=>{ PB_CHECKED = !!$('#modal-box input[type=checkbox]')?.checked; closeModal(); res(v); };
    $('#pb-form').onsubmit=e=>{ e.preventDefault(); done($('#pb-in').value.trim()||null); };
    $('#pb-ok').onclick=()=>done($('#pb-in').value.trim()||null);
    $('#pb-cancel').onclick=()=>done(null);
  });
}
function confirmBox(title, text, ok='אישור'){
  return new Promise(res=>{
    modal(`<h3>${title}</h3><div class="mb"><p>${text}</p></div><div class="mf"><button id="cb-no">ביטול</button><button class="primary" id="cb-yes">${ok}</button></div>`);
    $('#cb-yes').onclick=()=>{ closeModal(); res(true); }; $('#cb-no').onclick=()=>{ closeModal(); res(false); };
  });
}
async function catalogSettings(){
  const s=await api('/api/status'), c=s.counts;
  modal(`<h3>הגדרות קטלוג</h3><div class="mb">
    <p>כל הקבצים נשמרים מקומית במחשב שלך. תמונות מיובאות מועתקות לתיקיית המדיה ומסודרות לפי שנת צילום. המקור של כל תמונה שנערכה נשמר בנפרד.</p>
    <div class="pathrow"><span>ספריית הקטלוג</span><code>${esc(s.library_root)}</code></div>
    <div class="pathrow"><span>קבצי מדיה</span><code>${esc(s.media_path)}</code></div>
    <div class="pathrow"><span>קובץ הקטלוג</span><code>${esc(s.db_path)}</code></div>
    <div class="pathrow"><span>תוכן</span><span>${num(c.photos)} פריטים · ${num(c.videos)} סרטונים · ${num(c.albums)} אוספים · ${num(c.people)} אנשים · ${num(c.tags)} מילות מפתח · ${num(c.trashed)} באשפה</span></div>
    <div class="pathrow"><span>אשפה</span><span>פריטים נמחקים לצמיתות אחרי ${s.trash_days} יום</span></div>
    <label class="fld"><span>מיקום קטלוג אחר</span><div class="frow"><input type="text" id="lib-path" dir="ltr" value="${esc(s.library_root)}"><button id="lib-pick">בחר...</button></div></label>
  </div><div class="mf"><button onclick="closeModal()">סגור</button><button class="primary" id="lib-set">עבור לקטלוג</button></div>`);
  $('#lib-pick').onclick=async()=>{ const r=await api('/api/pick-file?kind=folder&title='+encodeURIComponent('בחר תיקיית קטלוג')); if(r.path) $('#lib-path').value=r.path; };
  $('#lib-set').onclick=async()=>{ const p=$('#lib-path').value.trim(); if(!p) return; await send('POST','/api/settings/library',{path:p}); closeModal(); toast('הקטלוג הוחלף'); await reloadAll(); };
}
async function preferences(){
  const s=await api('/api/status'), at=s.autotag;
  const tagState = !at.model_ready ? ['stat-off','המודל יורד אוטומטית (כ‑600MB, פעם אחת) בהרצה הראשונה.']
    : at.pending ? ['stat-warn',`${num(at.pending)} תמונות ממתינות לתיוג`] : ['stat-ok',`מעודכן · ${num(at.embedded)} תמונות נותחו`];
  modal(`<h3>העדפות · זיהוי ותיוג</h3><div class="mb">
    <p>הכול רץ מקומית במחשב — שום תמונה לא נשלחת לשירות חיצוני, ואין צורך להתקין שום דבר.</p>
    <div class="pathrow"><span>זיהוי פנים</span><span>InsightFace · ${num(s.counts.faces)} פרצופים זוהו עד כה</span></div>
    <div class="pathrow"><span>מילות מפתח אוטומטיות</span><span class="${tagState[0]}">${tagState[1]}</span></div>
    <p>מילות המפתח (באנגלית) נבחרות מתוך אוצר מילים קבוע לפי תוכן התמונה (CLIP), ומקומות לפי GPS. התיוג רץ לבד אחרי כל ייבוא.
      מילת מפתח אוטומטית שמחקתם לא תחזור. חיפוש חכם: מסנן ספרייה ← טקסט ← „חיפוש חכם לפי תוכן".</p>
  </div><div class="mf"><button onclick="closeModal()">סגור</button>
    <button id="pf-faces">זהה פנים</button><button class="primary" id="pf-tag">תייג עכשיו</button></div>`);
  $('#pf-faces').onclick=()=>{ closeModal(); runJob('/api/faces','faces','זיהוי פנים'); };
  $('#pf-tag').onclick=()=>{ closeModal(); runJob('/api/autotag','tags','תיוג אוטומטי'); };
}
async function memories(){
  const m=await api('/api/memories'), t=m.titles||[], c=m.comments||[];
  modal(`<h3>זיכרונות ותגובות מ‑Google Photos</h3><div class="mb">
    ${!t.length&&!c.length?'<p>לא יובאו זיכרונות או תגובות.</p>':''}
    ${t.length?`<p>כותרות זיכרונות</p><div class="mem-list">${t.map(x=>`<div>${esc(x)}</div>`).join('')}</div>`:''}
    ${c.length?`<p>תגובות באלבומים משותפים</p><div class="mem-list">${c.map(x=>`<div><time>${fdate(x.created_at)}</time>${x.liked?'♥ ':''}${esc(x.text)||'(לייק)'}</div>`).join('')}</div>`:''}
  </div><div class="mf"><button class="primary" onclick="closeModal()">סגור</button></div>`);
}
function shortcuts(){
  const k=(key,t)=>`<kbd>${key}</kbd><span>${t}</span>`;
  modal(`<h3>קיצורי מקשים</h3><div class="mb"><div class="kgrid">
    <h4>תצוגות</h4>${k('G','רשת')}${k('E','זכוכית מגדלת')}${k('C','השוואה')}${k('N','סקירה')}${k('O','אנשים')}${k('D','מודול פיתוח')}${k('Ctrl+Enter','מצגת')}${k('Esc','חזרה / יציאה')}
    <h4>דירוג וסימון</h4>${k('P','דגל נבחרת')}${k('X','דגל נדחית')}${k('U','הסר דגל')}${k('`','החלף דגל')}${k('0–5','דירוג כוכבים')}${k('[ / ]','הורד / העלה דירוג')}${k('6–9','תווית אדום/צהוב/ירוק/כחול')}${k('Shift+מקש','סמן ועבור לבאה')}${k('B','אוסף מהיר')}${k('Ctrl+B','הצג אוסף מהיר')}
    <h4>בחירה</h4>${k('Ctrl+A','בחר הכול')}${k('Ctrl+D','בטל בחירה')}${k('Ctrl+לחיצה','הוסף לבחירה')}${k('Shift+לחיצה','בחר טווח')}${k('← → ↑ ↓','מעבר בין תמונות')}${k('Delete','העבר לאשפה')}
    <h4>ממשק</h4>${k('Tab','הסתר לוחות צד')}${k('Shift+Tab','הסתר את כל הלוחות')}${k('F5 / F6','לוח עליון / רצועת תמונות')}${k('F7 / F8','לוח ימני / שמאלי')}${k('T','סרגל כלים')}${k('L','כבה אורות')}${k('J','סגנון תאים')}${k('I','מידע בזכוכית מגדלת')}${k('\\\\','סרגל סינון / לפני-אחרי')}${k('Ctrl+L','הפעל/השבת מסננים')}${k('Ctrl+F','חיפוש טקסט')}${k('Z / רווח','זום 1:1')}
    <h4>קבצים</h4>${k('Ctrl+Shift+I','ייבוא')}${k('Ctrl+Shift+E','ייצוא')}${k('Ctrl+N','אוסף חדש')}${k('Ctrl+[ / ]','סיבוב')}${k('Ctrl+R','הצג בסייר')}${k('Ctrl+S','שמור מטא-נתונים לקובץ')}${k('Ctrl+K','הוסף מילות מפתח')}${k('R','חיתוך (פיתוח)')}
  </div></div><div class="mf"><button class="primary" onclick="closeModal()">סגור</button></div>`);
}

// ---------- export ----------
function openExport(){
  const ids=targets().length ? targets() : [];
  if(!ids.length) return toast('בחרו תמונות לייצוא');
  const last=pref.get('export', {dest:'', mode:'current', edge:2048, q:90});
  modal(`<h3>ייצוא ${num(ids.length)} קבצים</h3><div class="mb">
    <label class="fld"><span>ייצא אל</span><div class="frow"><input type="text" id="ex-dest" dir="ltr" value="${esc(last.dest)}" placeholder="C:\\Users\\...\\Pictures\\Export"><button id="ex-pick">בחר...</button></div></label>
    <div class="fld"><span>הגדרות קובץ</span>
      <label class="check" style="padding:0"><input type="radio" name="ex-mode" value="current" ${last.mode==='current'?'checked':''}> הקובץ כפי שהוא בקטלוג (כולל עריכות)</label>
      <label class="check" style="padding:0"><input type="radio" name="ex-mode" value="original" ${last.mode==='original'?'checked':''}> המקור, בלי עריכות</label>
      <label class="check" style="padding:0"><input type="radio" name="ex-mode" value="jpeg" ${last.mode==='jpeg'?'checked':''}> JPEG בגודל מותאם</label></div>
    <div class="frow" id="ex-jpeg"><span>צלע ארוכה</span><input type="number" id="ex-edge" min="200" max="20000" value="${last.edge}" style="width:90px" dir="ltr"><span>פיקסלים · איכות</span>
      <input type="range" id="ex-q" min="40" max="100" value="${last.q}" style="width:120px"><output id="ex-qv">${last.q}</output></div>
    <p>סרטונים תמיד מועתקים כמו שהם. שמות קבצים כפולים מקבלים מספר.</p>
  </div><div class="mf"><button onclick="closeModal()">ביטול</button><button class="primary" id="ex-go">ייצוא</button></div>`);
  $('#ex-q').oninput=e=>$('#ex-qv').textContent=e.target.value;
  $('#ex-pick').onclick=async()=>{ const r=await api('/api/pick-file?kind=folder&title='+encodeURIComponent('בחר תיקיית ייצוא')); if(r.path) $('#ex-dest').value=r.path; };
  $('#ex-go').onclick=async()=>{
    const dest=$('#ex-dest').value.trim(), mode=$('input[name=ex-mode]:checked').value, edge=+$('#ex-edge').value||null, q=+$('#ex-q').value;
    if(!dest) return toast('בחרו תיקיית יעד');
    pref.set('export', {dest, mode, edge:edge||2048, q});
    closeModal();
    runJob('/api/export','export','ייצוא',{ids, dest, originals:mode==='original', long_edge:mode==='jpeg'?edge:null, quality:mode==='jpeg'?q:100});
  };
}

// ---------- import (full-window dialog like Lightroom's) ----------
const IM = {mode:'folder', path:'', zip:'', lrcat:'', lrinfo:null, recursive:true, files:[], on:new Set(), skipDup:true, show:'all'};
function openImport(mode){
  IM.mode = mode || IM.mode;
  IM.path = IM.path || pref.get('importPath','');
  $('#import').classList.remove('hidden');
  renderImport();
  if(IM.mode==='folder' && IM.path && !IM.files.length) scanImport();
}
function closeImport(){ $('#import').classList.add('hidden'); }
function renderImport(){
  const el=$('#import'), folder=IM.mode==='folder', lr=IM.mode==='lrcat', li=IM.lrinfo;
  const shown = IM.files.filter(f=>IM.show==='all' || !f.dup);
  const chosen = IM.files.filter(f=>IM.on.has(f.path));
  const bytes = chosen.reduce((a,f)=>a+f.bytes,0);
  const recent = pref.get('importRecent', []);
  const ready = folder ? chosen.length : lr ? (li && li.images-li.missing>0) : IM.zip;
  el.innerHTML = `
  <div class="im-top">
    <div class="blk"><span>מקור</span><b>${esc(folder ? (IM.path||'בחרו תיקייה') : lr ? (IM.lrcat||'בחרו קטלוג Lightroom') : (IM.zip||'בחרו קובץ ZIP'))}</b></div>
    <span class="im-arrow">←</span>
    <nav class="im-modes"><a data-im="folder" class="${folder?'on':''}">העתק<small>מתיקייה / כרטיס זיכרון</small></a><a data-im="lrcat" class="${lr?'on':''}">Lightroom Classic<small>קטלוג ‎.lrcat</small></a><a data-im="zip" class="${IM.mode==='zip'?'on':''}">Google Takeout<small>קובץ ZIP מגוגל פוטוס</small></a></nav>
    <span class="im-arrow">←</span>
    <div class="blk"><span>יעד</span><b>${esc(S.status?.media_path||'')}</b></div>
  </div>
  <div class="im-body">
    <aside class="im-side">
      <section class="pnl"><h3><span>מקור</span></h3><div class="pbody">
        ${folder ? `<div class="btnrow"><button id="im-pick">${I('folder')} בחר תיקייה...</button></div>
          <label class="check"><input type="checkbox" id="im-rec" ${IM.recursive?'checked':''}> כולל תיקיות משנה</label>
          ${recent.length?`<div class="lbl-sub" style="padding-top:8px">אחרונים</div>${recent.map(p=>`<div class="row" data-recent="${esc(p)}">${I('folder')}<span class="nm" dir="ltr" title="${esc(p)}">${esc(p)}</span></div>`).join('')}`:''}`
        : lr ? `<div class="btnrow"><button id="im-lrcat">${I('import')} בחר קטלוג Lightroom...</button></div>
          <div class="hint">קובץ ‎<code>.lrcat</code>‎ של Lightroom Classic (בדרך כלל ב‑Pictures/Lightroom). מומלץ לסגור את Lightroom לפני הייבוא.</div>`
        : `<div class="btnrow"><button id="im-zip">${I('import')} בחר קובץ ZIP...</button></div>
          <div class="hint">הורידו את הספרייה מ‑takeout.google.com (Google Photos). הקובץ נקרא ישירות, בלי לפרוס אותו.</div>`}
      </div></section>
    </aside>
    <div class="im-center">
      ${folder ? `<div class="im-bar"><a data-show="all" class="${IM.show==='all'?'on':''}">כל התמונות</a><a data-show="new" class="${IM.show==='new'?'on':''}">תמונות חדשות</a>
        <span class="spacer"></span><a data-chk="all">סמן הכול</a><a data-chk="none">בטל סימון</a></div>
        <div class="im-grid" id="im-grid">${IM.loading?'<div class="im-empty">סורק…</div>':!IM.files.length?`<div class="im-empty">${IM.path?'לא נמצאו תמונות או סרטונים בתיקייה.':'בחרו תיקייה או כרטיס זיכרון מהלוח „מקור".'}</div>`
          : shown.slice(0,3000).map(f=>`<div class="im-cell ${IM.on.has(f.path)?'':'off'}" data-path="${esc(f.path)}" title="${esc(f.path)}">
            <input type="checkbox" ${IM.on.has(f.path)?'checked':''}>${f.dup?'<span class="dup" title="כבר בקטלוג (אותו שם וגודל)">כפילות</span>':''}
            ${f.is_video?`<span class="vid">${I('play')}</span>`:`<img loading="lazy" src="/api/local-thumb?path=${encodeURIComponent(f.path)}" alt="">`}
            <span class="nm">${esc(f.name)}</span></div>`).join('') + (shown.length>3000?`<div class="im-empty">מוצגות 3,000 הראשונות מתוך ${num(shown.length)} — כולן ייובאו אם מסומנות.</div>`:'')}</div>`
      : lr ? `<div class="im-grid"><div class="im-empty">${IM.lrloading?'קורא את הקטלוג…':!li?'בחרו קטלוג Lightroom Classic מהלוח „מקור".'
          : `<b dir="ltr">${esc(IM.lrcat)}</b><br>${num(li.images)} תמונות · ${num(li.keywords)} מילות מפתח · ${num(li.collections)} אוספים · ${fsize(li.bytes)}
             ${li.missing?`<br><span style="color:var(--yellow)">${num(li.missing)} קבצים שהקטלוג מפנה אליהם לא נמצאו בדיסק ולא ייובאו.</span>`:''}`}</div></div>`
      : `<div class="im-grid"><div class="im-empty">${IM.zip?`<b dir="ltr">${esc(IM.zip)}</b><br>ייבוא ישמור אלבומים, תאריכים, מיקומים, מועדפים, תגי אנשים וזיכרונות. תמונות שכבר בקטלוג לא ישוכפלו.`:'בחרו את קובץ ה‑ZIP מ‑Google Takeout.'}</div></div>`}
    </div>
    <aside class="im-side">
      ${folder?`<section class="pnl"><h3><span>טיפול בקבצים</span></h3><div class="pbody">
        <label class="check"><input type="checkbox" id="im-skipdup" ${IM.skipDup?'checked':''}> אל תייבא כפילויות חשודות</label>
        <div class="hint">קבצים זהים לגמרי (לפי תוכן) אף פעם לא נשמרים פעמיים.</div></div></section>
      <section class="pnl"><h3><span>החל במהלך הייבוא</span></h3><div class="pbody">
        <label class="fld" style="padding:4px 12px"><span>מילות מפתח</span><input type="text" id="im-kw" placeholder="חופשה, משפחה"></label>
        <label class="fld" style="padding:4px 12px"><span>הוסף לאוסף</span><input type="text" id="im-album" list="im-albums" placeholder="ללא"></label>
        <datalist id="im-albums">${S.albums.filter(a=>a.kind==='album').map(a=>`<option value="${esc(a.name)}">`).join('')}</datalist></div></section>`:''}
      ${lr?`<section class="pnl"><h3><span>מה מיובא</span></h3><div class="pbody"><div class="hint">
        דירוגים, דגלים, תוויות צבע, כיתובים, תאריכי צילום, מיקומים, מילות מפתח (מילות „אדם" הופכות לאנשים), אוספים והאוסף המהיר.
        הקבצים מועתקים לספרייה — המקור לא משתנה. עריכות Develop נשמרות בפורמט של Lightroom ולא מועברות; מיובא הקובץ המקורי.
        עותקים וירטואליים ואוספים חכמים מדולגים.</div></div></section>`:''}
      <section class="pnl"><h3><span>יעד</span></h3><div class="pbody">
        <div class="hint"><code>${esc(S.status?.media_path||'')}</code><br>מסודר בתיקיות לפי שנת צילום (למשל <code>2024</code>). ניתן לשנות מקובץ ← הגדרות קטלוג.</div></div></section>
    </aside>
  </div>
  <div class="im-foot">${folder?`${num(chosen.length)} תמונות / ${fsize(bytes)}`:lr&&li?`${num(li.images-li.missing)} תמונות / ${fsize(li.bytes)}`:''}<span class="spacer"></span>
    <button id="im-cancel">ביטול</button><button class="primary" id="im-go" ${ready?'':'disabled'}>ייבוא</button></div>`;
}
async function scanImport(){
  if(!IM.path) return;
  IM.loading=true; renderImport();
  try{
    const r=await api('/api/scan-folder?'+new URLSearchParams({path:IM.path, recursive:IM.recursive?1:0}));
    IM.files=r.files; IM.on=new Set(r.files.filter(f=>!(IM.skipDup&&f.dup)).map(f=>f.path));
    const rec=[IM.path, ...pref.get('importRecent',[]).filter(p=>p!==IM.path)].slice(0,5); pref.set('importRecent', rec); pref.set('importPath', IM.path);
  } finally { IM.loading=false; renderImport(); }
}
$('#import').addEventListener('click', async e=>{
  const t=e.target;
  const mode=t.closest('[data-im]'); if(mode){ IM.mode=mode.dataset.im; renderImport(); return; }
  if(t.closest('#im-cancel')) return closeImport();
  if(t.closest('#im-pick')){ const r=await api('/api/pick-file?kind=folder&title='+encodeURIComponent('בחר תיקייה לייבוא')); if(r.path){ IM.path=r.path; scanImport(); } return; }
  if(t.closest('#im-zip')){ const r=await api('/api/pick-file?kind=zip'); if(r.path){ IM.zip=r.path; renderImport(); } return; }
  if(t.closest('#im-lrcat')){ const r=await api('/api/pick-file?kind=lrcat'); if(!r.path) return;
    IM.lrcat=r.path; IM.lrinfo=null; IM.lrloading=true; renderImport();
    try{ IM.lrinfo=await api('/api/lrcat-info?'+new URLSearchParams({path:r.path})); } finally { IM.lrloading=false; renderImport(); } return; }
  const rec=t.closest('[data-recent]'); if(rec){ IM.path=rec.dataset.recent; scanImport(); return; }
  const sh=t.closest('[data-show]'); if(sh){ IM.show=sh.dataset.show; renderImport(); return; }
  const ck=t.closest('[data-chk]'); if(ck){ IM.on = ck.dataset.chk==='all' ? new Set(IM.files.filter(f=>IM.show==='all'||!f.dup).map(f=>f.path)) : new Set(); renderImport(); return; }
  const cell=t.closest('.im-cell'); if(cell){ const p=cell.dataset.path; IM.on.has(p)?IM.on.delete(p):IM.on.add(p);
    cell.classList.toggle('off', !IM.on.has(p)); cell.querySelector('input').checked=IM.on.has(p);
    const chosen=IM.files.filter(f=>IM.on.has(f.path)); $('#import .im-foot').firstChild.textContent=`${num(chosen.length)} תמונות / ${fsize(chosen.reduce((a,f)=>a+f.bytes,0))}`;
    $('#im-go').disabled=!chosen.length; return; }
  if(t.closest('#im-go')){
    if(IM.mode==='zip'){ await send('POST','/api/import',{zip_path:IM.zip}); closeImport(); pollJob('import','ייבוא מ‑Google'); return; }
    if(IM.mode==='lrcat'){ await send('POST','/api/import-lrcat',{path:IM.lrcat}); closeImport(); pollJob('import','ייבוא מ‑Lightroom'); return; }
    const paths=IM.files.filter(f=>IM.on.has(f.path)).map(f=>f.path);
    await send('POST','/api/import-folder',{paths, keywords:($('#im-kw').value||'').split(','), album:$('#im-album').value||null});
    closeImport(); IM.files=[]; pollJob('import','ייבוא');
  }
});
$('#import').addEventListener('change', e=>{
  if(e.target.id==='im-rec'){ IM.recursive=e.target.checked; scanImport(); }
  if(e.target.id==='im-skipdup'){ IM.skipDup=e.target.checked; IM.files.forEach(f=>{ if(f.dup){ IM.skipDup?IM.on.delete(f.path):IM.on.add(f.path); } }); renderImport(); }
});

// ---------- background jobs (activity indicator in the identity plate) ----------
async function runJob(url, name, label, body){ await send('POST', url, body); pollJob(name, label); }
async function pollJob(name, label){
  let p; try{ p=await api('/api/job/'+name); }catch{ return; }
  const act=$('#activity'), bar=$('.act-bar');
  act.classList.remove('hidden');
  const pct = p.total ? Math.round(100*p.done/p.total) : null;
  $('#act-label').textContent = `${label}${pct!=null?` · ${pct}%`:'…'}`;
  bar.classList.toggle('indet', pct==null); $('#act-fill').style.width = (pct??0)+'%';
  act.title = `${label}: ${p.msg||p.state}`;
  act.onclick = ()=>toast(`<b>${label}</b><br>${esc(p.msg||p.state)}${p.total?` (${num(p.done)}/${num(p.total)})`:''}`);
  if(['done','error','idle'].includes(p.state)){
    act.classList.add('hidden');
    toast(`${label}: ${esc(p.error||p.msg||'הושלם')}`, 4000);
    if(['import','faces','tags'].includes(name)){
      await reloadAll();
      if(name==='import' && p.state==='done' && S.status.last_import) setSource(srcFromKey('prev'));
      if(name==='import' && p.state==='done') setTimeout(()=>pollJob('tags','תיוג אוטומטי'), 600);
      if(S.view==='people') renderPeople();
    }
    return;
  }
  setTimeout(()=>pollJob(name, label), 800);
}

// ---------- menu bar ----------
const sep='-';
const MENUS = [
  ['קובץ', [
    ['ייבוא תמונות וסרטונים...', 'Ctrl+Shift+I', ()=>openImport('folder')],
    ['ייבוא מקטלוג Lightroom...', '', ()=>openImport('lrcat')],
    ['ייבוא מ‑Google Takeout...', '', ()=>openImport('zip')],
    ['ייצוא...', 'Ctrl+Shift+E', openExport],
    sep,
    ['הגדרות קטלוג...', 'Ctrl+Alt+,', catalogSettings],
    ['העדפות...', 'Ctrl+,', preferences],
  ]],
  ['עריכה', [
    ['בחר הכול', 'Ctrl+A', selectAll],
    ['בטל בחירה', 'Ctrl+D', selectNone],
    ['הפוך בחירה', '', selectInvert],
    ['בחר את המסומנות בדגל', 'Ctrl+Alt+A', selectPicks],
  ]],
  ['ספרייה', [
    ['אוסף חדש...', 'Ctrl+N', newCollection],
    sep,
    ['הצג אוסף מהיר', 'Ctrl+B', ()=>setSource(srcFromKey('quick'))],
    ['נקה אוסף מהיר', '', async()=>{ const ids=S.all.filter(p=>p.quick).map(p=>p.id); if(ids.length) await setAttr({quick:0}, ids); }],
    sep,
    ['הפעל מסננים', 'Ctrl+L', ()=>{ S.F.on=!S.F.on; applyFilter(); }, null, ()=>S.F.on],
    ['הצג סרגל סינון', '\\', ()=>{ $('#filterbar').classList.toggle('hidden'); }, null, ()=>!$('#filterbar').classList.contains('hidden')],
    sep,
    ['זיהוי פנים', '', ()=>runJob('/api/faces','faces','זיהוי פנים')],
    ['תיוג אוטומטי', '', ()=>runJob('/api/autotag','tags','תיוג אוטומטי')],
    sep,
    ['זיכרונות ותגובות מ‑Google...', '', memories],
  ]],
  ['תמונה', [
    ['הוסף לאוסף המהיר', 'B', toggleQuick],
    ['הצג בסייר', 'Ctrl+R', reveal],
    sep,
    ['סובב שמאלה', 'Ctrl+[', ()=>rotateSel(-90)],
    ['סובב ימינה', 'Ctrl+]', ()=>rotateSel(90)],
    sep,
    ['דגל: נבחרת', 'P', ()=>setFlag(1)],
    ['דגל: נדחית', 'X', ()=>setFlag(-1)],
    ['ללא דגל', 'U', ()=>setFlag(0)],
    sep,
    ...[0,1,2,3,4,5].map(n=>[n?`${'★'.repeat(n)}`:'ללא דירוג', String(n), ()=>setRating(n)]),
    sep,
    ...LABELS.map(([k,n,key])=>[`תווית: ${n}`, key, ()=>setLabel(k)]),
    ['ללא תווית', '', ()=>setAttr({label:''})],
    sep,
    ['העבר לאשפה / שחזר', 'Delete', trashSelected],
  ]],
  ['מטא-נתונים', [
    ['הוסף מילות מפתח', 'Ctrl+K', ()=>{ document.body.classList.remove('hide-right'); $('.pnl[data-p=kwing]').classList.remove('shut'); $('#kw-add')?.focus(); }],
    ['שמור מטא-נתונים לקובץ', 'Ctrl+S', saveMetaToFile],
    ['סנכרן מטא-נתונים', '', ()=>$('#btn-sync-meta').click()],
  ]],
  ['תצוגה', [
    ['רשת', 'G', ()=>setView('grid')], ['זכוכית מגדלת', 'E', ()=>setView('loupe')], ['השוואה', 'C', ()=>setView('compare')],
    ['סקירה', 'N', ()=>setView('survey')], ['אנשים', 'O', ()=>setView('people')], ['פיתוח', 'D', ()=>setModule('develop')],
    ['מצגת', 'Ctrl+Enter', ssStart],
    sep,
    ['החלף סגנון תאים', 'J', cycleCellStyle],
    ['מידע בזכוכית מגדלת', 'I', ()=>{ S.loupeInfo=!S.loupeInfo; renderLoupe(); renderToolbar(); }, null, ()=>S.loupeInfo],
    sep,
    ['הסתר/הצג לוחות צד', 'Tab', toggleSides],
    ['הסתר/הצג את כל הלוחות', 'Shift+Tab', toggleAllPanels],
    ['סרגל כלים', 'T', ()=>togglePanel('tool'), null, ()=>!document.body.classList.contains('hide-tool')],
    ['כבה אורות', 'L', cycleLights],
  ]],
  ['עזרה', [
    ['קיצורי מקשים', 'Ctrl+/', shortcuts],
    ['אודות photag', '', ()=>modal(`<h3>photag</h3><div class="mb"><p>ניהול ושמירת תמונות מקומי בהשראת Lightroom Classic: קטלוג, אוספים, דגלים, דירוגים, תוויות צבע, מילות מפתח, זיהוי פנים ועריכה לא הורסת — המקור תמיד נשמר.</p></div><div class="mf"><button class="primary" onclick="closeModal()">סגור</button></div>`)],
  ]],
];
let MENU_OPEN=null;
$('#menubar').innerHTML = MENUS.map(([n],i)=>`<button data-menu="${i}">${n}</button>`).join('');
function openMenu(i){
  const b=$(`#menubar [data-menu="${i}"]`), pop=$('#menu-pop'), items=MENUS[i][1];
  $$('#menubar button').forEach(x=>x.classList.toggle('open', x===b));
  pop.innerHTML = items.map((it,j)=>it===sep?'<hr>':`<div class="mi ${it[4]&&it[4]()?'chk':''}" data-mi="${j}"><span>${it[0]}</span><span class="k">${it[1]||''}</span></div>`).join('');
  const r=b.getBoundingClientRect(); pop.style.top=r.bottom+'px'; pop.style.right=(innerWidth-r.right)+'px'; pop.style.left='auto';
  pop.classList.remove('hidden'); MENU_OPEN=i;
}
function closeMenu(){ $('#menu-pop').classList.add('hidden'); $$('#menubar button').forEach(x=>x.classList.remove('open')); MENU_OPEN=null; }
$('#menubar').addEventListener('mousedown', e=>{ const b=e.target.closest('[data-menu]'); if(!b) return; e.stopPropagation(); MENU_OPEN===+b.dataset.menu ? closeMenu() : openMenu(+b.dataset.menu); });
$('#menubar').addEventListener('mouseover', e=>{ const b=e.target.closest('[data-menu]'); if(b && MENU_OPEN!=null && MENU_OPEN!==+b.dataset.menu) openMenu(+b.dataset.menu); });
$('#menu-pop').addEventListener('mousedown', e=>{ e.stopPropagation(); const it=e.target.closest('[data-mi]'); if(!it) return; const f=MENUS[MENU_OPEN][1][+it.dataset.mi][2]; closeMenu(); f(); });
document.addEventListener('mousedown', ()=>{ if(MENU_OPEN!=null) closeMenu(); });

// ---------- buttons ----------
$('#btn-import').onclick = ()=>openImport('folder');
$('#btn-export').onclick = openExport;
function cycleCellStyle(){ S.cell = S.cell==='compact'?'xp':S.cell==='xp'?'plain':'compact'; pref.set('cellStyle', S.cell); layoutGrid(true); }
function toggleSides(){ const hide = !(document.body.classList.contains('hide-left') && document.body.classList.contains('hide-right')); togglePanel('left', hide); togglePanel('right', hide); }
function toggleAllPanels(){ const hide = !(document.body.classList.contains('hide-left') && document.body.classList.contains('hide-film')); ['left','right','top','film'].forEach(k=>togglePanel(k, hide)); }

// ---------- keyboard (Lightroom's shortcuts) ----------
document.addEventListener('keydown', e=>{
  const typing = /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) && e.target.type!=='checkbox' && e.target.type!=='range';
  const k=e.key, ctrl=e.ctrlKey||e.metaKey;
  if(!$('#slideshow').classList.contains('hidden')){
    if(k==='Escape') ssStop(); else if(k==='ArrowLeft') ssStep(1); else if(k==='ArrowRight') ssStep(-1); else if(k===' '){ e.preventDefault(); ssToggle(); }
    return;
  }
  if(!$('#modal').classList.contains('hidden')){ if(k==='Escape') closeModal(); return; }
  if(!$('#import').classList.contains('hidden')){ if(k==='Escape') closeImport(); return; }
  if(MENU_OPEN!=null && k==='Escape'){ closeMenu(); return; }
  if(typing){ if(k==='Escape') e.target.blur(); return; }

  const code=e.code, shift=e.shiftKey, adv = shift && !ctrl;
  // ctrl combos
  if(ctrl){
    const map = {
      KeyA: ()=>e.altKey?selectPicks():selectAll(), KeyD: selectNone, KeyB: ()=>setSource(srcFromKey('quick')), KeyN: newCollection,
      KeyL: ()=>{ S.F.on=!S.F.on; applyFilter(); toast(S.F.on?'המסננים הופעלו':'המסננים הושבתו', 1200); },
      KeyF: ()=>{ S.fb='text'; renderFilterBar(); $('#filterbar').classList.remove('hidden'); $('#ft-q').focus(); },
      KeyR: reveal, KeyS: saveMetaToFile, KeyK: ()=>MENUS[4][1][0][2](),
      BracketLeft: ()=>rotateSel(-90), BracketRight: ()=>rotateSel(90),
      Slash: shortcuts, Enter: ssStart, Comma: preferences,
    };
    if(shift && code==='KeyI'){ e.preventDefault(); openImport('folder'); return; }
    if(shift && code==='KeyE'){ e.preventDefault(); openExport(); return; }
    if(map[code]){ e.preventDefault(); map[code](); }
    return;
  }
  if(e.altKey) return;
  switch(code){
    case 'KeyG': setView('grid'); return;
    case 'KeyE': setView('loupe'); return;
    case 'KeyC': setView('compare'); return;
    case 'KeyN': setView('survey'); return;
    case 'KeyO': setView('people'); return;
    case 'KeyD': setModule('develop'); return;
    case 'KeyP': setFlag(1, adv); return;
    case 'KeyX': setFlag(-1, adv); return;
    case 'KeyU': setFlag(0, adv); return;
    case 'Backquote': toggleFlag(); return;
    case 'KeyB': toggleQuick(); return;
    case 'KeyJ': cycleCellStyle(); return;
    case 'KeyI': S.loupeInfo=!S.loupeInfo; renderLoupe(); renderToolbar(); return;
    case 'KeyL': cycleLights(); return;
    case 'KeyT': togglePanel('tool'); return;
    case 'KeyR': if(S.mod==='develop') devCropToggle(); return;
    case 'KeyZ': if(S.view==='loupe') zoomLoupe(); else if(S.mod==='library'){ setView('loupe'); } return;
    case 'Space': if(S.view==='loupe'){ e.preventDefault(); zoomLoupe(); } return;
    case 'BracketLeft': bumpRating(-1); return;
    case 'BracketRight': bumpRating(1); return;
    case 'Backslash': if(S.mod==='develop') devBefore(); else $('#filterbar').classList.toggle('hidden'); return;
    case 'Tab': e.preventDefault(); shift ? toggleAllPanels() : toggleSides(); return;
    case 'F5': e.preventDefault(); togglePanel('top'); return;
    case 'F6': e.preventDefault(); togglePanel('film'); return;
    case 'F7': e.preventDefault(); togglePanel('left'); return;
    case 'F8': e.preventDefault(); togglePanel('right'); return;
    case 'Delete': case 'Backspace': trashSelected(); return;
    case 'Enter': if(S.mod==='develop' && DEV.crop){ devCropToggle(); return; } if(S.view==='grid' && S.act!=null) setView('loupe'); return;
    case 'Escape':
      if(S.mod==='develop'){ if(DEV.crop){ devCropToggle(); return; } setModule('library'); return; }
      if(S.lights){ S.lights=2; cycleLights(); return; }
      if(S.view!=='grid') setView('grid'); return;
  }
  const digit = /^Digit(\d)$/.exec(code)?.[1] ?? /^Numpad(\d)$/.exec(code)?.[1];
  if(digit!=null){
    const d=+digit;
    if(d<=5) setRating(d, adv);
    else { const l=LABELS[d-6]; if(l) setLabel(l[0], adv); }
    return;
  }
  const rtl = true;   // the grid flows right-to-left
  if(k==='ArrowLeft' || k==='ArrowRight'){
    e.preventDefault(); const fwd = (k==='ArrowLeft')===rtl ? 1 : -1;
    if(S.view==='compare') compareStep(fwd); else moveAct(fwd, shift && S.view==='grid');
  } else if((k==='ArrowDown' || k==='ArrowUp') && S.view==='grid'){ e.preventDefault(); moveAct((k==='ArrowDown'?1:-1)*G.cols, shift); }
});

// Develop: moving to another photo auto-applies pending settings, then opens the new one
let _devPending=null;
function devFollowSelection(){
  if(S.mod!=='develop') return;
  const p=actPhoto(); if(!p || p.id===DEV.id) return;
  clearTimeout(_devPending);
  _devPending=setTimeout(async()=>{ await devLeave(); devOpen(); }, 60);
}
document.addEventListener('mouseup', ()=>{ if(S.mod==='develop') devFollowSelection(); });
document.addEventListener('keyup', ()=>{ if(S.mod==='develop') devFollowSelection(); });

// ---------- boot ----------
(async function boot(){
  await Promise.all([loadCatalog(), loadSide()]);
  S.hist=[S.src]; S.histPos=0;
  await fetchSource();
  setView('grid');
  if(!S.all.length && !S.status.counts.trashed) openImport('folder');
  // resume the activity indicator if a job is already running (e.g. after a reload)
  [['import','ייבוא'],['faces','זיהוי פנים'],['tags','תיוג אוטומטי'],['export','ייצוא']].forEach(async ([n,l])=>{
    try{ const p=await api('/api/job/'+n); if(p && p.state && !['done','error','idle'].includes(p.state)) pollJob(n,l); }catch{}
  });
})();
