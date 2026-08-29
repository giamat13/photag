const $ = s => document.querySelector(s);
const api = async (u, opt) => (await fetch(u, opt)).json();
const jpost = (u, body) => api(u, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body||{})});
const jpatch = (u, body) => api(u, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body||{})});
const esc = s => (s??'').toString().replace(/[<>&"]/g, c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
const fdate = t => t ? new Date(t*1000).toLocaleString('he-IL') : '—';
const show = id => $('#'+id).classList.remove('hidden');
const hide = id => $('#'+id).classList.add('hidden');
let STATE = {view:'all', filter:{}, photos:[], sort:'date_desc'};
let TRASH_DAYS = 60;

function toast(msg, keep){ const t=$('#toast'); t.innerHTML=msg; t.classList.remove('hidden');
  clearTimeout(t._h); if(!keep) t._h=setTimeout(()=>t.classList.add('hidden'), 3500); }

// ---------- navigation ----------
document.querySelectorAll('#nav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('#nav button').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); STATE.filter={}; route(b.dataset.view);
});
$('#search').oninput = debounce(()=>{ if($('#search').value){STATE.view='all'; STATE.filter={q:$('#search').value}; loadPhotos('חיפוש');} }, 300);
$('#btn-import').onclick = ()=>show('import-modal');
$('#sort').value = STATE.sort;
$('#sort').onchange = ()=>{ STATE.sort=$('#sort').value; if(STATE.photos.length) renderPhotoGrid(); };

function debounce(fn,ms){let h;return(...a)=>{clearTimeout(h);h=setTimeout(()=>fn(...a),ms);};}

async function route(view){
  STATE.view=view; $('#crumb').textContent='';
  if(view==='all') return loadPhotos('כל התמונות');
  if(view==='favorites'){STATE.filter={favorite:1}; return loadPhotos('מועדפים');}
  if(view==='trash'){STATE.filter={trashed:1}; return loadPhotos(`אשפה — תמונות נמחקות לצמיתות אחרי ${TRASH_DAYS} יום`);}
  if(view==='albums') return loadAlbums();
  if(view==='people') return loadPeople();
  if(view==='tags') return loadTags();
  if(view==='memories') return loadMemories();
  if(view==='settings') return loadSettings();
}

// ---------- photo grid ----------
async function loadPhotos(title){
  $('#crumb').textContent = title||'';
  const q = new URLSearchParams(STATE.filter).toString();
  STATE.photos = await api('/api/photos?'+q);
  renderPhotoGrid();
}
function sortPhotos(photos, sort){
  const arr=photos.slice();
  if(sort==='date_asc') arr.sort((a,b)=>(a.taken_at||0)-(b.taken_at||0));
  else if(sort==='name_asc') arr.sort((a,b)=>a.filename.localeCompare(b.filename));
  else if(sort==='name_desc') arr.sort((a,b)=>b.filename.localeCompare(a.filename));
  else if(sort==='favorite') arr.sort((a,b)=>(b.favorited-a.favorited)||((b.taken_at||0)-(a.taken_at||0)));
  else arr.sort((a,b)=>(b.taken_at||0)-(a.taken_at||0)); // date_desc
  return arr;
}
function dayLabel(t){
  return t ? new Date(t*1000).toLocaleDateString('he-IL', {weekday:'long', day:'numeric', month:'long', year:'numeric'}) : 'ללא תאריך';
}
function renderPhotoGrid(){
  const c=$('#content');
  if(!STATE.photos.length){ c.innerHTML=`<div class="empty">אין תמונות כאן.<br>לחצו על «ייבוא מגוגל פוטוס» כדי להתחיל.</div>`; return; }
  const sorted = sortPhotos(STATE.photos, STATE.sort);
  if(STATE.sort==='date_asc' || STATE.sort==='date_desc'){
    let html='', curDay=null, buf=[];
    const flush=()=>{ if(buf.length) html+=`<div class="day-header">${curDay}</div><div class="grid">${buf.join('')}</div>`; buf=[]; };
    for(const p of sorted){
      const day = dayLabel(p.taken_at ? p.taken_at - (p.taken_at % 86400) : null);
      if(day!==curDay){ flush(); curDay=day; }
      buf.push(cell(p));
    }
    flush();
    c.innerHTML = html;
  } else {
    c.innerHTML = `<div class="grid">${sorted.map(cell).join('')}</div>`;
  }
  c.querySelectorAll('.cell').forEach(el=>el.onclick=()=>openLightbox(+el.dataset.id));
}
function cell(p){
  const badge = p.is_video ? `<span class="badge">▶</span>` : '';
  const fav = p.favorited ? `<span class="fav">⭐</span>` : '';
  return `<div class="cell" data-id="${p.id}">
    <img loading="lazy" src="/thumb/${p.id}" onerror="this.parentElement.classList.add('noimg');this.remove()">
    ${badge}${fav}</div>`;
}

// ---------- albums / people / tags ----------
async function loadAlbums(){
  $('#crumb').textContent='אלבומים';
  const al = await api('/api/albums');
  $('#content').innerHTML = `<div class="cards">${al.map(a=>`
    <div class="card" onclick="openAlbum(${a.id},'${esc(a.name)}')">
      <div class="thumb">${a.cover?`<img src="/thumb/${a.cover}">`:'📁'}</div>
      <div class="meta"><b>${esc(a.name)}</b><span>${a.n} פריטים · ${a.kind==='year'?'לפי שנה':a.kind==='people-share'?'משותף':'אלבום'}</span></div>
    </div>`).join('')}</div>`;
}
window.openAlbum=(id,name)=>{STATE.view='all'; STATE.filter={album:id}; loadPhotos('אלבום: '+name);};

async function loadPeople(){
  $('#crumb').textContent='אנשים';
  const pl = await api('/api/people');
  if(!pl.length){ $('#content').innerHTML=`<div class="empty">אין עדיין אנשים.<br>הריצו «זיהוי פנים» מההגדרות.</div>`; return; }
  $('#content').innerHTML = `<div class="cards">${pl.map(p=>`
    <div class="card person">
      <div class="thumb" onclick="openPerson(${p.id},'${esc(p.name)}')">${p.cover_face?`<img class="round" src="/thumb/${faceOwner(p)}">`:'🧑'}</div>
      <div class="meta"><b onclick="renamePerson(${p.id})" title="לחצו לשינוי שם">${esc(p.name)} ✏️</b>
      <span>${(p.face_photos||0)+ (p.tag_photos||0)} תמונות</span></div>
    </div>`).join('')}</div>`;
}
function faceOwner(p){return p.cover_face;} // thumb endpoint is per-photo; cover shown from person photos below
window.openPerson=(id,name)=>{STATE.view='all'; STATE.filter={person:id}; loadPhotos('אדם: '+name);};
window.renamePerson=async id=>{const n=prompt('שם חדש לאדם:'); if(!n)return; await jpost(`/api/person/${id}/rename`,{name:n}); loadPeople();};

async function loadTags(){
  $('#crumb').textContent='תגיות';
  const tg = await api('/api/tags');
  if(!tg.length){ $('#content').innerHTML=`<div class="empty">אין תגיות עדיין.<br>הריצו «תיוג חכם (Ollama)» מההגדרות.</div>`; return; }
  $('#content').innerHTML = `<div class="chips">${tg.map(t=>`<div class="chip" onclick="openTag(${t.id},'${esc(t.name)}')">${esc(t.name)} <b>${t.n}</b></div>`).join('')}</div>`;
}
window.openTag=(id,name)=>{STATE.view='all'; STATE.filter={tag:id}; loadPhotos('תגית: '+name);};

async function loadMemories(){
  $('#crumb').textContent='זיכרונות';
  const m = await api('/api/memories');
  $('#content').innerHTML = `
    <h2 class="title">שמות זיכרונות מגוגל</h2>
    <div class="chips">${(m.titles||[]).map(t=>`<div class="chip">${esc(t)}</div>`).join('')||'<div class="empty">—</div>'}</div>
    <h2 class="title" style="margin-top:24px">תגובות באלבומים משותפים</h2>
    ${(m.comments||[]).map(c=>`<div class="kv"><span>${fdate(c.created_at)} ${c.liked?'❤️':''}</span><div>${esc(c.text)||'(לייק)'}</div></div>`).join('')||'<div class="empty">—</div>'}`;
}

// ---------- lightbox ----------
let CUR=null;
async function openLightbox(id){
  CUR = await api('/api/photo/'+id);
  const stage=$('#lb-media');
  stage.innerHTML = CUR.is_video ? `<video src="/media/${id}" controls autoplay></video>`
                                 : `<img src="/media/${id}">`;
  renderInfo(); renderMeta(); renderEdit();
  switchTab('info'); show('lightbox');
}
window.closeLightbox=()=>{ hide('lightbox'); $('#lb-media').innerHTML=''; if(STATE.view==='all') loadPhotos($('#crumb').textContent); };
document.querySelectorAll('.lb-tabs button').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
function switchTab(t){
  document.querySelectorAll('.lb-tabs button').forEach(b=>b.classList.toggle('active', b.dataset.tab===t));
  ['info','meta','edit'].forEach(x=>$('#tab-'+x).classList.toggle('hidden', x!==t));
}
function renderInfo(){
  const p=CUR;
  $('#tab-info').innerHTML = `
    <div class="kv"><span>קובץ</span><b>${esc(p.filename)}</b></div>
    <div class="kv"><span>צולם</span>${fdate(p.taken_at)}</div>
    <div class="kv"><span>מידות</span>${p.width||'?'}×${p.height||'?'}</div>
    <div class="kv"><span>גודל</span>${p.bytes?(p.bytes/1048576).toFixed(1)+' MB':'—'}</div>
    <div class="kv"><span>מיקום</span>${p.lat?`${p.lat.toFixed(4)}, ${p.lng.toFixed(4)}`:'—'}</div>
    ${p.trashed?`<div class="kv"><span>באשפה</span>יימחק לצמיתות בעוד ${Math.max(0, TRASH_DAYS - Math.floor((Date.now()/1000 - p.trashed_at)/86400))} ימים</div>`:''}
    <div class="kv"><span>מועדף</span>${p.favorited?'⭐':'—'}</div>
    <div class="kv"><span>דירוג</span>${'★'.repeat(p.rating||0)||'—'}</div>
    ${p.description?`<div class="field" style="margin-top:10px"><label>תיאור</label>${esc(p.description)}</div>`:''}
    <div class="field" style="margin-top:12px"><label>אלבומים</label><div class="chips">${p.albums.map(a=>`<span class="tag">${esc(a.name)}</span>`).join('')||'—'}</div></div>
    <div class="field"><label>אנשים</label><div class="chips">${p.people.map(a=>`<span class="tag">${esc(a.name)}</span>`).join('')||'—'}</div></div>
    <div class="field"><label>תגיות</label><div class="chips">${p.tags.map(a=>`<span class="tag">${esc(a.name)}</span>`).join('')||'—'}</div></div>
    ${p.gphotos_url?`<a href="${p.gphotos_url}" target="_blank">פתח בגוגל פוטוס ↗</a>`:''}`;
}
function renderMeta(){
  const p=CUR;
  $('#tab-meta').innerHTML = `
    <div class="field"><label>תיאור</label><textarea id="m-desc" rows="2">${esc(p.description||'')}</textarea></div>
    <div class="field"><label>תאריך צילום</label><input id="m-date" type="datetime-local" value="${p.taken_at?new Date(p.taken_at*1000).toISOString().slice(0,16):''}"></div>
    <div class="row">
      <div class="field" style="flex:1"><label>קו רוחב</label><input id="m-lat" type="number" step="any" value="${p.lat??''}"></div>
      <div class="field" style="flex:1"><label>קו אורך</label><input id="m-lng" type="number" step="any" value="${p.lng??''}"></div>
    </div>
    <div class="row">
      <label><input id="m-fav" type="checkbox" ${p.favorited?'checked':''}> מועדף</label>
      <label>דירוג <select id="m-rating">${[0,1,2,3,4,5].map(n=>`<option ${n==(p.rating||0)?'selected':''}>${n}</option>`).join('')}</select></label>
    </div>
    <div class="field"><label>תגיות</label><div id="m-tags" class="tagline">${p.tags.map(t=>`<span class="tag">${esc(t.name)}<b onclick="delTag(${t.id})">✕</b></span>`).join('')}</div>
      <div class="row"><input id="m-newtag" placeholder="הוסף תגית"><button onclick="addTag()">+</button></div></div>
    <label><input id="m-exif" type="checkbox"> כתוב גם לקובץ (EXIF, JPG בלבד)</label>
    <div class="row"><button class="primary" onclick="saveMeta()">שמור</button>
      <button class="danger" onclick="trashPhoto()">${p.trashed?'שחזר':'העבר לאשפה'}</button></div>`;
}
window.addTag=async()=>{const v=$('#m-newtag').value.trim(); if(!v)return; CUR=await jpatch('/api/photo/'+CUR.id,{add_tags:[v]}); renderMeta(); renderInfo();};
window.delTag=async id=>{CUR=await jpatch('/api/photo/'+CUR.id,{remove_tag_ids:[id]}); renderMeta(); renderInfo();};
window.saveMeta=async()=>{
  const d=$('#m-date').value; const body={
    description:$('#m-desc').value,
    taken_at: d? Math.floor(new Date(d).getTime()/1000): null,
    lat: $('#m-lat').value!==''?parseFloat($('#m-lat').value):null,
    lng: $('#m-lng').value!==''?parseFloat($('#m-lng').value):null,
    favorited: $('#m-fav').checked?1:0, rating: parseInt($('#m-rating').value),
    write_exif: $('#m-exif').checked };
  CUR=await jpatch('/api/photo/'+CUR.id, body); renderInfo(); toast('נשמר ✓');
};
window.trashPhoto=async()=>{ CUR=await jpatch('/api/photo/'+CUR.id,{trashed:CUR.trashed?0:1}); toast('בוצע ✓'); renderMeta(); };

function renderEdit(){
  if(CUR.is_video){ $('#tab-edit').innerHTML='<div class="empty">עריכה זמינה לתמונות בלבד</div>'; return; }
  $('#tab-edit').innerHTML = `
    <div class="slider"><span>סיבוב</span><input id="e-rot" type="range" min="-180" max="180" value="0"><span id="e-rotv">0°</span></div>
    <div class="slider"><span>בהירות</span><input id="e-bri" type="range" min="0.3" max="2" step="0.05" value="1"></div>
    <div class="slider"><span>ניגודיות</span><input id="e-con" type="range" min="0.3" max="2" step="0.05" value="1"></div>
    <div class="slider"><span>רוויה</span><input id="e-sat" type="range" min="0" max="2" step="0.05" value="1"></div>
    <label><input id="e-gray" type="checkbox"> שחור-לבן</label>
    <div class="hint">חיתוך: גררו לימין את הערכים (0–1). ריק = ללא חיתוך.</div>
    <div class="row"><input id="e-crop" placeholder="x1,y1,x2,y2 (למשל 0.1,0.1,0.9,0.9)" style="flex:1"></div>
    <div class="row"><button class="primary" onclick="applyEdit()">החל ושמור</button>
      ${CUR.edited?'<button onclick="revertEdit()">שחזר מקורי</button>':''}</div>
    <div class="hint">המקור נשמר תמיד — אפשר לחזור אליו.</div>`;
  $('#e-rot').oninput=e=>$('#e-rotv').textContent=e.target.value+'°';
}
window.applyEdit=async()=>{
  const crop=$('#e-crop').value.trim();
  const body={ rotate:parseFloat($('#e-rot').value)||null,
    brightness:parseFloat($('#e-bri').value), contrast:parseFloat($('#e-con').value),
    saturation:parseFloat($('#e-sat').value), grayscale:$('#e-gray').checked||null,
    crop: crop? crop.split(',').map(Number): null };
  await jpost('/api/photo/'+CUR.id+'/edit', body);
  const img=$('#lb-media img'); if(img) img.src='/media/'+CUR.id+'?t='+Date.now();
  toast('נשמר ✓'); CUR=await api('/api/photo/'+CUR.id); renderEdit();
};
window.revertEdit=async()=>{ await jpost('/api/photo/'+CUR.id+'/revert'); const img=$('#lb-media img'); if(img) img.src='/media/'+CUR.id+'?t='+Date.now(); CUR=await api('/api/photo/'+CUR.id); renderEdit(); toast('שוחזר ✓'); };

// ---------- import + jobs ----------
window.pickZip=async()=>{
  const r=await api('/api/pick-file?kind=zip');
  if(r.path) $('#zip-path').value=r.path;
};
window.doImport=async()=>{
  const zip=$('#zip-path').value.trim(); if(!zip)return;
  const r=await jpost('/api/import',{zip_path:zip});
  if(r.detail){toast('שגיאה: '+r.detail);return;}
  hide('import-modal'); pollJob('import','ייבוא');
};
async function pollJob(name, label){
  const p=await api('/api/job/'+name);
  const bar = p.total? `<div class="progress"><i style="width:${100*p.done/p.total}%"></i></div>`:'';
  toast(`${label}: ${p.msg||p.state} ${p.total?`(${p.done}/${p.total})`:''}${bar}`, true);
  if(['done','error'].includes(p.state)){
    toast(`${label}: ${p.error||p.msg||'הושלם'} ✓`); refreshStats();
    if(name==='import') route('all');
    if(name==='faces') { if(STATE.view==='people') loadPeople(); }
    if(name==='pull_model' && STATE.view==='settings') loadSettings();
    return;
  }
  setTimeout(()=>pollJob(name,label), 800);
}

// ---------- settings ----------
async function loadSettings(){
  $('#crumb').textContent='הגדרות';
  const [s, vm] = await Promise.all([api('/api/status'), api('/api/vision-models')]);
  $('#content').innerHTML = `
    <div class="settings-card">
      <h2 class="title">📂 איפה נשמרות התמונות</h2>
      <div class="pathrow">ספריית התמונות: <code>${esc(s.library_root)}</code></div>
      <div class="pathrow">קבצי מדיה: <code>${esc(s.media_path)}</code></div>
      <div class="pathrow">מסד הנתונים: <code>${esc(s.db_path)}</code></div>
      <div class="row"><input id="lib-path" placeholder="נתיב ספרייה חדש" style="flex:1" value="${esc(s.library_root)}">
        <button onclick="pickLib()">📂 בחר תיקייה…</button>
        <button onclick="setLib()">שנה מיקום</button></div>
    </div>
    <div class="settings-card" style="margin-top:16px">
      <h2 class="title">🧠 עיבוד</h2>
      <div class="pathrow">תמונות: <b>${s.counts.photos}</b> · סרטונים: <b>${s.counts.videos}</b> · אלבומים: <b>${s.counts.albums}</b>
        · אנשים: <b>${s.counts.people}</b> · פרצופים: <b>${s.counts.faces}</b> · תגיות: <b>${s.counts.tags}</b></div>
      ${vm.models.length ? `<div class="row">
        <label>מודל תיוג:
          <select id="vision-model">
            <option value="">אוטומטי (הכי גדול שנכנס בזיכרון)</option>
            ${vm.models.map(m=>`<option value="${esc(m.name)}" ${vm.override===m.name?'selected':''}>${esc(m.name)} (${(m.size/1024**3).toFixed(1)}GB)</option>`).join('')}
          </select>
        </label>
        <button onclick="setVisionModel()">שמור</button>
      </div>` : ''}
      <div class="row">
        <button class="primary" onclick="jpost('/api/faces').then(()=>pollJob('faces','זיהוי פנים'))">🧑 זהה פרצופים (buffalo_l)</button>
        <button ${s.ollama_vision_model?'':'disabled title="אין מודל ראייה מותקן ב-Ollama"'} onclick="jpost('/api/tags').then(()=>pollJob('tags','תיוג חכם'))">🏷️ תיוג חכם (Ollama${s.ollama_vision_model?': '+s.ollama_vision_model:''})</button>
        ${s.ollama_vision_model && !s.ollama_vision_model_fits ?
          `<button onclick="jpost('/api/pull-model',{model:'${esc(s.ollama_recommended_small_model)}'}).then(()=>pollJob('pull_model','התקנת מודל קטן'))">⬇️ התקן מודל קטן יותר (${esc(s.ollama_recommended_small_model)})</button>` : ''}
      </div>
      <div class="hint">${!s.ollama ? 'לא ניתן להפעיל את Ollama אוטומטית (ודאו שהוא מותקן) — כדי לקבל תגיות אוטומטיות'
        : !s.ollama_vision_model ? 'Ollama רץ אבל אין מודל ראייה מותקן — הריצו: <code>ollama pull llava</code>'
        : !s.ollama_vision_model_fits ? `Ollama מחובר, אבל <b>${esc(s.ollama_vision_model)}</b> גדול על הזיכרון הפנוי כרגע — התקינו מודל קטן יותר או סגרו תוכנות אחרות`
        : `Ollama מחובר, ישתמש במודל <b>${esc(s.ollama_vision_model)}</b> לתיוג`}</div>
    </div>`;
}
window.pickLib=async()=>{
  const r=await api('/api/pick-file?kind=folder');
  if(r.path) $('#lib-path').value=r.path;
};
window.setLib=async()=>{ const p=$('#lib-path').value.trim(); if(!p)return; await jpost('/api/settings/library',{path:p}); toast('המיקום עודכן ✓'); loadSettings(); refreshStats(); };
window.setVisionModel=async()=>{ const v=$('#vision-model').value; await jpost('/api/settings/vision-model',{model:v||null}); toast('המודל עודכן ✓'); loadSettings(); };

async function refreshStats(){
  const s=await api('/api/status');
  TRASH_DAYS = s.trash_days;
  $('#stats').innerHTML = `${s.counts.photos} תמונות · ${s.counts.albums} אלבומים<br>${s.counts.people} אנשים · ${s.counts.faces} פרצופים<br>Ollama: ${s.ollama?'מחובר':'—'}`;
}

// ---------- boot ----------
refreshStats(); route('all');
