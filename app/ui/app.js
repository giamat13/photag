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
const fsize = b => !b ? '—' : b >= 1099511627776 ? (b/1099511627776).toFixed(2)+' TB' : b >= 1073741824 ? (b/1073741824).toFixed(b >= 10737418240 ? 1 : 2)+' GB' : b > 1048576 ? (b/1048576).toFixed(1)+' MB' : Math.max(1, Math.round(b/1024))+' KB';
const I = (n, cls='') => `<svg class="ic ${cls}"><use href="#i-${n}"/></svg>`;
const ext = p => (p.filename.split('.').pop()||'').toUpperCase();
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function debounce(fn, ms){ let h; return (...a)=>{ clearTimeout(h); h=setTimeout(()=>fn(...a), ms); }; }
function toast(msg, ms=2600){ const tg=$('#toast'); tg.innerHTML=msg; tg.classList.remove('hidden'); clearTimeout(tg._h); tg._h=setTimeout(()=>tg.classList.add('hidden'), ms); }
window.addEventListener('unhandledrejection', e=>toast(t('Error: ')+esc(e.reason?.message||e.reason)));
const pref = {
  get(k, d){ try{ const v=localStorage.getItem('pm.'+k); return v==null ? d : JSON.parse(v); }catch{ return d; } },
  set(k, v){ try{ localStorage.setItem('pm.'+k, JSON.stringify(v)); }catch{} },
};

const LABELS = [['red',t('Red'),'6'],['yellow',t('Yellow'),'7'],['green',t('Green'),'8'],['blue',t('Blue'),'9'],['purple',t('Purple'),'']];
const LNAME = Object.fromEntries(LABELS.map(([k,n])=>[k,n]));
const lcol = k => `var(--${k})`;
// photo/media urls carry a version so edited photos don't come back stale from the browser cache
const VER = {};
const thumbUrl = id => `/thumb/${id}${VER[id]?'?v='+VER[id]:''}`;
const mediaUrl = id => `/media/${id}${VER[id]?'?v='+VER[id]:''}`;

// ---------- state ----------
const S = {
  mod:'library', view:'grid', prevView:'grid',
  src:{kind:'all', name:t('All Photographs')}, hist:[], histPos:-1,
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
  ['red',   t('Red Label'),          p=>p.label==='red'],
  ['five',  t('Five Stars'),         p=>p.rating===5],
  ['picks', t('Picks (Flag)'),           p=>p.flag===1],
  ['month', t('Last Month'),          p=>(p.taken_at||0) > Date.now()/1000 - MONTH_S],
  ['video', t('Video Files'),           p=>!!p.is_video],
  ['nokw',  t('No Keywords'),        p=>!p.has_kw],
  ['edited',t('Edited'),                p=>!!p.edited],
  ['fav',   t('Favorites from Google'),      p=>!!p.favorited],
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
const yearOf = p => p.taken_at ? String(new Date(p.taken_at*1000).getFullYear()) : t('None');
const monthOf = p => p.taken_at ? String(new Date(p.taken_at*1000).getMonth()+1).padStart(2,'0') : t('None');
const orientOf = p => !p.width||!p.height ? t('Unknown') : p.width>p.height*1.05 ? t('Landscape') : p.height>p.width*1.05 ? t('Portrait') : t('Square');
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
const META_COLS = [['year',t('Date'),yearOf],['month',t('Month'),monthOf],['ext',t('File Type'),ext],['orient',t('Orientation'),orientOf]];
function passMeta(p, upto=META_COLS.length){
  for(let i=0;i<upto;i++){ const [k,,fn]=META_COLS[i]; const set=S.F.meta[k]; if(set.size && !set.has(fn(p))) return false; }
  return true;
}
function filterActive(){
  const F=S.F; return !!(F.q || F.flags.size || F.rating || F.labels.size || F.kinds.size || Object.values(F.meta).some(s=>s.size));
}
// every sort ends with the same tie-breakers (capture time, then file name, then id), so equal values never shuffle
const byName = (a,b)=>a.filename.localeCompare(b.filename, I18N.locale, {numeric:true, sensitivity:'base'});
const tie = (a,b)=>(a.taken_at||0)-(b.taken_at||0) || byName(a,b) || a.id-b.id;
const megapix = p => (p.width||0)*(p.height||0);
const SORTS = {
  capture:[t('Capture Time'), (a,b)=>tie(a,b)],
  import: [t('Added Order'), (a,b)=>(a.imported_at||0)-(b.imported_at||0) || tie(a,b)],
  name:   [t('File Name'),   (a,b)=>byName(a,b) || tie(a,b)],
  ext:    [t('File Type'),   (a,b)=>ext(a).localeCompare(ext(b)) || tie(a,b)],
  folder: [t('Folder'),      (a,b)=>(a.folder||'').localeCompare(b.folder||'', I18N.locale, {numeric:true}) || tie(a,b)],
  rating: [t('Rating'),     (a,b)=>(a.rating||0)-(b.rating||0) || tie(a,b)],
  pick:   [t('Flag'),       (a,b)=>(a.flag||0)-(b.flag||0) || tie(a,b)],
  label:  [t('Color Label'), (a,b)=>lrank(a)-lrank(b) || tie(a,b)],
  size:   [t('File Size'), (a,b)=>(a.bytes||0)-(b.bytes||0) || tie(a,b)],
  dims:   [t('Dimensions'), (a,b)=>megapix(a)-megapix(b) || tie(a,b)],
  edited: [t('Edited'),    (a,b)=>(a.edited?1:0)-(b.edited?1:0) || tie(a,b)],
  trashed:[t('Date in Trash'), (a,b)=>(a.trashed_at||0)-(b.trashed_at||0) || tie(a,b)],
};
const SORT_ASC_FIRST = new Set(['name','ext','folder']);      // text sorts start A→Z, the rest start with the biggest / newest
const sortKeys = () => Object.keys(SORTS).filter(k => k!=='trashed' || S.src.kind==='trash');
const lrank = p => p.label ? LABELS.findIndex(l=>l[0]===p.label) : 9;
function applyFilter({keepScroll=true}={}){
  let rows = S.base;
  if(S.F.on){
    if(S.F.q && S.F.qf==='name'){ const q=S.F.q.toLowerCase(); rows = rows.filter(p=>p.filename.toLowerCase().includes(q)); }
    rows = rows.filter(p=>passAttr(p) && passMeta(p));
  }
  if(!SORTS[S.sort] || !sortKeys().includes(S.sort)) S.sort='capture';      // e.g. 'Date in Trash' after leaving the trash
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
  toast(on ? `${t("Added to Quick Collection ({0})", [num(ids.length)])}` : t('Removed from Quick Collection'));
}
async function restoreSelected(){
  const ids=targets(); if(!ids.length) return;
  await setAttr({trashed:0}, ids);
  toast(`${t("{0} items restored", [num(ids.length)])}`);
}
// inside the trash the Delete key deletes for good, after asking; restoring is a separate command (menu / right-click)
async function deleteForever(){
  const ids=targets(); if(!ids.length || S.src.kind!=='trash') return;
  if(!await confirmBox(t('Delete permanently?'), t('{0} items will be deleted permanently, together with their files. This cannot be undone.', [num(ids.length)]), t('Delete permanently'))) return;
  await send('POST', '/api/photos/delete-forever', {ids});
  await reloadAll();
  toast(t('{0} items deleted permanently', [num(ids.length)]));
}
async function emptyTrash(){
  const ids = S.base.map(p=>p.id); if(S.src.kind!=='trash' || !ids.length) return;
  if(!await confirmBox(t('Delete permanently?'), t('{0} items will be deleted permanently, together with their files. This cannot be undone.', [num(ids.length)]), t('Delete permanently'))) return;
  await send('POST', '/api/photos/delete-forever', {ids});
  await reloadAll();
  toast(t('{0} items deleted permanently', [num(ids.length)]));
}
async function trashSelected(){
  if(S.src.kind==='trash') return deleteForever();
  const ids=targets(); if(!ids.length) return;
  const restore = false;
  if(!restore && !pref.get('trashNoAsk', false)){      // deleting always asks first (restoring does not)
    const days = S.status?.trash_days || 60;
    const ok = await confirmBox(t('Move to trash?'),
      `${t('{0} items will be moved to the trash. You can restore them from there, and they will be permanently deleted after {1} days.', [num(ids.length), days])}
       <br><label class="chkrow" style="margin-top:12px"><input type="checkbox" id="cb-never"> ${t('Don\'t ask again about moving to trash')}</label>`, t('Move to Trash'));
    if(!ok) return;
    if(CB_NEVER) pref.set('trashNoAsk', true);
  }
  await setAttr({trashed: restore?0:1}, ids);
  toast(restore ? `${t("{0} items restored", [num(ids.length)])}` : `${t("{0} items moved to Trash · permanently deleted after {1} days", [num(ids.length), S.status?.trash_days||60])}`);
}
async function rotateSel(deg){
  const ids = targets().filter(id=>!(S.byId.get(id)||{}).is_video); if(!ids.length) return;
  toast(t('Rotating…'), 1200);
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
  if(!S.all.length && S.src.kind==='all') e.innerHTML = `<b>${t("The catalog is empty")}</b><div>${t("Import photos from a folder, a memory card or Google Takeout.")}</div><button class="primary" onclick="openImport()">${t("Import...")}</button>`;
  else if(S.base.length) e.innerHTML = `<b>${t("No photos match the filter")}</b><div>${t("{0} photos in this source are hidden by the filter.", [num(S.base.length)])}</div><button onclick="clearFilters()">${t("Clear Filter")}</button>`;
  else e.innerHTML = `<b>${t("No photos here")}</b>`;
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
    `<button class="flag ${p.flag===1?'pick':p.flag===-1?'rej':''}" data-a="flag" title="${t("Flag (P / X / U)")}">${I(p.flag===-1?'reject':'flag')}</button>
     <button class="qc ${p.quick?'on':''}" data-a="qc" title="${t("Quick Collection (B)")}">${I('dot')}</button>
     <button class="rot l" data-a="rotl" title="${t("Rotate Left (Ctrl+[)")}">${I('rotl')}</button>
     <button class="rot r" data-a="rotr" title="${t("Rotate Right (Ctrl+])")}">${I('rotr')}</button>
     <div class="stars ${r?'':'none'}">${stars}</div>
     ${badges.length?`<div class="badges" style="inset-block-start:${Math.round(by+c._h-18)}px;inset-inline-end:${Math.round(bx+4)}px">${badges.map(b=>`<i>${b}</i>`).join('')}</div>`:''}
     ${p.is_video?`<span class="dur" style="inset-block-start:${Math.round(by+c._h-18)}px;inset-inline-start:${Math.round(bx+4)}px">${I('play')}${t("Video")}</span>`:''}`;
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
  $('#fs-path').innerHTML = `<b>${esc(S.src.name)}</b> ${t(": {0} photos", [num(n)])}${S.base.length!==n?` ${t("(of {0})", [num(S.base.length)])}`:''}${s?` ${t("/ {0} selected", [num(s)])}`:''}${p?` / <b dir="ltr">${esc(p.filename)}</b>`:''}`;
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
    + `<button class="tg lab ${F.labels.has('none')?'on':''}" data-fl="none" title="${t("No Label")}"><span class="sw" style="background:#555"></span></button>`;
  const flags = flag('pick', I('flag'), t('Picks')) + flag('none', `<svg class="ic"><use href="#i-flag"/></svg>`.replace('ic"','ic" style="opacity:.45"'), t('Unflagged')) + flag('rej', I('reject'), t('Rejects'));
  if(small) return `<span>${t("Filter:")}</span>${flags}<span class="tb-sep"></span>
    <select class="op" data-fop><option ${F.rop==='>='?'selected':''} value=">=">≥</option><option ${F.rop==='<='?'selected':''} value="<=">≤</option><option ${F.rop==='='?'selected':''} value="=">=</option></select>${stars}<span class="tb-sep"></span>${labs}
    <button class="tg ${F.on?'':'on'}" data-foff title="${t("Enable/Disable Filters (Ctrl+L)")}">${F.on?t('On'):t('Off')}</button>`;
  return `<div class="attr-grp"><span>${t("Flag")}</span>${flags}</div>
    <div class="attr-grp"><span>${t("Rating")}</span><select class="op" data-fop><option ${F.rop==='>='?'selected':''} value=">=">≥</option><option ${F.rop==='<='?'selected':''} value="<=">≤</option><option ${F.rop==='='?'selected':''} value="=">=</option></select>${stars}</div>
    <div class="attr-grp"><span>${t("Color")}</span>${labs}</div>
    <div class="attr-grp"><span>${t("Kind")}</span>
      <button class="tg ${F.kinds.has('photo')?'on':''}" data-fk="photo" title="${t("Photos")}">${I('photos')}</button>
      <button class="tg ${F.kinds.has('video')?'on':''}" data-fk="video" title="${t("Video")}">${I('play')}</button>
      <button class="tg ${F.kinds.has('edited')?'on':''}" data-fk="edited" title="${t("Edited")}">${I('dev')}</button></div>`;
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
  $('#fb-state').innerHTML = filterActive() ? (S.F.on ? ("<b>"+t("Filter active")+"</b>") : t('Filter off')) : '';
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
    const label = v => k==='month' && v!==t('None') ? new Date(2000, +v-1, 1).toLocaleDateString(I18N.locale,{month:'long'}) : v;
    return `<div class="mcol"><h4>${title}</h4><div class="mlist">
      <div class="row ${set.size?'':'on'}" data-mk="${k}" data-mv=""><span class="nm">${t("All ({0})", [vals.length])}</span><span class="n">${num([...counts.values()].reduce((a,b)=>a+b,0))}</span></div>
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
    row('all', I('photos'), t('All Photographs'), S.all.length) +
    row('quick', I('coll'), t('Quick Collection +'), q) +
    (st.last_import ? row('prev', I('import'), t('Previous Import'), prev) : '') +
    row('trash', I('trash'), t('Trash'), st.counts.trashed);
  markSourceRows();
}
function renderFolders(){
  const f=S.folders;
  $('#p-folders').innerHTML = `<div class="vol" title="${esc(f.root)}">${I('drive')}<span>${t("Library")}</span><span class="path">${esc(f.root)}</span></div>` +
    (f.folders.map(x=>row('folder:'+x.name, I('folder'), x.name || t('(root)'), x.n, '', 'ind')).join('') || ("<div class=\"hint\">"+t("No folders yet")+"</div>"));
  markSourceRows();
}
const OPEN_SETS = new Set(pref.get('openSets', ['smart','album']));
function renderColls(){
  const al=S.albums;
  const set = (key, title, items) => {
    const open=OPEN_SETS.has(key);
    return `<div class="row set" data-set="${key}"><span class="tw">${open?'▼':'◀'}</span>${I('set')}<span class="nm">${title}</span></div>` + (open ? items : '');
  };
  const coll = a => row('album:'+a.id, I('coll'), a.name, a.n, `<button class="x" data-del="${a.id}" title="${t("Delete Collection")}">${I('close')}</button>`, 'ind');
  const smart = SMART.map(([k,n,f])=>row('smart:'+k, I('smart'), n, S.all.filter(f).length, '', 'ind')).join('');
  const people = S.people.map(p=>row('person:'+p.id, I('people'), p.name, (p.face_photos||0)+(p.tag_photos||0), '', 'ind')).join('');
  $('#p-colls').innerHTML =
    set('smart', t('Smart Collections'), smart) +
    set('album', t('Collections'), al.filter(a=>a.kind==='album').map(coll).join('') || ("<div class=\"hint\">"+t("Drag photos here after creating a collection")+"</div>")) +
    (al.some(a=>a.kind==='people-share') ? set('shared', t('Shared Albums'), al.filter(a=>a.kind==='people-share').map(coll).join('')) : '') +
    (al.some(a=>a.kind==='year') ? set('year', t('By Year (Google)'), al.filter(a=>a.kind==='year').map(coll).join('')) : '') +
    (S.people.length ? set('people', t('People'), people) : '');
  markSourceRows();
}
function markSourceRows(){ const k=srcKey(S.src); $$('#left [data-src]').forEach(r=>r.classList.toggle('on', r.dataset.src===k)); }
function srcFromKey(key){
  const [kind, id] = key.split(/:(.*)/s);
  const name = {all:t('All Photographs'), quick:t('Quick Collection'), prev:t('Previous Import'), trash:t('Trash')}[kind]
    || (kind==='folder' ? (id||t('(root)')) : kind==='smart' ? SMART.find(s=>s[0]===id)[1]
      : kind==='album' ? S.albums.find(a=>a.id==id)?.name : kind==='person' ? S.people.find(p=>p.id==id)?.name : '');
  return {kind, id: id===undefined ? null : (['album','person','tag','cluster'].includes(kind) ? +id : id), name};
}
$('#left').addEventListener('click', async e=>{
  const del=e.target.closest('[data-del]');
  if(del){ e.stopPropagation(); const a=S.albums.find(x=>x.id==del.dataset.del);
    if(!await confirmBox(`${t("Delete the collection “{0}”?", [esc(a.name)])}`, t('The photos themselves will stay in the catalog.'), t('Delete'))) return;
    await send('DELETE', '/api/album/'+a.id); if(S.src.kind==='album' && S.src.id===a.id) setSource(srcFromKey('all')); loadSide(); return; }
  const st=e.target.closest('[data-set]');
  if(st){ const k=st.dataset.set; OPEN_SETS.has(k)?OPEN_SETS.delete(k):OPEN_SETS.add(k); pref.set('openSets',[...OPEN_SETS]); renderColls(); return; }
  const r=e.target.closest('[data-src]'); if(r){ if(S.mod!=='library') setModule('library'); setSource(srcFromKey(r.dataset.src)); }
});
$('#left').addEventListener('dblclick', async e=>{
  const r=e.target.closest('[data-src^="album:"]'); if(!r) return;
  const a=S.albums.find(x=>'album:'+x.id===r.dataset.src);
  const n=await promptBox(t('Rename Collection'), a.name); if(!n || n===a.name) return;
  await send('POST', `/api/album/${a.id}/rename`, {name:n}); await loadSide(); if(S.src.kind==='album'&&S.src.id===a.id){ S.src.name=n; renderPath(); }
});
// drag photos onto a collection / the Quick Collection
$('#left').addEventListener('dragover', e=>{ const r=e.target.closest('[data-src^="album:"],[data-src="quick"]'); if(r && e.dataTransfer.types.includes('text/x-pm-ids')){ e.preventDefault(); $$('#left .drop').forEach(x=>x!==r&&x.classList.remove('drop')); r.classList.add('drop'); } });
$('#left').addEventListener('dragleave', e=>{ const r=e.target.closest('.drop'); if(r && !r.contains(e.relatedTarget)) r.classList.remove('drop'); });
$('#left').addEventListener('drop', async e=>{
  const r=e.target.closest('[data-src^="album:"],[data-src="quick"]'); $$('#left .drop').forEach(x=>x.classList.remove('drop')); if(!r) return;
  e.preventDefault(); const ids=JSON.parse(e.dataTransfer.getData('text/x-pm-ids')||'[]'); if(!ids.length) return;
  if(r.dataset.src==='quick'){ await setAttr({quick:1}, ids); toast(`${t("{0} added to Quick Collection", [num(ids.length)])}`); return; }
  const aid=+r.dataset.src.split(':')[1];
  await send('POST', `/api/album/${aid}/add`, {ids}); toast(`${t("{0} photos added to “{1}”", [num(ids.length), esc(S.albums.find(a=>a.id===aid)?.name)])}`); loadSide();
});
async function newCollection(){
  const ids=[...S.sel];
  const n=await promptBox(t('Create Collection'), '', ids.length?`<label class="check" style="padding:0"><input type="checkbox" id="nc-sel" checked> ${t("Include the selected photos ({0})", [num(ids.length)])}</label>`:'');
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
  ['grid','loupe','compare','survey','people','map'].forEach(k=>$('#v-'+k).classList.toggle('hidden', k!==v));
  $('#v-empty').classList.add('hidden');
  if(v!=='loupe'){ closeLoupeMedia(); }
  if(v==='grid'){ layoutGrid(true); scrollToAct(); $('#v-grid').focus({preventScroll:true}); if(!S.list.length){ $('#v-empty').classList.remove('hidden'); renderEmpty(); } }
  if(v==='loupe') renderLoupe();
  if(v==='compare') renderCompare();
  if(v==='survey') renderSurvey();
  if(v==='people') renderPeople();
  if(v==='map') renderMapView();
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
  .then(r=>{ if(r.player==='default') toast(t('Opened in the system\'s default player')); });

function mountPlayer(m, p){
  vpDestroy();
  const el = document.createElement('div'); el.className = 'vp paused'; el.dir = 'ltr';
  const b = (k, inner, title, cls='') => `<button data-vp="${k}" class="${cls}" title="${esc(title)}" aria-label="${esc(title)}">${inner}</button>`;
  const volTip = t('Volume (↑ ↓)');
  el.innerHTML = `
    <video src="${mediaUrl(p.id)}" playsinline preload="auto" autoplay></video>
    <div class="vp-err hidden"><span>${t('This format can\'t be played inside the app.')}</span><button class="primary" data-vp="ext">${t('Open in external player')}</button></div>
    <div class="vp-bar">
      <div class="vp-seek"><div class="vp-buf"></div><div class="vp-played"></div><div class="vp-knob"></div>
        <div class="vp-tip hidden"><canvas width="160" height="90"></canvas><span></span></div></div>
      <div class="vp-row">
        ${b('play', I('play'), t('Play / Pause (Space)'))}
        ${b('back', I('rotl')+'<i class="n10">10</i>', t('Back 10 seconds'))}
        ${b('fwd', I('rotr')+'<i class="n10">10</i>', t('Forward 10 seconds'))}
        <span class="vp-time"><b data-vp="cur">0:00</b> / <span data-vp="dur">0:00</span></span>
        <span class="spacer"></span>
        ${b('mute', I('vol'), t('Mute (M)'))}<input class="vp-vol" type="range" min="0" max="1" step="0.02" title="${esc(volTip)}" aria-label="${esc(volTip)}">
        ${b('rate', '1×', t('Playback speed'), 'vp-rate')}
        ${b('loop', I('loop'), t('Loop video'))}
        ${document.pictureInPictureEnabled ? b('pip', I('pip'), t('Picture in picture')) : ''}
        ${b('compress', I('compress'), t('Compress Video (HandBrake)'))}
        ${b('ext', I('external'), t('Open in external player (VLC if installed)'))}
        ${b('full', I('full'), t('Fullscreen (F)'))}
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
  info.innerHTML = `<b>${esc(p.filename)}</b><span>${fdate(p.taken_at)}</span><br><span dir="ltr">${p.width&&p.height?p.width+' × '+p.height:''}  ${fsize(p.bytes)}</span>${p.flag===-1?("<br><span>"+t("Rejected")+"</span>"):''}`;
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
  $('#v-compare').innerHTML = pane(a,t('Selection'),'sel') + pane(S.byId.get(b)||S.base.find(x=>x.id===b),t('Candidate'),'');
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
    return `<div class="sv ${id===S.act?'act':''}" data-id="${id}" style="width:${bw}px;height:${bh}px"><img src="${bw>300?mediaUrl(id):thumbUrl(id)}" alt=""><button class="x" data-x title="${t("Remove from Survey")}">${I('close')}</button></div>`; }).join('');
}
$('#v-survey').addEventListener('click', e=>{ const c=e.target.closest('.sv'); if(!c) return; const id=+c.dataset.id;
  if(e.target.closest('[data-x]')){ S.sel.delete(id); if(S.act===id) S.act=[...S.sel][0]??null; onSelChange(); return; }
  S.act=id; onSelChange(); });

async function renderPeople(){
  const el=$('#v-people'); el.innerHTML=("<div class=\"hint\">"+t("Loading…")+"</div>");
  const [people, clusters] = await Promise.all([api('/api/people'), api('/api/clusters')]);
  S.people=people;
  const face = src => src ? `<img loading="lazy" src="${src}" alt="">` : I('people');
  el.innerHTML = `<h2>${t("Named People")} <span>${num(people.length)}</span></h2>
    <div class="pgrid">${people.map(p=>`<div class="pc" data-person="${p.id}"><div class="face">${face(p.cover_face?'/face/'+p.cover_face:p.cover_photo?thumbUrl(p.cover_photo):'')}</div>
      <div class="nm" title="${t("Double-click to rename")}">${esc(p.name)}</div><div class="ct">${num((p.face_photos||0)+(p.tag_photos||0))}</div></div>`).join('') || ("<div class=\"hint\">"+t("There are no named people yet.")+"</div>")}</div>
    <h2>${t("Unnamed People")} <span>${num(clusters.length)}</span></h2>
    ${clusters.length ? `<div class="pgrid">${clusters.map(c=>`<div class="pc" data-cluster="${c.id}"><div class="face">${face(c.cover_face?'/face/'+c.cover_face:'')}</div>
      <input placeholder="?" data-name-cluster="${c.id}" title="${t("Type a name and press Enter")}"><div class="ct">${num(c.n)}</div></div>`).join('')}</div>`
      : `<div class="hint">${S.status?.counts.faces ? t('All face groups have been named.') : t('Face detection has not been run yet. Library → Face Detection.')}</div>`}`;
}
$('#v-people').addEventListener('click', e=>{
  const face=e.target.closest('.face'); if(!face) return;
  const pc=face.closest('.pc');
  if(pc.dataset.person){ const p=S.people.find(x=>x.id==pc.dataset.person); setSource({kind:'person', id:p.id, name:p.name}); setView('grid'); }
  else setSource({kind:'cluster', id:+pc.dataset.cluster, name:t('Unnamed person')}).then(()=>setView('grid'));
});
$('#v-people').addEventListener('dblclick', async e=>{
  const nm=e.target.closest('.nm'); if(!nm) return; const id=+nm.closest('.pc').dataset.person, p=S.people.find(x=>x.id===id);
  const n=await promptBox(t('Rename'), p.name); if(!n) return;
  await send('POST', `/api/person/${id}/rename`, {name:n}); toast(t('Name updated')); await loadSide(); renderPeople();
});
$('#v-people').addEventListener('keydown', async e=>{
  const inp=e.target.closest('[data-name-cluster]'); if(!inp || e.key!=='Enter') return;
  const n=inp.value.trim(); if(!n) return;
  await send('POST', `/api/cluster/${inp.dataset.nameCluster}/name`, {name:n}); toast(`${t("Named “{0}”", [esc(n)])}`); await loadSide(); renderPeople();
});

// ---------- map: pins for the photos you selected (Leaflet, bundled; tiles from OpenStreetMap) ----------
let MAP = null, MAPLAYER = null, SIDEMAP = null, MAPINFO = '', MAPSEQ = 0;
const TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
function mapTiles(onFail){
  let bad = 0;
  const l = L.tileLayer(TILES, {maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>', className: 'map-tiles'});
  l.on('tileerror', ()=>{ if(++bad === 4 && onFail) onFail(); });
  l.on('tileload', ()=>{ bad = 0; });
  return l;
}
const pinIcon = n => L.divIcon({className: 'mp', html: `<i></i>${n > 1 ? `<b>${n > 99 ? '99+' : n}</b>` : ''}`, iconSize: [26, 26], iconAnchor: [13, 30], popupAnchor: [0, -28]});
function drawMiniMap(d){
  const m = L.map('mini-map', {zoomControl: false, attributionControl: false, scrollWheelZoom: false, dragging: false, doubleClickZoom: false, boxZoom: false, keyboard: false, touchZoom: false})
    .setView([d.lat, d.lng], 13);
  mapTiles().addTo(m);
  L.marker([d.lat, d.lng], {icon: pinIcon(1), interactive: false}).addTo(m);
  SIDEMAP = m;
}
async function renderMapView(){
  const note = $('#map-note'); note.className = 'map-note'; note.textContent = '';
  if(!MAP){
    MAP = L.map('map-canvas', {zoomControl: true, worldCopyJump: true}).setView([31.8, 35.0], 3);
    mapTiles(()=>{ const n = $('#map-note'); n.classList.add('show', 'warn'); n.textContent = t('No internet connection, so map tiles can\'t load. The pins are still shown.'); }).addTo(MAP);
    MAPLAYER = L.layerGroup().addTo(MAP);
  }
  MAP.invalidateSize();
  const ids = S.sel.size ? [...S.sel] : S.list.map(p=>p.id);
  const token = ++MAPSEQ;
  let r; try{ r = await send('POST', '/api/map', {ids}); } catch(e){ return toast(e.message); }
  if(token !== MAPSEQ) return;                    // a newer render started meanwhile: only the last one draws
  MAPLAYER.clearLayers();
  // photos taken at the same place share one pin (with a count)
  const groups = new Map();
  for(const p of r.points){ const k = p.lat.toFixed(4) + ',' + p.lng.toFixed(4); (groups.get(k) || groups.set(k, []).get(k)).push(p); }
  const bounds = [];
  for(const [, ps] of groups){
    const m = L.marker([ps[0].lat, ps[0].lng], {icon: pinIcon(ps.length), title: ps.length === 1 ? ps[0].filename : ''}).addTo(MAPLAYER);
    m.bindPopup(()=>`<div class="mp-pop" dir="auto"><div class="mp-grid">${ps.slice(0, 12).map(p=>`<img src="${thumbUrl(p.id)}" data-pid="${p.id}" title="${esc(p.filename)}" alt="">`).join('')}</div>
      <div class="mp-cap">${ps.length === 1 ? `<bdi>${esc(ps[0].filename)}</bdi> · ${fdate(ps[0].taken_at)}` : t('{0} photos at this place', [num(ps.length)])}</div></div>`, {minWidth: 190, maxWidth: 320});
    bounds.push([ps[0].lat, ps[0].lng]);
  }
  if(bounds.length === 1) MAP.setView(bounds[0], 14);
  else if(bounds.length) MAP.fitBounds(bounds, {padding: [50, 50], maxZoom: 15});
  MAPINFO = r.points.length ? t('{0} photos on the map, {1} places', [num(r.points.length), num(groups.size)]) + (r.without ? ' · ' + t('{0} without location', [num(r.without)]) : '')
    : t('The selected photos have no location');
  const inf = $('#map-info'); if(inf) inf.textContent = MAPINFO;
  if(!r.points.length){ note.classList.add('show'); note.textContent = t('The selected photos have no GPS location. You can add a latitude and longitude in the metadata panel on the side.'); }
}
$('#v-map').addEventListener('click', e=>{
  const im = e.target.closest('[data-pid]'); if(!im) return;
  selectOnly(+im.dataset.pid); setView('loupe');
});

// ---------- toolbar ----------
function tbViews(){
  const b=(v,ic,tg)=>`<button class="tb-btn ${S.view===v?'on':''}" data-view="${v}" title="${tg}">${I(ic)}</button>`;
  return `<div class="tb-grp">${b('grid','grid',t('Grid View (G)'))}${b('loupe','loupe',t('Loupe (E)'))}${b('compare','compare',t('Compare (C)'))}${b('survey','survey',t('Survey (N)'))}${b('people','face',t('People (O)'))}${b('map','pin',t('Map: selected photos on the map'))}</div>`;
}
function tbAttrs(){
  const p=actPhoto(), r=p?.rating||0;
  return `<div class="tb-grp"><button class="tb-btn tb-flag pick ${p?.flag===1?'on':''}" data-t="pick" title="${t("Flag as Pick (P)")}">${I('flag')}</button>
    <button class="tb-btn tb-flag rej ${p?.flag===-1?'on':''}" data-t="rej" title="${t("Flag as Rejected (X)")}">${I('reject')}</button></div>
    <span class="tb-sep"></span><span class="tb-stars">${[1,2,3,4,5].map(n=>`<b data-star="${n}" class="${n<=r?'on':''}" title="${t("{0} (key {1})", [n, n])}">★</b>`).join('')}</span>
    <span class="tb-sep"></span><span class="tb-labs">${LABELS.map(([k,n,key])=>`<button data-lab="${k}" class="${p?.label===k?'on':''}" style="background:${lcol(k)}" title="${n}${key?` (${key})`:''}"></button>`).join('')}</span>
    <span class="tb-sep"></span><div class="tb-grp"><button class="tb-btn" data-t="rotl" title="${t("Rotate Left (Ctrl+[)")}">${I('rotl')}</button><button class="tb-btn" data-t="rotr" title="${t("Rotate Right (Ctrl+])")}">${I('rotr')}</button></div>`;
}
function renderToolbar(){
  const tb=$('#toolbar');
  if(S.mod==='develop'){ const p=actPhoto();
    tb.innerHTML = `<button class="tb-btn ${DEV.before?'on':''}" data-t="before" title="${t("Before/After (\\)")}">${t("Before / After")}</button><span class="tb-sep"></span>
      <button class="tb-btn ${DEV.crop?'on':''}" data-t="crop" title="${t("Crop (R)")}">${I('crop')}</button><span class="spacer"></span>
      <span class="tb-info">${p?`<bdi>${esc(p.filename)}</bdi>`:''}${DEV.dirty?t(' · unapplied changes'):''}</span>`; return; }
  let h = tbViews() + '<span class="tb-sep"></span>';
  if(S.view==='grid') h += `<div class="tb-sort"><span class="tb-lbl">${t("Sort:")}</span><button class="tb-btn" data-t="asc" title="${S.asc?t('Ascending'):t('Descending')}" style="${S.asc?'':'transform:scaleY(-1)'}">${I('sort')}</button>
      <select data-t="sort">${sortKeys().map(k=>`<option value="${k}" ${k===S.sort?'selected':''}>${SORTS[k][0]}</option>`).join('')}</select></div><span class="tb-sep"></span>` + tbAttrs() +
      `<label class="tb-size"><span>${t("Thumbnails")}</span><input type="range" data-t="size" min="110" max="420" step="10" value="${S.cellsz}"></label>`;
  else if(S.view==='loupe') h += tbAttrs() + `<span class="spacer"></span><button class="tb-btn ${S.loupeInfo?'on':''}" data-t="info" title="${t("Info (I)")}">${t("Info")}</button>`;
  else if(S.view==='compare') h += tbAttrs() + `<span class="spacer"></span><button class="tb-btn" data-t="swap" title="${t("Swap Selection and Candidate")}">${t("Replace")}</button><button class="tb-btn" data-t="done" title="${t("Done (Esc)")}">${t("Done")}</button>`;
  else if(S.view==='survey') h += tbAttrs() + `<span class="spacer"></span><span class="tb-info">${t("{0} photos in Survey", [num(S.sel.size)])}</span>`;
  else if(S.view==='map') h += `<span class="spacer"></span><span class="tb-info" id="map-info">${MAPINFO}</span>`;
  else h += `<span class="spacer"></span><span class="tb-info">${t("Type a name below the faces to name them")}</span>`;
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
$('#toolbar').addEventListener('change', e=>{ if(e.target.dataset.t==='sort'){ S.sort=e.target.value; S.asc=SORT_ASC_FIRST.has(S.sort); pref.set('sort',S.sort); pref.set('asc',S.asc); applyFilter(); renderToolbar(); } });
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
  if(!ids.length){ el.innerHTML=("<div class=\"hint\">"+t("Select photos to tag them.")+"</div>"); return; }
  const txt = kws.map(k=>k.name + (k.n<ids.length?' *':'')).join(', ');
  const top = S.tags.slice(0,30).map(tg=>tg.name);
  const sug = [...new Set([...S.recentKw, ...top])].filter(n=>!kws.some(k=>k.name===n && k.n===ids.length)).slice(0,9);
  el.innerHTML = `<div class="lbl-sub">${t("Keywords")}${ids.length>1?` ${t("· {0} photos (* = only some of them)", [num(ids.length)])}`:''}</div>
    <label class="kwbox"><textarea id="kw-text" spellcheck="false" placeholder="${t("Type keywords separated by commas")}">${esc(txt)}</textarea></label>
    <label class="kwadd"><input id="kw-add" placeholder="${t("Click here to add keywords")}"></label>
    <div class="kwai"><button id="kw-ai" title="${esc(t('Tag the selected photos using AI'))}">${I('spark')}${t('AI tagging')}</button><button id="kw-ai-cfg" class="cfg" title="${esc(t('AI tagging settings'))}" aria-label="${esc(t('AI tagging settings'))}">${I('dev')}</button></div>
    <div class="lbl-sub" style="padding-top:8px">${t("Keyword Suggestions")}</div>
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
  el.innerHTML = `<div class="kwlist-filter"><input id="kw-filter" type="search" placeholder="${t("Keyword Filter")}" value="${esc(KWF)}"></div>
    <div class="kwrows">${tags.slice(0,800).map(tg=>{ const c=on.get(tg.id)||0;
      return `<div class="row" data-kw="${tg.id}"><input type="checkbox" ${nSel&&c===nSel?'checked':''} ${nSel?'':'disabled'} data-part="${c&&c<nSel?1:0}" title="${nSel?t('Add/remove for selected photos'):''}"><span class="nm">${esc(tg.name)}</span>
        <button class="go" data-go="${tg.id}" title="${t("Show photos with this keyword")}">${I('next')}</button><span class="n">${num(tg.n)}</span></div>`; }).join('')
      || ("<div class=\"hint\">"+t("No keywords yet. Tag photos or run Smart Tagging.")+"</div>")}</div>`;
  $$('#p-kwlist [data-part="1"]').forEach(c=>c.indeterminate=true);
}
$('#p-kwlist').addEventListener('input', debounce(e=>{ if(e.target.id==='kw-filter'){ KWF=e.target.value.toLowerCase(); renderKwList(); $('#kw-filter').focus(); } }, 150));
$('#p-kwlist').addEventListener('click', e=>{
  const go=e.target.closest('[data-go]');
  if(go){ const tg=S.tags.find(x=>x.id==go.dataset.go); setSource({kind:'tag', id:tg.id, name:t('Keyword: ')+tg.name}); return; }
  const cb=e.target.closest('input[type=checkbox]');
  if(cb){ const tg=S.tags.find(x=>x.id==cb.closest('[data-kw]').dataset.kw);
    if(cb.checked) commitKeywords([tg.name], []); else commitKeywords([], [tg.id]); }
});

function renderMeta(ids, d){
  const el=$('#p-meta');
  if(SIDEMAP){ SIDEMAP.remove(); SIDEMAP = null; }
  if(!ids.length || !d){ el.innerHTML=("<div class=\"hint\">"+t("No photo selected.")+"</div>"); return; }
  const multi = ids.length>1;
  const sel = ids.map(id=>S.byId.get(id)).filter(Boolean);
  const same = k => sel.every(p=>(p[k]??null)===(sel[0][k]??null));
  const MIX = ("<span class=\"mixed\">"+t("&lt;mixed&gt;")+"</span>");
  const rating = !multi || same('rating') ? (d.rating||0) : -1;
  const label = !multi || same('label') ? (d.label||'') : '*';
  const local = d.taken_at ? new Date(d.taken_at*1000 - new Date().getTimezoneOffset()*60000).toISOString().slice(0,16) : '';
  const links = (arr, kind) => arr.length ? arr.map(a=>`<a data-src-link="${kind}:${a.id}">${esc(a.name)}</a>`).join(', ') : '—';
  el.innerHTML = `
    ${multi?`<div class="lbl-sub">${t("{0} photos selected — changes will apply to all of them", [num(ids.length)])}</div>`:''}
    <div class="kv"><span>${t("File Name")}</span>${multi?MIX:`<span dir="ltr" title="${esc(d.filename)}">${esc(d.filename)}</span>`}</div>
    <div class="kv"><span>${t("Folder")}</span>${multi&&!same('folder')?MIX:`<a data-src-link="folder:${esc(sel[0]?.folder??'')}">${esc(sel[0]?.folder||t('(root)'))}</a>`}</div>
    <div class="kv"><span>${t("Rating")}</span><span class="stars-in" id="m-stars">${[1,2,3,4,5].map(n=>`<b data-mr="${n}" class="${rating>=n?'on':''}">★</b>`).join('')}${rating<0?' '+MIX:''}</span></div>
    <div class="kv"><span>${t("Label")}</span><select id="m-label"><option value="">${t("None")}</option>${LABELS.map(([k,n])=>`<option value="${k}" ${label===k?'selected':''}>${n}</option>`).join('')}${label==='*'?("<option selected disabled>"+t("&lt;mixed&gt;")+"</option>"):''}</select></div>
    <div class="meta-sub">${t("Content")}</div>
    <div class="kv tall"><span>${t("Caption")}</span><textarea id="m-desc" placeholder="${multi?t('<mixed>'):''}">${multi?'':esc(d.description||'')}</textarea></div>
    <div class="meta-sub">${t("Photo")}</div>
    <div class="kv"><span>${t("Capture Time")}</span>${multi?MIX:`<input id="m-date" type="datetime-local" value="${local}">`}</div>
    <div class="kv"><span>${t("Dimensions")}</span>${multi?MIX:`<span dir="ltr">${d.width||'?'} × ${d.height||'?'}</span>`}</div>
    <div class="kv"><span>${t("File Size")}</span>${multi?MIX:fsize(d.bytes)}</div>
    <div class="kv"><span>${t("Kind")}</span>${multi&&!sel.every(p=>ext(p)===ext(sel[0]))?MIX:esc(ext(d))}${d.edited&&!multi?t(' · edited'):''}</div>
    <div class="meta-sub">${t("Location")}</div>
    ${multi?`<div class="kv"><span>GPS</span>${MIX}</div><div class="btnrow"><button id="m-showmap">${I('pin')} ${t('Show the selected photos on the map')}</button></div>`:`
    <div class="kv"><span>${t("Latitude")}</span><input id="m-lat" type="number" step="any" dir="ltr" value="${d.lat??''}"></div>
    <div class="kv"><span>${t("Longitude")}</span><input id="m-lng" type="number" step="any" dir="ltr" value="${d.lng??''}"></div>
    ${d.lat!=null?`<div class="mini-map" id="mini-map" dir="ltr" title="${t('Click to open the large map')}"></div>`:`<div class="hint">${t('This photo has no location. Enter a latitude and longitude to make it appear on the map.')}</div>`}
    <div class="meta-sub">${t("Catalog")}</div>
    <div class="kv tall"><span>${t("Collections")}</span><span style="white-space:normal">${links(d.albums,'album')}</span></div>
    <div class="kv tall"><span>${t("People")}</span><span style="white-space:normal">${links(d.people,'person')}</span></div>
    <div class="kv"><span>${t("Google Favorite")}</span><label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="m-fav" ${d.favorited?'checked':''}></label></div>
    <div class="kv"><span>${t("Imported")}</span>${fdate(d.imported_at)}</div>
    <div class="btnrow"><button id="m-exif" title="${t("Write the description, date and location into the JPG file (Ctrl+S)")}">${t("Save Metadata to File")}</button><button id="m-reveal" title="Ctrl+R">${t("Show in Explorer")}</button></div>`}`;
  if(!multi && d.lat!=null && $('#mini-map')) drawMiniMap(d);
}
$('#p-meta').addEventListener('click', e=>{
  const s=e.target.closest('[data-mr]'); if(s){ const n=+s.dataset.mr, p=actPhoto(); setRating(p&&p.rating===n&&targets().length===1?0:n); setTimeout(renderRight, 50); return; }
  const l=e.target.closest('[data-src-link]'); if(l){ setSource(srcFromKey(l.dataset.srcLink)); return; }
  if(e.target.id==='m-exif') saveMetaToFile();
  if(e.target.closest('#m-showmap')) setView('map');
  if(e.target.closest('#mini-map') && !e.target.closest('.leaflet-control')) setView('map');
  if(e.target.id==='m-reveal') reveal();
});
$('#p-meta').addEventListener('change', async e=>{
  const ids=targets(), tg=e.target;
  if(tg.id==='m-label') setAttr({label:tg.value}, ids);
  else if(tg.id==='m-desc'){ await send('PATCH','/api/photos',{ids, description:tg.value}); toast(t('Caption saved'), 1200); }
  else if(tg.id==='m-fav'){ setAttr({favorited:tg.checked?1:0}, ids); }
  else if(tg.id==='m-date' && tg.value){ const ts=Math.floor(new Date(tg.value).getTime()/1000);
    await send('PATCH','/api/photo/'+ids[0],{taken_at:ts}); const p=S.byId.get(ids[0]); if(p) p.taken_at=ts; applyFilter(); toast(t('Capture time updated'), 1200); }
  else if(tg.id==='m-lat' || tg.id==='m-lng'){ const lat=$('#m-lat').value, lng=$('#m-lng').value;
    if((lat==='')!==(lng==='')) return;
    await send('PATCH','/api/photo/'+ids[0],{lat: lat===''?null:+lat, lng: lng===''?null:+lng}); toast(t('Location saved'), 1200); renderRight(); }
});
$('#p-meta').addEventListener('keydown', e=>{ if(e.target.id==='m-desc' && e.key==='Enter' && !e.shiftKey){ e.preventDefault(); e.target.blur(); } });
async function saveMetaToFile(){
  const d=DETAIL; if(!d || targets().length!==1) return toast(t('Select a single photo'));
  await send('PATCH','/api/photo/'+d.id,{description:d.description, taken_at:d.taken_at, lat:d.lat, lng:d.lng, write_exif:true});
  toast(/\.jpe?g$/i.test(d.filename) ? t('Metadata written to file') : t('Writing to file is supported for JPG only'));
}
async function reveal(){ const p=actPhoto(); if(p) await send('POST', `/api/photo/${p.id}/reveal`); }
$('#btn-sync-meta').onclick = async ()=>{
  const ids=targets(), d=DETAIL; if(ids.length<2 || !d) return toast(t('Select several photos; the values will be copied from the active photo'));
  await send('PATCH','/api/photos',{ids, description:d.description||'', rating:d.rating||0, label:d.label||''});
  ids.forEach(id=>{ const p=S.byId.get(id); if(p){ p.rating=d.rating||0; p.label=d.label||null; } });
  toast(`${t("{0} photos synced", [num(ids.length)])}`); applyFilter();
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
    ['grid','loupe','compare','survey','people','map','empty'].forEach(k=>$('#v-'+k).classList.add('hidden'));
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
  [t('None (reset tones)'), {bri:0,con:0,sat:0,gray:false}],
  [t('Black & White'), {gray:true, con:10}],
  [t('High-Contrast Black & White'), {gray:true, con:45, bri:5}],
  [t('Vivid and Colorful'), {sat:40, con:15}],
  [t('Muted'), {sat:-40, con:-10}],
  [t('Light and Airy'), {bri:20, con:-15, sat:-10}],
  [t('Dark and Dramatic'), {bri:-15, con:35, sat:-15}],
];
async function devOpen(){
  const p=actPhoto();
  DEV.crop=false; DEV.before=false; DEV.dirty=false;
  if(!p){ DEV.id=null; $('#dev-img').removeAttribute('src'); renderDevPanels(); return; }
  if(p.is_video){ DEV.id=null; $('#dev-img').removeAttribute('src'); renderDevPanels(); toast(t('Editing is available for photos only')); return; }
  DEV.id=p.id;
  const d=await api('/api/photo/'+p.id); if(DEV.id!==p.id) return;
  const o = d.edit_ops ? JSON.parse(d.edit_ops) : {};
  DEV.ops = {bri:unfac(o.brightness,.3), con:unfac(o.contrast,.3), sat:unfac(o.saturation,0), gray:!!o.grayscale,
             rot:o.rotate||0, crop:o.crop&&o.crop.length===4?o.crop:[0,0,1,1]};
  DEV.saved = JSON.stringify(DEV.ops);
  DEV.hist=[{t:d.edited?t('Saved Settings'):t('Import'), ops:{...DEV.ops}}];
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
  const sl=(k,label,cls,min=-100,max=100)=>`<div class="dsl ${cls}"><label for="d-${k}">${label}</label><input id="d-${k}" data-k="${k}" type="range" min="${min}" max="${max}" step="1" value="${o[k]}" ${dis} title="${t("Double-click to reset")}"><output>${o[k]>0?'+':''}${o[k]}</output></div>`;
  $('#p-basic').innerHTML = `
    <div class="dsec">${t("Treatment")}</div>
    <div class="treat"><a data-gray="0" class="${o.gray?'':'on'}">${t("Color")}</a><a data-gray="1" class="${o.gray?'on':''}">${t("Black & White")}</a></div>
    <div class="dsec">${t("Tone")}</div>
    ${sl('bri',t('Exposure'),'exp')}${sl('con',t('Contrast'),'con')}
    <div class="dsec">${t("Presence")}</div>
    ${sl('sat',t('Saturation'),'sat')}`;
  const fine = Math.round((o.rot - Math.round(o.rot/90)*90)*10)/10;
  $('#p-transform').innerHTML = `
    <div class="dsl"><label for="d-straight">${t("Straighten")}</label><input id="d-straight" data-k="straight" type="range" min="-45" max="45" step="0.5" value="${fine}" ${dis}><output>${fine>0?'+':''}${fine}°</output></div>
    <div class="btnrow90"><button data-rot="-90" ${dis}>${I('rotl')} 90°</button><button data-rot="90" ${dis}>90° ${I('rotr')}</button></div>
    <div class="btnrow90"><button data-t="crop" ${dis}>${I('crop')} ${DEV.crop?t('Done Cropping (Enter)'):t('Crop (R)')}</button><button data-t="cropreset" ${dis}>${t("Reset Crop")}</button></div>`;
  $('#p-presets').innerHTML = PRESETS.map(([n],i)=>`<div class="row" data-preset="${i}">${I('dev')}<span class="nm">${n}</span></div>`).join('');
  $('#p-history').innerHTML = DEV.hist.map((h,i)=>`<div class="row ${i===DEV.hist.length-1?'':''}" data-hist="${i}"><span class="nm">${esc(h.t)}</span></div>`).reverse().join('') || '<div class="hint">—</div>';
  $$('#dev-tools [data-tool]').forEach(b=>b.classList.toggle('on', DEV.crop));
}
const DLABEL = {bri:t('Exposure'), con:t('Contrast'), sat:t('Saturation')};
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
  devSet({}, k==='straight' ? `${t("Straighten")} ${v>0?'+':''}${v}°` : `${DLABEL[k]} ${v>0?'+':''}${v}`);
});
$('#right').addEventListener('dblclick', e=>{
  const k=e.target.dataset?.k; if(!k || DEV.id==null) return;
  if(k==='straight') devSet({rot:Math.round(DEV.ops.rot/90)*90}, t('Straighten 0°')); else devSet({[k]:0}, `${DLABEL[k]} 0`);
});
$('#right').addEventListener('click', e=>{
  if(S.mod!=='develop' || DEV.id==null) return;
  const g=e.target.closest('[data-gray]'); if(g){ const on=g.dataset.gray==='1'; if(on!==DEV.ops.gray) devSet({gray:on}, on?t('Black & White'):t('Color')); return; }
  const r=e.target.closest('[data-rot]'); if(r){ const d=+r.dataset.rot; const k=DEV.ops.crop, c = d>0 ? [1-k[3],k[0],1-k[1],k[2]] : [k[1],1-k[2],k[3],1-k[0]];
    let rot=DEV.ops.rot+d; if(rot>180) rot-=360; if(rot<=-180) rot+=360; devSet({rot, crop:c}, d>0?t('Rotate Right'):t('Rotate Left')); return; }
  const tg=e.target.closest('[data-t]')?.dataset.t;
  if(tg==='crop') devCropToggle();
  if(tg==='cropreset') devSet({crop:[0,0,1,1]}, t('Reset Crop'));
  if(e.target.closest('[data-tool="crop"]')) devCropToggle();
});
$('#left').addEventListener('click', e=>{
  if(S.mod!=='develop' || DEV.id==null) return;
  const pr=e.target.closest('[data-preset]'); if(pr){ const [n,o]=PRESETS[+pr.dataset.preset]; devSet({...NEUTRAL(), rot:DEV.ops.rot, crop:DEV.ops.crop, ...o}, t('Preset: ')+n); return; }
  const h=e.target.closest('[data-hist]'); if(h){ const s=DEV.hist[+h.dataset.hist]; devSet({...s.ops, crop:[...s.ops.crop]}); }
});
function devCropToggle(){ if(DEV.id==null) return; DEV.crop=!DEV.crop; if(!DEV.crop) devSet({}, t('Crop')); else { layoutDev(); renderDevPanels(); renderToolbar(); } }
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
  if(!silent) toast(t('Applying…'), 1500);
  await send('POST', `/api/photo/${id}/edit`, devOpsToApi(ops));
  const d=await api('/api/photo/'+id);
  const p=S.byId.get(id); if(p) Object.assign(p, {width:d.width, height:d.height, edited:d.edited, bytes:d.bytes});
  VER[id]=Date.now();
  DEV.last = {bri:ops.bri, con:ops.con, sat:ops.sat, gray:ops.gray}; pref.set('lastDev', DEV.last);
  if(DEV.id===id){ DEV.saved=JSON.stringify(DEV.ops); DEV.dirty=false; renderToolbar(); }
  G.cells.forEach(c=>{ if(+c.dataset.id===id){ const img=c.querySelector('img'); if(img) img.src=thumbUrl(id); } });
  renderFilm(true); renderColls();
  if(!silent) toast(t('Settings applied · original kept'));
}
$('#btn-dev-apply').onclick = ()=>devApply();
$('#btn-dev-reset').onclick = ()=>{ if(DEV.id!=null) devSet(NEUTRAL(), t('Reset')); };
$('#btn-copy-prev').onclick = ()=>{ if(DEV.id==null) return; if(!DEV.last) return toast(t('No settings have been applied to another photo yet')); devSet({...DEV.last}, t('Previous Settings')); };
$('#btn-dev-revert').onclick = async ()=>{
  if(DEV.id==null) return; const p=actPhoto();
  if(!p?.edited){ devSet(NEUTRAL(), t('Reset')); return; }
  if(!await confirmBox(t('Revert to the original file?'), t('The edits saved in the file will be deleted and the original file will be restored.'), t('Restore the original'))) return;
  await send('POST', `/api/photo/${DEV.id}/revert`); VER[DEV.id]=Date.now();
  Object.assign(p, {edited:0}); const d=await api('/api/photo/'+p.id); Object.assign(p,{width:d.width,height:d.height,bytes:d.bytes});
  toast(t('Reverted to the original file')); renderFilm(true); devOpen();
};

// ---------- slideshow ----------
const SS={list:[], i:0, t:null, playing:true, cur:'a'};
function ssStart(){
  let list = S.sel.size>1 ? S.list.filter(p=>S.sel.has(p.id)) : S.list;
  list = list.filter(p=>!p.is_video); if(!list.length) return toast(t('No photos to show'));
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
      <div class="mf"><button id="pb-cancel">${t("Cancel")}</button><button class="primary" id="pb-ok">${t("OK")}</button></div>`);
    const done=v=>{ PB_CHECKED = !!$('#modal-box input[type=checkbox]')?.checked; closeModal(); res(v); };
    $('#pb-form').onsubmit=e=>{ e.preventDefault(); done($('#pb-in').value.trim()||null); };
    $('#pb-ok').onclick=()=>done($('#pb-in').value.trim()||null);
    $('#pb-cancel').onclick=()=>done(null);
  });
}
let CB_NEVER = false;   // the optional "don't ask again" checkbox of the last confirmBox
function confirmBox(title, text, ok=t('OK')){
  return new Promise(res=>{
    modal(`<h3>${title}</h3><div class="mb"><p>${text}</p></div><div class="mf"><button id="cb-no">${t("Cancel")}</button><button class="primary" id="cb-yes">${ok}</button></div>`);
    $('#cb-yes').onclick=()=>{ CB_NEVER = !!$('#cb-never')?.checked; closeModal(); res(true); }; $('#cb-no').onclick=()=>{ closeModal(); res(false); };
  });
}
async function catalogSettings(){
  const s=await api('/api/status'), c=s.counts;
  modal(`<h3>${t("Catalog Settings")}</h3><div class="mb">
    <p>${t("All files are stored locally on your computer. Imported photos are copied to the media folder and organized by capture year. The original of every edited photo is kept separately.")}</p>
    <div class="pathrow"><span>${t("Catalog Library")}</span><code>${esc(s.library_root)}</code></div>
    <div class="pathrow"><span>${t("Media Files")}</span><code>${esc(s.media_path)}</code></div>
    <div class="pathrow"><span>${t("Catalog File")}</span><code>${esc(s.db_path)}</code></div>
    <div class="pathrow"><span>${t("Content")}</span><span>${t("{0} items · {1} videos · {2} collections · {3} people · {4} keywords · {5} in Trash", [num(c.photos), num(c.videos), num(c.albums), num(c.people), num(c.tags), num(c.trashed)])}</span></div>
    <div class="pathrow"><span>${t("Trash")}</span><span>${t("Items are permanently deleted after {0} days", [s.trash_days])}</span></div>
    <label class="fld"><span>${t("Different catalog location")}</span><div class="frow"><input type="text" id="lib-path" dir="ltr" value="${esc(s.library_root)}"><button id="lib-pick">${t("Choose...")}</button></div></label>
  </div><div class="mf"><button onclick="closeModal()">${t("Close")}</button><button class="primary" id="lib-set">${t("Go to Catalog")}</button></div>`);
  $('#lib-pick').onclick=async()=>{ const r=await api('/api/pick-file?kind=folder&title='+encodeURIComponent(t('Choose catalog folder'))); if(r.path) $('#lib-path').value=r.path; };
  $('#lib-set').onclick=async()=>{ const p=$('#lib-path').value.trim(); if(!p) return; await send('POST','/api/settings/library',{path:p}); closeModal(); toast(t('Catalog replaced')); await reloadAll(); };
}
async function preferences(){
  const s=await api('/api/status');
  modal(`<h3>${t("Preferences · Face detection")}</h3><div class="mb">
    <p>${t("Face detection runs locally on your computer, without sending photos. AI tagging sends small thumbnails to the provider you choose, only when you start it.")}</p>
    <div class="pathrow"><span>${t("Face Detection")}</span><span>${t("InsightFace · {0} faces detected so far", [num(s.counts.faces)])}</span></div>
  </div><div class="mf"><button onclick="closeModal()">${t("Close")}</button>
    <button id="pf-ai">${t("AI tagging settings")}</button><button class="primary" id="pf-faces">${t("Detect Faces")}</button></div>`);
  $('#pf-ai').onclick=()=>{ closeModal(); aiSettings(); };
  $('#pf-faces').onclick=()=>{ closeModal(); runJob('/api/faces','faces',t('Face Detection')); };
}
// ---------- AI tagging: OpenAI / Claude / Gemini / OpenRouter / any OpenAI-compatible API ----------
const AI_PROVIDERS = ['openai', 'anthropic', 'gemini', 'openrouter', 'custom'];
const aiShort = id => ({openai:'OpenAI', anthropic:'Claude', gemini:'Gemini', openrouter:'OpenRouter'})[id] || t('Custom server');
const aiLabel = id => id==='custom' ? t('Custom (OpenAI-compatible: Groq, Together, Ollama and more)')
  : id==='openrouter' ? 'OpenRouter · ' + t('All models with one key')
  : id==='anthropic' ? 'Claude (Anthropic)' : id==='gemini' ? 'Gemini (Google)' : 'OpenAI';

async function aiSettings(){
  const s = await api('/api/ai/settings');
  modal(`<h3>${t('AI tagging settings')}</h3><div class="mb">
    <p>${t('Keywords are generated by an AI provider, using your key. A small thumbnail (up to 512px) of each photo being tagged is sent, and only when you start tagging. The key is stored encrypted in your Windows account.')}</p>
    <label class="fld"><span>${t('Provider')}</span><select id="ai-provider">${AI_PROVIDERS.map(id=>`<option value="${id}" ${id===s.provider?'selected':''}>${esc(aiLabel(id))}</option>`).join('')}</select></label>
    <label class="fld hidden" id="ai-base-row"><span>${t('API address (up to /v1)')}</span><input type="text" id="ai-base" dir="ltr" value="${esc(s.base_url)}" placeholder="http://localhost:11434/v1"></label>
    <div class="fld"><span>${t('API key')}</span>
      <div class="frow"><input type="password" id="ai-key" dir="ltr" autocomplete="off" spellcheck="false"><button id="ai-key-del" class="hidden">${t('Remove key')}</button></div>
      <span class="hint" id="ai-key-hint" style="margin:0;padding:0"></span></div>
    <div class="fld"><span>${t('Model')}</span>
      <label class="check" style="padding:0"><input type="radio" name="ai-m" value="auto"> ${t('Automatic (recommended): picks a cheap, fast model that understands images')}</label>
      <label class="check" style="padding:0"><input type="radio" name="ai-m" value="manual"> ${t('Choose manually')}</label>
      <div class="frow"><input type="text" id="ai-model" list="ai-models" dir="ltr" autocomplete="off" spellcheck="false" placeholder="model-id"><datalist id="ai-models"></datalist><button id="ai-load">${t('Load model list')}</button></div></div>
    <label class="fld"><span>${t('Keyword language')}</span><select id="ai-lang">${LANGS.map(([c,n])=>`<option value="${c}" ${c===s.language?'selected':''}>${n}</option>`).join('')}</select></label>
    <div class="hint" id="ai-status" style="min-height:18px;padding:0"></div>
  </div><div class="mf"><button id="ai-close">${t('Close')}</button><button id="ai-save" class="primary">${t('Save')}</button></div>`);
  const q = id => $('#'+id), radio = v => $(`input[name=ai-m][value=${v}]`);
  const status = (msg, cls='') => { q('ai-status').textContent = msg; q('ai-status').className = 'hint ' + cls; };
  radio(s.model ? 'manual' : 'auto').checked = true; q('ai-model').value = s.model;
  const refresh = ()=>{
    const p = q('ai-provider').value, info = s.providers[p] || {};
    q('ai-base-row').classList.toggle('hidden', p!=='custom');
    q('ai-key-hint').textContent = info.has_key ? t('Saved key: {0}', [info.hint]) : p==='custom' ? t('Optional key (a local server usually doesn\'t need one)') : t('No key saved');
    q('ai-key-del').classList.toggle('hidden', !info.has_key);
    q('ai-model').disabled = !radio('manual').checked;
  };
  refresh();
  q('ai-provider').onchange = ()=>{ radio('auto').checked = true; q('ai-model').value = ''; q('ai-models').innerHTML = ''; status(''); refresh(); };
  $$('input[name=ai-m]').forEach(r=>r.onchange = refresh);
  q('ai-key-del').onclick = async ()=>{ const ok = await confirmBox(t('Delete the API key?'), t('The key will be deleted from this computer.'), t('Delete')); if(ok) await send('DELETE', '/api/ai/key/' + q('ai-provider').value); aiSettings(); };
  const probe = ()=>({provider:q('ai-provider').value, base_url:q('ai-base').value.trim(), api_key:q('ai-key').value.trim() || null});
  q('ai-load').onclick = async ()=>{
    status(t('Loading…'));
    try{
      const r = await send('POST', '/api/ai/models', probe());
      q('ai-models').innerHTML = r.models.map(m=>`<option value="${esc(m.id)}">${esc(m.name!==m.id ? m.name : '')}</option>`).join('');
      status(t('Found {0} models · in Automatic mode this will be chosen: {1}', [r.models.length, r.auto || '—']), 'ok');
    }catch(e){ status(e.message, 'err'); }
  };
  q('ai-save').onclick = async ()=>{
    const manual = radio('manual').checked, model = q('ai-model').value.trim();
    if(manual && !model) return status(t('Type a model ID, or choose Automatic'), 'err');
    try{
      await send('POST', '/api/ai/settings', {...probe(), model: manual ? model : '', language: q('ai-lang').value});
      closeModal(); toast(t('Settings saved'));
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
  modal(`<h3>${t('AI tagging')}</h3><div class="mb">
    <p>${t('{0} · Model: {1}', [name, esc(s.model || t('Automatic'))])}</p>
    <div class="fld">
      <label class="check" style="padding:0"><input type="radio" name="ai-scope" value="sel" ${sel.length?'checked':'disabled'}> ${t('Selected photos ({0})', [num(sel.length)])}</label>
      <label class="check" style="padding:0"><input type="radio" name="ai-scope" value="untagged" ${sel.length?'':'checked'} ${untagged?'':'disabled'}> ${t('All photos without keywords ({0})', [num(untagged)])}</label></div>
    <p>${t('A small thumbnail of each photo will be sent to {0}. Keywords previously created by AI on these photos will be replaced; manual keywords are not affected.', [name])}</p>
  </div><div class="mf"><button id="air-cfg">${t('Settings')}</button><span class="spacer"></span><button id="air-cancel">${t('Cancel')}</button><button class="primary" id="air-go">${t('Start tagging')}</button></div>`);
  $('#air-cfg').onclick = ()=>{ closeModal(); aiSettings(); };
  $('#air-cancel').onclick = closeModal;
  $('#air-go').onclick = async ()=>{
    const scope = $('input[name=ai-scope]:checked')?.value; if(!scope) return;
    closeModal();
    await send('POST', '/api/aitag', scope==='sel' ? {ids:sel} : {only_untagged:true});
    pollJob('aitag', t('AI tagging'));
  };
}

// ---------- video compression (HandBrake) ----------
const ltr = s => '\u2066' + s + '\u2069';   // isolate numbers/Latin inside RTL text so "10.6 MB" doesn't flip
const fmtBytes = b => ltr(b>=1099511627776 ? (b/1099511627776).toFixed(2)+' TB' : b>=1073741824 ? (b/1073741824).toFixed(2)+' GB' : (b/1048576).toFixed(b>=104857600 ? 0 : 1)+' MB');
const STRENGTH_LABELS = () => [t('Very weak'), t('Weak'), t('Medium'), t('Strong'), t('Very strong')];
const SPEED_LABELS = () => [t('Very slow'), t('Slow'), t('A bit slow'), t('Medium'), t('A bit fast'), t('Fast'), t('Very fast')];
const GRADE_LABELS = () => ({excellent:t('Almost identical to the original'), very_good:t('Very good'), good:t('Good'), noticeable:t('Noticeable quality loss')});
let CMP_PID = null;

function hbInstallDialog(st, retry){
  modal(`<h3>${t('Compressing videos requires HandBrake')}</h3><div class="mb">
    <p>${t('HandBrake is a free app that does the compression. Install the command-line version (HandBrakeCLI) from their website, then click «Check again».')}</p>
    <p class="hint" style="padding:0">${t('You can also keep HandBrakeCLI.exe in any folder and choose it manually.')}</p>
    <div class="hint err" id="hb-msg" style="padding:0;min-height:16px"></div>
  </div><div class="mf"><button id="hb-pick">${t('Choose HandBrakeCLI.exe...')}</button><span class="spacer"></span>
    <button id="hb-again">${t('Check again')}</button><button id="hb-open" class="primary">${t('Open the installation page')}</button><button id="hb-close">${t('Close')}</button></div>`);
  $('#hb-open').onclick = ()=>send('POST', '/api/handbrake/open-page');
  $('#hb-again').onclick = async ()=>{ const s = await api('/api/handbrake'); if(s.found){ closeModal(); retry(); } else $('#hb-msg').textContent = t('HandBrakeCLI is still not found.'); };
  $('#hb-pick').onclick = async ()=>{
    const r = await api('/api/pick-file?kind=exe&title=' + encodeURIComponent(t('Choose HandBrakeCLI.exe'))); if(!r.path) return;
    try{ const s = await send('POST', '/api/handbrake/path', {path:r.path}); if(s.found){ closeModal(); retry(); } }
    catch(e){ $('#hb-msg').textContent = e.message; }
  };
  $('#hb-close').onclick = closeModal;
}

// plain-words description of the current choices ("what was chosen for you"), shown under the sliders
const QUALITY_TEXT = () => [t('Quality almost identical to the original'), t('High quality, the difference is almost invisible'), t('Good quality, a slight difference may be noticeable in detailed scenes'),
  t('Lower quality, artifacts may appear in complex scenes'), t('Low quality, artifacts are visible')];
const KIND_TEXT = () => [t('Thorough, slow compression: same quality in a smaller file'), t('Compression balanced between speed and file size'),
  t('Fast compression: finishes quickly, but the file is larger than it could be')];
const ENCODER_NOTE = () => ({x264:t('H.264: plays on every device'), x265:t('H.265: smaller file, but less widely supported'), svt_av1:t('AV1: smallest file, slow and less widely supported')});
function compressSummary(o, e, speedIdx){
  const frac = (o.quality - e.rf[0]) / (e.rf[1] - e.rf[0]);
  const quality = QUALITY_TEXT()[Math.max(0, Math.min(4, Math.floor(frac * 5)))];
  const lines = [
    t('Quality: {0}', [o.max_height ? t('{0} (up to {1})', [quality, ltr(o.max_height + 'p')]) : quality]),
    t('Compression type: {0} · {1}', [KIND_TEXT()[speedIdx <= 2 ? 0 : speedIdx === 3 ? 1 : 2], ENCODER_NOTE()[o.encoder]]),
    o.fps_mode === 'same' ? t('Frames: all kept')
      : o.fps_mode === 'limit' ? t('Frames: rate capped at {0} per second (frames will be removed)', [ltr(String(o.fps))])
      : t('Frames: constant rate of {0} per second (frames will be removed or duplicated)', [ltr(String(o.fps))]),
    o.audio === 'auto' ? t('Audio: kept as is (AAC), otherwise converted to AAC')
      : o.audio === 'aac' ? t('Audio: converted to AAC at {0} kbps', [ltr(String(o.audio_bitrate))]) : t('Audio: removed'),
    t('The original file was saved in backups and can be restored'),
  ];
  return lines.map(l => `<li>${esc(l)}</li>`).join('');
}

// the same dialog serves one video, one photo, or any mix of several: only the sections that apply are shown
const IMG_Q = [40, 98];
const IMG_MAX_SIDES = [0, 4096, 3000, 2560, 2048, 1600, 1200];
function compressSummaryImg(o){
  const frac = (IMG_Q[1] - o.quality) / (IMG_Q[1] - IMG_Q[0]);
  const lines = [
    t('Quality: {0}', [QUALITY_TEXT()[Math.max(0, Math.min(4, Math.floor(frac * 5)))]]),
    o.max_side ? t('The image will be downscaled to at most {0} pixels on the long side', [ltr(String(o.max_side))]) : t('Image dimensions (in pixels) will not change'),
    t('PNG, BMP and TIFF are compressed with no quality loss; this setting affects JPEG and WebP'),
    t('Shooting details (EXIF), location and orientation are kept'),
    t('The original file was saved in backups and can be restored'),
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
  if(!vids.length && !imgs.length) return toast(items.length ? t('The selection has no files that can be compressed (JPG, PNG, WebP, TIFF, BMP and videos)') : t('Choose photos or videos to compress'));
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

  const title = single ? (single.is_video ? t('Video compression') : t('Compress photo')) : t('Compress files');
  const head = single
    ? `<div class="cmp-file"><b dir="ltr">${esc(single.filename)}</b><span>${fmtBytes(single.bytes)}</span></div>`
    : `<div class="cmp-file"><b>${t('{0} files', [num(vids.length + imgs.length)])}</b><span>${[vids.length ? t('{0} videos', [num(vids.length)]) : '', imgs.length ? t('{0} photos', [num(imgs.length)]) : ''].filter(Boolean).join(' · ')} · ${fmtBytes([...vids, ...imgs].reduce((a, p) => a + (p.bytes || 0), 0))}</span></div>`
      + (skipped ? `<div class="hint warn" style="padding:0">${t('{0} files of an unsupported type will be skipped', [num(skipped)])}</div>` : '');
  const sect = h => (hasV && hasI) ? `<div class="cmp-h">${h}</div>` : '';

  const videoHtml = hasV ? `${sect(t('Videos'))}
    <div class="cmp-sl"><div class="cmp-sl-h"><label for="cp-str">${t('Compression strength')}</label><output id="cp-str-v"></output></div>
      <input type="range" id="cp-str" step="1"><div class="cmp-ends"><span>${t('Very weak')}</span><span>${t('Very strong')}</span></div></div>
    <div class="cmp-sl"><div class="cmp-sl-h"><label for="cp-spd">${t('Speed')}</label><output id="cp-spd-v"></output></div>
      <input type="range" id="cp-spd" min="0" max="6" step="1"><div class="cmp-ends"><span>${t('Slow and good')}</span><span>${t('Fast and less good')}</span></div></div>
    <div class="cmp-sum"><div class="lbl-sub" style="padding:0">${t('What was chosen for you')}</div><ul id="cp-sum"></ul></div>
    <p class="hint" style="padding:0">${t('At a slow speed the encoder works harder: same quality in a smaller file, but it takes longer.')}</p>
    <button class="linkbtn" id="cp-adv-t">${t('Advanced')} ▾</button>
    <div id="cp-adv" class="hidden">
      <label class="fld"><span>${t('Encoder')}</span><select id="cp-enc">${Object.entries(E).map(([k,v])=>`<option value="${k}">${esc(v.label)}</option>`).join('')}</select></label>
      <div class="two"><label class="fld"><span>${t('Quality (RF, lower = higher quality)')}</span><input type="number" id="cp-q" step="0.5" dir="ltr"></label>
        <label class="fld"><span>${t('Speed preset')}</span><select id="cp-pre"></select></label></div>
      <div class="two"><label class="fld"><span>${t('Maximum resolution')}</span><select id="cp-h"><option value="0">${t('No change')}</option>${[2160,1440,1080,720,480].map(h=>`<option value="${h}">${h}p</option>`).join('')}</select></label>
        <label class="fld"><span>${t('Frame rate')}</span><select id="cp-fm"><option value="same">${t('Keep every frame (default)')}</option><option value="limit">${t('Cap the frame rate (drops frames)')}</option><option value="constant">${t('Constant rate (changes frames)')}</option></select></label></div>
      <label class="fld hidden" id="cp-fps-row"><span>${t('Frames per second')}</span><input type="number" id="cp-fps" min="1" max="240" step="0.001" dir="ltr">
        <span class="hint warn" style="padding:0">${t('Frames will be removed from the video. Verification will check the frame count according to the rate you chose.')}</span></label>
      <div class="two"><label class="fld"><span>${t('Audio')}</span><select id="cp-au"><option value="auto">${t('Keep AAC, otherwise convert to AAC')}</option><option value="aac">${t('Convert to AAC')}</option><option value="none">${t('Remove audio')}</option></select></label>
        <label class="fld"><span>${t('Audio bitrate (kbps)')}</span><input type="number" id="cp-ab" min="32" max="512" step="16" dir="ltr"></label></div>
      <div class="hint" style="padding:0">HandBrake ${esc(st.version || '')} · <bdi dir="ltr">${esc(st.path)}</bdi></div>
    </div>` : '';
  const hbNote = (vids.length && !st.found) ? `${sect(t('Videos'))}<div class="cmp-sum"><p class="hint warn" style="padding:0">${t('HandBrake is not installed, so videos will be skipped.')}</p>
    <button class="linkbtn" id="cp-hb">${t('Open the installation page')}</button></div>` : '';
  const imageHtml = hasI ? `${sect(t('Photos'))}
    <div class="cmp-sl"><div class="cmp-sl-h"><label for="ci-str">${t('Compression strength')}</label><output id="ci-str-v"></output></div>
      <input type="range" id="ci-str" min="0" max="${IMG_Q[1] - IMG_Q[0]}" step="1"><div class="cmp-ends"><span>${t('Very weak')}</span><span>${t('Very strong')}</span></div></div>
    <div class="cmp-sum"><div class="lbl-sub" style="padding:0">${t('What was chosen for you')}</div><ul id="ci-sum"></ul></div>
    <button class="linkbtn" id="ci-adv-t">${t('Advanced')} ▾</button>
    <div id="ci-adv" class="hidden"><div class="two">
      <label class="fld"><span>${t('JPEG / WebP quality (higher = better quality)')}</span><input type="number" id="ci-q" min="${IMG_Q[0]}" max="${IMG_Q[1]}" step="1" dir="ltr"></label>
      <label class="fld"><span>${t('Longest side max')}</span><select id="ci-ms"><option value="0">${t('No change')}</option>${IMG_MAX_SIDES.slice(1).map(v=>`<option value="${v}">${v}px</option>`).join('')}</select></label></div></div>` : '';
  const restoreHtml = (single && single.video_backups)
    ? `<div class="cmp-restore"><span>${single.is_video ? t('A previous version of the video exists in backups.') : t('A previous version of this image exists in backups.')}</span><button id="cp-restore">${t('Restore previous version')}</button></div>` : '';
  modal(`<h3>${title}</h3><div class="mb cmp">${head}${videoHtml}${hbNote}${imageHtml}${restoreHtml}
  </div><div class="mf"><button id="cp-reset">${t('Default')}</button><span class="spacer"></span><button id="cp-cancel">${t('Cancel')}</button><button id="cp-go" class="primary">${t('Start compression')}</button></div>`);

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
      q('ci-str-v').textContent = `${STRENGTH_LABELS()[Math.max(0, Math.min(4, Math.floor(frac * 5)))]} · ${ltr(t('{0} quality', [oi.quality]))}`;
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
      pollJob('compress', p.is_video ? t('Video compression') : t('Compress photo'));
    } else {
      CMP_PID = null;
      await send('POST', '/api/compress/batch', {ids: [...(hasV ? vids : []), ...imgs].map(p => p.id), video: o, image: oi});
      pollJob('compress', t('Compress files'));
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
  toast((S.byId.has(pid) && !S.byId.get(pid).is_video) ? t('The photo\'s version was replaced') : t('Video version replaced'));
  await afterVideoChanged(pid);
}

function compressReport(r, pid){
  if(!r || !r.checks || !r.checks.length) return;
  const names = {frames:t('Frame count'), duration:t('Video length'), resolution:t('Resolution'), audio:t('Audio'), size:t('File Size'), ssim:t('Similarity to the original image (SSIM)'), metadata:t('Shooting details (EXIF) and color profile')};
  const img = r.kind === 'image';
  const yn = v => v === 'yes' ? t('Yes') : t('None');
  const detail = c => c.id==='frames' ? t('Expected {0} · got {1}', [ltr(String(c.expected)), ltr(num(c.actual))])
    : c.id==='duration' ? t('Deviation {0} (allowed {1}) · container {2} ms, video {3} ms', [ltr(c.actual), ltr(c.expected), ltr(String(c.container_ms)), ltr(String(c.video_ms))])
    : c.id==='resolution' ? t('Expected {0} · got {1}', [ltr(c.expected), ltr(c.actual)])
    : c.id==='audio' ? t('Expected {0} · got {1}', [yn(c.expected), yn(c.actual)])
    : c.id==='size' ? t('{0} → {1}', [fmtBytes(r.src.bytes), fmtBytes(r.out.bytes)])
    : c.id==='metadata' ? (c.ok ? t('Saved unchanged') : t('Changed'))
    : img ? t('Similarity {0} · {1}', [ltr(String(c.mean)), GRADE_LABELS()[c.grade]])
    : t('Average {0} · worst frame {1} · {2}', [ltr(String(c.mean)), ltr(String(c.min)), GRADE_LABELS()[c.grade]]);
  const ok = r.applied;
  modal(`<h3>${t('Compression report')}</h3><div class="mb">
    <p style="color:${ok?'var(--green)':'var(--yellow)'}">${ok ? (img ? t('The image was compressed. The previous version is saved in backups.') : t('Video compressed. The previous version was saved in backups.')) : t('The compression was not applied. The original file was not changed.')}</p>
    <p>${t('Size: {0} → {1} ({2}% of the original)', [fmtBytes(r.src.bytes), fmtBytes(r.out.bytes), ltr(String(Math.round(r.ratio * 100)))])}</p>
    <div>${r.checks.map(c=>`<div class="chk ${c.ok?'ok':'bad'}">${I(c.ok ? 'check' : 'close')}<b>${names[c.id]}</b><span>${esc(detail(c))}</span></div>`).join('')}</div>
  </div><div class="mf">${ok ? `<button id="cr-restore">${t('Restore previous version')}</button><span class="spacer"></span>` : ''}<button class="primary" id="cr-close">${t('Close')}</button></div>`);
  $('#cr-close').onclick = closeModal;
  if(ok) $('#cr-restore').onclick = async ()=>{ closeModal(); await compressRestore(pid); };
}

// ---------- backups: automatic, with settings, restorable from here ----------
const fdt = ts => new Date(ts * 1000).toLocaleString(I18N.locale || undefined, {dateStyle: 'medium', timeStyle: 'short'});
async function backupDialog(){
  let i = await api('/api/backup');
  const INTERVALS = [[1, t('Every hour')], [6, t('Every 6 hours')], [12, t('Every 12 hours')], [24, t('Every day')], [72, t('Every 3 days')], [168, t('Every week')], [720, t('Every 30 days')]];
  const REASON = () => ({auto: t('Automatic'), manual: t('Manual'), 'before-restore': t('Before restore'), 'before-update': t('Before update'), 'before-compress': t('Before compression')});
  const status = () => {
    const s = i.settings, last = i.last ? t('Last backup: {0}', [ltr(fdt(i.last))]) : t('No backup yet');
    const next = !s.enabled ? t('Automatic backup is off') : t('Next backup: {0}', [ltr(fdt(Math.max(i.next, Date.now() / 1000)))]);
    return `${last} · ${next}`;
  };
  const healthHtml = () => {
    const h = i.health || {}, tk = h.task || {}, L = [];
    if(h.folder_error) L.push(['bad', t('Can\'t reach the backup folder (e.g. the drive is disconnected): {0}', [h.folder_error])]);
    else if(h.last_error) L.push(['bad', t('The last backup failed: {0}', [h.last_error])]);
    if(h.overdue) L.push(['bad', t('No backup has been made since {0}. Check that the drive is connected and has free space.', [ltr(fdt(h.last_success))])]);
    else if(h.never && h.enabled) L.push(['warn', t('No backup yet: the first one will start soon, or click «Back up now».')]);
    if(h.enabled){
      if(!tk.supported) L.push(['', t('When run from source, backup only runs while the app is open.')]);
      else if(tk.registered) L.push(['ok', t('Backup also runs when the app is closed: a Windows task checks every hour and at every sign-in, completes a missed run, runs in the background at low priority and exits as soon as it finishes. The app itself also checks while it is open.')
        + (tk.next_run ? ' ' + t('Next check: {0}', [ltr(fdt(new Date(tk.next_run).getTime() / 1000))]) : '')]);
      else L.push(['warn', t('The Windows backup task was not registered: backup will only run while the app is open.') + (tk.error ? ' (' + tk.error + ')' : '')]);
    }
    return L.map(([c, x]) => `<div class="bk-h ${c}">${esc(x)}</div>`).join('');
  };
  const coverage = () => {
    const s = i.settings, cat = i.snapshots.reduce((a, m) => a + m.bytes, 0);
    return s.include_media
      ? t('The backup includes the catalog and all photos and videos ({0}). Backups folder size: {1} (of which media files {2}); free space on the backup drive: {3}.',
          [fmtBytes(i.media_bytes), fmtBytes(cat + i.mirror_bytes), fmtBytes(i.mirror_bytes), fmtBytes(i.free_bytes)])
      : t('Note: the backup currently includes only the catalog (tags, albums, ratings) and not the photos and videos themselves, so it is small. Check «Also back up photo and video files» to back up everything.');
  };
  const rows = () => i.snapshots.length ? i.snapshots.map(m => `<div class="bk-row"><span class="bk-d">${ltr(fdt(m.created))}</span>
      <span class="bk-r">${esc(REASON()[m.reason] || m.reason)}</span><span class="bk-s">${t('{0} photos', [num(m.photos)])} · ${fmtBytes(m.total_bytes)}${m.includes_media ? ' · ' + t('Includes photos and videos') + (m.media ? ltr(` (${num(m.media.files)})`) : '') :' · ' + t('Catalog only')}</span>
      <button data-restore="${esc(m.name)}">${t('Restore…')}</button><button data-del="${esc(m.name)}" title="${t('Delete')}">✕</button></div>`).join('')
    : `<span class="hint" style="padding:0">${t('No backups yet.')}</span>`;
  const draw = () => {
    const s = i.settings;
    $('#bk-status').textContent = status();
    $('#bk-health').innerHTML = healthHtml();
    $('#bk-cover').textContent = coverage(); $('#bk-cover').classList.toggle('warn', !s.include_media);
    $('#bk-on').checked = s.enabled; $('#bk-int').value = String(s.interval_hours); $('#bk-keep').value = s.keep; $('#bk-media').checked = s.include_media;
    $('#bk-folder').value = i.folder; $('#bk-list').innerHTML = rows();
    $('#bk-int').disabled = $('#bk-keep').disabled = !s.enabled;
  };
  modal(`<h3>${t('Backup and restore')}</h3><div class="mb bk">
    <div class="bk-status" id="bk-status"></div>
    <div class="bk-cover" id="bk-cover"></div>
    <div id="bk-health"></div>
    <label class="chkrow"><input type="checkbox" id="bk-on"> ${t('Automatic backup of the catalog and settings')}</label>
    <div class="two"><label class="fld"><span>${t('Frequency')}</span><select id="bk-int">${INTERVALS.map(([h, l]) => `<option value="${h}">${l}</option>`).join('')}</select></label>
      <label class="fld"><span>${t('How many backups to keep')}</span><input type="number" id="bk-keep" min="3" max="200" dir="ltr"></label></div>
    <label class="chkrow"><input type="checkbox" id="bk-media"> ${t('Also back up photo and video files')}</label>
    <div class="hint" style="padding:0">${t('The catalog includes tags, albums, ratings, people and edits. The media files total about {0}: the first backup copies all of them, and later ones copy only new files.', [fmtBytes(i.media_bytes)])}</div>
    <label class="fld"><span>${t('Backups folder (preferably on another disk)')}</span>
      <div class="bk-path"><input id="bk-folder" readonly dir="ltr"><button id="bk-pick">${t('Choose…')}</button><button id="bk-reset">${t('Default')}</button></div></label>
    <div class="lbl-sub" style="padding:0">${t('Existing backups')}</div><div class="bk-list" id="bk-list"></div>
  </div><div class="mf"><button class="primary" id="bk-now">${t('Back up now')}</button><span class="spacer"></span><button id="bk-close">${t('Close')}</button></div>`);
  draw();
  const save = async patch => { try{ i = await send('POST', '/api/backup/settings', patch); draw(); } catch(e){ toast(e.message); draw(); } };
  $('#bk-on').onchange = () => save({enabled: $('#bk-on').checked});
  $('#bk-int').onchange = () => save({interval_hours: +$('#bk-int').value});
  $('#bk-keep').onchange = () => save({keep: +$('#bk-keep').value});
  $('#bk-media').onchange = () => save({include_media: $('#bk-media').checked});
  $('#bk-pick').onclick = async () => { const r = await api('/api/pick-file?kind=folder&title=' + encodeURIComponent(t('Choose a backup folder'))); if(r.path) save({folder: r.path}); };
  $('#bk-reset').onclick = () => save({folder: null});
  $('#bk-close').onclick = closeModal;
  $('#bk-now').onclick = async () => { closeModal(); await send('POST', '/api/backup/run'); pollJob('backup', t('Backup')); };
  $('#bk-list').onclick = async e => {
    const r = e.target.closest('[data-restore]'), d = e.target.closest('[data-del]');
    if(r) return backupRestoreDialog(i.snapshots.find(m => m.name === r.dataset.restore), i);
    if(d){ const ok = await confirmBox(t('Delete backup'), t('Delete this backup?')); if(ok) await send('DELETE', '/api/backup/' + encodeURIComponent(d.dataset.del)); backupDialog(); }
  };
}

function backupRestoreDialog(m, i){
  const canMedia = !!m.media_ok;
  modal(`<h3>${t('Restore from backup')}</h3><div class="mb bk">
    <p>${t('The catalog (tags, albums, ratings, people and edits) will be restored to its state from {0}: {1} photos.', [ltr(fdt(m.created)), num(m.photos)])}</p>
    <p>${t('A backup of the current state is saved before restoring, so you can undo the restore. Photo files are never deleted by a restore.')}</p>
    <label class="chkrow ${canMedia ? '' : 'off'}"><input type="checkbox" id="rs-media" ${canMedia ? 'checked' : 'disabled'}> ${t('Also restore image files missing from the folder (from the backup)')}</label>
    <label class="chkrow ${canMedia ? '' : 'off'}"><input type="checkbox" id="rs-over" ${canMedia ? '' : 'disabled'}> ${t('Also replace files that changed since this backup (for example compressed photos and videos) with the versions from the backup')}</label>
    <label class="chkrow"><input type="checkbox" id="rs-set"> ${t('Also restore AI settings and the HandBrake path')}</label>
  </div><div class="mf"><button id="rs-cancel">${t('Cancel')}</button><span class="spacer"></span><button class="primary" id="rs-go">${t('Restore')}</button></div>`);
  $('#rs-cancel').onclick = () => backupDialog();
  $('#rs-go').onclick = async () => {
    const body = {name: m.name, media: canMedia && $('#rs-media').checked, settings: $('#rs-set').checked, overwrite: canMedia && $('#rs-media').checked && $('#rs-over').checked};
    closeModal();
    try{ await send('POST', '/api/backup/restore', body); pollJob('backup', t('Restore from backup')); } catch(e){ toast(e.message); }
  };
}

// Tell the user at start-up (once a day) if automatic backups are not really happening.
async function backupHealthNotice(){
  let i; try{ i = await api('/api/backup'); }catch{ return; }
  const h = i.health || {};
  if(!(h.overdue || h.last_error || h.folder_error) || !h.enabled) return;
  const today = new Date().toDateString();
  if(pref.get('bkNoticeDay', '') === today) return;
  pref.set('bkNoticeDay', today);
  const why = h.folder_error ? t('Can\'t reach the backup folder (e.g. the drive is disconnected): {0}', [h.folder_error])
    : h.last_error ? t('The last backup failed: {0}', [h.last_error])
    : t('No backup has been made since {0}. Check that the drive is connected and has free space.', [ltr(fdt(h.last_success))]);
  modal(`<h3>${t('Automatic backup isn\'t working properly')}</h3><div class="mb"><p>${esc(why)}</p></div>
    <div class="mf"><button id="bn-settings">${t('Backup settings')}</button><span class="spacer"></span><button id="bn-close">${t('Close')}</button><button class="primary" id="bn-now">${t('Back up now')}</button></div>`);
  $('#bn-close').onclick = closeModal;
  $('#bn-settings').onclick = () => backupDialog();
  $('#bn-now').onclick = async () => { closeModal(); try{ await send('POST', '/api/backup/run'); pollJob('backup', t('Backup')); }catch(e){ toast(e.message); } };
}

// The default data folder is now ...\Photag: offer to move a library that still sits in ...\PhotoManager (a rename, nothing is copied).
async function libraryMoveNotice(){
  const st = S.status || await api('/api/status');
  const n = st.move_notice || {};
  if(n.moved_from || n.error){
    modal(`<h3>${n.error ? t('The library could not be moved') : t('Your library was moved')}</h3><div class="mb">
      <p>${n.error ? t('Your photos are still in {0}. Reason: {1}', [ltr(String(n.moved_from || st.library_root)), esc(n.error)]) : t('Your photos, catalog and backups are now in {0}.', [ltr(String(n.moved_to))])}</p></div>
      <div class="mf"><span class="spacer"></span><button class="primary" id="lm-ok">${t('OK')}</button></div>`);
    $('#lm-ok').onclick = async ()=>{ closeModal(); await send('POST', '/api/library/move-ack'); };
    return true;
  }
  if(!st.legacy_library || pref.get('legacyMoveNo', false)) return false;
  modal(`<h3>${t('Move your library to the new folder?')}</h3><div class="mb">
    <p>${t('photag now keeps its data in {0}. Your library is still in the old folder {1}.', [ltr(st.target_library), ltr(st.legacy_library)])}</p>
    <p>${t('Moving only renames the folder: nothing is copied or deleted, and it takes a moment. It happens the next time you start photag.')}</p></div>
    <div class="mf"><button id="lm-never">${t("Don't ask again")}</button><span class="spacer"></span><button id="lm-later">${t('Not now')}</button><button class="primary" id="lm-go">${t('Move it')}</button></div>`);
  $('#lm-later').onclick = closeModal;
  $('#lm-never').onclick = ()=>{ pref.set('legacyMoveNo', true); closeModal(); };
  $('#lm-go').onclick = async ()=>{ try{ await send('POST', '/api/library/move-legacy'); }catch(e){ toast(e.message); return; }
    modal(`<h3>${t('Almost done')}</h3><div class="mb"><p>${t('Close photag and open it again to finish moving your library.')}</p></div><div class="mf"><span class="spacer"></span><button class="primary" id="lm-ok2">${t('OK')}</button></div>`);
    $('#lm-ok2').onclick = closeModal; };
  return true;
}

// ---------- job screen: click the progress line in the corner for numbers, speed and time left (import has the most detail) ----------
const JOBSCR = {open: false, name: '', label: '', last: null, samples: [], t0: {}, fin: {}};
const JOB_CANCEL = {import: '/api/import/cancel', aitag: '/api/aitag/cancel', compress: '/api/compress/cancel'};
const JOB_UNIT = {import: 'files', compress: 'files', export: 'files', faces: 'photos', aitag: 'photos', backup: 'bytes', update: 'bytes'};
function jobScreenOpen(name, label){
  JOBSCR.open = true; JOBSCR.name = name; JOBSCR.label = label; JOBSCR.samples = [];
  modal(`<h3 id="ims-title">${esc(label)}</h3><div class="mb ims">
    <div class="cpg-file" id="ims-cur"></div>
    <div class="cpg-bar"><i id="ims-fill"></i><span id="ims-pct">0%</span></div>
    <div class="ims-grid" id="ims-grid"></div>
    <svg class="ims-spark" id="ims-spark" viewBox="0 0 300 44" preserveAspectRatio="none"></svg>
    <div class="hint" id="ims-fail" style="padding:0"></div>
  </div><div class="mf"><span class="spacer"></span><button id="ims-bg">${t('Continue in background')}</button>${JOB_CANCEL[name] ? `<button id="ims-cancel" class="danger">${t('Cancel')}</button>` : ''}</div>`);
  $('#ims-bg').onclick = ()=>{ JOBSCR.open = false; closeModal(); };
  if($('#ims-cancel')) $('#ims-cancel').onclick = async ()=>{ $('#ims-cancel').disabled = true; $('#ims-cancel').textContent = t('Cancelling…'); await send('POST', JOB_CANCEL[name]); };
  jobScreenUpdate(name, JOBSCR.last);
}
function jobScreenUpdate(name, p){
  if(p && name === JOBSCR.name) JOBSCR.last = p;
  if(!JOBSCR.open || name !== JOBSCR.name) return;
  p = JOBSCR.last;
  if(!document.getElementById('ims-fill')){ JOBSCR.open = false; return; }             // another dialog took the place
  if(!p) return;
  const x = p.extra || {}, now = Date.now(), finished = ['done', 'error'].includes(p.state), isImport = name === 'import';
  if(!JOBSCR.t0[name] || (JOBSCR.fin[name] && !finished)){ JOBSCR.t0[name] = now; delete JOBSCR.fin[name]; }          // a new run starts the clock again
  if(finished && !JOBSCR.fin[name]) JOBSCR.fin[name] = now;
  const t0 = isImport && x.t0 ? x.t0 * 1000 : JOBSCR.t0[name], el = Math.max(0.001, ((JOBSCR.fin[name] || now) - t0) / 1000);
  const total = p.total || 0, done = p.done || 0, bytes = isImport ? (x.bytes || 0) : (JOB_UNIT[name] === 'bytes' ? done : 0), btotal = isImport ? (x.bytes_total || 0) : (JOB_UNIT[name] === 'bytes' ? total : 0);
  if(!finished && total) JOBSCR.samples.push([now, bytes, done]);
  JOBSCR.samples = JOBSCR.samples.slice(-90);
  // speed over the last ~10 seconds (falls back to the average since the start)
  const recent = JOBSCR.samples.filter(s => now - s[0] <= 10000), a = recent[0], b = recent[recent.length - 1];
  const win = a && b && b[0] > a[0] ? (b[0] - a[0]) / 1000 : 0;
  const rateB = win ? (b[1] - a[1]) / win : bytes / el, rateF = win ? (b[2] - a[2]) / win : done / el;
  const avgB = bytes / el, avgF = done / el;
  const pct = total ? Math.min(100, done / total * 100) : 0;
  const left = !finished && pct > 1 ? (btotal && rateB > 0 ? (btotal - bytes) / rateB : (rateF > 0 ? (total - done) / rateF : null)) : null;
  const src = {takeout: t('Import from Google Takeout'), folder: t('Import from a folder or memory card'), lightroom: t('Import from Lightroom')}[x.source] || JOBSCR.label;
  const cancelled = finished && p.msg && /cancel/i.test(p.msg);
  $('#ims-title').textContent = p.state === 'error' ? `${JOBSCR.label}: ${t('Failed')}` : finished ? `${JOBSCR.label}: ${cancelled ? t('Cancelled') : t('Done')}` : (isImport ? src : JOBSCR.label);
  $('#ims-fill').style.width = (finished && p.state !== 'error' ? 100 : pct).toFixed(1) + '%';
  $('#ims-pct').textContent = !total && !finished ? '…' : (finished ? '' : Math.floor(pct) + '%');
  const msg = p.error_key ? t(p.error_key, p.vars) : p.parts ? p.parts.map(q => t(q.key, q.vars)).join(' · ') : p.key ? t(p.key, p.vars) : (p.msg || '');
  $('#ims-cur').innerHTML = finished || !isImport ? esc(msg) : `${x.album ? `<bdi>${esc(x.album)}</bdi> · ` : ''}<bdi>${esc(x.current || msg)}</bdi>`;
  const unit = {files: t('files/s'), photos: t('photos/s'), bytes: ''}[JOB_UNIT[name] || 'files'];
  const mbs = v => ltr((v / 1048576).toFixed(v >= 10485760 ? 1 : 2) + ' MB/s');
  const speed = fin => JOB_UNIT[name] === 'bytes' ? mbs(fin ? avgB : rateB) : ltr((fin ? avgF : rateF).toFixed(1) + ' ' + unit) + (bytes ? ' · ' + mbs(fin ? avgB : rateB) : '');
  const cards = [];
  if(total && JOB_UNIT[name] !== 'bytes') cards.push([t('Progress'), t('{0} of {1}', [num(done), num(total)]), '']);
  else cards.push([t('Progress'), finished ? '100%' : Math.floor(pct) + '%', '']);
  if(isImport || btotal) cards.push([t('Data'), btotal ? `${fsize(bytes)} / ${fsize(btotal)}` : fsize(bytes), '']);
  cards.push([t('Speed'), finished ? t('Average: {0}', [speed(true)]) : speed(false), '']);
  cards.push([t('Time elapsed'), fmtDur(el), '']);
  cards.push([finished ? t('Total time') : t('Estimated time left'), finished ? fmtDur(el) : left == null ? '…' : '~' + fmtDur(left), '']);
  if(isImport){
    cards.push([t('Added'), num(x.added || 0), 'ok']);
    cards.push([t('Already in catalog'), num(x.duplicates || 0), '']);
    cards.push([t('Failed'), num((x.failed || 0) + (x.missing || 0)), (x.failed || 0) + (x.missing || 0) ? 'bad' : '']);
  }
  $('#ims-grid').innerHTML = cards.map(([k, v, c]) => `<div class="ims-card ${c}"><span>${k}</span><b>${v}</b></div>`).join('');
  // speed over time
  const pts = JOBSCR.samples.map((s, i, arr) => i ? Math.max(0, (s[1] ? s[1] - arr[i - 1][1] : s[2] - arr[i - 1][2]) / Math.max(0.2, (s[0] - arr[i - 1][0]) / 1000)) : 0).slice(1);
  const mx = Math.max(1, ...pts);
  $('#ims-spark').innerHTML = pts.length > 1 ? `<polyline fill="none" stroke="var(--blue)" stroke-width="1.5" vector-effect="non-scaling-stroke" points="${pts.map((v, i) => `${(i / (pts.length - 1) * 300).toFixed(1)},${(42 - v / mx * 40).toFixed(1)}`).join(' ')}"/>` : '';
  const fl = x.failures || [];
  $('#ims-fail').innerHTML = fl.length ? `${t('Files that could not be imported')}: ${fl.slice(0, 6).map(n => `<bdi>${esc(n)}</bdi>`).join(', ')}${fl.length > 6 ? ' …' : ''}` : '';
  if(finished && !document.getElementById('ims-close')){
    $('.mf').innerHTML = `<span class="spacer"></span>${isImport && x.added ? `<button id="ims-show">${t('Show the imported photos')}</button>` : ''}<button class="primary" id="ims-close">${t('Close')}</button>`;
    $('#ims-close').onclick = ()=>{ JOBSCR.open = false; closeModal(); };
    if($('#ims-show')) $('#ims-show').onclick = ()=>{ JOBSCR.open = false; closeModal(); setSource(srcFromKey('prev')); };
  }
}

// ---------- compression progress screen: percent, elapsed / remaining time, steps ----------
const CPG = {alive: false};
const fmtDur = s => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return ltr((h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(x).padStart(2, '0')); };
function compressProgressOpen(items, reopen){
  Object.assign(CPG, {alive: true, n: items.length, items}, reopen && CPG.t0 ? {} : {t0: Date.now(), ts: {}, f: 0});
  modal(`<h3>${t('Compressing…')}</h3><div class="mb cpg">
    <div class="cpg-file" id="cpg-file"><bdi>${esc(items[0].filename)}</bdi></div>
    <div class="cpg-bar"><i id="cpg-fill"></i><span id="cpg-pct">0%</span></div>
    <div class="cpg-stats"><span>${t('Elapsed time')}: <b id="cpg-el">${fmtDur(0)}</b></span><span>${t('Estimated time remaining')}: <b id="cpg-eta">…</b></span><span id="cpg-fps"></span></div>
    <ul class="cpg-steps" id="cpg-steps"></ul>
    <div class="cpg-list" id="cpg-list"></div>
    <p class="hint" style="padding:0">${t('You can keep working in the app while compression runs.')}</p>
  </div><div class="mf"><span class="spacer"></span><button id="cpg-bg">${t('Continue in background')}</button><button id="cpg-cancel">${t('Cancel compression')}</button></div>`);
  $('#cpg-bg').onclick = ()=>{ CPG.alive = false; closeModal(); };
  $('#cpg-cancel').onclick = async ()=>{ $('#cpg-cancel').disabled = true; $('#cpg-cancel').textContent = t('Cancelling…'); await send('POST', '/api/compress/cancel'); };
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
  $('#cpg-fps').textContent = (isVid && st === 'encoding' && ex.fps) ? t('{0} frames per second', [ltr(String(Math.round(ex.fps)))]) : '';
  $('#cpg-file').innerHTML = CPG.n === 1 ? `<bdi>${esc(cur.filename)}</bdi>` : `${t('File {0} of {1}', [num(i + 1), num(CPG.n)])}: <bdi>${esc(cur.filename)}</bdi>`;
  const order = ['probing', 'encoding', 'verifying', 'replacing'], at = order.indexOf(st);
  const names = [t('Checking the original file'), t('Compression'), t('Verify result'), t('Back up and replace file')];
  $('#cpg-steps').innerHTML = names.map((nm, k) => `<li class="${k < at ? 'done' : k === at ? 'cur' : ''}"><i>${k < at ? '✓' : k === at ? '●' : '○'}</i>${nm}</li>`).join('');
  const items = (p.result && p.result.items) || [];
  const fin = items.filter(it => it.status !== 'running');
  if(CPG.n > 1){
    const saved = fin.reduce((a, it) => a + (it.status === 'done' ? it.src_bytes - it.out_bytes : 0), 0);
    $('#cpg-list').innerHTML = fin.length ? `<div class="hint" style="padding:0">${t('{0} of {1} done · {2} saved so far', [num(fin.length), num(CPG.n), fmtBytes(saved)])}</div>`
      + fin.slice(-6).map(it => `<div class="cpg-row ${it.status === 'done' ? 'ok' : it.status === 'failed' ? 'bad' : 'warn'}"><i>${it.status === 'done' ? '✓' : it.status === 'failed' ? '✗' : '–'}</i><bdi>${esc(it.filename)}</bdi></div>`).join('') : '';
  }
}

// report for a batch: one row per file
function compressBatchReport(r){
  const c = r.counts || {}, n = r.items.length;
  const label = {done:t('Compressed'), no_gain:t('No space saved: the file is already well compressed and was left as it was'), skipped:t('Skipped'), failed:t('Failed'), cancelled:t('Canceled')};
  const row = it => {
    const ok = it.status === 'done';
    const detail = ok ? t('{0} ← {1} ({2}% of original)', [fmtBytes(it.src_bytes), fmtBytes(it.out_bytes), ltr(String(Math.round(it.ratio * 100)))]) + (it.grade ? ' · ' + GRADE_LABELS()[it.grade] : '')
      : it.status !== 'no_gain' && it.error_key ? t(it.error_key, it.error_vars) : label[it.status];
    const cls = ok ? 'ok' : (it.status === 'no_gain' || it.status === 'skipped' || it.status === 'cancelled') ? 'warn' : 'bad';
    return `<div class="chk ${cls}">${I(ok ? 'check' : 'close')}<b><bdi>${esc(it.filename)}</bdi></b><span>${esc(detail)}</span></div>`;
  };
  modal(`<h3>${t('Compression report')}</h3><div class="mb">
    <p style="color:${c.done ? 'var(--green)' : 'var(--yellow)'}">${t('Compressed {0} of {1} files · saved {2}', [num(c.done || 0), num(n), fmtBytes(r.saved || 0)])}</p>
    <p class="hint" style="padding:0">${t('Each file was checked separately, and its previous version was saved in backups.')}</p>
    <div class="cmp-rows">${r.items.map(row).join('')}</div>
  </div><div class="mf"><span class="spacer"></span><button class="primary" id="cr-close">${t('Close')}</button></div>`);
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
  if(info.error){ if(manual) toast(t('Unable to check for updates: {0}', [info.error])); return; }
  if(!info.available){ if(manual) toast(t('You are using the latest version ({0})', [ltr(info.current)])); return; }
  if(info.skipped && !manual) return;
  updateDialog(info);
}

// After an update the app shows the release notes of the new version (fetched from GitHub); an update that
// did not finish says so, and that nothing was lost. Returns true when it showed something.
async function whatsNew(manual){
  let r;
  try{ r = await api('/api/update/whatsnew' + (manual ? '?current=1' : '')); } catch(e){ if(manual) toast(e.message); return false; }
  const notesHtml = n => !n ? '' : n.error
    ? `<p class="hint" style="padding:0">${t('Could not fetch the release notes from GitHub ({0}).', [n.error])}</p><button class="linkbtn" id="wn-page">${t('Open the release page')}</button>`
    : (n.notes && n.notes.trim() ? `<div class="upd-notes">${mdLite(n.notes)}</div>` : `<span class="hint" style="padding:0">${t('No details for this version.')}</span>`);
  const close = async ()=>{ closeModal(); if(!manual) await send('POST', '/api/update/whatsnew/ack'); };
  const wire = ()=>{ $('#wn-close').onclick = close; if($('#wn-page')) $('#wn-page').onclick = ()=>send('POST', '/api/update/open-page'); };
  if(!manual && r.failed){
    modal(`<h3>${t('Update not completed')}</h3><div class="mb upd">
      <p>${t('The update to version {0} didn\'t finish (for example, the computer shut down midway, or the installer was closed).', [ltr(String(r.failed.to || ''))])}</p>
      <p>${r.failed.restored ? t('The previous version was restored automatically.') : t('The previous version was left in place and works as usual.')}</p>
      <p>${t('Your photos, catalog and settings were not affected. You can try again from Help ← Check for updates.')}</p>
    </div><div class="mf"><span class="spacer"></span><button class="primary" id="wn-close">${t('Close')}</button></div>`);
    wire(); return true;
  }
  if(!manual && r.updated){
    modal(`<h3>${t('Updated to version {0}', [ltr(String(r.updated.to))])}</h3><div class="mb upd">
      <p>${t('The update has finished. Your photos and data are unchanged.')}</p>
      <div class="lbl-sub" style="padding:0">${t('What\'s new')}</div>${notesHtml(r.notes)}
    </div><div class="mf"><span class="spacer"></span><button class="primary" id="wn-close">${t('Close')}</button></div>`);
    wire(); return true;
  }
  if(manual){
    modal(`<h3>${t('What\'s new in version {0}', [ltr(String(r.current))])}</h3><div class="mb upd">${notesHtml(r.notes)}</div>
      <div class="mf"><span class="spacer"></span><button class="primary" id="wn-close">${t('Close')}</button></div>`);
    wire(); return true;
  }
  return false;
}

function updateDialog(info){
  const auto = info.can_install && info.frozen;
  modal(`<h3>${t('Update available')}</h3><div class="mb upd">
    <p>${t('Version {0} is available. Installed version: {1}.', [ltr(info.latest), ltr(info.current)])}</p>
    <div class="lbl-sub" style="padding:0">${t('What\'s new')}</div>
    <div class="upd-notes">${info.notes.trim() ? mdLite(info.notes) : `<span class="hint" style="padding:0">${t('No details for this version.')}</span>`}</div>
    ${auto ? `<p class="hint" style="padding:0">${t('The update is installed over the existing version. Your photos and data are not touched, and if it is interrupted midway (for example, the computer shuts down) the previous version is restored automatically.')}</p>` : ''}
    <div id="up-prog" class="hidden"><div class="progress"><i id="up-bar"></i></div></div>
    <div class="hint" id="up-msg" style="padding:0;min-height:16px"></div>
  </div><div class="mf"><button id="up-skip">${t('Skip this version')}</button><span class="spacer"></span>
    <button id="up-later">${t('Later')}</button><button id="up-go" class="primary">${auto ? t('Update now') : t('Open the release page')}</button></div>`);
  const msg = (text, cls='') => { $('#up-msg').textContent = text; $('#up-msg').className = 'hint ' + cls; $('#up-msg').style.padding = '0'; };
  const busy = on => ['up-skip', 'up-later', 'up-go'].forEach(id=>$('#' + id).disabled = on);
  $('#up-later').onclick = closeModal;
  $('#up-skip').onclick = async ()=>{ await send('POST', '/api/update/skip', {version:info.latest}); closeModal(); toast(t('This version will be skipped. You can always check manually in the Help menu.')); };
  $('#up-go').onclick = async ()=>{
    if(!auto){ await send('POST', '/api/update/open-page'); if(info.can_install) msg(t('An update can\'t be installed automatically when running from source code. The release page was opened.')); return; }
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
      if(r.mode === 'dry-run') msg(t('(Test) The file was downloaded and verified but not launched'), 'ok');
      else if(r.mode === 'page'){ await send('POST', '/api/update/open-page'); msg(t('An update can\'t be installed automatically when running from source code. The release page was opened.')); busy(false); }
      else msg(t('Installing and restarting…'), 'ok');
    }catch(e){ msg(e.message, 'err'); busy(false); $('#up-prog').classList.add('hidden'); }
  };
}

function languageDialog(){
  modal(`<h3>${t('Language')}${I18N.lang==='en'?'':' / Language'}</h3><div class="mb"><div class="lang-grid">
    ${LANGS.map(([c,n,d])=>`<button class="${c===I18N.lang?'primary':''}" data-lang="${c}" dir="${d}">${n}</button>`).join('')}</div>
    <p>${t('Automatic keywords stay in English in all languages.')}</p></div>
    <div class="mf"><button onclick="closeModal()">${t('Close')}</button></div>`);
  $$('#modal-box [data-lang]').forEach(b=>b.onclick=()=>{ if(b.dataset.lang!==I18N.lang) setLanguage(b.dataset.lang); else closeModal(); });
}
async function memories(){
  const m=await api('/api/memories'), tg=m.titles||[], c=m.comments||[];
  modal(`<h3>${t("Memories and comments from Google Photos")}</h3><div class="mb">
    ${!tg.length&&!c.length?("<p>"+t("No memories or comments will be imported.")+"</p>"):''}
    ${tg.length?`<p>${t("Memory Titles")}</p><div class="mem-list">${tg.map(x=>`<div>${esc(x)}</div>`).join('')}</div>`:''}
    ${c.length?`<p>${t("Comments in shared albums")}</p><div class="mem-list">${c.map(x=>`<div><time>${fdate(x.created_at)}</time>${x.liked?'♥ ':''}${esc(x.text)||t('(like)')}</div>`).join('')}</div>`:''}
  </div><div class="mf"><button class="primary" onclick="closeModal()">${t("Close")}</button></div>`);
}
function shortcuts(){
  const k=(key,tg)=>`<kbd>${key}</kbd><span>${tg}</span>`;
  modal(`<h3>${t("Keyboard Shortcuts")}</h3><div class="mb"><div class="kgrid">
    <h4>${t("Views")}</h4>${k('G',t('Grid'))}${k('E',t('Loupe'))}${k('C',t('Compare'))}${k('N',t('Survey'))}${k('O',t('People'))}${k('D',t('Develop Module'))}${k('Ctrl+Enter',t('Slideshow'))}${k('Esc',t('Back / Exit'))}
    <h4>${t("Rating and Flagging")}</h4>${k('P',t('Flag as Pick'))}${k('X',t('Flag as Rejected'))}${k('U',t('Remove Flag'))}${k('`',t('Toggle Flag'))}${k('0–5',t('Star Rating'))}${k('[ / ]',t('Decrease / Increase Rating'))}${k('6–9',t('Label Red/Yellow/Green/Blue'))}${k(t('Shift+key'),t('Mark and Go to Next'))}${k('B',t('Quick Collection'))}${k('Ctrl+B',t('Show Quick Collection'))}
    <h4>${t("Selection")}</h4>${k('Ctrl+A',t('Select All'))}${k('Ctrl+D',t('Deselect'))}${k(t('Ctrl+click'),t('Add to Selection'))}${k(t('Shift+click'),t('Select Range'))}${k('← → ↑ ↓',t('Move Between Photos'))}${k('Delete',t('Move to Trash'))}
    <h4>${t("Interface")}</h4>${k('Tab',t('Hide Side Panels'))}${k('Shift+Tab',t('Hide All Panels'))}${k('F5 / F6',t('Top Panel / Filmstrip'))}${k('F7 / F8',t('Right / Left Panel'))}${k('T',t('Toolbar'))}${k('L',t('Lights Out'))}${k('J',t('Grid Cell Style'))}${k('I',t('Loupe Info'))}${k('\\\\',t('Filter Bar / Before-After'))}${k('Ctrl+L',t('Enable/Disable Filters'))}${k('Ctrl+F',t('Text Search'))}${k(t('Z / Space'),t('Zoom 1:1'))}
    <h4>${t("Files")}</h4>${k('Ctrl+Shift+I',t('Import'))}${k('Ctrl+Shift+E',t('Export'))}${k('Ctrl+N',t('New Collection'))}${k('Ctrl+[ / ]',t('Rotation'))}${k('Ctrl+R',t('Show in Explorer'))}${k('Ctrl+S',t('Save Metadata to File'))}${k('Ctrl+K',t('Add Keywords'))}${k('R',t('Crop (Develop)'))}
    <h4>${t("Video")}</h4>${k('Space / K',t('Play / Pause'))}${k('Shift+← / →',t('Skip 10 seconds'))}${k(', / .',t('Frame back / forward'))}${k('Shift+, / .',t('Playback speed'))}${k('↑ / ↓',t('Volume'))}${k('M',t('Mute'))}${k('F',t('Fullscreen'))}
  </div></div><div class="mf"><button class="primary" onclick="closeModal()">${t("Close")}</button></div>`);
}

// ---------- export ----------
function openExport(){
  const ids=targets().length ? targets() : [];
  if(!ids.length) return toast(t('Select photos to export'));
  const last=pref.get('export', {dest:'', mode:'current', edge:2048, q:90});
  modal(`<h3>${t("Export {0} Files", [num(ids.length)])}</h3><div class="mb">
    <label class="fld"><span>${t("Export To")}</span><div class="frow"><input type="text" id="ex-dest" dir="ltr" value="${esc(last.dest)}" placeholder="C:\\Users\\...\\Pictures\\Export"><button id="ex-pick">${t("Choose...")}</button></div></label>
    <div class="fld"><span>${t("File Settings")}</span>
      <label class="check" style="padding:0"><input type="radio" name="ex-mode" value="current" ${last.mode==='current'?'checked':''}> ${t(" The file as it is in the catalog (including edits)")}</label>
      <label class="check" style="padding:0"><input type="radio" name="ex-mode" value="original" ${last.mode==='original'?'checked':''}> ${t(" The original, without edits")}</label>
      <label class="check" style="padding:0"><input type="radio" name="ex-mode" value="jpeg" ${last.mode==='jpeg'?'checked':''}> ${t(" JPEG, resized")}</label></div>
    <div class="frow" id="ex-jpeg"><span>${t("Long Edge")}</span><input type="number" id="ex-edge" min="200" max="20000" value="${last.edge}" style="width:90px" dir="ltr"><span>${t("Pixels · Quality")}</span>
      <input type="range" id="ex-q" min="40" max="100" value="${last.q}" style="width:120px"><output id="ex-qv">${last.q}</output></div>
    <p>${t("Videos are always copied as they are. Duplicate file names get a number.")}</p>
  </div><div class="mf"><button onclick="closeModal()">${t("Cancel")}</button><button class="primary" id="ex-go">${t("Export")}</button></div>`);
  $('#ex-q').oninput=e=>$('#ex-qv').textContent=e.target.value;
  $('#ex-pick').onclick=async()=>{ const r=await api('/api/pick-file?kind=folder&title='+encodeURIComponent(t('Choose export folder'))); if(r.path) $('#ex-dest').value=r.path; };
  $('#ex-go').onclick=async()=>{
    const dest=$('#ex-dest').value.trim(), mode=$('input[name=ex-mode]:checked').value, edge=+$('#ex-edge').value||null, q=+$('#ex-q').value;
    if(!dest) return toast(t('Choose a destination folder'));
    pref.set('export', {dest, mode, edge:edge||2048, q});
    closeModal();
    runJob('/api/export','export',t('Export'),{ids, dest, originals:mode==='original', long_edge:mode==='jpeg'?edge:null, quality:mode==='jpeg'?q:100});
  };
}

// ---------- import (full-window dialog like Lightroom's) ----------
const IM = {mode:'folder', path:'', zips:[], zipMissing:[], zipFound:0, lrcat:'', lrinfo:null, recursive:true, files:[], on:new Set(), skipDup:true, show:'all'};
function openImport(mode){
  IM.mode = mode || IM.mode;
  IM.path = IM.path || pref.get('importPath','');
  $('#import').classList.remove('hidden');
  renderImport();
  if(IM.mode==='folder' && IM.path && !IM.files.length) scanImport();
}
function closeImport(){ $('#import').classList.add('hidden'); }
// one key per file whatever the slashes and capitals (the file dialog says C:/x/a.zip, the server C:\x\a.zip)
const zkey = p => String(p).split('\\').join('/').toLowerCase();
// part numbers missing in a Takeout set (takeout-<stamp>-001.zip, -002.zip, ...), per export
function zipGaps(zips){
  const sets = new Map();
  for(const z of zips){ const m = /^(.+)-(\d{3,})\.zip$/i.exec(z.name); if(m){ const k = m[1].toLowerCase(); if(!sets.has(k)) sets.set(k, new Set()); sets.get(k).add(+m[2]); } }
  const gaps = new Set();
  for(const s of sets.values()) for(let n = 1; n < Math.max(...s); n++) if(!s.has(n)) gaps.add(n);
  return [...gaps].sort((a, b) => a - b);
}
function renderImport(){
  const el=$('#import'), folder=IM.mode==='folder', lr=IM.mode==='lrcat', li=IM.lrinfo;
  const shown = IM.files.filter(f=>IM.show==='all' || !f.dup);
  const chosen = IM.files.filter(f=>IM.on.has(f.path));
  const bytes = chosen.reduce((a,f)=>a+f.bytes,0);
  const recent = pref.get('importRecent', []);
  const ready = folder ? chosen.length : lr ? (li && li.images-li.missing>0) : IM.zips.length;
  const zipTotal = IM.zips.reduce((a, z) => a + z.bytes, 0);
  el.innerHTML = `
  <div class="im-top">
    <div class="blk"><span>${t("Source")}</span><b>${esc(folder ? (IM.path||t('Choose a folder')) : lr ? (IM.lrcat||t('Choose a Lightroom catalog')) : (IM.zips.length > 1 ? t('{0} ZIP files · {1}', [num(IM.zips.length), fsize(zipTotal)]) : IM.zips.length ? IM.zips[0].name : t('Choose a ZIP file')))}</b></div>
    <span class="im-arrow">←</span>
    <nav class="im-modes"><a data-im="folder" class="${folder?'on':''}">${t("Copy")}<small>${t("From Folder / Memory Card")}</small></a><a data-im="lrcat" class="${lr?'on':''}">Lightroom Classic<small>${t("Catalog .lrcat")}</small></a><a data-im="zip" class="${IM.mode==='zip'?'on':''}">Google Takeout<small>${t("ZIP file from Google Photos")}</small></a></nav>
    <span class="im-arrow">←</span>
    <div class="blk"><span>${t("Destination")}</span><b>${esc(S.status?.media_path||'')}</b></div>
  </div>
  <div class="im-body">
    <aside class="im-side">
      <section class="pnl"><h3><span>${t("Source")}</span></h3><div class="pbody">
        ${folder ? `<div class="btnrow"><button id="im-pick">${I('folder')} ${t(" Choose folder...")}</button></div>
          <label class="check"><input type="checkbox" id="im-rec" ${IM.recursive?'checked':''}> ${t(" Include subfolders")}</label>
          ${recent.length?`<div class="lbl-sub" style="padding-top:8px">${t("Recent")}</div>${recent.map(p=>`<div class="row" data-recent="${esc(p)}">${I('folder')}<span class="nm" dir="ltr" title="${esc(p)}">${esc(p)}</span></div>`).join('')}`:''}`
        : lr ? `<div class="btnrow"><button id="im-lrcat">${I('import')} ${t(" Choose Lightroom catalog...")}</button></div>
          <div class="hint">${t("File ")}<code>.lrcat</code>${t(" from Lightroom Classic (usually in Pictures/Lightroom). Closing Lightroom before importing is recommended.")}</div>`
        : `<div class="btnrow"><button id="im-zip">${I('import')} ${t(" Choose ZIP files...")}</button></div>
          <div class="hint">${t("Download your library from takeout.google.com (Google Photos). The files are read directly, without extracting them. A large export comes as several ZIP files (…-001.zip, …-002.zip): choose them all, or just one, and the other parts in the same folder are added automatically.")}</div>`}
      </div></section>
    </aside>
    <div class="im-center">
      ${folder ? `<div class="im-bar"><a data-show="all" class="${IM.show==='all'?'on':''}">${t("All Photographs")}</a><a data-show="new" class="${IM.show==='new'?'on':''}">${t("New Photos")}</a>
        <span class="spacer"></span><a data-chk="all">${t("Check All")}</a><a data-chk="none">${t("Uncheck")}</a></div>
        <div class="im-grid" id="im-grid">${IM.loading?("<div class=\"im-empty\">"+t("Scanning…")+"</div>"):!IM.files.length?`<div class="im-empty">${IM.path?t('No photos or videos were found in the folder.'):t('Choose a folder or memory card from the “Source” panel.')}</div>`
          : shown.slice(0,3000).map(f=>`<div class="im-cell ${IM.on.has(f.path)?'':'off'}" data-path="${esc(f.path)}" title="${esc(f.path)}">
            <input type="checkbox" ${IM.on.has(f.path)?'checked':''}>${f.dup?("<span class=\"dup\" title=\""+t("Already in catalog (same name and size)")+"\">"+t("Duplicate")+"</span>"):''}
            ${f.is_video?`<span class="vid">${I('play')}</span>`:`<img loading="lazy" src="/api/local-thumb?path=${encodeURIComponent(f.path)}" alt="">`}
            <span class="nm">${esc(f.name)}</span></div>`).join('') + (shown.length>3000?`<div class="im-empty">${t("Showing the first 3,000 of {0} — all will be imported if checked.", [num(shown.length)])}</div>`:'')}</div>`
      : lr ? `<div class="im-grid"><div class="im-empty">${IM.lrloading?t('Reading the catalog…'):!li?t('Choose a Lightroom Classic catalog from the “Source” panel.')
          : `<b dir="ltr">${esc(IM.lrcat)}</b><br>${t("{0} photos · {1} keywords · {2} collections · {3}", [num(li.images), num(li.keywords), num(li.collections), fsize(li.bytes)])}
             ${li.missing?`<br><span style="color:var(--yellow)">${t("{0} files referenced by the catalog were not found on disk and will not be imported.", [num(li.missing)])}</span>`:''}`}</div></div>`
      : `<div class="im-grid"><div class="im-empty">${IM.zips.length ? `${IM.zips.map((z,i)=>`<div class="zrow" dir="ltr"><b>${esc(z.name)}</b> · ${fsize(z.bytes)}<button class="zrm" data-zrm="${i}" title="${t('Remove this file from the import')}">✕</button></div>`).join('')}
          <div><a class="zclear" data-zclear>${t('Remove all')}</a></div>
          ${IM.zipFound ? `<br>${t('{0} more parts from the same folder were added.', [num(IM.zipFound)])}` : ''}
          ${IM.zipMissing.length ? `<br><span style="color:var(--yellow)">${t('Part {0} is missing: photos that are only in it will not be imported. You can import it later; photos that were already imported are skipped.', [IM.zipMissing.map(n=>String(n).padStart(3,'0')).join(', ')])}</span>` : ''}
          <br><br>${t("Import will keep albums, dates, locations, favorites, people tags and memories. Photos already in the catalog will not be duplicated.")}`
          : t('Choose the ZIP files from Google Takeout.')}</div></div>`}
    </div>
    <aside class="im-side">
      ${folder?`<section class="pnl"><h3><span>${t("File Handling")}</span></h3><div class="pbody">
        <label class="check"><input type="checkbox" id="im-skipdup" ${IM.skipDup?'checked':''}> ${t(" Don't import suspected duplicates")}</label>
        <div class="hint">${t("Files that are completely identical (by content) are never saved twice.")}</div></div></section>
      <section class="pnl"><h3><span>${t("Apply During Import")}</span></h3><div class="pbody">
        <label class="fld" style="padding:4px 12px"><span>${t("Keywords")}</span><input type="text" id="im-kw" placeholder="${t("Vacation, family")}"></label>
        <label class="fld" style="padding:4px 12px"><span>${t("Add to Collection")}</span><input type="text" id="im-album" list="im-albums" placeholder="${t("None")}"></label>
        <datalist id="im-albums">${S.albums.filter(a=>a.kind==='album').map(a=>`<option value="${esc(a.name)}">`).join('')}</datalist></div></section>`:''}
      ${lr?`<section class="pnl"><h3><span>${t("What Is Imported")}</span></h3><div class="pbody"><div class="hint">
        ${t(" Ratings, flags, color labels, captions, capture dates, locations, keywords (“person” keywords become People), collections and the Quick Collection. Files are copied into the library — the original is not changed. Develop edits are saved in Lightroom's format and are not transferred; the original file is imported. Virtual copies and smart collections are skipped.")}</div></div></section>`:''}
      <section class="pnl"><h3><span>${t("Destination")}</span></h3><div class="pbody">
        <div class="hint"><code>${esc(S.status?.media_path||'')}</code><br>${t("Organized in folders by capture year (for example")} <code>2024</code>${t("). You can change this under File → Catalog Settings.")}</div></div></section>
    </aside>
  </div>
  <div class="im-foot">${folder?`${t("{0} photos / {1}", [num(chosen.length), fsize(bytes)])}`:lr&&li?`${t("{0} photos / {1}", [num(li.images-li.missing), fsize(li.bytes)])}`:''}<span class="spacer"></span>
    <button id="im-cancel">${t("Cancel")}</button><button class="primary" id="im-go" ${ready?'':'disabled'}>${t("Import")}</button></div>`;
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
  if(tg.closest('#im-pick')){ const r=await api('/api/pick-file?kind=folder&title='+encodeURIComponent(t('Choose folder to import'))); if(r.path){ IM.path=r.path; scanImport(); } return; }
  if(tg.closest('#im-zip')){
    const r=await api('/api/pick-file?kind=zip&title='+encodeURIComponent(t('Choose Google Takeout ZIP files')));
    if(!(r.files||[]).length) return;
    const picked = new Map(IM.zips.map(z => [zkey(z.path), z]));          // choosing again ADDS to the list
    r.files.forEach(f => picked.set(zkey(f.path), f));
    const before = picked.size;
    let info = {parts:[]}; try{ info = await api('/api/takeout/parts?path='+encodeURIComponent(r.files[0].path)); }catch{}
    info.parts.forEach(p => { if(!picked.has(zkey(p.path))) picked.set(zkey(p.path), p); });
    IM.zips = [...picked.values()].sort((a,b)=>a.name.localeCompare(b.name));
    IM.zipFound = picked.size - before; IM.zipMissing = zipGaps(IM.zips);
    renderImport(); return; }
  const zrm = tg.closest('[data-zrm]');                                              // a part added by mistake: take it out again
  if(zrm){ IM.zips.splice(+zrm.dataset.zrm, 1); IM.zipFound = 0; IM.zipMissing = zipGaps(IM.zips); renderImport(); return; }
  if(tg.closest('[data-zclear]')){ IM.zips = []; IM.zipFound = 0; IM.zipMissing = []; renderImport(); return; }
  if(tg.closest('#im-lrcat')){ const r=await api('/api/pick-file?kind=lrcat&title='+encodeURIComponent(t('Choose Lightroom catalog'))); if(!r.path) return;
    IM.lrcat=r.path; IM.lrinfo=null; IM.lrloading=true; renderImport();
    try{ IM.lrinfo=await api('/api/lrcat-info?'+new URLSearchParams({path:r.path})); } finally { IM.lrloading=false; renderImport(); } return; }
  const rec=tg.closest('[data-recent]'); if(rec){ IM.path=rec.dataset.recent; scanImport(); return; }
  const sh=tg.closest('[data-show]'); if(sh){ IM.show=sh.dataset.show; renderImport(); return; }
  const ck=tg.closest('[data-chk]'); if(ck){ IM.on = ck.dataset.chk==='all' ? new Set(IM.files.filter(f=>IM.show==='all'||!f.dup).map(f=>f.path)) : new Set(); renderImport(); return; }
  const cell=tg.closest('.im-cell'); if(cell){ const p=cell.dataset.path; IM.on.has(p)?IM.on.delete(p):IM.on.add(p);
    cell.classList.toggle('off', !IM.on.has(p)); cell.querySelector('input').checked=IM.on.has(p);
    const chosen=IM.files.filter(f=>IM.on.has(f.path)); $('#import .im-foot').firstChild.textContent=`${t("{0} photos / {1}", [num(chosen.length), fsize(chosen.reduce((a,f)=>a+f.bytes,0))])}`;
    $('#im-go').disabled=!chosen.length; return; }
  if(tg.closest('#im-go')){
    if(IM.mode==='zip'){ await send('POST','/api/import',{zip_paths:IM.zips.map(z=>z.path)}); closeImport(); pollJob('import',t('Import from Google')); return; }
    if(IM.mode==='lrcat'){ await send('POST','/api/import-lrcat',{path:IM.lrcat}); closeImport(); pollJob('import',t('Import from Lightroom')); return; }
    const paths=IM.files.filter(f=>IM.on.has(f.path)).map(f=>f.path);
    await send('POST','/api/import-folder',{paths, keywords:($('#im-kw').value||'').split(','), album:$('#im-album').value||null});
    closeImport(); IM.files=[]; pollJob('import',t('Import'));
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
  jobScreenUpdate(name, p);
  const act=$('#activity'), bar=$('.act-bar');
  act.classList.remove('hidden');
  const pct = p.total ? Math.round(100*p.done/p.total) : null;
  $('#act-label').textContent = `${label}${pct!=null?` · ${pct}%`:'…'}`;
  bar.classList.toggle('indet', pct==null); $('#act-fill').style.width = (pct??0)+'%';
  const msg = p.parts ? p.parts.map(x=>t(x.key, x.vars)).join(' · ') : p.key ? t(p.key, p.vars) : (p.msg || p.state);
  act.title = `${label}: ${msg}`;
  act.onclick = ()=>{ if(name==='compress' && CPG.items && CPG.items.length) compressProgressOpen(CPG.items, true); else jobScreenOpen(name, label); };
  if(['done','error','idle'].includes(p.state)){
    act.classList.add('hidden');
    if(name==='compress' && CPG.alive){ CPG.alive = false; closeModal(); }
    toast(`<bdi>${label}</bdi>: <bdi>${esc(p.error_key ? t(p.error_key, p.vars) : p.error || msg || t('Done'))}</bdi>`, 4000);   // bdi: Latin model names must not scramble RTL text
    if(['import','faces','aitag','compress','backup'].includes(name)){
      await reloadAll();
      if(name==='import' && p.state==='done' && S.status.last_import) setSource(srcFromKey('prev'));
      if(S.view==='people') renderPeople();
      if(name==='aitag' || name==='backup') renderRight();
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
  [t('File'), [
    [t('Import...'), 'Ctrl+Shift+I', ()=>openImport('folder')],        // one screen: choose the source there (folder / card, Lightroom, Google Takeout)
    [t('Export...'), 'Ctrl+Shift+E', openExport],
    sep,
    [t('Backup and restore...'), '', backupDialog],
    [t('Catalog Settings...'), 'Ctrl+Alt+,', catalogSettings],
    [t('Preferences...'), 'Ctrl+,', preferences],
  ]],
  [t('Edit'), [
    [t('Select All'), 'Ctrl+A', selectAll],
    [t('Deselect'), 'Ctrl+D', selectNone],
    [t('Invert Selection'), '', selectInvert],
    [t('Select Flagged Photos'), 'Ctrl+Alt+A', selectPicks],
  ]],
  [t('Library'), [
    [t('New Collection...'), 'Ctrl+N', newCollection],
    sep,
    [t('Show Quick Collection'), 'Ctrl+B', ()=>setSource(srcFromKey('quick'))],
    [t('Clear Quick Collection'), '', async()=>{ const ids=S.all.filter(p=>p.quick).map(p=>p.id); if(ids.length) await setAttr({quick:0}, ids); }],
    sep,
    [t('Enable Filters'), 'Ctrl+L', ()=>{ S.F.on=!S.F.on; applyFilter(); }, null, ()=>S.F.on],
    [t('Show Library Filter Bar'), '\\', ()=>{ $('#filterbar').classList.toggle('hidden'); }, null, ()=>!$('#filterbar').classList.contains('hidden')],
    sep,
    [t('Face Detection'), '', ()=>runJob('/api/faces','faces',t('Face Detection'))],
    sep,
    [t('Memories and comments from Google...'), '', memories],
  ]],
  [t('Photo'), [
    [t('Add to Quick Collection'), 'B', toggleQuick],
    [t('Show in Explorer'), 'Ctrl+R', reveal],
    sep,
    [t('Rotate Left'), 'Ctrl+[', ()=>rotateSel(-90)],
    [t('Rotate Right'), 'Ctrl+]', ()=>rotateSel(90)],
    sep,
    [t('Flag: Pick'), 'P', ()=>setFlag(1)],
    [t('Flag: Rejected'), 'X', ()=>setFlag(-1)],
    [t('Unflagged'), 'U', ()=>setFlag(0)],
    sep,
    ...[0,1,2,3,4,5].map(n=>[n?`${'★'.repeat(n)}`:t('No Rating'), String(n), ()=>setRating(n)]),
    sep,
    ...LABELS.map(([k,n,key])=>[`${t("Label: {0}", [n])}`, key, ()=>setLabel(k)]),
    [t('No Label'), '', ()=>setAttr({label:''})],
    sep,
    [t('Compress selected files...'), '', ()=>compressDialog(targets())],
    [t('Stop compression'), '', ()=>send('POST','/api/compress/cancel')],
    sep,
    [t('Move to Trash'), 'Delete', trashSelected],
    [t('Restore'), '', restoreSelected],
    [t('Delete permanently'), 'Delete', deleteForever],
    [t('Empty the trash'), '', emptyTrash],
  ]],
  [t('Metadata'), [
    [t('Add Keywords'), 'Ctrl+K', ()=>{ document.body.classList.remove('hide-right'); $('.pnl[data-p=kwing]').classList.remove('shut'); $('#kw-add')?.focus(); }],
    [t('AI tagging for selected photos...'), '', aiRun],
    [t('AI tagging settings...'), '', aiSettings],
    [t('Stop AI tagging'), '', ()=>send('POST','/api/aitag/cancel')],
    sep,
    [t('Save Metadata to File'), 'Ctrl+S', saveMetaToFile],
    [t('Synchronize Metadata'), '', ()=>$('#btn-sync-meta').click()],
  ]],
  [t('View'), [
    [t('Language') + (I18N.lang==='en' ? '' : ' / Language') + '...', '', languageDialog],
    sep,
    [t('Grid'), 'G', ()=>setView('grid')], [t('Loupe'), 'E', ()=>setView('loupe')], [t('Compare'), 'C', ()=>setView('compare')],
    [t('Survey'), 'N', ()=>setView('survey')], [t('People'), 'O', ()=>setView('people')], [t('Map'), '', ()=>setView('map')], [t('Develop'), 'D', ()=>setModule('develop')],
    [t('Slideshow'), 'Ctrl+Enter', ssStart],
    sep,
    [t('Cycle Grid Cell Style'), 'J', cycleCellStyle],
    [t('Loupe Info'), 'I', ()=>{ S.loupeInfo=!S.loupeInfo; renderLoupe(); renderToolbar(); }, null, ()=>S.loupeInfo],
    sep,
    [t('Hide/Show Side Panels'), 'Tab', toggleSides],
    [t('Hide/Show All Panels'), 'Shift+Tab', toggleAllPanels],
    [t('Toolbar'), 'T', ()=>togglePanel('tool'), null, ()=>!document.body.classList.contains('hide-tool')],
    [t('Lights Out'), 'L', cycleLights],
  ]],
  [t('Help'), [
    [t('Keyboard Shortcuts'), 'Ctrl+/', shortcuts],
    [t('Check for Updates...'), '', ()=>updateCheck(true)],
    [t('What\'s new in this version...'), '', ()=>whatsNew(true)],
    [t('About photag'), '', ()=>modal(`<h3>photag</h3><div class="mb"><p class="hint" style="padding:0">${t('Version {0}', [ltr(S.status?.version || '')])}</p><p>${t("Local photo management and storage inspired by Lightroom Classic: catalog, collections, flags, ratings, color labels, keywords, face detection and non-destructive editing — the original is always preserved.")}</p><p class="hint" style="padding:0">${t("Free software under the GPL-3.0 license, with no warranty. You may modify and redistribute it under the license terms.")}</p></div><div class="mf"><button class="primary" onclick="closeModal()">${t("Close")}</button></div>`)],
  ]],
];
let MENU_OPEN=null, MENU_ITEMS=[];
$('#menubar').innerHTML = MENUS.map(([n],i)=>`<button data-menu="${i}">${n}</button>`).join('');
function openMenu(i){
  const b=$(`#menubar [data-menu="${i}"]`), pop=$('#menu-pop'), items=MENU_ITEMS=MENUS[i][1];
  $$('#menubar button').forEach(x=>x.classList.toggle('open', x===b));
  pop.innerHTML = items.map((it,j)=>it===sep?'<hr>':`<div class="mi ${it[4]&&it[4]()?'chk':''}" data-mi="${j}"><span>${it[0]}</span><span class="k">${it[1]||''}</span></div>`).join('');
  const r=b.getBoundingClientRect(); pop.style.top=r.bottom+'px'; if(RTL){ pop.style.right=(innerWidth-r.right)+'px'; pop.style.left='auto'; } else { pop.style.left=r.left+'px'; pop.style.right='auto'; }
  pop.classList.remove('hidden'); MENU_OPEN=i;
}
// right-click menu on photos: the same popup as the menu bar, placed at the pointer
function photoMenuItems(){
  const trash = S.src.kind==='trash', n = targets().length, photos = targets().some(id=>!(S.byId.get(id)||{}).is_video);
  const sep = null;
  if(trash) return [
    [t('Restore'), '', restoreSelected],
    [t('Show in Explorer'), 'Ctrl+R', reveal],
    sep,
    [t('Delete permanently'), 'Delete', deleteForever],
    [t('Empty the trash'), '', emptyTrash],
    sep,
    [t('Select All'), 'Ctrl+A', selectAll],
  ];
  return [
    [t('Loupe'), 'E', ()=>setView('loupe')],
    [t('Add to Quick Collection'), 'B', toggleQuick],
    [t('Show in Explorer'), 'Ctrl+R', reveal],
    sep,
    ...(photos ? [[t('Rotate Left'), 'Ctrl+[', ()=>rotateSel(-90)], [t('Rotate Right'), 'Ctrl+]', ()=>rotateSel(90)], sep] : []),
    [t('Flag: Pick'), 'P', ()=>setFlag(1)],
    [t('Flag: Rejected'), 'X', ()=>setFlag(-1)],
    [t('Unflagged'), 'U', ()=>setFlag(0)],
    sep,
    ...[5,4,3,2,1,0].map(r=>[r?'★'.repeat(r):t('No Rating'), String(r), ()=>setRating(r)]),
    sep,
    [t('Export...'), '', openExport],
    [t('Compress selected files...'), '', ()=>compressDialog(targets())],
    sep,
    [t('Move to Trash'), 'Delete', trashSelected],
  ];
}
function openContextMenu(x, y){
  const pop=$('#menu-pop'), items=MENU_ITEMS=photoMenuItems();
  pop.innerHTML = items.map((it,j)=>it===null?'<hr>':`<div class="mi" data-mi="${j}"><span>${it[0]}</span><span class="k">${it[1]||''}</span></div>`).join('');
  pop.classList.remove('hidden'); MENU_OPEN=-1;
  const w=pop.offsetWidth, h=pop.offsetHeight;
  pop.style.left=Math.max(0, Math.min(x, innerWidth-w-4))+'px'; pop.style.right='auto';
  pop.style.top=Math.max(0, Math.min(y, innerHeight-h-4))+'px';
}
document.addEventListener('contextmenu', e=>{
  const c = e.target.closest('.cell, .fc');
  if(!c || !c.dataset.id){ if(!e.target.closest('input,textarea')) e.preventDefault(); return; }
  e.preventDefault();
  const id=+c.dataset.id;
  if(!S.sel.has(id)) selectOnly(id); else { S.act=id; }
  openContextMenu(e.clientX, e.clientY);
});
function closeMenu(){ $('#menu-pop').classList.add('hidden'); $$('#menubar button').forEach(x=>x.classList.remove('open')); MENU_OPEN=null; }
$('#menubar').addEventListener('mousedown', e=>{ const b=e.target.closest('[data-menu]'); if(!b) return; e.stopPropagation(); MENU_OPEN===+b.dataset.menu ? closeMenu() : openMenu(+b.dataset.menu); });
$('#menubar').addEventListener('mouseover', e=>{ const b=e.target.closest('[data-menu]'); if(b && MENU_OPEN!=null && MENU_OPEN!==+b.dataset.menu) openMenu(+b.dataset.menu); });
$('#menu-pop').addEventListener('mousedown', e=>{ e.stopPropagation(); const it=e.target.closest('[data-mi]'); if(!it) return; const f=MENU_ITEMS[+it.dataset.mi][2]; closeMenu(); f(); });
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
      KeyL: ()=>{ S.F.on=!S.F.on; applyFilter(); toast(S.F.on?t('Filters enabled'):t('Filters disabled'), 1200); },
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
      if(MENU_OPEN!=null){ closeMenu(); return; }
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
  setTimeout(async ()=>{ if(await libraryMoveNotice()) return; if(!(await whatsNew(false))) updateCheck(false); }, 2500);
  setTimeout(backupHealthNotice, 8000);   // quiet check on start-up; a window appears only when a newer release exists
  // an empty catalog shows the empty-state screen with an Import button; it never jumps to the Import screen by itself
  // resume the activity indicator if a job is already running (e.g. after a reload)
  [['import',t('Import')],['faces',t('Face Detection')],['aitag',t('AI tagging')],['compress',t('Video compression')],['backup',t('Backup')],['export',t('Export')]].forEach(async ([n,l])=>{
    try{ const p=await api('/api/job/'+n); if(p && p.state && !['done','error','idle'].includes(p.state)) pollJob(n,l); }catch{}
  });
})();
