/* photag — Lightroom Classic-style Library / Develop / Slideshow.
   One page, no framework. State lives in S; the grid and filmstrip are
   virtualized so catalogs with tens of thousands of photos stay smooth. */
'use strict';

// ---------- helpers ----------
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];
async function api(u, opt){
  const r = await fetch(u, opt); const tg = await r.text(); let d = {};
  try{ d = tg ? JSON.parse(tg) : {}; }catch{}
  if(!r.ok) throw new Error(d.detail && d.detail.key ? t(d.detail.key, d.detail.vars) : t(d.detail || r.statusText));
  return d;
}
const send = (method, u, body) => api(u, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body||{})});
const esc = s => (s??'').toString().replace(/[<>&"']/g, c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));
const num = n => (n||0).toLocaleString(I18N.locale);
const fdate = ts => ts ? new Date(ts*1000).toLocaleString(I18N.locale, {dateStyle:'medium', timeStyle:'short'}) : '—';
const fsize = b => !b ? '—' : b > 1048576 ? (b/1048576).toFixed(1)+' MB' : Math.max(1, Math.round(b/1024))+' KB';
const I = (n, cls='') => `<svg class="ic ${cls}"><use href="#i-${n}"/></svg>`;
const ext = p => (p.filename.split('.').pop()||'').toUpperCase();
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function debounce(fn, ms){ let h; return (...a)=>{ clearTimeout(h); h=setTimeout(()=>fn(...a), ms); }; }
function toast(msg, ms=2600){ const tg=$('#toast'); tg.innerHTML=msg; tg.classList.remove('hidden'); clearTimeout(tg._h); tg._h=setTimeout(()=>tg.classList.add('hidden'), ms); }
window.addEventListener('unhandledrejection', e=>toast(t('שגיאה: ')+esc(e.reason?.message||e.reason)));
const pref = {
  get(k, d){ try{ const v=localStorage.getItem('pm.'+k); return v==null ? d : JSON.parse(v); }catch{ return d; } },
  set(k, v){ try{ localStorage.setItem('pm.'+k, JSON.stringify(v)); }catch{} },
};

const LABELS = [['red',t('אדום'),'6'],['yellow',t('צהוב'),'7'],['green',t('ירוק'),'8'],['blue',t('כחול'),'9'],['purple',t('סגול'),'']];
const LNAME = Object.fromEntries(LABELS.map(([k,n])=>[k,n]));
const lcol = k => `var(--${k})`;
// photo/media urls carry a version so edited photos don't come back stale from the browser cache
const VER = {};
const thumbUrl = id => `/thumb/${id}${VER[id]?'?v='+VER[id]:''}`;
const mediaUrl = id => `/media/${id}${VER[id]?'?v='+VER[id]:''}`;

// ---------- state ----------
const S = {
  mod:'library', view:'grid', prevView:'grid',
  src:{kind:'all', name:t('כל התמונות')}, hist:[], histPos:-1,
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
  ['red',   t('תווית אדומה'),          p=>p.label==='red'],
  ['five',  t('חמישה כוכבים'),         p=>p.rating===5],
  ['picks', t('נבחרו (דגל)'),           p=>p.flag===1],
  ['month', t('החודש האחרון'),          p=>(p.taken_at||0) > Date.now()/1000 - MONTH_S],
  ['video', t('קובצי וידאו'),           p=>!!p.is_video],
  ['nokw',  t('ללא מילות מפתח'),        p=>!p.has_kw],
  ['edited',t('ערוכות'),                p=>!!p.edited],
  ['fav',   t('מועדפים מ‑Google'),      p=>!!p.favorited],
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
    const p = new URLSearchParams({limit:10000000, ...srcParams(src)}); if(q) p.set('q', q);
    rows = (await api('/api/photos?'+p)).map(r=>S.byId.get(r.id) || r);
    if(S.src!==src) return;   // clicked elsewhere meanwhile
  }
  if(src.kind==='smart'){ const f = SMART.find(x=>x[0]===src.id); rows = rows.filter(f[2]); }
  S.base = rows;
  applyFilter();
}

// ---------- filtering + sorting ----------
const yearOf = p => p.taken_at ? String(new Date(p.taken_at*1000).getFullYear()) : t('ללא');
const monthOf = p => p.taken_at ? String(new Date(p.taken_at*1000).getMonth()+1).padStart(2,'0') : t('ללא');
const orientOf = p => !p.width||!p.height ? t('לא ידוע') : p.width>p.height*1.05 ? t('לרוחב') : p.height>p.width*1.05 ? t('לאורך') : t('ריבועי');
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
const META_COLS = [['year',t('תאריך'),yearOf],['month',t('חודש'),monthOf],['ext',t('סוג קובץ'),ext],['orient',t('כיוון'),orientOf]];
function passMeta(p, upto=META_COLS.length){
  for(let i=0;i<upto;i++){ const [k,,fn]=META_COLS[i]; const set=S.F.meta[k]; if(set.size && !set.has(fn(p))) return false; }
  return true;
}
function filterActive(){
  const F=S.F; return !!(F.q || F.flags.size || F.rating || F.labels.size || F.kinds.size || Object.values(F.meta).some(s=>s.size));
}
const SORTS = {
  capture:[t('זמן צילום'), (a,b)=>(a.taken_at||0)-(b.taken_at||0) || a.id-b.id],
  import: [t('סדר הוספה'), (a,b)=>(a.imported_at||0)-(b.imported_at||0) || a.id-b.id],
  name:   [t('שם קובץ'),   (a,b)=>a.filename.localeCompare(b.filename, I18N.locale, {numeric:true})],
  rating: [t('דירוג'),     (a,b)=>(a.rating||0)-(b.rating||0) || (a.taken_at||0)-(b.taken_at||0)],
  pick:   [t('דגל'),       (a,b)=>(a.flag||0)-(b.flag||0) || (a.taken_at||0)-(b.taken_at||0)],
  label:  [t('תווית צבע'), (a,b)=>lrank(a)-lrank(b) || (a.taken_at||0)-(b.taken_at||0)],
  size:   [t('גודל קובץ'), (a,b)=>(a.bytes||0)-(b.bytes||0)],
};
const lrank = p => p.label ? LABELS.findIndex(l=>l[0]===p.label) : 9;
function applyFilter({keepScroll=true}={}){
  let rows = S.base;
  if(S.F.on){
    if(S.F.q && S.F.qf==='name'){ const q=S.F.q.toLowerCase(); rows = rows.filter(p=>p.filename.toLowerCase().includes(q)); }
    rows = rows.filter(p=>passAttr(p) && passMeta(p));
  }
  const cmp = SORTS[S.sort][1];
  rows = rows.slice().sort(S.asc ? cmp : (a,b)=>cmp(b,a));
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
  toast(on ? `${t("נוסף לאוסף המהיר ({0})", [num(ids.length)])}` : t('הוסר מהאוסף המהיר'));
}
async function trashSelected(){
  const ids=targets(); if(!ids.length) return;
  const restore = S.src.kind==='trash';
  await setAttr({trashed: restore?0:1}, ids);
  toast(restore ? `${t("שוחזרו {0} פריטים", [num(ids.length)])}` : `${t("הועברו לאשפה {0} פריטים · נמחקים לצמיתות אחרי {1} יום", [num(ids.length), S.status?.trash_days||60])}`);
}
async function rotateSel(deg){
  const ids = targets().filter(id=>!(S.byId.get(id)||{}).is_video); if(!ids.length) return;
  toast(t('מסובב…'), 1200);
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
  if(!S.all.length && S.src.kind==='all') e.innerHTML = `<b>${t("הקטלוג ריק")}</b><div>${t("ייבאו תמונות מתיקייה, מכרטיס זיכרון או מ‑Google Takeout.")}</div><button class="primary" onclick="openImport()">${t("ייבוא...")}</button>`;
  else if(S.base.length) e.innerHTML = `<b>${t("אין תמונות שתואמות למסנן")}</b><div>${t("{0} תמונות במקור הזה מוסתרות על ידי המסנן.", [num(S.base.length)])}</div><button onclick="clearFilters()">${t("נקה מסנן")}</button>`;
  else e.innerHTML = `<b>${t("אין כאן תמונות")}</b>`;
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
    `<button class="flag ${p.flag===1?'pick':p.flag===-1?'rej':''}" data-a="flag" title="${t("דגל (P / X / U)")}">${I(p.flag===-1?'reject':'flag')}</button>
     <button class="qc ${p.quick?'on':''}" data-a="qc" title="${t("אוסף מהיר (B)")}">${I('dot')}</button>
     <button class="rot l" data-a="rotl" title="${t("סובב שמאלה (Ctrl+[)")}">${I('rotl')}</button>
     <button class="rot r" data-a="rotr" title="${t("סובב ימינה (Ctrl+])")}">${I('rotr')}</button>
     <div class="stars ${r?'':'none'}">${stars}</div>
     ${badges.length?`<div class="badges" style="inset-block-start:${Math.round(by+c._h-18)}px;inset-inline-end:${Math.round(bx+4)}px">${badges.map(b=>`<i>${b}</i>`).join('')}</div>`:''}
     ${p.is_video?`<span class="dur" style="inset-block-start:${Math.round(by+c._h-18)}px;inset-inline-start:${Math.round(bx+4)}px">${I('play')}${t("וידאו")}</span>`:''}`;
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
  const fromStart = RTL ? innerW - (strip.scrollLeft + strip.clientWidth) : strip.scrollLeft;   // offset from the first photo
  const a = Math.max(0, Math.floor(fromStart / FW) - 4), b = Math.min(S.list.length-1, Math.ceil((fromStart+strip.clientWidth)/FW) + 4);
  for(const [i,c] of F_.cells) if(i<a || i>b){ c.remove(); F_.cells.delete(i); }
  const frag=document.createDocumentFragment();
  for(let i=a;i<=b;i++){
    let c=F_.cells.get(i);
    const p=S.list[i];
    if(!c){ c=document.createElement('div'); c.className='fc'; c.style[RTL?'right':'left']=(4+i*FW)+'px'; c.dataset.i=i; c.draggable=true;
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
  const left = RTL ? innerW - (4+i*FW) - FW : 4+i*FW;   // left edge of cell i (the strip scrolls in LTR coords)
  if(left < strip.scrollLeft || left+FW > strip.scrollLeft+strip.clientWidth) strip.scrollLeft = left - strip.clientWidth/2 + FW/2;
}
$('#fs-strip').addEventListener('scroll', ()=>requestAnimationFrame(drawFilm), {passive:true});
$('#fs-strip').addEventListener('wheel', e=>{ if(Math.abs(e.deltaY)>Math.abs(e.deltaX)){ e.preventDefault(); $('#fs-strip').scrollLeft += RTL ? -e.deltaY : e.deltaY; } }, {passive:false});
$('#fs-inner').addEventListener('mousedown', e=>{ const c=e.target.closest('.fc'); if(c && e.button===0) selectClick(+c.dataset.id, e); });
$('#fs-inner').addEventListener('dblclick', e=>{ const c=e.target.closest('.fc'); if(c){ selectOnly(+c.dataset.id); if(S.mod==='library') setView('loupe'); } });
$('#fs-inner').addEventListener('dragstart', e=>{ const c=e.target.closest('.fc'); if(!c) return; const id=+c.dataset.id; if(!S.sel.has(id)) selectOnly(id);
  e.dataTransfer.setData('text/x-pm-ids', JSON.stringify([...S.sel])); });
function renderPath(){
  const n=S.list.length, s=S.sel.size, p=actPhoto();
  $('#fs-path').innerHTML = `<b>${esc(S.src.name)}</b> ${t(": {0} תמונות", [num(n)])}${S.base.length!==n?` ${t("(מתוך {0})", [num(S.base.length)])}`:''}${s?` ${t("/ {0} נבחרו", [num(s)])}`:''}${p?` / <b dir="ltr">${esc(p.filename)}</b>`:''}`;
}
$('#fs-grid').onclick = ()=>{ if(S.mod!=='library') setModule('library'); setView('grid'); };
$('#fs-back').onclick = ()=>{ if(S.histPos>0){ S.histPos--; setSource(S.hist[S.histPos], {push:false}); } };
$('#fs-fwd').onclick = ()=>{ if(S.histPos<S.hist.length-1){ S.histPos++; setSource(S.hist[S.histPos], {push:false}); } };

// quick filter in the filmstrip header (shares state with the Library Filter's attribute tab)
function attrControls(small){
  const F=S.F;
  const flag = (k, ic, tg) => `<button class="tg ${F.flags.has(k)?'on':''}" data-ff="${k}" title="${tg}">${ic}</button>`;
  const stars = `<span class="fstars" data-fr>${[1,2,3,4,5].map(n=>`<b data-n="${n}" class="${n<=F.rating?'on':''}">★</b>`).join('')}</span>`;
  const labs = LABELS.map(([k,n])=>`<button class="tg lab ${F.labels.has(k)?'on':''}" data-fl="${k}" title="${n}"><span class="sw" style="background:${lcol(k)}"></span></button>`).join('')
    + `<button class="tg lab ${F.labels.has('none')?'on':''}" data-fl="none" title="${t("ללא תווית")}"><span class="sw" style="background:#555"></span></button>`;
  const flags = flag('pick', I('flag'), t('נבחרו')) + flag('none', `<svg class="ic"><use href="#i-flag"/></svg>`.replace('ic"','ic" style="opacity:.45"'), t('ללא דגל')) + flag('rej', I('reject'), t('נדחו'));
  if(small) return `<span>${t("סינון:")}</span>${flags}<span class="tb-sep"></span>
    <select class="op" data-fop><option ${F.rop==='>='?'selected':''} value=">=">≥</option><option ${F.rop==='<='?'selected':''} value="<=">≤</option><option ${F.rop==='='?'selected':''} value="=">=</option></select>${stars}<span class="tb-sep"></span>${labs}
    <button class="tg ${F.on?'':'on'}" data-foff title="${t("הפעל/השבת מסננים (Ctrl+L)")}">${F.on?t('מופעל'):t('כבוי')}</button>`;
  return `<div class="attr-grp"><span>${t("דגל")}</span>${flags}</div>
    <div class="attr-grp"><span>${t("דירוג")}</span><select class="op" data-fop><option ${F.rop==='>='?'selected':''} value=">=">≥</option><option ${F.rop==='<='?'selected':''} value="<=">≤</option><option ${F.rop==='='?'selected':''} value="=">=</option></select>${stars}</div>
    <div class="attr-grp"><span>${t("צבע")}</span>${labs}</div>
    <div class="attr-grp"><span>${t("סוג")}</span>
      <button class="tg ${F.kinds.has('photo')?'on':''}" data-fk="photo" title="${t("תמונות")}">${I('photos')}</button>
      <button class="tg ${F.kinds.has('video')?'on':''}" data-fk="video" title="${t("וידאו")}">${I('play')}</button>
      <button class="tg ${F.kinds.has('edited')?'on':''}" data-fk="edited" title="${t("ערוכות")}">${I('dev')}</button></div>`;
}
function renderFsFilter(){ $('#fs-filter').innerHTML = attrControls(true); }
function bindAttrControls(root){
  root.addEventListener('click', e=>{
    const tg=e.target.closest('[data-ff],[data-fl],[data-fk],[data-n],[data-foff]'); if(!tg) return;
    const F=S.F, tog=(set,k)=>set.has(k)?set.delete(k):set.add(k);
    if(tg.dataset.ff) tog(F.flags, tg.dataset.ff);
    else if(tg.dataset.fl) tog(F.labels, tg.dataset.fl);
    else if(tg.dataset.fk) tog(F.kinds, tg.dataset.fk);
    else if(tg.dataset.n){ const n=+tg.dataset.n; F.rating = F.rating===n ? 0 : n; }
    else if('foff' in tg.dataset){ F.on=!F.on; }
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
  $('#fb-state').innerHTML = filterActive() ? (S.F.on ? ("<b>"+t("מסנן פעיל")+"</b>") : t('המסנן כבוי')) : '';
}
$('#ft-q').addEventListener('input', debounce(()=>{ S.F.q=$('#ft-q').value.trim(); if(S.F.qf!=='name') fetchSource(); else applyFilter(); }, 300));
$('#ft-field').onchange = ()=>{ S.F.qf=$('#ft-field').value; if(S.F.q) fetchSource(); };
function renderMetaBrowser(){
  const base = S.base;
  $('#fb-meta').innerHTML = META_COLS.map(([k,title,fn], ci)=>{
    const counts = new Map();
    for(const p of base) if(passMeta(p, ci)){ const v=fn(p); counts.set(v, (counts.get(v)||0)+1); }
    const vals = [...counts.entries()].sort((a,b)=> k==='year'||k==='month' ? b[0].localeCompare(a[0]) : b[1]-a[1]);
    const set=S.F.meta[k];
    const label = v => k==='month' && v!==t('ללא') ? new Date(2000, +v-1, 1).toLocaleDateString(I18N.locale,{month:'long'}) : v;
    return `<div class="mcol"><h4>${title}</h4><div class="mlist">
      <div class="row ${set.size?'':'on'}" data-mk="${k}" data-mv=""><span class="nm">${t("הכול ({0})", [vals.length])}</span><span class="n">${num([...counts.values()].reduce((a,b)=>a+b,0))}</span></div>
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
    row('all', I('photos'), t('כל התמונות'), S.all.length) +
    row('quick', I('coll'), t('אוסף מהיר +'), q) +
    (st.last_import ? row('prev', I('import'), t('ייבוא קודם'), prev) : '') +
    row('trash', I('trash'), t('אשפה'), st.counts.trashed);
  markSourceRows();
}
function renderFolders(){
  const f=S.folders;
  $('#p-folders').innerHTML = `<div class="vol" title="${esc(f.root)}">${I('drive')}<span>${t("ספרייה")}</span><span class="path">${esc(f.root)}</span></div>` +
    (f.folders.map(x=>row('folder:'+x.name, I('folder'), x.name || t('(שורש)'), x.n, '', 'ind')).join('') || ("<div class=\"hint\">"+t("אין תיקיות עדיין")+"</div>"));
  markSourceRows();
}
const OPEN_SETS = new Set(pref.get('openSets', ['smart','album']));
function renderColls(){
  const al=S.albums;
  const set = (key, title, items) => {
    const open=OPEN_SETS.has(key);
    return `<div class="row set" data-set="${key}"><span class="tw">${open?'▼':'◀'}</span>${I('set')}<span class="nm">${title}</span></div>` + (open ? items : '');
  };
  const coll = a => row('album:'+a.id, I('coll'), a.name, a.n, `<button class="x" data-del="${a.id}" title="${t("מחק אוסף")}">${I('close')}</button>`, 'ind');
  const smart = SMART.map(([k,n,f])=>row('smart:'+k, I('smart'), n, S.all.filter(f).length, '', 'ind')).join('');
  const people = S.people.map(p=>row('person:'+p.id, I('people'), p.name, (p.face_photos||0)+(p.tag_photos||0), '', 'ind')).join('');
  $('#p-colls').innerHTML =
    set('smart', t('אוספים חכמים'), smart) +
    set('album', t('אוספים'), al.filter(a=>a.kind==='album').map(coll).join('') || ("<div class=\"hint\">"+t("גררו תמונות לכאן אחרי יצירת אוסף")+"</div>")) +
    (al.some(a=>a.kind==='people-share') ? set('shared', t('אלבומים משותפים'), al.filter(a=>a.kind==='people-share').map(coll).join('')) : '') +
    (al.some(a=>a.kind==='year') ? set('year', t('לפי שנה (Google)'), al.filter(a=>a.kind==='year').map(coll).join('')) : '') +
    (S.people.length ? set('people', t('אנשים'), people) : '');
  markSourceRows();
}
function markSourceRows(){ const k=srcKey(S.src); $$('#left [data-src]').forEach(r=>r.classList.toggle('on', r.dataset.src===k)); }
function srcFromKey(key){
  const [kind, id] = key.split(/:(.*)/s);
  const name = {all:t('כל התמונות'), quick:t('אוסף מהיר'), prev:t('ייבוא קודם'), trash:t('אשפה')}[kind]
    || (kind==='folder' ? (id||t('(שורש)')) : kind==='smart' ? SMART.find(s=>s[0]===id)[1]
      : kind==='album' ? S.albums.find(a=>a.id==id)?.name : kind==='person' ? S.people.find(p=>p.id==id)?.name : '');
  return {kind, id: id===undefined ? null : (['album','person','tag','cluster'].includes(kind) ? +id : id), name};
}
$('#left').addEventListener('click', async e=>{
  const del=e.target.closest('[data-del]');
  if(del){ e.stopPropagation(); const a=S.albums.find(x=>x.id==del.dataset.del);
    if(!await confirmBox(`${t("למחוק את האוסף „{0}\"?", [esc(a.name)])}`, t('התמונות עצמן יישארו בקטלוג.'), t('מחק'))) return;
    await send('DELETE', '/api/album/'+a.id); if(S.src.kind==='album' && S.src.id===a.id) setSource(srcFromKey('all')); loadSide(); return; }
  const st=e.target.closest('[data-set]');
  if(st){ const k=st.dataset.set; OPEN_SETS.has(k)?OPEN_SETS.delete(k):OPEN_SETS.add(k); pref.set('openSets',[...OPEN_SETS]); renderColls(); return; }
  const r=e.target.closest('[data-src]'); if(r){ if(S.mod!=='library') setModule('library'); setSource(srcFromKey(r.dataset.src)); }
});
$('#left').addEventListener('dblclick', async e=>{
  const r=e.target.closest('[data-src^="album:"]'); if(!r) return;
  const a=S.albums.find(x=>'album:'+x.id===r.dataset.src);
  const n=await promptBox(t('שינוי שם אוסף'), a.name); if(!n || n===a.name) return;
  await send('POST', `/api/album/${a.id}/rename`, {name:n}); await loadSide(); if(S.src.kind==='album'&&S.src.id===a.id){ S.src.name=n; renderPath(); }
});
// drag photos onto a collection / the Quick Collection
$('#left').addEventListener('dragover', e=>{ const r=e.target.closest('[data-src^="album:"],[data-src="quick"]'); if(r && e.dataTransfer.types.includes('text/x-pm-ids')){ e.preventDefault(); $$('#left .drop').forEach(x=>x!==r&&x.classList.remove('drop')); r.classList.add('drop'); } });
$('#left').addEventListener('dragleave', e=>{ const r=e.target.closest('.drop'); if(r && !r.contains(e.relatedTarget)) r.classList.remove('drop'); });
$('#left').addEventListener('drop', async e=>{
  const r=e.target.closest('[data-src^="album:"],[data-src="quick"]'); $$('#left .drop').forEach(x=>x.classList.remove('drop')); if(!r) return;
  e.preventDefault(); const ids=JSON.parse(e.dataTransfer.getData('text/x-pm-ids')||'[]'); if(!ids.length) return;
  if(r.dataset.src==='quick'){ await setAttr({quick:1}, ids); toast(`${t("נוספו {0} לאוסף המהיר", [num(ids.length)])}`); return; }
  const aid=+r.dataset.src.split(':')[1];
  await send('POST', `/api/album/${aid}/add`, {ids}); toast(`${t("נוספו {0} תמונות ל„{1}\"", [num(ids.length), esc(S.albums.find(a=>a.id===aid)?.name)])}`); loadSide();
});
async function newCollection(){
  const ids=[...S.sel];
  const n=await promptBox(t('צור אוסף'), '', ids.length?`<label class="check" style="padding:0"><input type="checkbox" id="nc-sel" checked> ${t("כלול את התמונות שנבחרו ({0})", [num(ids.length)])}</label>`:'');
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
  if(v!=='loupe'){ closeLoupeMedia(); }
  if(v==='grid'){ layoutGrid(true); scrollToAct(); $('#v-grid').focus({preventScroll:true}); if(!S.list.length){ $('#v-empty').classList.remove('hidden'); renderEmpty(); } }
  if(v==='loupe') renderLoupe();
  if(v==='compare') renderCompare();
  if(v==='survey') renderSurvey();
  if(v==='people') renderPeople();
  renderToolbar(); renderRight(); updateNavigator();
}
// ---------- video player: custom controls over <video> ----------
// Decoding is the browser's (H.264/MP4 and friends); everything you see and use is ours:
// seek bar with buffered range and hover preview, ±10 s, volume, speed, loop, frame step,
// picture-in-picture, fullscreen, and "open in VLC" for formats we can't play.
const VP = {el:null, v:null, pv:null, frame:1/30, raf:0, idleT:0, act:null};
const RATES = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2];
const mmss = s => { s = Math.max(0, Math.floor(isFinite(s) ? s : 0)); const h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = String(s%60).padStart(2,'0');
  return h ? `${h}:${String(m).padStart(2,'0')}:${x}` : `${m}:${x}`; };

function vpDestroy(){
  if(!VP.el) return;
  if(document.fullscreenElement===VP.el) document.exitFullscreen().catch(()=>{});
  try{ VP.v.pause(); VP.v.removeAttribute('src'); VP.v.load(); }catch{}
  if(VP.pv){ VP.pv.removeAttribute('src'); VP.pv.load(); VP.pv = null; }
  cancelAnimationFrame(VP.raf); clearTimeout(VP.idleT);
  VP.el.remove(); VP.el = VP.v = VP.act = null;
}
function closeLoupeMedia(){ vpDestroy(); const m=$('#loupe-media'); m.innerHTML=''; m.dataset.id=''; }
const openExternal = id => send('POST', `/api/photo/${id}/open-external`)
  .then(r=>{ if(r.player==='default') toast(t('נפתח בנגן ברירת המחדל של המערכת')); });

function mountPlayer(m, p){
  vpDestroy();
  const el = document.createElement('div'); el.className = 'vp paused'; el.dir = 'ltr';
  const b = (k, inner, title, cls='') => `<button data-vp="${k}" class="${cls}" title="${esc(title)}" aria-label="${esc(title)}">${inner}</button>`;
  const volTip = t('עוצמת קול (↑ ↓)');
  el.innerHTML = `
    <video src="${mediaUrl(p.id)}" playsinline preload="auto" autoplay></video>
    <div class="vp-err hidden"><span>${t('הפורמט הזה לא מתנגן בתוך האפליקציה.')}</span><button class="primary" data-vp="ext">${t('פתח בנגן חיצוני')}</button></div>
    <div class="vp-bar">
      <div class="vp-seek"><div class="vp-buf"></div><div class="vp-played"></div><div class="vp-knob"></div>
        <div class="vp-tip hidden"><canvas width="160" height="90"></canvas><span></span></div></div>
      <div class="vp-row">
        ${b('play', I('play'), t('הפעל / השהה (רווח)'))}
        ${b('back', I('rotl')+'<i class="n10">10</i>', t('10 שניות אחורה'))}
        ${b('fwd', I('rotr')+'<i class="n10">10</i>', t('10 שניות קדימה'))}
        <span class="vp-time"><b data-vp="cur">0:00</b> / <span data-vp="dur">0:00</span></span>
        <span class="spacer"></span>
        ${b('mute', I('vol'), t('השתק (M)'))}<input class="vp-vol" type="range" min="0" max="1" step="0.02" title="${esc(volTip)}" aria-label="${esc(volTip)}">
        ${b('rate', '1×', t('מהירות ניגון'), 'vp-rate')}
        ${b('loop', I('loop'), t('חזרה על הסרטון'))}
        ${document.pictureInPictureEnabled ? b('pip', I('pip'), t('תמונה בתוך תמונה')) : ''}
        ${b('compress', I('compress'), t('דחוס את הסרטון (HandBrake)'))}
        ${b('ext', I('external'), t('פתח בנגן חיצוני (VLC אם מותקן)'))}
        ${b('full', I('full'), t('מסך מלא (F)'))}
      </div>
    </div>`;
  m.appendChild(el);
  const q = s => el.querySelector(s), v = q('video'), btn = k => q(`button[data-vp=${k}]`);
  const seek = q('.vp-seek'), played = q('.vp-played'), buf = q('.vp-buf'), knob = q('.vp-knob');
  const cur = q('[data-vp=cur]'), dur = q('[data-vp=dur]'), vol = q('.vp-vol'), tip = q('.vp-tip');
  VP.el = el; VP.v = v; VP.frame = 1/30;
  v.volume = pref.get('vol', 1); v.muted = pref.get('muted', false); v.playbackRate = pref.get('rate', 1);

  // --- state -> UI
  const pct = f => (clamp(f, 0, 1) * 100) + '%';
  const paint = ()=>{ const f = v.duration ? v.currentTime / v.duration : 0; played.style.width = pct(f); knob.style.left = pct(f); cur.textContent = mmss(v.currentTime); };
  const paintBuf = ()=>{ let end = 0; for(let i=0;i<v.buffered.length;i++) if(v.buffered.start(i) <= v.currentTime + .5) end = Math.max(end, v.buffered.end(i));
    buf.style.width = pct(v.duration ? end / v.duration : 0); };
  const tick = ()=>{ if(VP.el!==el) return; paint(); if(!v.paused && !v.ended) VP.raf = requestAnimationFrame(tick); };
  const syncPlay = ()=>{ btn('play').innerHTML = I(v.paused ? 'play' : 'pause'); el.classList.toggle('paused', v.paused); };
  const syncVol = ()=>{ btn('mute').innerHTML = I(v.muted || v.volume===0 ? 'mute' : 'vol'); vol.value = v.muted ? 0 : v.volume; };
  const syncRate = ()=>{ btn('rate').textContent = v.playbackRate + '×'; };
  const idleSoon = ()=>{ clearTimeout(VP.idleT); el.classList.remove('idle');
    if(!v.paused) VP.idleT = setTimeout(()=>{ if(!v.paused && !q('.vp-menu')) el.classList.add('idle'); }, 2500); };
  v.addEventListener('loadedmetadata', ()=>{ dur.textContent = mmss(v.duration); paint(); paintBuf(); });
  v.addEventListener('durationchange', ()=>{ dur.textContent = mmss(v.duration); });
  v.addEventListener('timeupdate', ()=>{ paint(); paintBuf(); });
  v.addEventListener('progress', paintBuf);
  v.addEventListener('play', ()=>{ syncPlay(); cancelAnimationFrame(VP.raf); tick(); idleSoon(); });
  v.addEventListener('pause', ()=>{ syncPlay(); idleSoon(); });
  v.addEventListener('ended', ()=>{ syncPlay(); idleSoon(); });
  v.addEventListener('volumechange', ()=>{ syncVol(); pref.set('vol', v.volume); pref.set('muted', v.muted); });
  v.addEventListener('ratechange', ()=>{ syncRate(); pref.set('rate', v.playbackRate); });
  v.addEventListener('error', ()=>q('.vp-err').classList.remove('hidden'));
  el.addEventListener('mousemove', idleSoon); el.addEventListener('mousedown', idleSoon);
  el.addEventListener('mouseleave', ()=>{ if(!v.paused) el.classList.add('idle'); });
  syncPlay(); syncVol(); syncRate();

  // real frame duration (for frame stepping), measured from the first playing frames
  if('requestVideoFrameCallback' in v){
    const ds = []; let last = null;
    const cb = (_n, meta)=>{ if(VP.el!==el) return;
      if(last!=null && meta.mediaTime>last) ds.push(meta.mediaTime - last); last = meta.mediaTime;
      if(ds.length < 12) v.requestVideoFrameCallback(cb);
      else { ds.sort((a,c)=>a-c); const med = ds[ds.length>>1]; if(med>0.004 && med<0.2) VP.frame = med; } };
    v.addEventListener('play', ()=>v.requestVideoFrameCallback(cb), {once:true});
  }

  // --- actions (also used by the keyboard)
  const toggle = ()=>{ if(v.paused || v.ended) v.play().catch(()=>{}); else v.pause(); };
  const rel = s => { v.currentTime = clamp(v.currentTime + s, 0, v.duration || 0); paint(); };
  const step = d => { v.pause(); v.currentTime = clamp(v.currentTime + d*VP.frame, 0, v.duration || 0); paint(); };
  const setVol = d => { v.muted = false; v.volume = clamp(Math.round((v.volume + d)*100)/100, 0, 1); };
  const mute = ()=>{ v.muted = !v.muted; if(!v.muted && v.volume===0) v.volume = .5; };
  const full = ()=>{ if(document.fullscreenElement) document.exitFullscreen().catch(()=>{}); else el.requestFullscreen?.().catch(()=>{}); };
  const rate = d => { const i = RATES.indexOf(v.playbackRate), j = clamp((i<0 ? RATES.indexOf(1) : i) + d, 0, RATES.length-1);
    v.playbackRate = RATES[j]; toast(RATES[j] + '×', 900); };
  VP.act = {toggle, rel, step, vol:setVol, mute, full, rate};

  const rateMenu = ()=>{ const old = q('.vp-menu'); if(old){ old.remove(); return; }
    const menu = document.createElement('div'); menu.className = 'vp-menu';
    menu.innerHTML = RATES.map(r=>`<div data-r="${r}" class="${r===v.playbackRate?'on':''}">${r}×</div>`).join('');
    const bt = btn('rate'); menu.style.left = (bt.offsetLeft + bt.offsetWidth/2) + 'px';
    menu.addEventListener('click', ev=>{ const r = ev.target.closest('[data-r]'); if(r) v.playbackRate = +r.dataset.r; menu.remove(); idleSoon(); });
    q('.vp-row').appendChild(menu); };

  el.addEventListener('click', e=>{
    const bt = e.target.closest('button[data-vp]');
    if(!e.target.closest('.vp-menu') && !(bt && bt.dataset.vp==='rate')) q('.vp-menu')?.remove();
    if(bt){
      switch(bt.dataset.vp){
        case 'play': toggle(); break;            case 'back': rel(-10); break;     case 'fwd': rel(10); break;
        case 'mute': mute(); break;              case 'rate': rateMenu(); break;   case 'full': full(); break;
        case 'ext': openExternal(p.id); break;
        case 'compress': v.pause(); compressDialog(p.id); break;
        case 'loop': v.loop = !v.loop; bt.classList.toggle('on', v.loop); break;
        case 'pip': if(document.pictureInPictureElement) document.exitPictureInPicture(); else v.requestPictureInPicture().catch(()=>{}); break;
      }
      bt.blur();   // keep Space for play/pause instead of re-clicking the focused button
      return;
    }
    if(e.target===v) toggle();
  });
  el.addEventListener('dblclick', e=>{ if(e.target===v) full(); });
  vol.addEventListener('input', ()=>{ v.muted = false; v.volume = +vol.value; });

  // --- seek bar: click / drag to scrub, hover for a time + frame preview
  const ratioAt = e => { const r = seek.getBoundingClientRect(); return clamp((e.clientX - r.left) / r.width, 0, 1); };
  const seekTo = f => { if(v.duration) v.currentTime = f * v.duration; paint(); };
  let scrubbing = false, pvWant = null, pvBusy = false;
  const pvGo = ()=>{ const pv = VP.pv;
    if(!pv || pv.readyState < 1 || Math.abs(pv.currentTime - pvWant) < .05){ pvBusy = false; return; }
    pvBusy = true; pv.currentTime = pvWant; };
  const preview = tm => {
    if(!VP.pv){
      const pv = document.createElement('video'); pv.muted = true; pv.preload = 'auto'; pv.src = v.currentSrc || v.src;
      pv.addEventListener('seeked', ()=>{ const c = tip.querySelector('canvas');
        c.height = Math.round(c.width * (pv.videoHeight||9) / (pv.videoWidth||16));
        c.getContext('2d').drawImage(pv, 0, 0, c.width, c.height);
        pvBusy = false; if(pvWant!=null && Math.abs(pv.currentTime - pvWant) > .25) pvGo(); });
      VP.pv = pv;
    }
    pvWant = tm; if(!pvBusy) pvGo();
  };
  const hover = f => { if(!v.duration) return;
    tip.classList.remove('hidden');
    const w = seek.getBoundingClientRect().width, tw = tip.offsetWidth || 170;
    tip.style.left = clamp(f * w, tw/2, w - tw/2) + 'px';
    tip.querySelector('span').textContent = mmss(f * v.duration);
    preview(f * v.duration); };
  seek.addEventListener('pointerdown', e=>{ if(e.button!==0) return; scrubbing = true; el.classList.add('scrub'); seek.setPointerCapture(e.pointerId); seekTo(ratioAt(e)); });
  seek.addEventListener('pointermove', e=>{ const f = ratioAt(e); if(scrubbing) seekTo(f); hover(f); });
  seek.addEventListener('pointerup', ()=>{ scrubbing = false; el.classList.remove('scrub'); });
  seek.addEventListener('pointerleave', ()=>{ if(!scrubbing) tip.classList.add('hidden'); });
}
// keyboard while a video is open in the Loupe; returns true when the key was ours
function vpKey(e){
  if(!VP.act || S.view!=='loupe') return false;
  const A = VP.act, c = e.code, sh = e.shiftKey;
  if(c==='Space' || c==='KeyK') A.toggle();
  else if(sh && (c==='ArrowLeft' || c==='ArrowRight')) A.rel(c==='ArrowLeft' ? -10 : 10);
  else if(c==='Comma' || c==='Period') { if(sh) A.rate(c==='Comma' ? -1 : 1); else A.step(c==='Comma' ? -1 : 1); }
  else if(c==='ArrowUp' || c==='ArrowDown') A.vol(c==='ArrowUp' ? .1 : -.1);
  else if(c==='KeyM') A.mute();
  else if(c==='KeyF') A.full();
  else return false;
  e.preventDefault(); return true;
}

function renderLoupe(){
  const p=actPhoto(), m=$('#loupe-media');
  if(!p){ m.innerHTML=''; $('#loupe-info').innerHTML=''; return; }
  if(m.dataset.id!=String(p.id) || m.dataset.v!=String(VER[p.id]||'')){
    m.dataset.id=p.id; m.dataset.v=VER[p.id]||''; m.classList.remove('zoom');
    vpDestroy();
    if(p.is_video){ m.innerHTML=''; mountPlayer(m, p); histoFromThumb(); }
    else { m.innerHTML = `<img src="${mediaUrl(p.id)}" alt="" draggable="false">`; const img=m.querySelector('img'); if(img) img.onload=()=>drawHisto(img); }
  }
  const info=$('#loupe-info');
  info.classList.toggle('hidden', !S.loupeInfo);
  info.innerHTML = `<b>${esc(p.filename)}</b><span>${fdate(p.taken_at)}</span><br><span dir="ltr">${p.width&&p.height?p.width+' × '+p.height:''}  ${fsize(p.bytes)}</span>${p.flag===-1?("<br><span>"+t("נדחתה")+"</span>"):''}`;
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
  const sx=e.clientX, sy=e.clientY, l=m.scrollLeft, tg=m.scrollTop; e.preventDefault();
  const mv=ev=>{ if(Math.abs(ev.clientX-sx)+Math.abs(ev.clientY-sy)>3) m._dragged=true; m.scrollLeft=l-(ev.clientX-sx); m.scrollTop=tg-(ev.clientY-sy); };
  const up=()=>{ removeEventListener('mousemove',mv); removeEventListener('mouseup',up); setTimeout(()=>m._dragged=false); };
  addEventListener('mousemove',mv); addEventListener('mouseup',up);
});
$('#v-loupe').addEventListener('dblclick', e=>{ if(e.target.closest('.vp')) return; if(!$('#loupe-media').classList.contains('zoom')) setView('grid'); });

let CMP_CAND=null;
function renderCompare(){
  const a=actPhoto(); if(!a){ $('#v-compare').innerHTML=''; return; }
  let b = CMP_CAND && CMP_CAND!==a.id && S.idx.has(CMP_CAND) ? CMP_CAND : ([...S.sel].find(id=>id!==a.id) ?? S.list[(S.idx.get(a.id)+1)%S.list.length]?.id);
  CMP_CAND=b;
  const pane=(p,lab,cls)=>p?`<div class="cmp ${cls}" data-id="${p.id}"><span class="lab">${lab} · <bdi>${esc(p.filename)}</bdi> ${p.rating?'★'.repeat(p.rating):''}</span><img src="${mediaUrl(p.id)}" alt=""></div>`:'<div class="cmp"></div>';
  $('#v-compare').innerHTML = pane(a,t('בחירה'),'sel') + pane(S.byId.get(b)||S.base.find(x=>x.id===b),t('מועמד'),'');
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
    return `<div class="sv ${id===S.act?'act':''}" data-id="${id}" style="width:${bw}px;height:${bh}px"><img src="${bw>300?mediaUrl(id):thumbUrl(id)}" alt=""><button class="x" data-x title="${t("הסר מהסקירה")}">${I('close')}</button></div>`; }).join('');
}
$('#v-survey').addEventListener('click', e=>{ const c=e.target.closest('.sv'); if(!c) return; const id=+c.dataset.id;
  if(e.target.closest('[data-x]')){ S.sel.delete(id); if(S.act===id) S.act=[...S.sel][0]??null; onSelChange(); return; }
  S.act=id; onSelChange(); });

async function renderPeople(){
  const el=$('#v-people'); el.innerHTML=("<div class=\"hint\">"+t("טוען…")+"</div>");
  const [people, clusters] = await Promise.all([api('/api/people'), api('/api/clusters')]);
  S.people=people;
  const face = src => src ? `<img loading="lazy" src="${src}" alt="">` : I('people');
  el.innerHTML = `<h2>${t("אנשים עם שם")} <span>${num(people.length)}</span></h2>
    <div class="pgrid">${people.map(p=>`<div class="pc" data-person="${p.id}"><div class="face">${face(p.cover_face?'/face/'+p.cover_face:p.cover_photo?thumbUrl(p.cover_photo):'')}</div>
      <div class="nm" title="${t("לחיצה כפולה לשינוי שם")}">${esc(p.name)}</div><div class="ct">${num((p.face_photos||0)+(p.tag_photos||0))}</div></div>`).join('') || ("<div class=\"hint\">"+t("עוד אין אנשים עם שם.")+"</div>")}</div>
    <h2>${t("אנשים ללא שם")} <span>${num(clusters.length)}</span></h2>
    ${clusters.length ? `<div class="pgrid">${clusters.map(c=>`<div class="pc" data-cluster="${c.id}"><div class="face">${face(c.cover_face?'/face/'+c.cover_face:'')}</div>
      <input placeholder="?" data-name-cluster="${c.id}" title="${t("הקלידו שם ולחצו Enter")}"><div class="ct">${num(c.n)}</div></div>`).join('')}</div>`
      : `<div class="hint">${S.status?.counts.faces ? t('כל קבוצות הפנים קיבלו שם.') : t('עוד לא הורץ זיהוי פנים. ספרייה ← זיהוי פנים.')}</div>`}`;
}
$('#v-people').addEventListener('click', e=>{
  const face=e.target.closest('.face'); if(!face) return;
  const pc=face.closest('.pc');
  if(pc.dataset.person){ const p=S.people.find(x=>x.id==pc.dataset.person); setSource({kind:'person', id:p.id, name:p.name}); setView('grid'); }
  else setSource({kind:'cluster', id:+pc.dataset.cluster, name:t('אדם ללא שם')}).then(()=>setView('grid'));
});
$('#v-people').addEventListener('dblclick', async e=>{
  const nm=e.target.closest('.nm'); if(!nm) return; const id=+nm.closest('.pc').dataset.person, p=S.people.find(x=>x.id===id);
  const n=await promptBox(t('שינוי שם'), p.name); if(!n) return;
  await send('POST', `/api/person/${id}/rename`, {name:n}); toast(t('השם עודכן')); await loadSide(); renderPeople();
});
$('#v-people').addEventListener('keydown', async e=>{
  const inp=e.target.closest('[data-name-cluster]'); if(!inp || e.key!=='Enter') return;
  const n=inp.value.trim(); if(!n) return;
  await send('POST', `/api/cluster/${inp.dataset.nameCluster}/name`, {name:n}); toast(`${t("נקרא „{0}\"", [esc(n)])}`); await loadSide(); renderPeople();
});

// ---------- toolbar ----------
function tbViews(){
  const b=(v,ic,tg)=>`<button class="tb-btn ${S.view===v?'on':''}" data-view="${v}" title="${tg}">${I(ic)}</button>`;
  return `<div class="tb-grp">${b('grid','grid',t('תצוגת רשת (G)'))}${b('loupe','loupe',t('זכוכית מגדלת (E)'))}${b('compare','compare',t('השוואה (C)'))}${b('survey','survey',t('סקירה (N)'))}${b('people','face',t('אנשים (O)'))}</div>`;
}
function tbAttrs(){
  const p=actPhoto(), r=p?.rating||0;
  return `<div class="tb-grp"><button class="tb-btn tb-flag pick ${p?.flag===1?'on':''}" data-t="pick" title="${t("סמן כנבחרת (P)")}">${I('flag')}</button>
    <button class="tb-btn tb-flag rej ${p?.flag===-1?'on':''}" data-t="rej" title="${t("סמן כנדחית (X)")}">${I('reject')}</button></div>
    <span class="tb-sep"></span><span class="tb-stars">${[1,2,3,4,5].map(n=>`<b data-star="${n}" class="${n<=r?'on':''}" title="${t("{0} (מקש {1})", [n, n])}">★</b>`).join('')}</span>
    <span class="tb-sep"></span><span class="tb-labs">${LABELS.map(([k,n,key])=>`<button data-lab="${k}" class="${p?.label===k?'on':''}" style="background:${lcol(k)}" title="${n}${key?` (${key})`:''}"></button>`).join('')}</span>
    <span class="tb-sep"></span><div class="tb-grp"><button class="tb-btn" data-t="rotl" title="${t("סובב שמאלה (Ctrl+[)")}">${I('rotl')}</button><button class="tb-btn" data-t="rotr" title="${t("סובב ימינה (Ctrl+])")}">${I('rotr')}</button></div>`;
}
function renderToolbar(){
  const tb=$('#toolbar');
  if(S.mod==='develop'){ const p=actPhoto();
    tb.innerHTML = `<button class="tb-btn ${DEV.before?'on':''}" data-t="before" title="${t("לפני/אחרי (\\)")}">${t("לפני / אחרי")}</button><span class="tb-sep"></span>
      <button class="tb-btn ${DEV.crop?'on':''}" data-t="crop" title="${t("חיתוך (R)")}">${I('crop')}</button><span class="spacer"></span>
      <span class="tb-info">${p?`<bdi>${esc(p.filename)}</bdi>`:''}${DEV.dirty?t(' · שינויים שלא הוחלו'):''}</span>`; return; }
  let h = tbViews() + '<span class="tb-sep"></span>';
  if(S.view==='grid') h += `<div class="tb-sort"><span class="tb-lbl">${t("מיון:")}</span><button class="tb-btn" data-t="asc" title="${S.asc?t('סדר עולה'):t('סדר יורד')}" style="${S.asc?'':'transform:scaleY(-1)'}">${I('sort')}</button>
      <select data-t="sort">${Object.entries(SORTS).map(([k,[n]])=>`<option value="${k}" ${k===S.sort?'selected':''}>${n}</option>`).join('')}</select></div><span class="tb-sep"></span>` + tbAttrs() +
      `<label class="tb-size"><span>${t("תמונות ממוזערות")}</span><input type="range" data-t="size" min="110" max="420" step="10" value="${S.cellsz}"></label>`;
  else if(S.view==='loupe') h += tbAttrs() + `<span class="spacer"></span><button class="tb-btn ${S.loupeInfo?'on':''}" data-t="info" title="${t("מידע (I)")}">${t("מידע")}</button>`;
  else if(S.view==='compare') h += tbAttrs() + `<span class="spacer"></span><button class="tb-btn" data-t="swap" title="${t("החלף בחירה ומועמד")}">${t("החלף")}</button><button class="tb-btn" data-t="done" title="${t("סיום (Esc)")}">${t("סיום")}</button>`;
  else if(S.view==='survey') h += tbAttrs() + `<span class="spacer"></span><span class="tb-info">${t("{0} תמונות בסקירה", [num(S.sel.size)])}</span>`;
  else h += `<span class="spacer"></span><span class="tb-info">${t("הקלידו שם מתחת לפנים כדי לתת להן שם")}</span>`;
  tb.innerHTML = h;
}
$('#toolbar').addEventListener('click', e=>{
  const v=e.target.closest('[data-view]'); if(v){ setView(v.dataset.view); return; }
  const s=e.target.closest('[data-star]'); if(s){ const n=+s.dataset.star, p=actPhoto(); setRating(p&&p.rating===n?0:n); return; }
  const l=e.target.closest('[data-lab]'); if(l){ setLabel(l.dataset.lab); return; }
  const tg=e.target.closest('[data-t]')?.dataset.t; if(!tg) return;
  if(tg==='pick'){ const p=actPhoto(); setFlag(p?.flag===1?0:1); }
  else if(tg==='rej'){ const p=actPhoto(); setFlag(p?.flag===-1?0:-1); }
  else if(tg==='rotl') rotateSel(-90); else if(tg==='rotr') rotateSel(90);
  else if(tg==='asc'){ S.asc=!S.asc; pref.set('asc',S.asc); applyFilter(); }
  else if(tg==='info'){ S.loupeInfo=!S.loupeInfo; renderLoupe(); renderToolbar(); }
  else if(tg==='swap') compareSwap(); else if(tg==='done') setView('loupe');
  else if(tg==='before') devBefore(); else if(tg==='crop') devCropToggle();
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
  const [detail, kws] = await Promise.all([
    p ? api('/api/photo/'+p.id) : null,
    ids.length ? send('POST','/api/keywords',{ids}) : [],
  ]);
  if(p && actPhoto()?.id!==p.id) return;
  DETAIL=detail;
  renderKeywording(ids, kws); renderKwList(kws, ids.length); renderMeta(ids, detail);
}, 90);

function renderKeywording(ids, kws){
  const el=$('#p-kwing');
  if(!ids.length){ el.innerHTML=("<div class=\"hint\">"+t("בחרו תמונות כדי לתייג אותן.")+"</div>"); return; }
  const txt = kws.map(k=>k.name + (k.n<ids.length?' *':'')).join(', ');
  const top = S.tags.slice(0,30).map(tg=>tg.name);
  const sug = [...new Set([...S.recentKw, ...top])].filter(n=>!kws.some(k=>k.name===n && k.n===ids.length)).slice(0,9);
  el.innerHTML = `<div class="lbl-sub">${t("מילות מפתח")}${ids.length>1?` ${t("· {0} תמונות (* = רק בחלק מהן)", [num(ids.length)])}`:''}</div>
    <label class="kwbox"><textarea id="kw-text" spellcheck="false" placeholder="${t("הקלידו מילות מפתח מופרדות בפסיקים")}">${esc(txt)}</textarea></label>
    <label class="kwadd"><input id="kw-add" placeholder="${t("לחצו כאן כדי להוסיף מילות מפתח")}"></label>
    <div class="kwai"><button id="kw-ai" title="${esc(t('תייג בעזרת AI את התמונות שנבחרו'))}">${I('spark')}${t('תיוג AI')}</button><button id="kw-ai-cfg" class="cfg" title="${esc(t('הגדרות תיוג AI'))}" aria-label="${esc(t('הגדרות תיוג AI'))}">${I('dev')}</button></div>
    <div class="lbl-sub" style="padding-top:8px">${t("הצעות למילות מפתח")}</div>
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
$('#p-kwing').addEventListener('click', e=>{ const a=e.target.closest('[data-kwadd]'); if(a) commitKeywords([a.dataset.kwadd], []);
  if(e.target.closest('#kw-ai')) aiRun(); else if(e.target.closest('#kw-ai-cfg')) aiSettings(); });

let KWF='';
function renderKwList(selKws, nSel){
  const el=$('#p-kwlist'); if(!el) return;
  selKws = selKws || el._sel || []; nSel = nSel ?? el._n ?? 0; el._sel=selKws; el._n=nSel;
  const on = new Map(selKws.map(k=>[k.id,k.n]));
  const tags = S.tags.filter(tg=>!KWF || tg.name.toLowerCase().includes(KWF));
  el.innerHTML = `<div class="kwlist-filter"><input id="kw-filter" type="search" placeholder="${t("סינון מילות מפתח")}" value="${esc(KWF)}"></div>
    <div class="kwrows">${tags.slice(0,800).map(tg=>{ const c=on.get(tg.id)||0;
      return `<div class="row" data-kw="${tg.id}"><input type="checkbox" ${nSel&&c===nSel?'checked':''} ${nSel?'':'disabled'} data-part="${c&&c<nSel?1:0}" title="${nSel?t('הוסף/הסר לתמונות שנבחרו'):''}"><span class="nm">${esc(tg.name)}</span>
        <button class="go" data-go="${tg.id}" title="${t("הצג תמונות עם מילת המפתח")}">${I('next')}</button><span class="n">${num(tg.n)}</span></div>`; }).join('')
      || ("<div class=\"hint\">"+t("אין מילות מפתח עדיין. תייגו תמונות או הריצו תיוג חכם.")+"</div>")}</div>`;
  $$('#p-kwlist [data-part="1"]').forEach(c=>c.indeterminate=true);
}
$('#p-kwlist').addEventListener('input', debounce(e=>{ if(e.target.id==='kw-filter'){ KWF=e.target.value.toLowerCase(); renderKwList(); $('#kw-filter').focus(); } }, 150));
$('#p-kwlist').addEventListener('click', e=>{
  const go=e.target.closest('[data-go]');
  if(go){ const tg=S.tags.find(x=>x.id==go.dataset.go); setSource({kind:'tag', id:tg.id, name:t('מילת מפתח: ')+tg.name}); return; }
  const cb=e.target.closest('input[type=checkbox]');
  if(cb){ const tg=S.tags.find(x=>x.id==cb.closest('[data-kw]').dataset.kw);
    if(cb.checked) commitKeywords([tg.name], []); else commitKeywords([], [tg.id]); }
});

function renderMeta(ids, d){
  const el=$('#p-meta');
  if(!ids.length || !d){ el.innerHTML=("<div class=\"hint\">"+t("לא נבחרה תמונה.")+"</div>"); return; }
  const multi = ids.length>1;
  const sel = ids.map(id=>S.byId.get(id)).filter(Boolean);
  const same = k => sel.every(p=>(p[k]??null)===(sel[0][k]??null));
  const MIX = ("<span class=\"mixed\">"+t("&lt;מעורב&gt;")+"</span>");
  const rating = !multi || same('rating') ? (d.rating||0) : -1;
  const label = !multi || same('label') ? (d.label||'') : '*';
  const local = d.taken_at ? new Date(d.taken_at*1000 - new Date().getTimezoneOffset()*60000).toISOString().slice(0,16) : '';
  const links = (arr, kind) => arr.length ? arr.map(a=>`<a data-src-link="${kind}:${a.id}">${esc(a.name)}</a>`).join(', ') : '—';
  el.innerHTML = `
    ${multi?`<div class="lbl-sub">${t("{0} תמונות נבחרו — שינויים יחולו על כולן", [num(ids.length)])}</div>`:''}
    <div class="kv"><span>${t("שם קובץ")}</span>${multi?MIX:`<span dir="ltr" title="${esc(d.filename)}">${esc(d.filename)}</span>`}</div>
    <div class="kv"><span>${t("תיקייה")}</span>${multi&&!same('folder')?MIX:`<a data-src-link="folder:${esc(sel[0]?.folder??'')}">${esc(sel[0]?.folder||t('(שורש)'))}</a>`}</div>
    <div class="kv"><span>${t("דירוג")}</span><span class="stars-in" id="m-stars">${[1,2,3,4,5].map(n=>`<b data-mr="${n}" class="${rating>=n?'on':''}">★</b>`).join('')}${rating<0?' '+MIX:''}</span></div>
    <div class="kv"><span>${t("תווית")}</span><select id="m-label"><option value="">${t("ללא")}</option>${LABELS.map(([k,n])=>`<option value="${k}" ${label===k?'selected':''}>${n}</option>`).join('')}${label==='*'?("<option selected disabled>"+t("&lt;מעורב&gt;")+"</option>"):''}</select></div>
    <div class="meta-sub">${t("תוכן")}</div>
    <div class="kv tall"><span>${t("כיתוב")}</span><textarea id="m-desc" placeholder="${multi?t('<מעורב>'):''}">${multi?'':esc(d.description||'')}</textarea></div>
    <div class="meta-sub">${t("צילום")}</div>
    <div class="kv"><span>${t("זמן צילום")}</span>${multi?MIX:`<input id="m-date" type="datetime-local" value="${local}">`}</div>
    <div class="kv"><span>${t("מידות")}</span>${multi?MIX:`<span dir="ltr">${d.width||'?'} × ${d.height||'?'}</span>`}</div>
    <div class="kv"><span>${t("גודל קובץ")}</span>${multi?MIX:fsize(d.bytes)}</div>
    <div class="kv"><span>${t("סוג")}</span>${multi&&!sel.every(p=>ext(p)===ext(sel[0]))?MIX:esc(ext(d))}${d.edited&&!multi?t(' · נערך'):''}</div>
    <div class="meta-sub">${t("מיקום")}</div>
    ${multi?`<div class="kv"><span>GPS</span>${MIX}</div>`:`
    <div class="kv"><span>${t("קו רוחב")}</span><input id="m-lat" type="number" step="any" dir="ltr" value="${d.lat??''}"></div>
    <div class="kv"><span>${t("קו אורך")}</span><input id="m-lng" type="number" step="any" dir="ltr" value="${d.lng??''}"></div>
    ${d.lat!=null?`<div class="kv"><span></span><a href="https://www.google.com/maps?q=${d.lat},${d.lng}" target="_blank">${I('pin','')} ${t(" הצג במפה")}</a></div>`:''}
    <div class="meta-sub">${t("קטלוג")}</div>
    <div class="kv tall"><span>${t("אוספים")}</span><span style="white-space:normal">${links(d.albums,'album')}</span></div>
    <div class="kv tall"><span>${t("אנשים")}</span><span style="white-space:normal">${links(d.people,'person')}</span></div>
    <div class="kv"><span>${t("מועדף Google")}</span><label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="m-fav" ${d.favorited?'checked':''}></label></div>
    <div class="kv"><span>${t("יובא")}</span>${fdate(d.imported_at)}</div>
    ${d.gphotos_url?`<div class="kv"><span></span><a href="${esc(d.gphotos_url)}" target="_blank">${t("פתח ב‑Google Photos")} ${I('external')}</a></div>`:''}
    <div class="btnrow"><button id="m-exif" title="${t("כתוב תיאור, תאריך ומיקום לתוך קובץ ה‑JPG (Ctrl+S)")}">${t("שמור מטא-נתונים לקובץ")}</button><button id="m-reveal" title="Ctrl+R">${t("הצג בסייר")}</button></div>`}`;
}
$('#p-meta').addEventListener('click', e=>{
  const s=e.target.closest('[data-mr]'); if(s){ const n=+s.dataset.mr, p=actPhoto(); setRating(p&&p.rating===n&&targets().length===1?0:n); setTimeout(renderRight, 50); return; }
  const l=e.target.closest('[data-src-link]'); if(l){ setSource(srcFromKey(l.dataset.srcLink)); return; }
  if(e.target.id==='m-exif') saveMetaToFile();
  if(e.target.id==='m-reveal') reveal();
});
$('#p-meta').addEventListener('change', async e=>{
  const ids=targets(), tg=e.target;
  if(tg.id==='m-label') setAttr({label:tg.value}, ids);
  else if(tg.id==='m-desc'){ await send('PATCH','/api/photos',{ids, description:tg.value}); toast(t('הכיתוב נשמר'), 1200); }
  else if(tg.id==='m-fav'){ setAttr({favorited:tg.checked?1:0}, ids); }
  else if(tg.id==='m-date' && tg.value){ const ts=Math.floor(new Date(tg.value).getTime()/1000);
    await send('PATCH','/api/photo/'+ids[0],{taken_at:ts}); const p=S.byId.get(ids[0]); if(p) p.taken_at=ts; applyFilter(); toast(t('זמן הצילום עודכן'), 1200); }
  else if(tg.id==='m-lat' || tg.id==='m-lng'){ const lat=$('#m-lat').value, lng=$('#m-lng').value;
    if((lat==='')!==(lng==='')) return;
    await send('PATCH','/api/photo/'+ids[0],{lat: lat===''?null:+lat, lng: lng===''?null:+lng}); toast(t('המיקום נשמר'), 1200); renderRight(); }
});
$('#p-meta').addEventListener('keydown', e=>{ if(e.target.id==='m-desc' && e.key==='Enter' && !e.shiftKey){ e.preventDefault(); e.target.blur(); } });
async function saveMetaToFile(){
  const d=DETAIL; if(!d || targets().length!==1) return toast(t('בחרו תמונה אחת'));
  await send('PATCH','/api/photo/'+d.id,{description:d.description, taken_at:d.taken_at, lat:d.lat, lng:d.lng, write_exif:true});
  toast(/\.jpe?g$/i.test(d.filename) ? t('המטא-נתונים נכתבו לקובץ') : t('כתיבה לקובץ נתמכת רק ב‑JPG'));
}
async function reveal(){ const p=actPhoto(); if(p) await send('POST', `/api/photo/${p.id}/reveal`); }
$('#btn-sync-meta').onclick = async ()=>{
  const ids=targets(), d=DETAIL; if(ids.length<2 || !d) return toast(t('בחרו כמה תמונות; הערכים יועתקו מהתמונה הפעילה'));
  await send('PATCH','/api/photos',{ids, description:d.description||'', rating:d.rating||0, label:d.label||''});
  ids.forEach(id=>{ const p=S.byId.get(id); if(p){ p.rating=d.rating||0; p.label=d.label||null; } });
  toast(`${t("סונכרנו {0} תמונות", [num(ids.length)])}`); applyFilter();
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
    closeLoupeMedia();
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
  [t('ללא (איפוס טונים)'), {bri:0,con:0,sat:0,gray:false}],
  [t('שחור-לבן'), {gray:true, con:10}],
  [t('שחור-לבן בניגודיות גבוהה'), {gray:true, con:45, bri:5}],
  [t('חי וצבעוני'), {sat:40, con:15}],
  [t('מושתק'), {sat:-40, con:-10}],
  [t('בהיר ואוורירי'), {bri:20, con:-15, sat:-10}],
  [t('כהה ודרמטי'), {bri:-15, con:35, sat:-15}],
];
async function devOpen(){
  const p=actPhoto();
  DEV.crop=false; DEV.before=false; DEV.dirty=false;
  if(!p){ DEV.id=null; $('#dev-img').removeAttribute('src'); renderDevPanels(); return; }
  if(p.is_video){ DEV.id=null; $('#dev-img').removeAttribute('src'); renderDevPanels(); toast(t('עריכה זמינה לתמונות בלבד')); return; }
  DEV.id=p.id;
  const d=await api('/api/photo/'+p.id); if(DEV.id!==p.id) return;
  const o = d.edit_ops ? JSON.parse(d.edit_ops) : {};
  DEV.ops = {bri:unfac(o.brightness,.3), con:unfac(o.contrast,.3), sat:unfac(o.saturation,0), gray:!!o.grayscale,
             rot:o.rotate||0, crop:o.crop&&o.crop.length===4?o.crop:[0,0,1,1]};
  DEV.saved = JSON.stringify(DEV.ops);
  DEV.hist=[{t:d.edited?t('הגדרות שמורות'):t('ייבוא'), ops:{...DEV.ops}}];
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
  const sl=(k,label,cls,min=-100,max=100)=>`<div class="dsl ${cls}"><label for="d-${k}">${label}</label><input id="d-${k}" data-k="${k}" type="range" min="${min}" max="${max}" step="1" value="${o[k]}" ${dis} title="${t("לחיצה כפולה לאיפוס")}"><output>${o[k]>0?'+':''}${o[k]}</output></div>`;
  $('#p-basic').innerHTML = `
    <div class="dsec">${t("טיפול")}</div>
    <div class="treat"><a data-gray="0" class="${o.gray?'':'on'}">${t("צבע")}</a><a data-gray="1" class="${o.gray?'on':''}">${t("שחור-לבן")}</a></div>
    <div class="dsec">${t("טון")}</div>
    ${sl('bri',t('חשיפה'),'exp')}${sl('con',t('ניגודיות'),'con')}
    <div class="dsec">${t("נוכחות")}</div>
    ${sl('sat',t('רוויה'),'sat')}`;
  const fine = Math.round((o.rot - Math.round(o.rot/90)*90)*10)/10;
  $('#p-transform').innerHTML = `
    <div class="dsl"><label for="d-straight">${t("יישור")}</label><input id="d-straight" data-k="straight" type="range" min="-45" max="45" step="0.5" value="${fine}" ${dis}><output>${fine>0?'+':''}${fine}°</output></div>
    <div class="btnrow90"><button data-rot="-90" ${dis}>${I('rotl')} 90°</button><button data-rot="90" ${dis}>90° ${I('rotr')}</button></div>
    <div class="btnrow90"><button data-t="crop" ${dis}>${I('crop')} ${DEV.crop?t('סיום חיתוך (Enter)'):t('חיתוך (R)')}</button><button data-t="cropreset" ${dis}>${t("אפס חיתוך")}</button></div>`;
  $('#p-presets').innerHTML = PRESETS.map(([n],i)=>`<div class="row" data-preset="${i}">${I('dev')}<span class="nm">${n}</span></div>`).join('');
  $('#p-history').innerHTML = DEV.hist.map((h,i)=>`<div class="row ${i===DEV.hist.length-1?'':''}" data-hist="${i}"><span class="nm">${esc(h.t)}</span></div>`).reverse().join('') || '<div class="hint">—</div>';
  $$('#dev-tools [data-tool]').forEach(b=>b.classList.toggle('on', DEV.crop));
}
const DLABEL = {bri:t('חשיפה'), con:t('ניגודיות'), sat:t('רוויה')};
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
  devSet({}, k==='straight' ? `${t("יישור")} ${v>0?'+':''}${v}°` : `${DLABEL[k]} ${v>0?'+':''}${v}`);
});
$('#right').addEventListener('dblclick', e=>{
  const k=e.target.dataset?.k; if(!k || DEV.id==null) return;
  if(k==='straight') devSet({rot:Math.round(DEV.ops.rot/90)*90}, t('יישור 0°')); else devSet({[k]:0}, `${DLABEL[k]} 0`);
});
$('#right').addEventListener('click', e=>{
  if(S.mod!=='develop' || DEV.id==null) return;
  const g=e.target.closest('[data-gray]'); if(g){ const on=g.dataset.gray==='1'; if(on!==DEV.ops.gray) devSet({gray:on}, on?t('שחור-לבן'):t('צבע')); return; }
  const r=e.target.closest('[data-rot]'); if(r){ const d=+r.dataset.rot; const k=DEV.ops.crop, c = d>0 ? [1-k[3],k[0],1-k[1],k[2]] : [k[1],1-k[2],k[3],1-k[0]];
    let rot=DEV.ops.rot+d; if(rot>180) rot-=360; if(rot<=-180) rot+=360; devSet({rot, crop:c}, d>0?t('סיבוב ימינה'):t('סיבוב שמאלה')); return; }
  const tg=e.target.closest('[data-t]')?.dataset.t;
  if(tg==='crop') devCropToggle();
  if(tg==='cropreset') devSet({crop:[0,0,1,1]}, t('איפוס חיתוך'));
  if(e.target.closest('[data-tool="crop"]')) devCropToggle();
});
$('#left').addEventListener('click', e=>{
  if(S.mod!=='develop' || DEV.id==null) return;
  const pr=e.target.closest('[data-preset]'); if(pr){ const [n,o]=PRESETS[+pr.dataset.preset]; devSet({...NEUTRAL(), rot:DEV.ops.rot, crop:DEV.ops.crop, ...o}, t('הגדרה קבועה: ')+n); return; }
  const h=e.target.closest('[data-hist]'); if(h){ const s=DEV.hist[+h.dataset.hist]; devSet({...s.ops, crop:[...s.ops.crop]}); }
});
function devCropToggle(){ if(DEV.id==null) return; DEV.crop=!DEV.crop; if(!DEV.crop) devSet({}, t('חיתוך')); else { layoutDev(); renderDevPanels(); renderToolbar(); } }
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
  if(!silent) toast(t('מחיל…'), 1500);
  await send('POST', `/api/photo/${id}/edit`, devOpsToApi(ops));
  const d=await api('/api/photo/'+id);
  const p=S.byId.get(id); if(p) Object.assign(p, {width:d.width, height:d.height, edited:d.edited, bytes:d.bytes});
  VER[id]=Date.now();
  DEV.last = {bri:ops.bri, con:ops.con, sat:ops.sat, gray:ops.gray}; pref.set('lastDev', DEV.last);
  if(DEV.id===id){ DEV.saved=JSON.stringify(DEV.ops); DEV.dirty=false; renderToolbar(); }
  G.cells.forEach(c=>{ if(+c.dataset.id===id){ const img=c.querySelector('img'); if(img) img.src=thumbUrl(id); } });
  renderFilm(true); renderColls();
  if(!silent) toast(t('ההגדרות הוחלו · המקור נשמר'));
}
$('#btn-dev-apply').onclick = ()=>devApply();
$('#btn-dev-reset').onclick = ()=>{ if(DEV.id!=null) devSet(NEUTRAL(), t('איפוס')); };
$('#btn-copy-prev').onclick = ()=>{ if(DEV.id==null) return; if(!DEV.last) return toast(t('עוד לא הוחלו הגדרות על תמונה אחרת')); devSet({...DEV.last}, t('הגדרות קודמות')); };
$('#btn-dev-revert').onclick = async ()=>{
  if(DEV.id==null) return; const p=actPhoto();
  if(!p?.edited){ devSet(NEUTRAL(), t('איפוס')); return; }
  await send('POST', `/api/photo/${DEV.id}/revert`); VER[DEV.id]=Date.now();
  Object.assign(p, {edited:0}); const d=await api('/api/photo/'+p.id); Object.assign(p,{width:d.width,height:d.height,bytes:d.bytes});
  toast(t('הוחזר לקובץ המקורי')); renderFilm(true); devOpen();
};

// ---------- slideshow ----------
const SS={list:[], i:0, t:null, playing:true, cur:'a'};
function ssStart(){
  let list = S.sel.size>1 ? S.list.filter(p=>S.sel.has(p.id)) : S.list;
  list = list.filter(p=>!p.is_video); if(!list.length) return toast(t('אין תמונות להצגה'));
  SS.list=list; SS.i=Math.max(0, list.findIndex(p=>p.id===S.act)); SS.playing=true;
  if(S.view==='loupe') closeLoupeMedia();   // a video in the Loupe must not keep playing behind the slideshow
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
// Cast = Windows' own Connect flyout (Win+K): pick a TV / wireless display, then choose Duplicate.
window.ssCast=()=>send('POST','/api/cast');
window.ssStop=()=>{ clearInterval(SS.t); $('#slideshow').classList.add('hidden'); $('#ss-a').classList.remove('on'); $('#ss-b').classList.remove('on');
  if(document.fullscreenElement) document.exitFullscreen().catch(()=>{});
  if(S.view==='loupe' && S.mod==='library') renderLoupe(); };
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
      <div class="mf"><button id="pb-cancel">${t("ביטול")}</button><button class="primary" id="pb-ok">${t("אישור")}</button></div>`);
    const done=v=>{ PB_CHECKED = !!$('#modal-box input[type=checkbox]')?.checked; closeModal(); res(v); };
    $('#pb-form').onsubmit=e=>{ e.preventDefault(); done($('#pb-in').value.trim()||null); };
    $('#pb-ok').onclick=()=>done($('#pb-in').value.trim()||null);
    $('#pb-cancel').onclick=()=>done(null);
  });
}
function confirmBox(title, text, ok=t('אישור')){
  return new Promise(res=>{
    modal(`<h3>${title}</h3><div class="mb"><p>${text}</p></div><div class="mf"><button id="cb-no">${t("ביטול")}</button><button class="primary" id="cb-yes">${ok}</button></div>`);
    $('#cb-yes').onclick=()=>{ closeModal(); res(true); }; $('#cb-no').onclick=()=>{ closeModal(); res(false); };
  });
}
async function catalogSettings(){
  const s=await api('/api/status'), c=s.counts;
  modal(`<h3>${t("הגדרות קטלוג")}</h3><div class="mb">
    <p>${t("כל הקבצים נשמרים מקומית במחשב שלך. תמונות מיובאות מועתקות לתיקיית המדיה ומסודרות לפי שנת צילום. המקור של כל תמונה שנערכה נשמר בנפרד.")}</p>
    <div class="pathrow"><span>${t("ספריית הקטלוג")}</span><code>${esc(s.library_root)}</code></div>
    <div class="pathrow"><span>${t("קבצי מדיה")}</span><code>${esc(s.media_path)}</code></div>
    <div class="pathrow"><span>${t("קובץ הקטלוג")}</span><code>${esc(s.db_path)}</code></div>
    <div class="pathrow"><span>${t("תוכן")}</span><span>${t("{0} פריטים · {1} סרטונים · {2} אוספים · {3} אנשים · {4} מילות מפתח · {5} באשפה", [num(c.photos), num(c.videos), num(c.albums), num(c.people), num(c.tags), num(c.trashed)])}</span></div>
    <div class="pathrow"><span>${t("אשפה")}</span><span>${t("פריטים נמחקים לצמיתות אחרי {0} יום", [s.trash_days])}</span></div>
    <label class="fld"><span>${t("מיקום קטלוג אחר")}</span><div class="frow"><input type="text" id="lib-path" dir="ltr" value="${esc(s.library_root)}"><button id="lib-pick">${t("בחר...")}</button></div></label>
  </div><div class="mf"><button onclick="closeModal()">${t("סגור")}</button><button class="primary" id="lib-set">${t("עבור לקטלוג")}</button></div>`);
  $('#lib-pick').onclick=async()=>{ const r=await api('/api/pick-file?kind=folder&title='+encodeURIComponent(t('בחר תיקיית קטלוג'))); if(r.path) $('#lib-path').value=r.path; };
  $('#lib-set').onclick=async()=>{ const p=$('#lib-path').value.trim(); if(!p) return; await send('POST','/api/settings/library',{path:p}); closeModal(); toast(t('הקטלוג הוחלף')); await reloadAll(); };
}
async function preferences(){
  const s=await api('/api/status');
  modal(`<h3>${t("העדפות · זיהוי פנים")}</h3><div class="mb">
    <p>${t("זיהוי הפנים רץ מקומית במחשב, בלי לשלוח תמונות. תיוג AI שולח תמונות ממוזערות לספק שבחרתם, רק כשמפעילים אותו.")}</p>
    <div class="pathrow"><span>${t("זיהוי פנים")}</span><span>${t("InsightFace · {0} פרצופים זוהו עד כה", [num(s.counts.faces)])}</span></div>
  </div><div class="mf"><button onclick="closeModal()">${t("סגור")}</button>
    <button id="pf-ai">${t("הגדרות תיוג AI")}</button><button class="primary" id="pf-faces">${t("זהה פנים")}</button></div>`);
  $('#pf-ai').onclick=()=>{ closeModal(); aiSettings(); };
  $('#pf-faces').onclick=()=>{ closeModal(); runJob('/api/faces','faces',t('זיהוי פנים')); };
}
// ---------- AI tagging: OpenAI / Claude / Gemini / OpenRouter / any OpenAI-compatible API ----------
const AI_PROVIDERS = ['openai', 'anthropic', 'gemini', 'openrouter', 'custom'];
const aiShort = id => ({openai:'OpenAI', anthropic:'Claude', gemini:'Gemini', openrouter:'OpenRouter'})[id] || t('שרת מותאם אישית');
const aiLabel = id => id==='custom' ? t('מותאם אישית (תואם OpenAI: Groq, Together, Ollama ועוד)')
  : id==='openrouter' ? 'OpenRouter · ' + t('כל המודלים עם מפתח אחד')
  : id==='anthropic' ? 'Claude (Anthropic)' : id==='gemini' ? 'Gemini (Google)' : 'OpenAI';

async function aiSettings(){
  const s = await api('/api/ai/settings');
  modal(`<h3>${t('הגדרות תיוג AI')}</h3><div class="mb">
    <p>${t('מילות המפתח נוצרות על ידי ספק AI, עם המפתח שלכם. נשלחת תמונה ממוזערת (עד 512px) של כל תמונה שמתייגים, ורק כשמפעילים תיוג. המפתח נשמר מוצפן בחשבון ה‑Windows שלכם.')}</p>
    <label class="fld"><span>${t('ספק')}</span><select id="ai-provider">${AI_PROVIDERS.map(id=>`<option value="${id}" ${id===s.provider?'selected':''}>${esc(aiLabel(id))}</option>`).join('')}</select></label>
    <label class="fld hidden" id="ai-base-row"><span>${t('כתובת ה‑API (עד /v1)')}</span><input type="text" id="ai-base" dir="ltr" value="${esc(s.base_url)}" placeholder="http://localhost:11434/v1"></label>
    <div class="fld"><span>${t('מפתח API')}</span>
      <div class="frow"><input type="password" id="ai-key" dir="ltr" autocomplete="off" spellcheck="false"><button id="ai-key-del" class="hidden">${t('הסר מפתח')}</button></div>
      <span class="hint" id="ai-key-hint" style="margin:0;padding:0"></span></div>
    <div class="fld"><span>${t('מודל')}</span>
      <label class="check" style="padding:0"><input type="radio" name="ai-m" value="auto"> ${t('אוטומטי (מומלץ): נבחר מודל זול ומהיר שמבין תמונות')}</label>
      <label class="check" style="padding:0"><input type="radio" name="ai-m" value="manual"> ${t('בחירה ידנית')}</label>
      <div class="frow"><input type="text" id="ai-model" list="ai-models" dir="ltr" autocomplete="off" spellcheck="false" placeholder="model-id"><datalist id="ai-models"></datalist><button id="ai-load">${t('טען רשימת מודלים')}</button></div></div>
    <label class="fld"><span>${t('שפת מילות המפתח')}</span><select id="ai-lang">${LANGS.map(([c,n])=>`<option value="${c}" ${c===s.language?'selected':''}>${n}</option>`).join('')}</select></label>
    <div class="hint" id="ai-status" style="min-height:18px;padding:0"></div>
  </div><div class="mf"><button id="ai-close">${t('סגור')}</button><button id="ai-save" class="primary">${t('שמור')}</button></div>`);
  const q = id => $('#'+id), radio = v => $(`input[name=ai-m][value=${v}]`);
  const status = (msg, cls='') => { q('ai-status').textContent = msg; q('ai-status').className = 'hint ' + cls; };
  radio(s.model ? 'manual' : 'auto').checked = true; q('ai-model').value = s.model;
  const refresh = ()=>{
    const p = q('ai-provider').value, info = s.providers[p] || {};
    q('ai-base-row').classList.toggle('hidden', p!=='custom');
    q('ai-key-hint').textContent = info.has_key ? t('מפתח שמור: {0}', [info.hint]) : p==='custom' ? t('מפתח אופציונלי (שרת מקומי בדרך כלל לא צריך)') : t('לא נשמר מפתח');
    q('ai-key-del').classList.toggle('hidden', !info.has_key);
    q('ai-model').disabled = !radio('manual').checked;
  };
  refresh();
  q('ai-provider').onchange = ()=>{ radio('auto').checked = true; q('ai-model').value = ''; q('ai-models').innerHTML = ''; status(''); refresh(); };
  $$('input[name=ai-m]').forEach(r=>r.onchange = refresh);
  q('ai-key-del').onclick = async ()=>{ s.providers = (await send('DELETE', '/api/ai/key/' + q('ai-provider').value)).providers; refresh(); };
  const probe = ()=>({provider:q('ai-provider').value, base_url:q('ai-base').value.trim(), api_key:q('ai-key').value.trim() || null});
  q('ai-load').onclick = async ()=>{
    status(t('טוען…'));
    try{
      const r = await send('POST', '/api/ai/models', probe());
      q('ai-models').innerHTML = r.models.map(m=>`<option value="${esc(m.id)}">${esc(m.name!==m.id ? m.name : '')}</option>`).join('');
      status(t('נמצאו {0} מודלים · במצב אוטומטי ייבחר: {1}', [r.models.length, r.auto || '—']), 'ok');
    }catch(e){ status(e.message, 'err'); }
  };
  q('ai-save').onclick = async ()=>{
    const manual = radio('manual').checked, model = q('ai-model').value.trim();
    if(manual && !model) return status(t('הקלידו מזהה מודל, או בחרו אוטומטי'), 'err');
    try{
      await send('POST', '/api/ai/settings', {...probe(), model: manual ? model : '', language: q('ai-lang').value});
      closeModal(); toast(t('ההגדרות נשמרו'));
    }catch(e){ status(e.message, 'err'); }
  };
  q('ai-close').onclick = closeModal;
}

async function aiRun(){
  const s = await api('/api/ai/settings');
  if(!(s.providers[s.provider]||{}).has_key && s.provider!=='custom') return aiSettings();   // not set up yet
  const sel = targets().filter(id=>!(S.byId.get(id)||{}).is_video);
  const untagged = S.all.filter(p=>!p.is_video && !p.has_kw).length;
  const name = esc(aiShort(s.provider));
  modal(`<h3>${t('תיוג AI')}</h3><div class="mb">
    <p>${t('{0} · מודל: {1}', [name, esc(s.model || t('אוטומטי'))])}</p>
    <div class="fld">
      <label class="check" style="padding:0"><input type="radio" name="ai-scope" value="sel" ${sel.length?'checked':'disabled'}> ${t('התמונות שנבחרו ({0})', [num(sel.length)])}</label>
      <label class="check" style="padding:0"><input type="radio" name="ai-scope" value="untagged" ${sel.length?'':'checked'} ${untagged?'':'disabled'}> ${t('כל התמונות ללא מילות מפתח ({0})', [num(untagged)])}</label></div>
    <p>${t('תמונה ממוזערת של כל תמונה תישלח אל {0}. מילות מפתח שנוצרו קודם על ידי AI בתמונות האלה יוחלפו; מילות מפתח ידניות לא ייפגעו.', [name])}</p>
  </div><div class="mf"><button id="air-cfg">${t('הגדרות')}</button><span class="spacer"></span><button id="air-cancel">${t('ביטול')}</button><button class="primary" id="air-go">${t('התחל תיוג')}</button></div>`);
  $('#air-cfg').onclick = ()=>{ closeModal(); aiSettings(); };
  $('#air-cancel').onclick = closeModal;
  $('#air-go').onclick = async ()=>{
    const scope = $('input[name=ai-scope]:checked')?.value; if(!scope) return;
    closeModal();
    await send('POST', '/api/aitag', scope==='sel' ? {ids:sel} : {only_untagged:true});
    pollJob('aitag', t('תיוג AI'));
  };
}

// ---------- video compression (HandBrake) ----------
const ltr = s => '\u2066' + s + '\u2069';   // isolate numbers/Latin inside RTL text so "10.6 MB" doesn't flip
const fmtBytes = b => ltr(b>=1073741824 ? (b/1073741824).toFixed(2)+' GB' : (b/1048576).toFixed(b>=104857600 ? 0 : 1)+' MB');
const STRENGTH_LABELS = () => [t('חלשה מאוד'), t('חלשה'), t('בינונית'), t('חזקה'), t('חזקה מאוד')];
const SPEED_LABELS = () => [t('איטי מאוד'), t('איטי'), t('קצת איטי'), t('בינוני'), t('קצת מהיר'), t('מהיר'), t('מהיר מאוד')];
const GRADE_LABELS = () => ({excellent:t('כמעט זהה למקור'), very_good:t('טוב מאוד'), good:t('טוב'), noticeable:t('איבוד איכות מורגש')});
let CMP_PID = null;

function hbInstallDialog(st, retry){
  modal(`<h3>${t('דחיסת סרטונים דורשת את HandBrake')}</h3><div class="mb">
    <p>${t('HandBrake היא תוכנה חינמית שמבצעת את הדחיסה. התקינו את גרסת שורת הפקודה (HandBrakeCLI) מהאתר שלהם, ואז לחצו «בדוק שוב».')}</p>
    <p class="hint" style="padding:0">${t('אפשר גם לשמור את HandBrakeCLI.exe בכל תיקייה ולבחור אותו ידנית.')}</p>
    <div class="hint err" id="hb-msg" style="padding:0;min-height:16px"></div>
  </div><div class="mf"><button id="hb-pick">${t('בחר HandBrakeCLI.exe...')}</button><span class="spacer"></span>
    <button id="hb-again">${t('בדוק שוב')}</button><button id="hb-open" class="primary">${t('פתח את דף ההתקנה')}</button><button id="hb-close">${t('סגור')}</button></div>`);
  $('#hb-open').onclick = ()=>send('POST', '/api/handbrake/open-page');
  $('#hb-again').onclick = async ()=>{ const s = await api('/api/handbrake'); if(s.found){ closeModal(); retry(); } else $('#hb-msg').textContent = t('HandBrakeCLI עדיין לא נמצא.'); };
  $('#hb-pick').onclick = async ()=>{
    const r = await api('/api/pick-file?kind=exe&title=' + encodeURIComponent(t('בחר את HandBrakeCLI.exe'))); if(!r.path) return;
    try{ const s = await send('POST', '/api/handbrake/path', {path:r.path}); if(s.found){ closeModal(); retry(); } }
    catch(e){ $('#hb-msg').textContent = e.message; }
  };
  $('#hb-close').onclick = closeModal;
}

// plain-words description of the current choices ("what was chosen for you"), shown under the sliders
const QUALITY_TEXT = () => [t('איכות כמעט זהה למקור'), t('איכות גבוהה, ההבדל כמעט לא נראה'), t('איכות טובה, אפשר להבחין בהבדל קל בקטעים מפורטים'),
  t('איכות נמוכה יותר, ייתכנו פגמים בקטעים מורכבים'), t('איכות נמוכה, פגמים נראים לעין')];
const KIND_TEXT = () => [t('דחיסה יסודית ואיטית: אותה איכות בקובץ קטן יותר'), t('דחיסה מאוזנת בין מהירות לגודל הקובץ'),
  t('דחיסה מהירה: מסיימים מהר, אבל הקובץ גדול יותר מהאפשרי')];
const ENCODER_NOTE = () => ({x264:t('H.264: מתנגן בכל מכשיר'), x265:t('H.265: קובץ קטן יותר, אך נתמך פחות'), svt_av1:t('AV1: הקובץ הקטן ביותר, איטי ונתמך פחות')});
function compressSummary(o, e, speedIdx){
  const frac = (o.quality - e.rf[0]) / (e.rf[1] - e.rf[0]);
  const quality = QUALITY_TEXT()[Math.max(0, Math.min(4, Math.floor(frac * 5)))];
  const lines = [
    t('איכות: {0}', [o.max_height ? t('{0} (עד {1})', [quality, ltr(o.max_height + 'p')]) : quality]),
    t('סוג הדחיסה: {0} · {1}', [KIND_TEXT()[speedIdx <= 2 ? 0 : speedIdx === 3 ? 1 : 2], ENCODER_NOTE()[o.encoder]]),
    o.fps_mode === 'same' ? t('פריימים: כולם נשמרים')
      : o.fps_mode === 'limit' ? t('פריימים: הקצב יוגבל ל‑{0} לשנייה (פריימים יוסרו)', [ltr(String(o.fps))])
      : t('פריימים: קצב קבוע של {0} לשנייה (פריימים יוסרו או יוכפלו)', [ltr(String(o.fps))]),
    o.audio === 'auto' ? t('אודיו: נשמר כמו שהוא (AAC), אחרת מומר ל‑AAC')
      : o.audio === 'aac' ? t('אודיו: יומר ל‑AAC ב‑{0} kbps', [ltr(String(o.audio_bitrate))]) : t('אודיו: יוסר'),
    t('הקובץ המקורי נשמר בגיבויים ואפשר להחזיר אותו'),
  ];
  return lines.map(l => `<li>${esc(l)}</li>`).join('');
}

// the same dialog serves one video, one photo, or any mix of several: only the sections that apply are shown
const IMG_Q = [40, 98];
const IMG_MAX_SIDES = [0, 4096, 3000, 2560, 2048, 1600, 1200];
function compressSummaryImg(o){
  const frac = (IMG_Q[1] - o.quality) / (IMG_Q[1] - IMG_Q[0]);
  const lines = [
    t('איכות: {0}', [QUALITY_TEXT()[Math.max(0, Math.min(4, Math.floor(frac * 5)))]]),
    o.max_side ? t('גודל התמונה יוקטן עד {0} פיקסלים בצלע הארוכה', [ltr(String(o.max_side))]) : t('גודל התמונה (בפיקסלים) לא ישתנה'),
    t('PNG, BMP ו‑TIFF נדחסים ללא שום אובדן איכות; ההגדרה משפיעה על JPEG ו‑WebP'),
    t('פרטי הצילום (EXIF), המיקום והכיוון נשמרים'),
    t('הקובץ המקורי נשמר בגיבויים ואפשר להחזיר אותו'),
  ];
  return lines.map(l => `<li>${esc(l)}</li>`).join('');
}

async function compressDialog(arg){
  const ids = (Array.isArray(arg) ? arg : [arg]).filter(id => S.byId.has(id));
  const st = await api('/api/handbrake');
  const fx = p => (p.filename.match(/\.[^.]+$/) || [''])[0].toLowerCase();
  const items = ids.map(id => S.byId.get(id));
  const vids = items.filter(p => p.is_video && st.video_ext.includes(fx(p)));
  const imgs = items.filter(p => !p.is_video && st.image_ext.includes(fx(p)));
  const skipped = items.length - vids.length - imgs.length;
  if(!vids.length && !imgs.length) return toast(items.length ? t('אין בבחירה קבצים שאפשר לדחוס (JPG, PNG, WebP, TIFF, BMP וסרטונים)') : t('בחרו תמונות או סרטונים כדי לדחוס'));
  if(vids.length && !imgs.length && !st.found) return hbInstallDialog(st, ()=>compressDialog(ids));
  const hasV = vids.length > 0 && st.found, hasI = imgs.length > 0;
  const single = items.length === 1 ? await api('/api/photo/' + items[0].id) : null;
  const E = st.encoders, def = st.defaults, enc = ()=>E[o.encoder];
  const o = {...def, ...(pref.get('compress', null) || {})};
  if(!E[o.encoder]) o.encoder = def.encoder;
  const norm = ()=>{ const e = enc(); if(!(o.quality >= e.rf[0] && o.quality <= e.rf[1])) o.quality = e.rf[2]; if(!e.presets.includes(String(o.preset))) o.preset = e.default_preset; };
  if(o.quality == null) o.quality = enc().rf[2];
  norm();
  const idef = st.image_defaults, oi = {...idef, ...(pref.get('compress_img', null) || {})};
  oi.quality = Math.max(IMG_Q[0], Math.min(IMG_Q[1], +oi.quality || idef.quality));
  if(!IMG_MAX_SIDES.includes(+oi.max_side)) oi.max_side = 0;

  const title = single ? (single.is_video ? t('דחיסת סרטון') : t('דחיסת תמונה')) : t('דחיסת קבצים');
  const head = single
    ? `<div class="cmp-file"><b dir="ltr">${esc(single.filename)}</b><span>${fmtBytes(single.bytes)}</span></div>`
    : `<div class="cmp-file"><b>${t('{0} קבצים', [num(vids.length + imgs.length)])}</b><span>${[vids.length ? t('{0} סרטונים', [num(vids.length)]) : '', imgs.length ? t('{0} תמונות', [num(imgs.length)]) : ''].filter(Boolean).join(' · ')} · ${fmtBytes([...vids, ...imgs].reduce((a, p) => a + (p.bytes || 0), 0))}</span></div>`
      + (skipped ? `<div class="hint warn" style="padding:0">${t('{0} קבצים מסוג שאינו נתמך ידולגו', [num(skipped)])}</div>` : '');
  const sect = h => (hasV && hasI) ? `<div class="cmp-h">${h}</div>` : '';

  const videoHtml = hasV ? `${sect(t('סרטונים'))}
    <div class="cmp-sl"><div class="cmp-sl-h"><label for="cp-str">${t('עוצמת דחיסה')}</label><output id="cp-str-v"></output></div>
      <input type="range" id="cp-str" step="1"><div class="cmp-ends"><span>${t('חלשה מאוד')}</span><span>${t('חזקה מאוד')}</span></div></div>
    <div class="cmp-sl"><div class="cmp-sl-h"><label for="cp-spd">${t('מהירות')}</label><output id="cp-spd-v"></output></div>
      <input type="range" id="cp-spd" min="0" max="6" step="1"><div class="cmp-ends"><span>${t('איטי וטוב')}</span><span>${t('מהיר ופחות טוב')}</span></div></div>
    <div class="cmp-sum"><div class="lbl-sub" style="padding:0">${t('מה נבחר בשבילך')}</div><ul id="cp-sum"></ul></div>
    <p class="hint" style="padding:0">${t('בקצב איטי המקודד משקיע יותר מאמץ: אותה איכות בקובץ קטן יותר, אבל זה לוקח יותר זמן.')}</p>
    <button class="linkbtn" id="cp-adv-t">${t('מתקדם')} ▾</button>
    <div id="cp-adv" class="hidden">
      <label class="fld"><span>${t('מקודד')}</span><select id="cp-enc">${Object.entries(E).map(([k,v])=>`<option value="${k}">${esc(v.label)}</option>`).join('')}</select></label>
      <div class="two"><label class="fld"><span>${t('איכות (RF, נמוך = איכות גבוהה)')}</span><input type="number" id="cp-q" step="0.5" dir="ltr"></label>
        <label class="fld"><span>${t('פרסט מהירות')}</span><select id="cp-pre"></select></label></div>
      <div class="two"><label class="fld"><span>${t('רזולוציה מרבית')}</span><select id="cp-h"><option value="0">${t('ללא שינוי')}</option>${[2160,1440,1080,720,480].map(h=>`<option value="${h}">${h}p</option>`).join('')}</select></label>
        <label class="fld"><span>${t('קצב פריימים')}</span><select id="cp-fm"><option value="same">${t('שמור כל פריים (ברירת מחדל)')}</option><option value="limit">${t('הגבל לקצב מרבי (מוריד פריימים)')}</option><option value="constant">${t('קצב קבוע (משנה פריימים)')}</option></select></label></div>
      <label class="fld hidden" id="cp-fps-row"><span>${t('פריימים לשנייה')}</span><input type="number" id="cp-fps" min="1" max="240" step="0.001" dir="ltr">
        <span class="hint warn" style="padding:0">${t('פריימים יוסרו מהסרטון. האימות יבדוק את מספר הפריימים לפי הקצב שבחרתם.')}</span></label>
      <div class="two"><label class="fld"><span>${t('אודיו')}</span><select id="cp-au"><option value="auto">${t('שמור AAC, אחרת המר ל‑AAC')}</option><option value="aac">${t('המר ל‑AAC')}</option><option value="none">${t('הסר אודיו')}</option></select></label>
        <label class="fld"><span>${t('קצב סיביות לאודיו (kbps)')}</span><input type="number" id="cp-ab" min="32" max="512" step="16" dir="ltr"></label></div>
      <div class="hint" style="padding:0">HandBrake ${esc(st.version || '')} · <bdi dir="ltr">${esc(st.path)}</bdi></div>
    </div>` : '';
  const hbNote = (vids.length && !st.found) ? `${sect(t('סרטונים'))}<div class="cmp-sum"><p class="hint warn" style="padding:0">${t('HandBrake לא מותקן, ולכן הסרטונים ידולגו.')}</p>
    <button class="linkbtn" id="cp-hb">${t('פתח את דף ההתקנה')}</button></div>` : '';
  const imageHtml = hasI ? `${sect(t('תמונות'))}
    <div class="cmp-sl"><div class="cmp-sl-h"><label for="ci-str">${t('עוצמת דחיסה')}</label><output id="ci-str-v"></output></div>
      <input type="range" id="ci-str" min="0" max="${IMG_Q[1] - IMG_Q[0]}" step="1"><div class="cmp-ends"><span>${t('חלשה מאוד')}</span><span>${t('חזקה מאוד')}</span></div></div>
    <div class="cmp-sum"><div class="lbl-sub" style="padding:0">${t('מה נבחר בשבילך')}</div><ul id="ci-sum"></ul></div>
    <button class="linkbtn" id="ci-adv-t">${t('מתקדם')} ▾</button>
    <div id="ci-adv" class="hidden"><div class="two">
      <label class="fld"><span>${t('איכות JPEG / WebP (גבוה = איכות גבוהה)')}</span><input type="number" id="ci-q" min="${IMG_Q[0]}" max="${IMG_Q[1]}" step="1" dir="ltr"></label>
      <label class="fld"><span>${t('הצלע הארוכה מרבית')}</span><select id="ci-ms"><option value="0">${t('ללא שינוי')}</option>${IMG_MAX_SIDES.slice(1).map(v=>`<option value="${v}">${v}px</option>`).join('')}</select></label></div></div>` : '';
  const restoreHtml = (single && single.video_backups)
    ? `<div class="cmp-restore"><span>${single.is_video ? t('יש גרסה קודמת של הסרטון בגיבויים.') : t('יש גרסה קודמת של התמונה בגיבויים.')}</span><button id="cp-restore">${t('החזר גרסה קודמת')}</button></div>` : '';
  modal(`<h3>${title}</h3><div class="mb cmp">${head}${videoHtml}${hbNote}${imageHtml}${restoreHtml}
  </div><div class="mf"><button id="cp-reset">${t('ברירת מחדל')}</button><span class="spacer"></span><button id="cp-cancel">${t('ביטול')}</button><button id="cp-go" class="primary">${t('התחל דחיסה')}</button></div>`);

  const q = id => $('#' + id);
  let syncV = ()=>{}, syncI = ()=>{};
  if(hasV){
    // the two sliders and the advanced fields are one state (o): moving any of them moves the others
    const stopIndex = ()=>{ const e = enc(), pos = e.presets.indexOf(String(o.preset)); let best = 0, bd = 1e9;
      e.speed_stops.forEach((s,i)=>{ const dd = Math.abs(e.presets.indexOf(s) - pos); if(dd < bd){ bd = dd; best = i; } }); return best; };
    const sync = src => {
      const e = enc(), [lo, hi] = e.rf, frac = (o.quality - lo) / (hi - lo), idx = stopIndex();
      q('cp-enc').value = o.encoder;
      if(src !== 'str'){ q('cp-str').min = lo; q('cp-str').max = hi; q('cp-str').value = Math.round(o.quality); }
      q('cp-str-v').textContent = `${STRENGTH_LABELS()[Math.max(0, Math.min(4, Math.floor(frac * 5)))]} · ${ltr('RF ' + +(+o.quality).toFixed(1))}`;
      if(src !== 'q') q('cp-q').value = +(+o.quality).toFixed(1);
      q('cp-q').min = lo; q('cp-q').max = hi;
      if(src === 'enc' || src === 'init') q('cp-pre').innerHTML = e.presets.map(pr=>`<option value="${pr}">${pr}</option>`).join('');
      if(src !== 'pre') q('cp-pre').value = o.preset;
      if(src !== 'spd') q('cp-spd').value = idx;
      q('cp-spd-v').textContent = `${SPEED_LABELS()[idx]} · ${ltr(String(o.preset))}`;
      q('cp-h').value = String(o.max_height || 0); q('cp-fm').value = o.fps_mode; q('cp-fps').value = o.fps;
      q('cp-fps-row').classList.toggle('hidden', o.fps_mode === 'same');
      q('cp-au').value = o.audio; q('cp-ab').value = o.audio_bitrate;
      q('cp-sum').innerHTML = compressSummary(o, e, idx);
    };
    syncV = sync; sync('init');
    const refreshSummary = ()=>{ q('cp-sum').innerHTML = compressSummary(o, enc(), stopIndex()); };   // summary only: don't rewrite a field that's being typed in
    q('cp-adv-t').onclick = ()=>q('cp-adv').classList.toggle('hidden');
    q('cp-str').oninput = ()=>{ o.quality = +q('cp-str').value; sync('str'); };
    q('cp-q').oninput = ()=>{ const v = parseFloat(q('cp-q').value), [lo, hi] = enc().rf; if(isFinite(v)){ o.quality = Math.max(lo, Math.min(hi, v)); sync('q'); } };
    q('cp-q').onchange = ()=>sync('init2');
    q('cp-spd').oninput = ()=>{ o.preset = enc().speed_stops[+q('cp-spd').value]; sync('spd'); };
    q('cp-pre').onchange = ()=>{ o.preset = q('cp-pre').value; sync('pre'); };
    q('cp-enc').onchange = ()=>{   // keep "how strong" and "how slow" when the encoder (and its scales) change
      const e = enc(), fq = (o.quality - e.rf[0]) / (e.rf[1] - e.rf[0]), si = stopIndex();
      o.encoder = q('cp-enc').value; const n = enc();
      o.quality = Math.round(n.rf[0] + fq * (n.rf[1] - n.rf[0])); o.preset = n.speed_stops[si]; sync('enc'); };
    q('cp-h').onchange = ()=>{ o.max_height = +q('cp-h').value; refreshSummary(); };
    q('cp-fm').onchange = ()=>{ o.fps_mode = q('cp-fm').value; sync('fm'); };
    q('cp-fps').oninput = ()=>{ const v = parseFloat(q('cp-fps').value); if(v > 0){ o.fps = v; refreshSummary(); } };
    q('cp-au').onchange = ()=>{ o.audio = q('cp-au').value; refreshSummary(); };
    q('cp-ab').oninput = ()=>{ const v = parseInt(q('cp-ab').value); if(v > 0){ o.audio_bitrate = v; refreshSummary(); } };
  }
  if(q('cp-hb')) q('cp-hb').onclick = ()=>send('POST', '/api/handbrake/open-page');
  if(hasI){
    // photo: strength slider (0 = weakest) <-> JPEG/WebP quality field, one state (oi)
    syncI = src => {
      const frac = (IMG_Q[1] - oi.quality) / (IMG_Q[1] - IMG_Q[0]);
      if(src !== 'str') q('ci-str').value = IMG_Q[1] - oi.quality;
      q('ci-str-v').textContent = `${STRENGTH_LABELS()[Math.max(0, Math.min(4, Math.floor(frac * 5)))]} · ${ltr(t('איכות {0}', [oi.quality]))}`;
      if(src !== 'q') q('ci-q').value = oi.quality;
      q('ci-ms').value = String(oi.max_side || 0);
      q('ci-sum').innerHTML = compressSummaryImg(oi);
    };
    syncI('init');
    q('ci-adv-t').onclick = ()=>q('ci-adv').classList.toggle('hidden');
    q('ci-str').oninput = ()=>{ oi.quality = IMG_Q[1] - +q('ci-str').value; syncI('str'); };
    q('ci-q').oninput = ()=>{ const v = parseInt(q('ci-q').value); if(isFinite(v)){ oi.quality = Math.max(IMG_Q[0], Math.min(IMG_Q[1], v)); syncI('q'); } };
    q('ci-q').onchange = ()=>syncI('init2');
    q('ci-ms').onchange = ()=>{ oi.max_side = +q('ci-ms').value; syncI('ms'); };
  }
  q('cp-reset').onclick = ()=>{
    if(hasV){ Object.assign(o, def, {quality:null, preset:null}); o.quality = enc().rf[2]; o.preset = enc().default_preset; syncV('enc'); }
    if(hasI){ Object.assign(oi, idef); syncI('init'); }
  };
  q('cp-cancel').onclick = closeModal;
  if(q('cp-restore')) q('cp-restore').onclick = async ()=>{ closeModal(); await compressRestore(items[0].id); };
  q('cp-go').onclick = async ()=>{
    if(hasV) pref.set('compress', o);
    if(hasI) pref.set('compress_img', oi);
    closeModal();
    compressProgressOpen(items.length === 1 ? [items[0]] : [...(hasV ? vids : []), ...imgs]);
    if(items.length === 1){
      const p = items[0]; CMP_PID = p.id;
      await send('POST', `/api/photo/${p.id}/compress`, {options: p.is_video ? o : oi});
      pollJob('compress', p.is_video ? t('דחיסת וידאו') : t('דחיסת תמונה'));
    } else {
      CMP_PID = null;
      await send('POST', '/api/compress/batch', {ids: [...(hasV ? vids : []), ...imgs].map(p => p.id), video: o, image: oi});
      pollJob('compress', t('דחיסת קבצים'));
    }
  };
}

async function afterVideoChanged(pid){
  VER[pid] = Date.now();
  await reloadAll();
  if(S.view === 'loupe' && S.act === pid){ $('#loupe-media').dataset.id = ''; renderLoupe(); }
}
async function compressRestore(pid){
  await send('POST', `/api/photo/${pid}/compress/restore`);
  toast((S.byId.has(pid) && !S.byId.get(pid).is_video) ? t('הוחלפה הגרסה של התמונה') : t('הוחלפה הגרסה של הסרטון'));
  await afterVideoChanged(pid);
}

function compressReport(r, pid){
  if(!r || !r.checks || !r.checks.length) return;
  const names = {frames:t('מספר פריימים'), duration:t('אורך הסרטון'), resolution:t('רזולוציה'), audio:t('אודיו'), size:t('גודל קובץ'), ssim:t('דמיון לתמונה המקורית (SSIM)'), metadata:t('פרטי הצילום (EXIF) ופרופיל הצבע')};
  const img = r.kind === 'image';
  const yn = v => v === 'yes' ? t('יש') : t('אין');
  const detail = c => c.id==='frames' ? t('צפוי {0} · התקבל {1}', [ltr(String(c.expected)), ltr(num(c.actual))])
    : c.id==='duration' ? t('סטייה {0} (מותר {1}) · מיכל {2} ms, וידאו {3} ms', [ltr(c.actual), ltr(c.expected), ltr(String(c.container_ms)), ltr(String(c.video_ms))])
    : c.id==='resolution' ? t('צפוי {0} · התקבל {1}', [ltr(c.expected), ltr(c.actual)])
    : c.id==='audio' ? t('צפוי {0} · התקבל {1}', [yn(c.expected), yn(c.actual)])
    : c.id==='size' ? t('{0} ← {1}', [fmtBytes(r.src.bytes), fmtBytes(r.out.bytes)])
    : c.id==='metadata' ? (c.ok ? t('נשמרו ללא שינוי') : t('השתנו'))
    : img ? t('דמיון {0} · {1}', [ltr(String(c.mean)), GRADE_LABELS()[c.grade]])
    : t('ממוצע {0} · הפריים הגרוע ביותר {1} · {2}', [ltr(String(c.mean)), ltr(String(c.min)), GRADE_LABELS()[c.grade]]);
  const ok = r.applied;
  modal(`<h3>${t('דוח דחיסה')}</h3><div class="mb">
    <p style="color:${ok?'var(--green)':'var(--yellow)'}">${ok ? (img ? t('התמונה נדחסה. הגרסה הקודמת נשמרה בגיבויים.') : t('הסרטון נדחס. הגרסה הקודמת נשמרה בגיבויים.')) : t('הדחיסה לא הוחלה. הקובץ המקורי לא שונה.')}</p>
    <p>${t('הגודל: {0} ← {1} ({2}% מהמקור)', [fmtBytes(r.src.bytes), fmtBytes(r.out.bytes), ltr(String(Math.round(r.ratio * 100)))])}</p>
    <div>${r.checks.map(c=>`<div class="chk ${c.ok?'ok':'bad'}">${I(c.ok ? 'check' : 'close')}<b>${names[c.id]}</b><span>${esc(detail(c))}</span></div>`).join('')}</div>
  </div><div class="mf">${ok ? `<button id="cr-restore">${t('החזר גרסה קודמת')}</button><span class="spacer"></span>` : ''}<button class="primary" id="cr-close">${t('סגור')}</button></div>`);
  $('#cr-close').onclick = closeModal;
  if(ok) $('#cr-restore').onclick = async ()=>{ closeModal(); await compressRestore(pid); };
}

// ---------- compression progress screen: percent, elapsed / remaining time, steps ----------
const CPG = {alive: false};
const fmtDur = s => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return ltr((h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(x).padStart(2, '0')); };
function compressProgressOpen(items){
  Object.assign(CPG, {alive: true, t0: Date.now(), n: items.length, items, ts: {}, f: 0});
  modal(`<h3>${t('דוחס…')}</h3><div class="mb cpg">
    <div class="cpg-file" id="cpg-file"><bdi>${esc(items[0].filename)}</bdi></div>
    <div class="cpg-bar"><i id="cpg-fill"></i><span id="cpg-pct">0%</span></div>
    <div class="cpg-stats"><span>${t('זמן שחלף')}: <b id="cpg-el">${fmtDur(0)}</b></span><span>${t('זמן משוער שנותר')}: <b id="cpg-eta">…</b></span><span id="cpg-fps"></span></div>
    <ul class="cpg-steps" id="cpg-steps"></ul>
    <div class="cpg-list" id="cpg-list"></div>
    <p class="hint" style="padding:0">${t('אפשר להמשיך לעבוד באפליקציה בזמן הדחיסה.')}</p>
  </div><div class="mf"><span class="spacer"></span><button id="cpg-bg">${t('המשך ברקע')}</button><button id="cpg-cancel">${t('בטל דחיסה')}</button></div>`);
  $('#cpg-bg').onclick = ()=>{ CPG.alive = false; closeModal(); };
  $('#cpg-cancel').onclick = async ()=>{ $('#cpg-cancel').disabled = true; $('#cpg-cancel').textContent = t('מבטל…'); await send('POST', '/api/compress/cancel'); };
  compressProgressUpdate({state: 'probing', done: 0, total: 0, extra: {}, result: null});
}
function compressProgressUpdate(p){
  if(!CPG.alive || !document.getElementById('cpg-fill')) return;
  const now = Date.now(), st = p.state, ex = p.extra || {};
  const i = CPG.n === 1 ? 0 : (ex.i ?? 0);
  const cur = CPG.items[Math.min(i, CPG.items.length - 1)];
  const isVid = ex.kind ? ex.kind === 'video' : !!cur.is_video;
  const W = isVid ? {probing: [0, .04], encoding: [.04, .85], verifying: [.85, .98], replacing: [.98, 1]}
                  : {probing: [0, .1], encoding: [.1, .55], verifying: [.55, .95], replacing: [.95, 1]};
  const key = s => i + ':' + s;
  if(W[st]) CPG.ts[key(st)] = CPG.ts[key(st)] || now;
  let ff = 0;
  if(W[st]){
    const [a, b] = W[st]; let w = 0.5;
    if(st === 'encoding' && isVid) w = p.total ? p.done / p.total : 0;
    else if(st === 'verifying' && isVid){        // no real percentage from the check: assume it takes ~1/4 of the encoding time
      const enc = (CPG.ts[key('verifying')] - (CPG.ts[key('encoding')] || CPG.ts[key('verifying')])) || 8000;
      w = Math.min(1, (now - CPG.ts[key('verifying')]) / Math.max(2000, enc * .25));
    }
    ff = a + (b - a) * w;
  }
  const f = Math.max(CPG.f, Math.min(.995, (i + ff) / CPG.n)); CPG.f = f;
  const el = (now - CPG.t0) / 1000;
  let eta = f > .03 ? el * (1 - f) / f : null;
  if(CPG.n === 1 && isVid && st === 'encoding' && ex.eta != null) eta = ex.eta * 1.25 + 3;      // HandBrake's own estimate, plus the check afterwards
  $('#cpg-fill').style.width = (f * 100).toFixed(1) + '%';
  $('#cpg-pct').textContent = Math.floor(f * 100) + '%';
  $('#cpg-el').textContent = fmtDur(el);
  $('#cpg-eta').textContent = eta == null ? '…' : '~' + fmtDur(eta);
  $('#cpg-fps').textContent = (isVid && st === 'encoding' && ex.fps) ? t('{0} פריימים לשנייה', [ltr(String(Math.round(ex.fps)))]) : '';
  $('#cpg-file').innerHTML = CPG.n === 1 ? `<bdi>${esc(cur.filename)}</bdi>` : `${t('קובץ {0} מתוך {1}', [num(i + 1), num(CPG.n)])}: <bdi>${esc(cur.filename)}</bdi>`;
  const order = ['probing', 'encoding', 'verifying', 'replacing'], at = order.indexOf(st);
  const names = [t('בדיקת הקובץ המקורי'), t('דחיסה'), t('אימות התוצאה'), t('גיבוי והחלפת הקובץ')];
  $('#cpg-steps').innerHTML = names.map((nm, k) => `<li class="${k < at ? 'done' : k === at ? 'cur' : ''}"><i>${k < at ? '✓' : k === at ? '●' : '○'}</i>${nm}</li>`).join('');
  const items = (p.result && p.result.items) || [];
  const fin = items.filter(it => it.status !== 'running');
  if(CPG.n > 1){
    const saved = fin.reduce((a, it) => a + (it.status === 'done' ? it.src_bytes - it.out_bytes : 0), 0);
    $('#cpg-list').innerHTML = fin.length ? `<div class="hint" style="padding:0">${t('הושלמו {0} מתוך {1} · נחסכו עד כה {2}', [num(fin.length), num(CPG.n), fmtBytes(saved)])}</div>`
      + fin.slice(-6).map(it => `<div class="cpg-row ${it.status === 'done' ? 'ok' : it.status === 'failed' ? 'bad' : 'warn'}"><i>${it.status === 'done' ? '✓' : it.status === 'failed' ? '✗' : '–'}</i><bdi>${esc(it.filename)}</bdi></div>`).join('') : '';
  }
}

// report for a batch: one row per file
function compressBatchReport(r){
  const c = r.counts || {}, n = r.items.length;
  const label = {done:t('נדחס'), no_gain:t('לא נחסך מקום: הקובץ כבר דחוס היטב, הוא נשאר כמו שהיה'), skipped:t('דולג'), failed:t('נכשל'), cancelled:t('בוטל')};
  const row = it => {
    const ok = it.status === 'done';
    const detail = ok ? t('{0} ← {1} ({2}% מהמקור)', [fmtBytes(it.src_bytes), fmtBytes(it.out_bytes), ltr(String(Math.round(it.ratio * 100)))]) + (it.grade ? ' · ' + GRADE_LABELS()[it.grade] : '')
      : it.status !== 'no_gain' && it.error_key ? t(it.error_key, it.error_vars) : label[it.status];
    const cls = ok ? 'ok' : (it.status === 'no_gain' || it.status === 'skipped' || it.status === 'cancelled') ? 'warn' : 'bad';
    return `<div class="chk ${cls}">${I(ok ? 'check' : 'close')}<b><bdi>${esc(it.filename)}</bdi></b><span>${esc(detail)}</span></div>`;
  };
  modal(`<h3>${t('דוח דחיסה')}</h3><div class="mb">
    <p style="color:${c.done ? 'var(--green)' : 'var(--yellow)'}">${t('נדחסו {0} מתוך {1} קבצים · נחסכו {2}', [num(c.done || 0), num(n), fmtBytes(r.saved || 0)])}</p>
    <p class="hint" style="padding:0">${t('כל קובץ נבדק בנפרד, והגרסה הקודמת שלו נשמרה בגיבויים.')}</p>
    <div class="cmp-rows">${r.items.map(row).join('')}</div>
  </div><div class="mf"><span class="spacer"></span><button class="primary" id="cr-close">${t('סגור')}</button></div>`);
  $('#cr-close').onclick = closeModal;
}

// ---------- updates from GitHub releases ----------
// Release notes are Markdown; show the common subset (headings, bullets, **bold**, `code`) -- escaped first, so it is always safe.
function mdLite(md){
  const inline = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`(.+?)`/g, '<code>$1</code>').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
  const out = []; let list = false;
  for(const raw of String(md).split(/\r?\n/)){
    const line = raw.trimEnd(), li = /^\s*[-*]\s+(.*)$/.exec(line), h = /^#{1,6}\s+(.*)$/.exec(line);
    if(li){ if(!list){ out.push('<ul>'); list = true; } out.push('<li>' + inline(li[1]) + '</li>'); continue; }
    if(list){ out.push('</ul>'); list = false; }
    if(h) out.push('<h4>' + inline(h[1]) + '</h4>'); else if(line.trim()) out.push('<p>' + inline(line) + '</p>');
  }
  if(list) out.push('</ul>');
  return out.join('');
}

async function updateCheck(manual){
  let info;
  try{ info = await api('/api/update/check' + (manual ? '?force=1' : '')); }
  catch(e){ if(manual) toast(e.message); return; }
  if(info.error){ if(manual) toast(t('לא ניתן לבדוק עדכונים: {0}', [info.error])); return; }
  if(!info.available){ if(manual) toast(t('אתם משתמשים בגרסה העדכנית ({0})', [ltr(info.current)])); return; }
  if(info.skipped && !manual) return;
  updateDialog(info);
}

// After an update the app shows the release notes of the new version (fetched from GitHub); an update that
// did not finish says so, and that nothing was lost. Returns true when it showed something.
async function whatsNew(manual){
  let r;
  try{ r = await api('/api/update/whatsnew' + (manual ? '?current=1' : '')); } catch(e){ if(manual) toast(e.message); return false; }
  const notesHtml = n => !n ? '' : n.error
    ? `<p class="hint" style="padding:0">${t('לא ניתן להביא את פירוט הגרסה מ‑GitHub ({0}).', [n.error])}</p><button class="linkbtn" id="wn-page">${t('פתח את דף השחרור')}</button>`
    : (n.notes && n.notes.trim() ? `<div class="upd-notes">${mdLite(n.notes)}</div>` : `<span class="hint" style="padding:0">${t('אין פירוט לגרסה הזו.')}</span>`);
  const close = async ()=>{ closeModal(); if(!manual) await send('POST', '/api/update/whatsnew/ack'); };
  const wire = ()=>{ $('#wn-close').onclick = close; if($('#wn-page')) $('#wn-page').onclick = ()=>send('POST', '/api/update/open-page'); };
  if(!manual && r.failed){
    modal(`<h3>${t('העדכון לא הושלם')}</h3><div class="mb upd">
      <p>${t('העדכון לגרסה {0} לא הסתיים (למשל המחשב כבה באמצע, או שההתקנה נסגרה).', [ltr(String(r.failed.to || ''))])}</p>
      <p>${r.failed.restored ? t('הגרסה הקודמת הוחזרה אוטומטית.') : t('הגרסה הקודמת נשארה במקומה ועובדת כרגיל.')}</p>
      <p>${t('התמונות, הקטלוג וההגדרות שלכם לא נפגעו. אפשר לנסות שוב מתפריט עזרה ← בדוק עדכונים.')}</p>
    </div><div class="mf"><span class="spacer"></span><button class="primary" id="wn-close">${t('סגור')}</button></div>`);
    wire(); return true;
  }
  if(!manual && r.updated){
    modal(`<h3>${t('עודכנת לגרסה {0}', [ltr(String(r.updated.to))])}</h3><div class="mb upd">
      <p>${t('העדכון הסתיים. התמונות והנתונים שלכם נשארו כמו שהיו.')}</p>
      <div class="lbl-sub" style="padding:0">${t('מה חדש')}</div>${notesHtml(r.notes)}
    </div><div class="mf"><span class="spacer"></span><button class="primary" id="wn-close">${t('סגור')}</button></div>`);
    wire(); return true;
  }
  if(manual){
    modal(`<h3>${t('מה חדש בגרסה {0}', [ltr(String(r.current))])}</h3><div class="mb upd">${notesHtml(r.notes)}</div>
      <div class="mf"><span class="spacer"></span><button class="primary" id="wn-close">${t('סגור')}</button></div>`);
    wire(); return true;
  }
  return false;
}

function updateDialog(info){
  const auto = info.can_install && info.frozen;
  modal(`<h3>${t('עדכון זמין')}</h3><div class="mb upd">
    <p>${t('גרסה {0} זמינה. הגרסה המותקנת: {1}.', [ltr(info.latest), ltr(info.current)])}</p>
    <div class="lbl-sub" style="padding:0">${t('מה חדש')}</div>
    <div class="upd-notes">${info.notes.trim() ? mdLite(info.notes) : `<span class="hint" style="padding:0">${t('אין פירוט לגרסה הזו.')}</span>`}</div>
    ${auto ? `<p class="hint" style="padding:0">${t('העדכון מותקן על הגרסה הקיימת. התמונות והנתונים שלכם לא נוגעים בו, ואם הוא יופסק באמצע (למשל המחשב ייכבה) הגרסה הקודמת תוחזר אוטומטית.')}</p>` : ''}
    <div id="up-prog" class="hidden"><div class="progress"><i id="up-bar"></i></div></div>
    <div class="hint" id="up-msg" style="padding:0;min-height:16px"></div>
  </div><div class="mf"><button id="up-skip">${t('דלג על גרסה זו')}</button><span class="spacer"></span>
    <button id="up-later">${t('אחר כך')}</button><button id="up-go" class="primary">${auto ? t('עדכן עכשיו') : t('פתח את דף השחרור')}</button></div>`);
  const msg = (text, cls='') => { $('#up-msg').textContent = text; $('#up-msg').className = 'hint ' + cls; $('#up-msg').style.padding = '0'; };
  const busy = on => ['up-skip', 'up-later', 'up-go'].forEach(id=>$('#' + id).disabled = on);
  $('#up-later').onclick = closeModal;
  $('#up-skip').onclick = async ()=>{ await send('POST', '/api/update/skip', {version:info.latest}); closeModal(); toast(t('הגרסה תידלג. אפשר תמיד לבדוק ידנית בתפריט עזרה.')); };
  $('#up-go').onclick = async ()=>{
    if(!auto){ await send('POST', '/api/update/open-page'); if(info.can_install) msg(t('בהרצה מקוד המקור אי אפשר להתקין עדכון אוטומטית. נפתח דף השחרור.')); return; }
    busy(true); $('#up-prog').classList.remove('hidden');
    try{
      await send('POST', '/api/update/download');
      let p;
      do{ await new Promise(r=>setTimeout(r, 350)); p = await api('/api/job/update');
          $('#up-bar').style.width = (p.total ? Math.round(100 * p.done / p.total) : 0) + '%';
          msg(p.key ? t(p.key, p.vars) : (p.msg || '')); }
      while(!['done', 'error'].includes(p.state));
      if(p.state === 'error') throw new Error(p.error_key ? t(p.error_key, p.vars) : p.error);
      const r = await send('POST', '/api/update/install', {path:p.result.path});
      if(r.mode === 'dry-run') msg(t('(בדיקה) הקובץ הורד ואומת ולא הופעל'), 'ok');
      else if(r.mode === 'page'){ await send('POST', '/api/update/open-page'); msg(t('בהרצה מקוד המקור אי אפשר להתקין עדכון אוטומטית. נפתח דף השחרור.')); busy(false); }
      else msg(t('מתקין ומפעיל מחדש…'), 'ok');
    }catch(e){ msg(e.message, 'err'); busy(false); $('#up-prog').classList.add('hidden'); }
  };
}

function languageDialog(){
  modal(`<h3>${t('שפה')}${I18N.lang==='en'?'':' / Language'}</h3><div class="mb"><div class="lang-grid">
    ${LANGS.map(([c,n,d])=>`<button class="${c===I18N.lang?'primary':''}" data-lang="${c}" dir="${d}">${n}</button>`).join('')}</div>
    <p>${t('מילות המפתח האוטומטיות נשארות באנגלית בכל השפות.')}</p></div>
    <div class="mf"><button onclick="closeModal()">${t('סגור')}</button></div>`);
  $$('#modal-box [data-lang]').forEach(b=>b.onclick=()=>{ if(b.dataset.lang!==I18N.lang) setLanguage(b.dataset.lang); else closeModal(); });
}
async function memories(){
  const m=await api('/api/memories'), tg=m.titles||[], c=m.comments||[];
  modal(`<h3>${t("זיכרונות ותגובות מ‑Google Photos")}</h3><div class="mb">
    ${!tg.length&&!c.length?("<p>"+t("לא יובאו זיכרונות או תגובות.")+"</p>"):''}
    ${tg.length?`<p>${t("כותרות זיכרונות")}</p><div class="mem-list">${tg.map(x=>`<div>${esc(x)}</div>`).join('')}</div>`:''}
    ${c.length?`<p>${t("תגובות באלבומים משותפים")}</p><div class="mem-list">${c.map(x=>`<div><time>${fdate(x.created_at)}</time>${x.liked?'♥ ':''}${esc(x.text)||t('(לייק)')}</div>`).join('')}</div>`:''}
  </div><div class="mf"><button class="primary" onclick="closeModal()">${t("סגור")}</button></div>`);
}
function shortcuts(){
  const k=(key,tg)=>`<kbd>${key}</kbd><span>${tg}</span>`;
  modal(`<h3>${t("קיצורי מקשים")}</h3><div class="mb"><div class="kgrid">
    <h4>${t("תצוגות")}</h4>${k('G',t('רשת'))}${k('E',t('זכוכית מגדלת'))}${k('C',t('השוואה'))}${k('N',t('סקירה'))}${k('O',t('אנשים'))}${k('D',t('מודול פיתוח'))}${k('Ctrl+Enter',t('מצגת'))}${k('Esc',t('חזרה / יציאה'))}
    <h4>${t("דירוג וסימון")}</h4>${k('P',t('דגל נבחרת'))}${k('X',t('דגל נדחית'))}${k('U',t('הסר דגל'))}${k('`',t('החלף דגל'))}${k('0–5',t('דירוג כוכבים'))}${k('[ / ]',t('הורד / העלה דירוג'))}${k('6–9',t('תווית אדום/צהוב/ירוק/כחול'))}${k(t('Shift+מקש'),t('סמן ועבור לבאה'))}${k('B',t('אוסף מהיר'))}${k('Ctrl+B',t('הצג אוסף מהיר'))}
    <h4>${t("בחירה")}</h4>${k('Ctrl+A',t('בחר הכול'))}${k('Ctrl+D',t('בטל בחירה'))}${k(t('Ctrl+לחיצה'),t('הוסף לבחירה'))}${k(t('Shift+לחיצה'),t('בחר טווח'))}${k('← → ↑ ↓',t('מעבר בין תמונות'))}${k('Delete',t('העבר לאשפה'))}
    <h4>${t("ממשק")}</h4>${k('Tab',t('הסתר לוחות צד'))}${k('Shift+Tab',t('הסתר את כל הלוחות'))}${k('F5 / F6',t('לוח עליון / רצועת תמונות'))}${k('F7 / F8',t('לוח ימני / שמאלי'))}${k('T',t('סרגל כלים'))}${k('L',t('כבה אורות'))}${k('J',t('סגנון תאים'))}${k('I',t('מידע בזכוכית מגדלת'))}${k('\\\\',t('סרגל סינון / לפני-אחרי'))}${k('Ctrl+L',t('הפעל/השבת מסננים'))}${k('Ctrl+F',t('חיפוש טקסט'))}${k(t('Z / רווח'),t('זום 1:1'))}
    <h4>${t("קבצים")}</h4>${k('Ctrl+Shift+I',t('ייבוא'))}${k('Ctrl+Shift+E',t('ייצוא'))}${k('Ctrl+N',t('אוסף חדש'))}${k('Ctrl+[ / ]',t('סיבוב'))}${k('Ctrl+R',t('הצג בסייר'))}${k('Ctrl+S',t('שמור מטא-נתונים לקובץ'))}${k('Ctrl+K',t('הוסף מילות מפתח'))}${k('R',t('חיתוך (פיתוח)'))}
    <h4>${t("וידאו")}</h4>${k('Space / K',t('הפעל / השהה'))}${k('Shift+← / →',t('דילוג 10 שניות'))}${k(', / .',t('פריים אחורה / קדימה'))}${k('Shift+, / .',t('מהירות ניגון'))}${k('↑ / ↓',t('עוצמת קול'))}${k('M',t('השתק'))}${k('F',t('מסך מלא'))}
  </div></div><div class="mf"><button class="primary" onclick="closeModal()">${t("סגור")}</button></div>`);
}

// ---------- export ----------
function openExport(){
  const ids=targets().length ? targets() : [];
  if(!ids.length) return toast(t('בחרו תמונות לייצוא'));
  const last=pref.get('export', {dest:'', mode:'current', edge:2048, q:90});
  modal(`<h3>${t("ייצוא {0} קבצים", [num(ids.length)])}</h3><div class="mb">
    <label class="fld"><span>${t("ייצא אל")}</span><div class="frow"><input type="text" id="ex-dest" dir="ltr" value="${esc(last.dest)}" placeholder="C:\\Users\\...\\Pictures\\Export"><button id="ex-pick">${t("בחר...")}</button></div></label>
    <div class="fld"><span>${t("הגדרות קובץ")}</span>
      <label class="check" style="padding:0"><input type="radio" name="ex-mode" value="current" ${last.mode==='current'?'checked':''}> ${t(" הקובץ כפי שהוא בקטלוג (כולל עריכות)")}</label>
      <label class="check" style="padding:0"><input type="radio" name="ex-mode" value="original" ${last.mode==='original'?'checked':''}> ${t(" המקור, בלי עריכות")}</label>
      <label class="check" style="padding:0"><input type="radio" name="ex-mode" value="jpeg" ${last.mode==='jpeg'?'checked':''}> ${t(" JPEG בגודל מותאם")}</label></div>
    <div class="frow" id="ex-jpeg"><span>${t("צלע ארוכה")}</span><input type="number" id="ex-edge" min="200" max="20000" value="${last.edge}" style="width:90px" dir="ltr"><span>${t("פיקסלים · איכות")}</span>
      <input type="range" id="ex-q" min="40" max="100" value="${last.q}" style="width:120px"><output id="ex-qv">${last.q}</output></div>
    <p>${t("סרטונים תמיד מועתקים כמו שהם. שמות קבצים כפולים מקבלים מספר.")}</p>
  </div><div class="mf"><button onclick="closeModal()">${t("ביטול")}</button><button class="primary" id="ex-go">${t("ייצוא")}</button></div>`);
  $('#ex-q').oninput=e=>$('#ex-qv').textContent=e.target.value;
  $('#ex-pick').onclick=async()=>{ const r=await api('/api/pick-file?kind=folder&title='+encodeURIComponent(t('בחר תיקיית ייצוא'))); if(r.path) $('#ex-dest').value=r.path; };
  $('#ex-go').onclick=async()=>{
    const dest=$('#ex-dest').value.trim(), mode=$('input[name=ex-mode]:checked').value, edge=+$('#ex-edge').value||null, q=+$('#ex-q').value;
    if(!dest) return toast(t('בחרו תיקיית יעד'));
    pref.set('export', {dest, mode, edge:edge||2048, q});
    closeModal();
    runJob('/api/export','export',t('ייצוא'),{ids, dest, originals:mode==='original', long_edge:mode==='jpeg'?edge:null, quality:mode==='jpeg'?q:100});
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
    <div class="blk"><span>${t("מקור")}</span><b>${esc(folder ? (IM.path||t('בחרו תיקייה')) : lr ? (IM.lrcat||t('בחרו קטלוג Lightroom')) : (IM.zip||t('בחרו קובץ ZIP')))}</b></div>
    <span class="im-arrow">←</span>
    <nav class="im-modes"><a data-im="folder" class="${folder?'on':''}">${t("העתק")}<small>${t("מתיקייה / כרטיס זיכרון")}</small></a><a data-im="lrcat" class="${lr?'on':''}">Lightroom Classic<small>${t("קטלוג ‎.lrcat")}</small></a><a data-im="zip" class="${IM.mode==='zip'?'on':''}">Google Takeout<small>${t("קובץ ZIP מגוגל פוטוס")}</small></a></nav>
    <span class="im-arrow">←</span>
    <div class="blk"><span>${t("יעד")}</span><b>${esc(S.status?.media_path||'')}</b></div>
  </div>
  <div class="im-body">
    <aside class="im-side">
      <section class="pnl"><h3><span>${t("מקור")}</span></h3><div class="pbody">
        ${folder ? `<div class="btnrow"><button id="im-pick">${I('folder')} ${t(" בחר תיקייה...")}</button></div>
          <label class="check"><input type="checkbox" id="im-rec" ${IM.recursive?'checked':''}> ${t(" כולל תיקיות משנה")}</label>
          ${recent.length?`<div class="lbl-sub" style="padding-top:8px">${t("אחרונים")}</div>${recent.map(p=>`<div class="row" data-recent="${esc(p)}">${I('folder')}<span class="nm" dir="ltr" title="${esc(p)}">${esc(p)}</span></div>`).join('')}`:''}`
        : lr ? `<div class="btnrow"><button id="im-lrcat">${I('import')} ${t(" בחר קטלוג Lightroom...")}</button></div>
          <div class="hint">${t("קובץ ‎")}<code>.lrcat</code>${t("‎ של Lightroom Classic (בדרך כלל ב‑Pictures/Lightroom). מומלץ לסגור את Lightroom לפני הייבוא.")}</div>`
        : `<div class="btnrow"><button id="im-zip">${I('import')} ${t(" בחר קובץ ZIP...")}</button></div>
          <div class="hint">${t("הורידו את הספרייה מ‑takeout.google.com (Google Photos). הקובץ נקרא ישירות, בלי לפרוס אותו.")}</div>`}
      </div></section>
    </aside>
    <div class="im-center">
      ${folder ? `<div class="im-bar"><a data-show="all" class="${IM.show==='all'?'on':''}">${t("כל התמונות")}</a><a data-show="new" class="${IM.show==='new'?'on':''}">${t("תמונות חדשות")}</a>
        <span class="spacer"></span><a data-chk="all">${t("סמן הכול")}</a><a data-chk="none">${t("בטל סימון")}</a></div>
        <div class="im-grid" id="im-grid">${IM.loading?("<div class=\"im-empty\">"+t("סורק…")+"</div>"):!IM.files.length?`<div class="im-empty">${IM.path?t('לא נמצאו תמונות או סרטונים בתיקייה.'):t('בחרו תיקייה או כרטיס זיכרון מהלוח „מקור".')}</div>`
          : shown.slice(0,3000).map(f=>`<div class="im-cell ${IM.on.has(f.path)?'':'off'}" data-path="${esc(f.path)}" title="${esc(f.path)}">
            <input type="checkbox" ${IM.on.has(f.path)?'checked':''}>${f.dup?("<span class=\"dup\" title=\""+t("כבר בקטלוג (אותו שם וגודל)")+"\">"+t("כפילות")+"</span>"):''}
            ${f.is_video?`<span class="vid">${I('play')}</span>`:`<img loading="lazy" src="/api/local-thumb?path=${encodeURIComponent(f.path)}" alt="">`}
            <span class="nm">${esc(f.name)}</span></div>`).join('') + (shown.length>3000?`<div class="im-empty">${t("מוצגות 3,000 הראשונות מתוך {0} — כולן ייובאו אם מסומנות.", [num(shown.length)])}</div>`:'')}</div>`
      : lr ? `<div class="im-grid"><div class="im-empty">${IM.lrloading?t('קורא את הקטלוג…'):!li?t('בחרו קטלוג Lightroom Classic מהלוח „מקור".')
          : `<b dir="ltr">${esc(IM.lrcat)}</b><br>${t("{0} תמונות · {1} מילות מפתח · {2} אוספים · {3}", [num(li.images), num(li.keywords), num(li.collections), fsize(li.bytes)])}
             ${li.missing?`<br><span style="color:var(--yellow)">${t("{0} קבצים שהקטלוג מפנה אליהם לא נמצאו בדיסק ולא ייובאו.", [num(li.missing)])}</span>`:''}`}</div></div>`
      : `<div class="im-grid"><div class="im-empty">${IM.zip?`<b dir="ltr">${esc(IM.zip)}</b><br>${t("ייבוא ישמור אלבומים, תאריכים, מיקומים, מועדפים, תגי אנשים וזיכרונות. תמונות שכבר בקטלוג לא ישוכפלו.")}`:t('בחרו את קובץ ה‑ZIP מ‑Google Takeout.')}</div></div>`}
    </div>
    <aside class="im-side">
      ${folder?`<section class="pnl"><h3><span>${t("טיפול בקבצים")}</span></h3><div class="pbody">
        <label class="check"><input type="checkbox" id="im-skipdup" ${IM.skipDup?'checked':''}> ${t(" אל תייבא כפילויות חשודות")}</label>
        <div class="hint">${t("קבצים זהים לגמרי (לפי תוכן) אף פעם לא נשמרים פעמיים.")}</div></div></section>
      <section class="pnl"><h3><span>${t("החל במהלך הייבוא")}</span></h3><div class="pbody">
        <label class="fld" style="padding:4px 12px"><span>${t("מילות מפתח")}</span><input type="text" id="im-kw" placeholder="${t("חופשה, משפחה")}"></label>
        <label class="fld" style="padding:4px 12px"><span>${t("הוסף לאוסף")}</span><input type="text" id="im-album" list="im-albums" placeholder="${t("ללא")}"></label>
        <datalist id="im-albums">${S.albums.filter(a=>a.kind==='album').map(a=>`<option value="${esc(a.name)}">`).join('')}</datalist></div></section>`:''}
      ${lr?`<section class="pnl"><h3><span>${t("מה מיובא")}</span></h3><div class="pbody"><div class="hint">
        ${t(" דירוגים, דגלים, תוויות צבע, כיתובים, תאריכי צילום, מיקומים, מילות מפתח (מילות „אדם\" הופכות לאנשים), אוספים והאוסף המהיר. הקבצים מועתקים לספרייה — המקור לא משתנה. עריכות Develop נשמרות בפורמט של Lightroom ולא מועברות; מיובא הקובץ המקורי. עותקים וירטואליים ואוספים חכמים מדולגים.")}</div></div></section>`:''}
      <section class="pnl"><h3><span>${t("יעד")}</span></h3><div class="pbody">
        <div class="hint"><code>${esc(S.status?.media_path||'')}</code><br>${t("מסודר בתיקיות לפי שנת צילום (למשל")} <code>2024</code>${t("). ניתן לשנות מקובץ ← הגדרות קטלוג.")}</div></div></section>
    </aside>
  </div>
  <div class="im-foot">${folder?`${t("{0} תמונות / {1}", [num(chosen.length), fsize(bytes)])}`:lr&&li?`${t("{0} תמונות / {1}", [num(li.images-li.missing), fsize(li.bytes)])}`:''}<span class="spacer"></span>
    <button id="im-cancel">${t("ביטול")}</button><button class="primary" id="im-go" ${ready?'':'disabled'}>${t("ייבוא")}</button></div>`;
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
  const tg=e.target;
  const mode=tg.closest('[data-im]'); if(mode){ IM.mode=mode.dataset.im; renderImport(); return; }
  if(tg.closest('#im-cancel')) return closeImport();
  if(tg.closest('#im-pick')){ const r=await api('/api/pick-file?kind=folder&title='+encodeURIComponent(t('בחר תיקייה לייבוא'))); if(r.path){ IM.path=r.path; scanImport(); } return; }
  if(tg.closest('#im-zip')){ const r=await api('/api/pick-file?kind=zip&title='+encodeURIComponent(t('בחר קובץ ZIP של Google Takeout'))); if(r.path){ IM.zip=r.path; renderImport(); } return; }
  if(tg.closest('#im-lrcat')){ const r=await api('/api/pick-file?kind=lrcat&title='+encodeURIComponent(t('בחר קטלוג Lightroom'))); if(!r.path) return;
    IM.lrcat=r.path; IM.lrinfo=null; IM.lrloading=true; renderImport();
    try{ IM.lrinfo=await api('/api/lrcat-info?'+new URLSearchParams({path:r.path})); } finally { IM.lrloading=false; renderImport(); } return; }
  const rec=tg.closest('[data-recent]'); if(rec){ IM.path=rec.dataset.recent; scanImport(); return; }
  const sh=tg.closest('[data-show]'); if(sh){ IM.show=sh.dataset.show; renderImport(); return; }
  const ck=tg.closest('[data-chk]'); if(ck){ IM.on = ck.dataset.chk==='all' ? new Set(IM.files.filter(f=>IM.show==='all'||!f.dup).map(f=>f.path)) : new Set(); renderImport(); return; }
  const cell=tg.closest('.im-cell'); if(cell){ const p=cell.dataset.path; IM.on.has(p)?IM.on.delete(p):IM.on.add(p);
    cell.classList.toggle('off', !IM.on.has(p)); cell.querySelector('input').checked=IM.on.has(p);
    const chosen=IM.files.filter(f=>IM.on.has(f.path)); $('#import .im-foot').firstChild.textContent=`${t("{0} תמונות / {1}", [num(chosen.length), fsize(chosen.reduce((a,f)=>a+f.bytes,0))])}`;
    $('#im-go').disabled=!chosen.length; return; }
  if(tg.closest('#im-go')){
    if(IM.mode==='zip'){ await send('POST','/api/import',{zip_path:IM.zip}); closeImport(); pollJob('import',t('ייבוא מ‑Google')); return; }
    if(IM.mode==='lrcat'){ await send('POST','/api/import-lrcat',{path:IM.lrcat}); closeImport(); pollJob('import',t('ייבוא מ‑Lightroom')); return; }
    const paths=IM.files.filter(f=>IM.on.has(f.path)).map(f=>f.path);
    await send('POST','/api/import-folder',{paths, keywords:($('#im-kw').value||'').split(','), album:$('#im-album').value||null});
    closeImport(); IM.files=[]; pollJob('import',t('ייבוא'));
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
  if(name==='compress') compressProgressUpdate(p);
  const act=$('#activity'), bar=$('.act-bar');
  act.classList.remove('hidden');
  const pct = p.total ? Math.round(100*p.done/p.total) : null;
  $('#act-label').textContent = `${label}${pct!=null?` · ${pct}%`:'…'}`;
  bar.classList.toggle('indet', pct==null); $('#act-fill').style.width = (pct??0)+'%';
  const msg = p.parts ? p.parts.map(x=>t(x.key, x.vars)).join(' · ') : p.key ? t(p.key, p.vars) : (p.msg || p.state);
  act.title = `${label}: ${msg}`;
  act.onclick = ()=>toast(`<b>${label}</b><br>${esc(msg)}${p.total?` (${num(p.done)}/${num(p.total)})`:''}`);
  if(['done','error','idle'].includes(p.state)){
    act.classList.add('hidden');
    if(name==='compress' && CPG.alive){ CPG.alive = false; closeModal(); }
    toast(`<bdi>${label}</bdi>: <bdi>${esc(p.error_key ? t(p.error_key, p.vars) : p.error || msg || t('הושלם'))}</bdi>`, 4000);   // bdi: Latin model names must not scramble RTL text
    if(['import','faces','aitag','compress'].includes(name)){
      await reloadAll();
      if(name==='import' && p.state==='done' && S.status.last_import) setSource(srcFromKey('prev'));
      if(S.view==='people') renderPeople();
      if(name==='aitag') renderRight();
      if(name==='compress'){
        if(p.result && p.result.batch){
          for(const it of p.result.items) if(it.status === 'done') VER[it.id] = Date.now();
          await reloadAll();
          if(S.view === 'loupe'){ $('#loupe-media').dataset.id = ''; renderLoupe(); }
          compressBatchReport(p.result);
        } else { const pid = CMP_PID ?? S.act; await afterVideoChanged(pid); compressReport(p.result, pid); }
      }
    }
    return;
  }
  setTimeout(()=>pollJob(name, label), 800);
}

// ---------- menu bar ----------
const sep='-';
const MENUS = [
  [t('קובץ'), [
    [t('ייבוא תמונות וסרטונים...'), 'Ctrl+Shift+I', ()=>openImport('folder')],
    [t('ייבוא מקטלוג Lightroom...'), '', ()=>openImport('lrcat')],
    [t('ייבוא מ‑Google Takeout...'), '', ()=>openImport('zip')],
    [t('ייצוא...'), 'Ctrl+Shift+E', openExport],
    sep,
    [t('הגדרות קטלוג...'), 'Ctrl+Alt+,', catalogSettings],
    [t('העדפות...'), 'Ctrl+,', preferences],
  ]],
  [t('עריכה'), [
    [t('בחר הכול'), 'Ctrl+A', selectAll],
    [t('בטל בחירה'), 'Ctrl+D', selectNone],
    [t('הפוך בחירה'), '', selectInvert],
    [t('בחר את המסומנות בדגל'), 'Ctrl+Alt+A', selectPicks],
  ]],
  [t('ספרייה'), [
    [t('אוסף חדש...'), 'Ctrl+N', newCollection],
    sep,
    [t('הצג אוסף מהיר'), 'Ctrl+B', ()=>setSource(srcFromKey('quick'))],
    [t('נקה אוסף מהיר'), '', async()=>{ const ids=S.all.filter(p=>p.quick).map(p=>p.id); if(ids.length) await setAttr({quick:0}, ids); }],
    sep,
    [t('הפעל מסננים'), 'Ctrl+L', ()=>{ S.F.on=!S.F.on; applyFilter(); }, null, ()=>S.F.on],
    [t('הצג סרגל סינון'), '\\', ()=>{ $('#filterbar').classList.toggle('hidden'); }, null, ()=>!$('#filterbar').classList.contains('hidden')],
    sep,
    [t('זיהוי פנים'), '', ()=>runJob('/api/faces','faces',t('זיהוי פנים'))],
    sep,
    [t('זיכרונות ותגובות מ‑Google...'), '', memories],
  ]],
  [t('תמונה'), [
    [t('הוסף לאוסף המהיר'), 'B', toggleQuick],
    [t('הצג בסייר'), 'Ctrl+R', reveal],
    sep,
    [t('סובב שמאלה'), 'Ctrl+[', ()=>rotateSel(-90)],
    [t('סובב ימינה'), 'Ctrl+]', ()=>rotateSel(90)],
    sep,
    [t('דגל: נבחרת'), 'P', ()=>setFlag(1)],
    [t('דגל: נדחית'), 'X', ()=>setFlag(-1)],
    [t('ללא דגל'), 'U', ()=>setFlag(0)],
    sep,
    ...[0,1,2,3,4,5].map(n=>[n?`${'★'.repeat(n)}`:t('ללא דירוג'), String(n), ()=>setRating(n)]),
    sep,
    ...LABELS.map(([k,n,key])=>[`${t("תווית: {0}", [n])}`, key, ()=>setLabel(k)]),
    [t('ללא תווית'), '', ()=>setAttr({label:''})],
    sep,
    [t('דחיסת הקבצים שנבחרו...'), '', ()=>compressDialog(targets())],
    [t('עצור דחיסה'), '', ()=>send('POST','/api/compress/cancel')],
    sep,
    [t('העבר לאשפה / שחזר'), 'Delete', trashSelected],
  ]],
  [t('מטא-נתונים'), [
    [t('הוסף מילות מפתח'), 'Ctrl+K', ()=>{ document.body.classList.remove('hide-right'); $('.pnl[data-p=kwing]').classList.remove('shut'); $('#kw-add')?.focus(); }],
    [t('תיוג AI לתמונות שנבחרו...'), '', aiRun],
    [t('הגדרות תיוג AI...'), '', aiSettings],
    [t('עצור תיוג AI'), '', ()=>send('POST','/api/aitag/cancel')],
    sep,
    [t('שמור מטא-נתונים לקובץ'), 'Ctrl+S', saveMetaToFile],
    [t('סנכרן מטא-נתונים'), '', ()=>$('#btn-sync-meta').click()],
  ]],
  [t('תצוגה'), [
    [t('שפה') + (I18N.lang==='en' ? '' : ' / Language') + '...', '', languageDialog],
    sep,
    [t('רשת'), 'G', ()=>setView('grid')], [t('זכוכית מגדלת'), 'E', ()=>setView('loupe')], [t('השוואה'), 'C', ()=>setView('compare')],
    [t('סקירה'), 'N', ()=>setView('survey')], [t('אנשים'), 'O', ()=>setView('people')], [t('פיתוח'), 'D', ()=>setModule('develop')],
    [t('מצגת'), 'Ctrl+Enter', ssStart],
    sep,
    [t('החלף סגנון תאים'), 'J', cycleCellStyle],
    [t('מידע בזכוכית מגדלת'), 'I', ()=>{ S.loupeInfo=!S.loupeInfo; renderLoupe(); renderToolbar(); }, null, ()=>S.loupeInfo],
    sep,
    [t('הסתר/הצג לוחות צד'), 'Tab', toggleSides],
    [t('הסתר/הצג את כל הלוחות'), 'Shift+Tab', toggleAllPanels],
    [t('סרגל כלים'), 'T', ()=>togglePanel('tool'), null, ()=>!document.body.classList.contains('hide-tool')],
    [t('כבה אורות'), 'L', cycleLights],
  ]],
  [t('עזרה'), [
    [t('קיצורי מקשים'), 'Ctrl+/', shortcuts],
    [t('בדוק עדכונים...'), '', ()=>updateCheck(true)],
    [t('מה חדש בגרסה הזו...'), '', ()=>whatsNew(true)],
    [t('אודות photag'), '', ()=>modal(`<h3>photag</h3><div class="mb"><p class="hint" style="padding:0">${t('גרסה {0}', [ltr(S.status?.version || '')])}</p><p>${t("ניהול ושמירת תמונות מקומי בהשראת Lightroom Classic: קטלוג, אוספים, דגלים, דירוגים, תוויות צבע, מילות מפתח, זיהוי פנים ועריכה לא הורסת — המקור תמיד נשמר.")}</p></div><div class="mf"><button class="primary" onclick="closeModal()">${t("סגור")}</button></div>`)],
  ]],
];
let MENU_OPEN=null;
$('#menubar').innerHTML = MENUS.map(([n],i)=>`<button data-menu="${i}">${n}</button>`).join('');
function openMenu(i){
  const b=$(`#menubar [data-menu="${i}"]`), pop=$('#menu-pop'), items=MENUS[i][1];
  $$('#menubar button').forEach(x=>x.classList.toggle('open', x===b));
  pop.innerHTML = items.map((it,j)=>it===sep?'<hr>':`<div class="mi ${it[4]&&it[4]()?'chk':''}" data-mi="${j}"><span>${it[0]}</span><span class="k">${it[1]||''}</span></div>`).join('');
  const r=b.getBoundingClientRect(); pop.style.top=r.bottom+'px'; if(RTL){ pop.style.right=(innerWidth-r.right)+'px'; pop.style.left='auto'; } else { pop.style.left=r.left+'px'; pop.style.right='auto'; }
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
    if(k==='Escape') ssStop(); else if(k==='ArrowLeft') ssStep(RTL?1:-1); else if(k==='ArrowRight') ssStep(RTL?-1:1); else if(k===' '){ e.preventDefault(); ssToggle(); }
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
      KeyL: ()=>{ S.F.on=!S.F.on; applyFilter(); toast(S.F.on?t('המסננים הופעלו'):t('המסננים הושבתו'), 1200); },
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
  if(vpKey(e)) return;
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
  const rtl = RTL;   // the grid flows in reading direction
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
  setTimeout(async ()=>{ if(!(await whatsNew(false))) updateCheck(false); }, 2500);   // quiet check on start-up; a window appears only when a newer release exists
  if(!S.all.length && !S.status.counts.trashed) openImport('folder');
  // resume the activity indicator if a job is already running (e.g. after a reload)
  [['import',t('ייבוא')],['faces',t('זיהוי פנים')],['aitag',t('תיוג AI')],['compress',t('דחיסת וידאו')],['export',t('ייצוא')]].forEach(async ([n,l])=>{
    try{ const p=await api('/api/job/'+n); if(p && p.state && !['done','error','idle'].includes(p.state)) pollJob(n,l); }catch{}
  });
})();
