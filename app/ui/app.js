const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const api = async (u, opt) => (await fetch(u, opt)).json();
const jpost = (u, body) => api(u, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body||{})});
const jpatch = (u, body) => api(u, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body||{})});
const esc = s => (s??'').toString().replace(/[<>&"']/g, c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));
const fdate = t => t ? new Date(t*1000).toLocaleString('he-IL', {dateStyle:'long', timeStyle:'short'}) : '—';
const show = id => $('#'+id).classList.remove('hidden');
const hide = id => $('#'+id).classList.add('hidden');
const I = (name, cls='') => `<svg class="ic ${cls}"><use href="#i-${name}"/></svg>`;
const num = n => (n||0).toLocaleString('he-IL');
const ALL = 'limit=1000000';

let STATE = {view:'home', photos:[], sorted:[], sort:'date_desc', list:null};
let TRASH_DAYS = 60;
let STATUS = null;

function toast(msg, keep){ const t=$('#toast'); t.innerHTML=msg; t.classList.remove('hidden');
  clearTimeout(t._h); if(!keep) t._h=setTimeout(()=>t.classList.add('hidden'), 3500); }
function debounce(fn,ms){let h;return(...a)=>{clearTimeout(h);h=setTimeout(()=>fn(...a),ms);};}
const content = () => $('#content');

// A small promise-based replacement for prompt(): askText('title', 'value') -> string|null
function askText(title, value=''){
  return new Promise(res=>{
    $('#dialog-title').textContent=title; const inp=$('#dialog-input'); inp.value=value;
    show('dialog'); inp.focus(); inp.select();
    const done=v=>{ hide('dialog'); $('#dialog-form').onsubmit=null; $('#dialog-cancel').onclick=null; res(v); };
    $('#dialog-form').onsubmit=e=>{e.preventDefault(); done(inp.value.trim()||null);};
    $('#dialog-cancel').onclick=()=>done(null);
  });
}

// ---------- navigation ----------
$$('[data-view]').forEach(b=>b.onclick=()=>{ $('#search').value=''; route(b.dataset.view); });
function setActive(view){
  const top = ['favorites','trash','search'].includes(view) ? 'all' : view;
  $$('#nav button').forEach(x=>x.classList.toggle('active', x.dataset.view===top));
  $$('.tools [data-view]').forEach(x=>x.classList.toggle('active', x.dataset.view===view));
}
$('#search').oninput = debounce(()=>{
  const q=$('#search').value.trim();
  if(q) showPhotos({title:`תוצאות עבור „${q}"`, filter:{q}, view:'search'});
  else route(STATE.lastView||'home');
}, 300);
$('#btn-import').onclick = ()=>show('import-modal');

// grid toolbar: sort + thumbnail size (remembered per machine)
$('#sort').value = STATE.sort;
$('#sort').onchange = ()=>{ STATE.sort=$('#sort').value; renderPhotoGrid(); };
function setRowH(v){ document.documentElement.style.setProperty('--rowh', v+'px'); }
(()=>{ let v=190; try{ v=+localStorage.getItem('rowh')||190; }catch{} $('#thumb-size').value=v; setRowH(v); })();
$('#thumb-size').oninput = e=>{ setRowH(e.target.value); try{ localStorage.setItem('rowh', e.target.value); }catch{}
  if(STATE.list) renderPhotoGrid(); };

// ---------- side panel (catalog / albums / people) ----------
function sideBtn(key, label, n, icon, onclick){
  return `<button data-key="${key}" onclick="${onclick}">${icon}<span class="name">${esc(label)}</span>${n!=null?`<span class="n">${num(n)}</span>`:''}</button>`;
}
async function renderSide(){
  const [s, al, pl] = await Promise.all([api('/api/status'), api('/api/albums'), api('/api/people')]);
  STATUS=s; TRASH_DAYS=s.trash_days;
  $('#side-catalog').innerHTML =
    sideBtn('all','כל התמונות', s.counts.photos, I('photos'), "route('all')") +
    sideBtn('favorites','מועדפים', null, I('star'), "route('favorites')") +
    sideBtn('trash','אשפה', s.counts.trashed, I('trash'), "route('trash')");
  const albums = al.filter(a=>a.kind!=='year').concat(al.filter(a=>a.kind==='year'));
  $('#side-albums').innerHTML = albums.map(a=>sideBtn('album:'+a.id, a.name, a.n, I(a.kind==='year'?'calendar':'albums'),
    `openAlbum(${a.id},'${esc(a.name)}')`)).join('') || '<div class="hint" style="padding:0 14px">אין אלבומים</div>';
  $('#side-people').innerHTML = pl.map(p=>{
    const src = p.cover_face?'/face/'+p.cover_face : p.cover_photo?'/thumb/'+p.cover_photo : '';
    return sideBtn('person:'+p.id, p.name, (p.face_photos||0)+(p.tag_photos||0),
      src?`<img class="ava" loading="lazy" src="${src}" alt="">`:I('people'), `openPerson(${p.id},'${esc(p.name)}')`);
  }).join('') || '<div class="hint" style="padding:0 14px">הריצו זיהוי פנים כדי לראות אנשים</div>';
  markSide();
}
function markSide(){
  const k = STATE.sideKey || '';
  $$('#side .list button').forEach(b=>b.classList.toggle('active', b.dataset.key===k));
}

async function route(view){
  STATE.view=view; if(view!=='search') STATE.lastView=view; setActive(view);
  STATE.sideKey = ['all','favorites','trash'].includes(view) ? view : ''; markSide();
  content().scrollTop=0;
  if(view==='home') return loadHome();
  if(view==='all') return showPhotos({title:'כל התמונות', filter:{}, view});
  if(view==='favorites') return showPhotos({title:'מועדפים', filter:{favorite:1}, view, icon:'star',
      empty:'אין עדיין מועדפים.<br>סמנו תמונה בכוכב כדי שתופיע כאן.'});
  if(view==='trash') return showPhotos({title:'אשפה', filter:{trashed:1}, view, icon:'trash',
      note:`פריטים באשפה נמחקים לצמיתות אחרי ${TRASH_DAYS} יום. פתחו תמונה ולחצו «שחזר» כדי להחזיר אותה.`,
      empty:'האשפה ריקה.'});
  if(view==='albums') return loadAlbums();
  if(view==='people') return loadPeople();
  if(view==='tags') return loadTags();
  if(view==='memories') return loadMemories();
  if(view==='settings') return loadSettings();
}

// ---------- photo list screens ----------
// opts: {title, filter, view, back, note, icon, empty, side}
async function showPhotos(opts){
  STATE.list = opts; show('gridbar');
  if(opts.side){ STATE.sideKey=opts.side; markSide(); }
  const c=content();
  const back = opts.back ? `<button class="back" onclick="route('${opts.back}')" aria-label="חזרה">${I('back')}</button>` : '';
  c.innerHTML = `
    <div class="page-head">${back}<h1>${esc(opts.title)}</h1><span class="count" id="count"></span></div>
    ${opts.note?`<div class="note">${I('memories')}${opts.note}</div>`:''}
    <div id="grid"></div><div id="sentinel"></div>`;
  $('#gb-count').textContent='';
  const q = new URLSearchParams(opts.filter).toString();
  STATE.photos = await api('/api/photos?'+ALL+(q?'&'+q:''));
  if(STATE.list!==opts) return;  // user navigated away meanwhile
  renderPhotoGrid();
}
function noList(){ STATE.list=null; hide('gridbar'); if(_obs){ _obs.disconnect(); _obs=null; } }
async function reloadList(){ if(STATE.list) { const top=content().scrollTop; await showPhotos(STATE.list); content().scrollTop=top; } }

function sortPhotos(photos, sort){
  const arr=photos.slice();
  if(sort==='date_asc') arr.sort((a,b)=>(a.taken_at||0)-(b.taken_at||0));
  else if(sort==='name_asc') arr.sort((a,b)=>a.filename.localeCompare(b.filename));
  else if(sort==='name_desc') arr.sort((a,b)=>b.filename.localeCompare(a.filename));
  else if(sort==='favorite') arr.sort((a,b)=>(b.favorited-a.favorited)||((b.taken_at||0)-(a.taken_at||0)));
  else arr.sort((a,b)=>(b.taken_at||0)-(a.taken_at||0));
  return arr;
}
function monthLabel(t){
  return t ? new Date(t*1000).toLocaleDateString('he-IL', {month:'long', year:'numeric'}) : 'ללא תאריך';
}
function ratio(p){
  const r = (p.width && p.height) ? p.width/p.height : 1;
  return Math.min(2.4, Math.max(0.55, r));
}
function tile(p, i){
  const r=ratio(p), h=parseInt(getComputedStyle(document.documentElement).getPropertyValue('--rowh'))||210;
  return `<div class="tile" data-i="${i}" style="flex-grow:${r.toFixed(3)};width:${Math.round(r*h)}px"><i style="padding-bottom:${(100/r).toFixed(3)}%"></i>
    <img loading="lazy" decoding="async" src="/thumb/${p.id}" alt="" onerror="this.parentElement.classList.add('noimg')">
    ${p.is_video?`<span class="badge">${I('play')}</span>`:''}${p.favorited?`<svg class="fav"><use href="#i-star"/></svg>`:''}${p.rating?`<span class="rt">${'★'.repeat(p.rating)}</span>`:''}</div>`;
}
function grid(items, offset, cls=''){
  return `<div class="jgrid ${cls}">${items.map((p,k)=>tile(p, offset+k)).join('')}</div>`;
}

let _obs=null;
function renderPhotoGrid(){
  const g=$('#grid'); if(!g) return;
  const opts=STATE.list||{};
  $('#count').textContent = STATE.photos.length ? `${num(STATE.photos.length)} פריטים` : '';
  $('#gb-count').textContent = STATE.photos.length ? `${num(STATE.photos.length)} פריטים` : '';
  if(!STATE.photos.length){
    g.innerHTML = `<div class="empty">${I(opts.icon||'photos')}${opts.empty||'<b>אין כאן תמונות עדיין</b>ייבאו את הספרייה שלכם מ‑Google Photos כדי להתחיל.<br><button class="primary" onclick="show(\'import-modal\')">'+I('upload')+'ייבוא מגוגל פוטוס</button>'}</div>`;
    return;
  }
  STATE.sorted = sortPhotos(STATE.photos, STATE.sort);
  // group into days (date sorts) or one flat group, then render progressively on scroll
  const groups=[];
  if(STATE.sort.startsWith('date')){
    let cur=null;
    STATE.sorted.forEach((p,i)=>{
      const d=monthLabel(p.taken_at);
      if(!cur||cur.label!==d){ cur={label:d, start:i, items:[]}; groups.push(cur); }
      cur.items.push(p);
    });
  } else groups.push({label:null, start:0, items:STATE.sorted});
  g.innerHTML=''; let gi=0;
  const more=()=>{
    let html='', budget=240;
    while(gi<groups.length && budget>0){
      const gr=groups[gi++];
      if(gr.label) html+=`<div class="day-header"><b>${gr.label}</b><span>${num(gr.items.length)}</span></div>`;
      html+=grid(gr.items, gr.start, gr.label?'':'flat'); budget-=gr.items.length;
    }
    g.insertAdjacentHTML('beforeend', html);
    if(gi>=groups.length && _obs){ _obs.disconnect(); _obs=null; }
  };
  more();
  if(_obs) _obs.disconnect(); _obs=null;
  if(gi<groups.length){
    _obs=new IntersectionObserver(es=>{ if(es.some(e=>e.isIntersecting)) more(); }, {root:content(), rootMargin:'1200px'});
    _obs.observe($('#sentinel'));
  }
}
content().addEventListener('click', e=>{
  const t=e.target.closest('.tile'); if(!t) return;
  const list = t.closest('.home-recent') ? STATE.homeRecent : STATE.sorted;
  openLightbox(list, +t.dataset.i);
});

// ---------- home ----------
function greeting(){ const h=new Date().getHours();
  return h<5?'לילה טוב':h<12?'בוקר טוב':h<17?'צהריים טובים':h<21?'ערב טוב':'לילה טוב'; }
async function loadHome(){
  noList();
  const [s, people, albums, recent, mem] = await Promise.all([
    api('/api/status'), api('/api/people'), api('/api/albums'), api('/api/photos?limit=40'), api('/api/memories')]);
  STATUS=s; TRASH_DAYS=s.trash_days;
  if(STATE.view!=='home') return;
  const c=s.counts;
  if(!c.photos){
    content().innerHTML = `<div class="onboard">
      <h1>ברוכים הבאים ל‑PhotoManager</h1>
      <p>כל התמונות שלכם, על המחשב שלכם. שלושה צעדים ואתם מסודרים:</p>
      <div class="steps">
        <div class="step"><span class="num">1</span><div><b>ייבוא מ‑Google Photos</b><span>בחרו את קובץ ה‑ZIP מ‑Google Takeout — אלבומים, תאריכים ומיקומים נשמרים.</span></div>
          <button class="primary" onclick="show('import-modal')">${I('upload')}ייבוא</button></div>
        <div class="step"><span class="num">2</span><div><b>זיהוי פנים</b><span>מקבץ את האנשים בתמונות כדי שתוכלו לתת להם שמות.</span></div>
          <button onclick="route('settings')">להגדרות</button></div>
        <div class="step"><span class="num">3</span><div><b>תיוג חכם</b><span>תגיות אוטומטיות לחיפוש, בעזרת Ollama (אופציונלי).</span></div>
          <button onclick="route('settings')">להגדרות</button></div>
      </div></div>`;
    return;
  }
  STATE.homeRecent = recent;
  const named = people.filter(p=>p.cover_face||p.cover_photo).slice(0,14);
  const realAlbums = albums.filter(a=>a.kind!=='year').slice(0,10);
  const years = albums.filter(a=>a.kind==='year');
  content().innerHTML = `
    <section class="hero"><h1>${greeting()}</h1>
      <p>${num(c.photos)} תמונות ו‑${num(c.videos)} סרטונים בספרייה שלך</p>
      <div class="stat-row">
        <div class="stat" onclick="route('all')"><b>${num(c.photos)}</b><span>תמונות</span></div>
        <div class="stat" onclick="route('albums')"><b>${num(c.albums)}</b><span>אלבומים</span></div>
        <div class="stat" onclick="route('people')"><b>${num(c.people)}</b><span>אנשים</span></div>
        <div class="stat" onclick="route('tags')"><b>${num(c.tags)}</b><span>תגיות</span></div>
      </div></section>
    ${named.length?`<div class="sec-head"><h2 class="sec">אנשים</h2><a onclick="route('people')">הכול</a></div>
      <div class="strip">${named.map(personCard).join('')}</div>`:''}
    <div class="sec-head"><h2 class="sec">נוספו לאחרונה</h2><a onclick="route('all')">כל התמונות</a></div>
    <div class="home-recent">${grid(recent,0)}</div>
    ${realAlbums.length?`<div class="sec-head"><h2 class="sec">אלבומים</h2><a onclick="route('albums')">הכול</a></div>
      <div class="strip">${realAlbums.map(albumCard).join('')}</div>`:''}
    ${years.length?`<div class="sec-head"><h2 class="sec">לפי שנה</h2></div>
      <div class="chips">${years.map(a=>`<button class="chip" onclick="openAlbum(${a.id},'${esc(a.name)}','home')">${esc(a.name)} <b>${num(a.n)}</b></button>`).join('')}</div>`:''}
    ${(mem.titles||[]).length?`<div class="sec-head"><h2 class="sec">זיכרונות</h2><a onclick="route('memories')">הכול</a></div>
      <div class="chips">${mem.titles.slice(0,12).map(t=>`<span class="chip">${esc(t)}</span>`).join('')}</div>`:''}`;
}

// ---------- albums ----------
function albumCard(a){
  const kind = a.kind==='year'?'לפי שנה':a.kind==='people-share'?'משותף':'אלבום';
  return `<div class="card" onclick="openAlbum(${a.id},'${esc(a.name)}')">
    <div class="thumb">${a.cover?`<img loading="lazy" src="/thumb/${a.cover}" alt="">`:I('albums')}</div>
    <div class="meta"><b>${esc(a.name)}</b><span>${num(a.n)} פריטים · ${kind}</span></div></div>`;
}
async function loadAlbums(kind){
  noList(); STATE.albumKind = kind ?? STATE.albumKind ?? 'all';
  const al = await api('/api/albums');
  const k=STATE.albumKind;
  const shown = al.filter(a=> k==='all' || (k==='year'?a.kind==='year':k==='shared'?a.kind==='people-share':(a.kind!=='year'&&a.kind!=='people-share')));
  const seg=[['all','הכול'],['album','אלבומים'],['year','שנים'],['shared','משותפים']];
  content().innerHTML = `<div class="page-head"><h1>אלבומים</h1><span class="count">${num(al.length)}</span><div class="spacer"></div>
      <div class="seg">${seg.map(([v,l])=>`<button class="${v===k?'active':''}" onclick="loadAlbums('${v}')">${l}</button>`).join('')}</div></div>
    ${shown.length?`<div class="cards">${shown.map(albumCard).join('')}</div>`:`<div class="empty">${I('albums')}אין אלבומים כאן.</div>`}`;
}
window.openAlbum=(id,name,back='albums')=>showPhotos({title:name, filter:{album:id}, view:'albums', back, side:'album:'+id});

// ---------- people ----------
function personCard(p){
  const n=(p.face_photos||0)+(p.tag_photos||0);
  return `<div class="card person" onclick="openPerson(${p.id},'${esc(p.name)}')">
    <div class="thumb">${p.cover_face||p.cover_photo?`<img loading="lazy" src="${p.cover_face?'/face/'+p.cover_face:'/thumb/'+p.cover_photo}" alt="">`:I('people')}</div>
    <div class="meta"><b>${esc(p.name)}<button class="rename" title="שינוי שם" aria-label="שינוי שם" onclick="event.stopPropagation();renamePerson(${p.id},'${esc(p.name)}')">${I('edit')}</button></b>
    <span>${num(n)} תמונות</span></div></div>`;
}
async function loadPeople(){
  noList();
  const pl = await api('/api/people');
  if(!pl.length){ content().innerHTML=`<div class="page-head"><h1>אנשים</h1></div>
    <div class="empty">${I('face')}<b>עוד לא זוהו אנשים</b>הריצו «זיהוי פנים» כדי לקבץ את האנשים בתמונות.<br>
    <button class="primary" onclick="route('settings')">להגדרות</button></div>`; return; }
  content().innerHTML = `<div class="page-head"><h1>אנשים</h1><span class="count">${num(pl.length)}</span></div>
    <div class="cards people">${pl.map(personCard).join('')}</div>`;
}
window.openPerson=(id,name)=>showPhotos({title:name, filter:{person:id}, view:'people', back:STATE.view==='home'?'home':'people', side:'person:'+id});
window.renamePerson=async (id,cur)=>{ const n=await askText('שם חדש', cur||''); if(!n) return;
  const r=await jpost(`/api/person/${id}/rename`,{name:n}); if(r&&r.detail){toast('שגיאה: '+esc(r.detail)); return;}
  toast('השם עודכן'); renderSide(); route(STATE.view==='home'?'home':'people'); };

// ---------- tags ----------
async function loadTags(){
  noList();
  const tg = await api('/api/tags');
  if(!tg.length){ content().innerHTML=`<div class="page-head"><h1>תגיות</h1></div>
    <div class="empty">${I('tags')}<b>אין תגיות עדיין</b>הריצו «תיוג חכם» כדי לקבל תגיות אוטומטיות לחיפוש.<br>
    <button class="primary" onclick="route('settings')">להגדרות</button></div>`; return; }
  const max=Math.log(tg[0].n+1);
  content().innerHTML = `<div class="page-head"><h1>תגיות</h1><span class="count">${num(tg.length)}</span></div>
    <input class="filter" id="tag-filter" type="search" placeholder="סינון תגיות…">
    <div class="chips tag-cloud" id="tag-cloud">${tg.map(t=>`<button class="chip" data-name="${esc(t.name.toLowerCase())}" style="font-size:${(13+6*Math.log(t.n+1)/max).toFixed(1)}px" onclick="openTag(${t.id},'${esc(t.name)}')">${esc(t.name)} <b>${num(t.n)}</b></button>`).join('')}</div>`;
  $('#tag-filter').oninput=e=>{ const v=e.target.value.toLowerCase();
    $$('#tag-cloud .chip').forEach(c=>c.classList.toggle('hidden', !c.dataset.name.includes(v))); };
}
window.openTag=(id,name)=>showPhotos({title:name, filter:{tag:id}, view:'tags', back:'tags', icon:'tags'});

// ---------- memories ----------
async function loadMemories(){
  noList();
  const m = await api('/api/memories');
  const titles=m.titles||[], comments=m.comments||[];
  content().innerHTML = `<div class="page-head"><h1>זיכרונות</h1></div>
    ${!titles.length&&!comments.length?`<div class="empty">${I('memories')}<b>אין זיכרונות עדיין</b>זיכרונות ותגובות מיובאים מ‑Google Photos.</div>`:''}
    ${titles.length?`<div class="sec-head"><h2 class="sec">מ‑Google Photos</h2></div>
      <div class="mem-grid">${titles.map(t=>`<div class="mem">${I('memories')}<span>${esc(t)}</span></div>`).join('')}</div>`:''}
    ${comments.length?`<div class="sec-head"><h2 class="sec">תגובות באלבומים משותפים</h2></div>
      ${comments.map(c=>`<div class="comment"><time>${fdate(c.created_at)}</time><div>${c.liked?I('heart')+' ':''}${esc(c.text)||'(לייק)'}</div></div>`).join('')}`:''}`;
}

// ---------- loupe view ----------
let CUR=null, LB={list:[], i:0}, LB_DIRTY=false;
async function openLightbox(list, i){
  LB={list, i}; show('lightbox'); await loadLightboxItem();
}
async function loadLightboxItem(){
  const item=LB.list[LB.i]; if(!item) return;
  const want=item.id;
  CUR = await api('/api/photo/'+want);
  if(LB.list[LB.i]?.id!==want) return;   // stepped again meanwhile
  const id=CUR.id;
  $('#lb-media').innerHTML = CUR.is_video ? `<video src="/media/${id}" controls autoplay></video>`
                                          : `<img src="/media/${id}" alt="">`;
  const img=$('#lb-media img');
  if(img) img.onload=()=>drawHisto(img); else drawHisto(null);
  $('#lb-counter').textContent = `${num(LB.i+1)} / ${num(LB.list.length)}`;
  $('.lb-prev').classList.toggle('hidden', LB.i<=0);
  $('.lb-next').classList.toggle('hidden', LB.i>=LB.list.length-1);
  renderHead(); renderInfo(); renderMeta(); renderEdit(); renderFilmstrip();
}
window.stepLightbox=d=>{ const j=LB.i+d; if(j<0||j>=LB.list.length) return; LB.i=j; loadLightboxItem(); };
window.jumpLightbox=j=>{ LB.i=j; loadLightboxItem(); };
window.closeLightbox=()=>{ hide('lightbox'); $('#lb-media').innerHTML='';
  if(LB_DIRTY){ LB_DIRTY=false; renderSide(); if(STATE.list) reloadList(); else if(STATE.view==='home') loadHome(); } };
document.addEventListener('keydown', e=>{
  if($('#lightbox').classList.contains('hidden') || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
  if(e.key==='Escape') closeLightbox();
  else if(e.key==='ArrowLeft') stepLightbox(1);   // RTL: left = next
  else if(e.key==='ArrowRight') stepLightbox(-1);
  else if(/^[0-5]$/.test(e.key)) setRating(+e.key, true);   // Lightroom: number keys rate
});

function renderFilmstrip(){
  const from=Math.max(0, LB.i-40), to=Math.min(LB.list.length, LB.i+41);
  let h='';
  for(let j=from;j<to;j++) h+=`<img loading="lazy" src="/thumb/${LB.list[j].id}" alt="" class="${j===LB.i?'cur':''}" onclick="jumpLightbox(${j})">`;
  const fs=$('#filmstrip'); fs.innerHTML=h;
  const cur=fs.querySelector('.cur');
  if(cur){ // center the current frame without scrolling any ancestor (scrollIntoView would nudge the window)
    const a=cur.getBoundingClientRect(), b=fs.getBoundingClientRect();
    fs.scrollLeft += (a.left + a.width/2) - (b.left + b.width/2);
  }
}

// RGB histogram of the displayed image, drawn like Lightroom's (additive channels)
function drawHisto(img){
  const cv=$('#histo'), ctx=cv.getContext('2d'); ctx.clearRect(0,0,cv.width,cv.height);
  const p=CUR;
  $('#histo-meta').innerHTML = `<span>${p.width||'?'} × ${p.height||'?'}</span><span>${p.bytes?(p.bytes/1048576).toFixed(1)+' MB':''}</span><span>${esc((p.filename.split('.').pop()||'').toUpperCase())}</span>`;
  if(!img || !img.naturalWidth) return;
  const w=240, h=Math.max(1, Math.round(w*img.naturalHeight/img.naturalWidth));
  const off=document.createElement('canvas'); off.width=w; off.height=h;
  const o=off.getContext('2d'); o.drawImage(img,0,0,w,h);
  let d; try{ d=o.getImageData(0,0,w,h).data; }catch{ return; }
  const H=[new Uint32Array(256),new Uint32Array(256),new Uint32Array(256)];
  for(let k=0;k<d.length;k+=4){ H[0][d[k]]++; H[1][d[k+1]]++; H[2][d[k+2]]++; }
  let max=1; for(const c of H) for(let k=2;k<254;k++) max=Math.max(max,c[k]);
  const colors=['rgba(235,70,70,.75)','rgba(70,210,90,.75)','rgba(70,130,250,.75)'];
  ctx.globalCompositeOperation='lighter';
  H.forEach((c,ci)=>{
    ctx.fillStyle=colors[ci]; ctx.beginPath(); ctx.moveTo(0,cv.height);
    for(let k=0;k<256;k++) ctx.lineTo(k*cv.width/255, cv.height - Math.min(1,c[k]/max)*(cv.height-4));
    ctx.lineTo(cv.width,cv.height); ctx.closePath(); ctx.fill();
  });
  ctx.globalCompositeOperation='source-over';
}

function renderHead(){
  $('#lb-name').textContent=CUR.filename;
  $('#lb-date').textContent=fdate(CUR.taken_at);
  const r=CUR.rating||0;
  $('#lb-stars').innerHTML=[1,2,3,4,5].map(n=>`<button class="${n<=r?'on':''}" aria-label="${n} כוכבים" title="${n} (מקש ${n})" onclick="setRating(${n})">★</button>`).join('');
  const fav=$('#lb-fav');
  fav.classList.toggle('on', !!CUR.favorited);
  fav.querySelector('span').textContent = CUR.favorited?'מועדף':'סמן כמועדף';
  const tr=$('#lb-trash');
  tr.querySelector('span').textContent = CUR.trashed?'שחזר מהאשפה':'העבר לאשפה';
  tr.querySelector('use').setAttribute('href', CUR.trashed?'#i-undo':'#i-trash');
}
window.setRating=async (n, exact)=>{
  const v = (!exact && (CUR.rating||0)===n) ? 0 : n;
  CUR=await jpatch('/api/photo/'+CUR.id,{rating:v}); LB_DIRTY=true;
  const item=LB.list[LB.i]; if(item) item.rating=v;
  renderHead(); renderInfo();
};
function renderInfo(){
  const p=CUR;
  const link=(arr,fn)=>arr.map(a=>`<a class="tag" onclick="closeLightbox();${fn}(${a.id},'${esc(a.name)}')">${esc(a.name)}</a>`).join('')||'<span class="label">—</span>';
  $('#tab-info').innerHTML = `
    <div class="kv"><span>צולם</span>${fdate(p.taken_at)}</div>
    <div class="kv"><span>מידות</span><bdi>${p.width||'?'} × ${p.height||'?'}</bdi></div>
    <div class="kv"><span>גודל</span><bdi>${p.bytes?(p.bytes/1048576).toFixed(1)+' MB':'—'}</bdi></div>
    <div class="kv"><span>מיקום</span>${p.lat!=null?`<a href="https://www.google.com/maps?q=${p.lat},${p.lng}" target="_blank" dir="ltr">${p.lat.toFixed(4)}, ${p.lng.toFixed(4)}</a>`:'—'}</div>
    ${p.trashed?`<div class="kv"><span>באשפה</span>יימחק בעוד ${Math.max(0, TRASH_DAYS - Math.floor((Date.now()/1000 - p.trashed_at)/86400))} ימים</div>`:''}
    ${p.description?`<div class="field" style="margin-top:10px"><span class="label">תיאור</span>${esc(p.description)}</div>`:''}
    <div class="field" style="margin-top:10px"><span class="label">אנשים</span><div class="chips">${link(p.people,'openPerson')}</div></div>
    <div class="field"><span class="label">אלבומים</span><div class="chips">${link(p.albums,'openAlbum')}</div></div>
    <div class="field"><span class="label">תגיות</span><div class="chips">${link(p.tags,'openTag')}</div></div>
    ${p.gphotos_url?`<a class="ext" href="${esc(p.gphotos_url)}" target="_blank">פתח בגוגל פוטוס ${I('external')}</a>`:''}`;
}
function renderMeta(){
  const p=CUR;
  const local = p.taken_at ? new Date(p.taken_at*1000 - new Date().getTimezoneOffset()*60000).toISOString().slice(0,16) : '';
  $('#tab-meta').innerHTML = `
    <div class="field"><label for="m-desc">תיאור</label><textarea id="m-desc" rows="3">${esc(p.description||'')}</textarea></div>
    <div class="field"><label for="m-date">תאריך צילום</label><input id="m-date" type="datetime-local" value="${local}"></div>
    <div class="two">
      <div class="field"><label for="m-lat">קו רוחב</label><input id="m-lat" type="number" step="any" dir="ltr" value="${p.lat??''}"></div>
      <div class="field"><label for="m-lng">קו אורך</label><input id="m-lng" type="number" step="any" dir="ltr" value="${p.lng??''}"></div>
    </div>
    <div class="field"><span class="label">מילות מפתח</span><div id="m-tags" class="tagline">${p.tags.map(t=>`<span class="tag">${esc(t.name)}<b onclick="delTag(${t.id})" title="הסר">✕</b></span>`).join('')}</div>
      <div class="row" style="margin-top:4px"><input id="m-newtag" placeholder="הוסף מילת מפתח" style="flex:1" onkeydown="if(event.key==='Enter')addTag()"><button onclick="addTag()">הוסף</button></div></div>
    <label class="check"><input id="m-exif" type="checkbox"> כתוב גם לקובץ (EXIF, JPG בלבד)</label>
    <div class="row"><button class="primary" style="flex:1" onclick="saveMeta()">שמור מאפיינים</button></div>`;
}
window.addTag=async()=>{const v=$('#m-newtag').value.trim(); if(!v)return; CUR=await jpatch('/api/photo/'+CUR.id,{add_tags:[v]}); renderMeta(); renderInfo(); $('#m-newtag').focus();};
window.delTag=async id=>{CUR=await jpatch('/api/photo/'+CUR.id,{remove_tag_ids:[id]}); renderMeta(); renderInfo();};
window.saveMeta=async()=>{
  const d=$('#m-date').value; const body={
    description:$('#m-desc').value,
    taken_at: d? Math.floor(new Date(d).getTime()/1000): null,
    lat: $('#m-lat').value!==''?parseFloat($('#m-lat').value):null,
    lng: $('#m-lng').value!==''?parseFloat($('#m-lng').value):null,
    write_exif: $('#m-exif').checked };
  CUR=await jpatch('/api/photo/'+CUR.id, body); LB_DIRTY=true; renderHead(); renderInfo(); toast('נשמר');
};
window.toggleFav=async()=>{ CUR=await jpatch('/api/photo/'+CUR.id,{favorited:CUR.favorited?0:1}); LB_DIRTY=true;
  const item=LB.list[LB.i]; if(item) item.favorited=CUR.favorited; renderHead(); };
window.trashPhoto=async()=>{ const was=CUR.trashed; CUR=await jpatch('/api/photo/'+CUR.id,{trashed:was?0:1}); LB_DIRTY=true;
  toast(was?'שוחזר מהאשפה':'הועבר לאשפה'); renderHead(); renderInfo(); };

// Develop sliders use Lightroom's -100..+100 scale; converted to factors for the backend.
const fac = (v, lo) => v>=0 ? 1+v/100 : 1+(v/100)*(1-lo);
function renderEdit(){
  if(CUR.is_video){ $('#tab-edit').innerHTML=`<div class="hint">עריכה זמינה לתמונות בלבד</div>`; return; }
  const s=(id,label,min,max,unit='')=>`<div class="slider"><label for="${id}">${label}</label>
    <input id="${id}" type="range" min="${min}" max="${max}" step="1" value="0" oninput="previewEdit()" ondblclick="this.value=0;previewEdit()" title="לחיצה כפולה לאיפוס"><output id="${id}-v">0${unit}</output></div>`;
  $('#tab-edit').innerHTML = `
    <div class="slider-group">
      ${s('e-bri','בהירות',-100,100)}
      ${s('e-con','ניגודיות',-100,100)}
      ${s('e-sat','רוויה',-100,100)}
      <label class="check"><input id="e-gray" type="checkbox" onchange="previewEdit()"> שחור-לבן</label>
    </div>
    <div class="slider-group">
      ${s('e-rot','יישור',-180,180,'°')}
      <div class="row" style="margin-top:2px"><button onclick="rot(-90)">${I('rotate')}<span dir="ltr">-90°</span></button><button onclick="rot(90)"><span dir="ltr">+90°</span></button></div>
      <div class="field" style="margin-top:10px"><label for="e-crop">חיתוך (0–1: x1,y1,x2,y2)</label>
        <input id="e-crop" dir="ltr" placeholder="0.1,0.1,0.9,0.9"></div>
    </div>
    <div class="row"><button class="primary" style="flex:1" onclick="applyEdit()">החל</button>
      <button onclick="renderEdit();previewEdit(true)">אפס</button>
      ${CUR.edited?`<button onclick="revertEdit()">${I('undo')}מקור</button>`:''}</div>
    <div class="hint">לחיצה כפולה על סליידר מאפסת אותו. המקור נשמר תמיד.</div>`;
}
window.rot=d=>{ const r=$('#e-rot'); let v=(+r.value)+d; if(v>180)v-=360; if(v<-180)v+=360; r.value=v; previewEdit(); };
window.previewEdit=(reset)=>{
  const img=$('#lb-media img'); if(!img) return;
  if(reset || !$('#e-rot')){ img.style.filter=''; img.style.transform=''; return; }
  const v=id=>+$('#'+id).value;
  ['e-rot','e-bri','e-con','e-sat'].forEach(id=>{ const x=v(id); $('#'+id+'-v').textContent=(x>0?'+':'')+x+(id==='e-rot'?'°':''); });
  img.style.filter=`brightness(${fac(v('e-bri'),.3)}) contrast(${fac(v('e-con'),.3)}) saturate(${fac(v('e-sat'),0)})${$('#e-gray').checked?' grayscale(1)':''}`;
  img.style.transform=`rotate(${-v('e-rot')}deg)`;
};
window.applyEdit=async()=>{
  const crop=$('#e-crop').value.trim(), v=id=>+$('#'+id).value;
  const body={ rotate:v('e-rot')||null,
    brightness:fac(v('e-bri'),.3), contrast:fac(v('e-con'),.3), saturation:fac(v('e-sat'),0),
    grayscale:$('#e-gray').checked||null, crop: crop? crop.split(',').map(Number): null };
  await jpost('/api/photo/'+CUR.id+'/edit', body);
  const img=$('#lb-media img'); if(img){ img.style.filter=''; img.style.transform=''; img.src='/media/'+CUR.id+'?t='+Date.now(); }
  toast('נשמר'); CUR=await api('/api/photo/'+CUR.id); renderEdit();
};
window.revertEdit=async()=>{ await jpost('/api/photo/'+CUR.id+'/revert'); const img=$('#lb-media img');
  if(img) img.src='/media/'+CUR.id+'?t='+Date.now(); CUR=await api('/api/photo/'+CUR.id); renderEdit(); toast('שוחזר המקור'); };

// ---------- import + jobs ----------
window.pickZip=async()=>{ const r=await api('/api/pick-file?kind=zip'); if(r.path) $('#zip-path').value=r.path; };
window.doImport=async()=>{
  const zip=$('#zip-path').value.trim(); if(!zip)return;
  const r=await jpost('/api/import',{zip_path:zip});
  if(r.detail){toast('שגיאה: '+esc(r.detail));return;}
  hide('import-modal'); pollJob('import','ייבוא');
};
window.runJob=async(url, name, label, body)=>{
  const r=await jpost(url, body); if(r&&r.detail){ toast('שגיאה: '+esc(r.detail)); return; }
  pollJob(name, label);
};
async function pollJob(name, label){
  const p=await api('/api/job/'+name);
  const pct = p.total ? Math.round(100*p.done/p.total) : null;
  const j=$('#job'); j.classList.remove('hidden');
  $('#job-label').textContent = `${label}${pct!=null?` · ${pct}%`:''}`;
  j.title = `${label}: ${p.msg||p.state} ${p.total?`(${p.done}/${p.total})`:''}`;
  j.onclick = ()=>toast(`<b>${label}</b><br>${esc(p.msg||p.state)} ${p.total?`(${num(p.done)}/${num(p.total)})`:''}${pct!=null?`<div class="progress"><i style="width:${pct}%"></i></div>`:''}`);
  if(['done','error'].includes(p.state)){
    j.classList.add('hidden'); renderSide();
    toast(`${label}: ${esc(p.error||p.msg||'הושלם')}`);
    if(name==='import') route('home');
    else if(name==='faces' && ['people','home'].includes(STATE.view)) route(STATE.view);
    else if(STATE.view==='settings') loadSettings();
    return;
  }
  setTimeout(()=>pollJob(name,label), 800);
}

// ---------- settings ----------
async function loadSettings(tab){
  noList(); STATE.setTab = tab ?? STATE.setTab ?? 'library';
  const [s, vm] = await Promise.all([api('/api/status'), api('/api/vision-models')]);
  STATUS=s; TRASH_DAYS=s.trash_days;
  const t=STATE.setTab, c=s.counts;
  const tabs=[['library','ספרייה'],['ai','זיהוי ותיוג'],['about','אודות']];
  let body='';
  if(t==='library') body=`
    <div class="panel"><h2>איפה נשמרות התמונות</h2><p>כל הקבצים נשמרים מקומית על המחשב שלך.</p>
      <div class="pathrow"><span>ספריית התמונות</span><code>${esc(s.library_root)}</code></div>
      <div class="pathrow"><span>קבצי מדיה</span><code>${esc(s.media_path)}</code></div>
      <div class="pathrow"><span>מסד הנתונים</span><code>${esc(s.db_path)}</code></div>
      <div class="row"><input id="lib-path" dir="ltr" placeholder="נתיב ספרייה חדש" style="flex:1" value="${esc(s.library_root)}">
        <button onclick="pickLib()">${I('folder-open')}בחר תיקייה…</button>
        <button class="primary" onclick="setLib()">שנה מיקום</button></div></div>
    <div class="panel"><h2>מה יש בספרייה</h2>
      <div class="counts">
        <div><b>${num(c.photos)}</b>תמונות</div><div><b>${num(c.videos)}</b>סרטונים</div>
        <div><b>${num(c.albums)}</b>אלבומים</div><div><b>${num(c.people)}</b>אנשים</div>
        <div><b>${num(c.faces)}</b>פרצופים</div><div><b>${num(c.tags)}</b>תגיות</div>
      </div></div>`;
  if(t==='ai'){
    const ol = !s.ollama ? ['','לא ניתן להפעיל את Ollama אוטומטית — ודאו שהוא מותקן כדי לקבל תגיות.']
      : !s.ollama_vision_model ? ['warn','Ollama רץ, אבל אין מודל ראייה מותקן. הריצו: <code>ollama pull llava</code>']
      : !s.ollama_vision_model_fits ? ['warn',`המודל <b>${esc(s.ollama_vision_model)}</b> גדול מהזיכרון הפנוי כרגע — התקינו מודל קטן יותר או סגרו תוכנות אחרות.`]
      : ['ok',`מחובר, ישתמש במודל <b>${esc(s.ollama_vision_model)}</b>`];
    body=`
    <div class="panel"><h2>עיבוד חכם</h2><p>הכול רץ מקומית על המחשב, בלי לשלוח תמונות לאף שירות.</p>
      <div class="action"><div class="ico">${I('face')}</div>
        <div><b>זיהוי פנים</b><span>מזהה פרצופים ומקבץ אותם לאנשים (InsightFace buffalo_l). ${num(c.faces)} פרצופים זוהו עד כה.</span></div>
        <button class="primary" onclick="runJob('/api/faces','faces','זיהוי פנים')">הפעל</button></div>
      <div class="action"><div class="ico">${I('spark')}</div>
        <div><b>תיוג חכם</b><span class="status ${ol[0]}"><i></i>Ollama: ${ol[1]}</span></div>
        <button ${s.ollama_vision_model?'':'disabled'} onclick="runJob('/api/tags','tags','תיוג חכם')">הפעל</button></div>
      ${s.ollama_vision_model && !s.ollama_vision_model_fits ? `<div class="action"><div class="ico">${I('download')}</div>
        <div><b>מודל קטן יותר</b><span>${esc(s.ollama_recommended_small_model)} — מתאים לזיכרון הפנוי.</span></div>
        <button onclick="runJob('/api/pull-model','pull_model','התקנת מודל',{model:'${esc(s.ollama_recommended_small_model)}'})">התקן</button></div>`:''}
    </div>
    ${vm.models.length?`<div class="panel"><h2>מודל תיוג</h2><p>ברירת המחדל בוחרת את המודל הגדול ביותר שנכנס בזיכרון.</p>
      <div class="row"><select id="vision-model" style="flex:1">
        <option value="">אוטומטי</option>
        ${vm.models.map(m=>`<option value="${esc(m.name)}" ${vm.override===m.name?'selected':''}>${esc(m.name)} · ${(m.size/1024**3).toFixed(1)}GB</option>`).join('')}
      </select><button class="primary" onclick="setVisionModel()">שמור</button></div></div>`:''}`;
  }
  if(t==='about') body=`
    <div class="panel"><h2>PhotoManager</h2><p>ניהול תמונות מקומי: ייבוא מ‑Google Takeout, זיהוי פנים, תיוג חכם ועריכה.</p>
      <div class="pathrow"><span>אשפה</span>פריטים נמחקים לצמיתות אחרי ${TRASH_DAYS} יום</div>
      <div class="pathrow"><span>קיצורי מקשים</span>בתצוגת תמונה: ← / → מעבר, Esc סגירה</div></div>`;
  content().innerHTML = `<div class="settings"><div class="page-head"><h1>הגדרות</h1></div>
    <div class="seg">${tabs.map(([v,l])=>`<button class="${v===t?'active':''}" onclick="loadSettings('${v}')">${l}</button>`).join('')}</div>
    ${body}</div>`;
}
window.pickLib=async()=>{ const r=await api('/api/pick-file?kind=folder'); if(r.path) $('#lib-path').value=r.path; };
window.setLib=async()=>{ const p=$('#lib-path').value.trim(); if(!p)return; await jpost('/api/settings/library',{path:p}); toast('המיקום עודכן'); loadSettings(); renderSide(); };
window.setVisionModel=async()=>{ const v=$('#vision-model').value; await jpost('/api/settings/vision-model',{model:v||null}); toast('המודל עודכן'); loadSettings(); };

// ---------- boot ----------
renderSide();
route(['home','all','albums','people','tags','favorites','memories','trash','settings'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'home');
// resume the progress pill if a job is already running (e.g. after a reload)
[['import','ייבוא'],['faces','זיהוי פנים'],['tags','תיוג חכם'],['pull_model','התקנת מודל']].forEach(async ([n,l])=>{
  try{ const p=await api('/api/job/'+n); if(p && p.state && !['done','error','idle'].includes(p.state)) pollJob(n,l); }catch{}
});
