"""The inline card (MCP Apps) for photag: one self-contained HTML page the host shows in a sandboxed iframe next to a tool result.
Same protocol as the HP-librery connector (postMessage JSON-RPC, spec 2026-01-26). Thumbnails arrive inside the data as data: URIs,
so the page loads nothing from the network."""
VIEW_URI = "ui://photag/card.html"
VIEW_MIME = "text/html;profile=mcp-app"

VIEW_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#f6f7f9;--fg:#1c1e24;--mut:#6a7080;--card:#fff;--line:#e2e5ea;--acc:#2f6fed}
:root[data-theme=dark]{--bg:#15171c;--fg:#eceef3;--mut:#9aa1b2;--card:#1d2027;--line:#2c313b;--acc:#7aa2ff}
*{box-sizing:border-box}
body{margin:0;padding:10px;background:transparent;color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.busy{opacity:.55;pointer-events:none}
.hd{padding:12px 16px;border-bottom:1px solid var(--line)}.hd h1{margin:0;font-size:16px}.hd .sub{color:var(--mut);font-size:12.5px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:8px;padding:12px}
.tile{position:relative;border-radius:10px;overflow:hidden;background:var(--bg);border:1px solid var(--line);cursor:pointer;aspect-ratio:1/1}
.tile:hover{border-color:var(--acc)}
.tile img{width:100%;height:100%;object-fit:cover;display:block}
.tile .ph{display:grid;place-items:center;height:100%;font-size:28px;color:var(--mut)}
.tile .cap{position:absolute;left:0;right:0;bottom:0;padding:14px 6px 4px;font-size:11px;color:#fff;background:linear-gradient(transparent,#000a);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .fl{position:absolute;top:4px;right:6px;font-size:13px}
.big{padding:12px;text-align:center}.big img{max-width:100%;max-height:360px;border-radius:10px}
dl.rows{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:0;padding:12px 16px}
dl.rows dt{color:var(--mut);font-size:12.5px}dl.rows dd{margin:0;word-break:break-word}
.bar{display:flex;gap:8px;align-items:center;padding:10px 16px;flex-wrap:wrap}
.btn{font:inherit;font-size:13px;padding:5px 12px;border-radius:99px;border:1px solid var(--line);background:var(--bg);color:var(--fg);cursor:pointer}
.btn:hover{border-color:var(--acc)}
.row{display:flex;align-items:center;gap:10px;padding:8px 16px;border-top:1px solid var(--line)}
.row .lb{width:78px;flex:none;color:var(--mut);font-size:12.5px}
.row .tr{flex:1;background:var(--bg);border-radius:6px;height:14px;overflow:hidden}.row .tr i{display:block;height:100%;background:var(--acc)}
.row .v{width:110px;flex:none;text-align:right;font-size:12.5px}
.note{padding:6px 16px 12px;color:var(--mut);font-size:12.5px}
.sec{padding:10px 16px 0;font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--mut)}
.tw{overflow:auto;padding:6px 12px 10px}table{border-collapse:collapse;font-size:12.5px;width:100%}
th,td{text-align:start;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top;max-width:260px;word-break:break-word}th{color:var(--mut);font-weight:600}
.empty{padding:24px;text-align:center;color:var(--mut)}
</style></head><body>
<div id="root"><div class="empty">Waiting for photag...</div></div>
<script>
(function(){
var root=document.getElementById("root"),nextId=2,pending={},stack=[],cur=null,busy=false;
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
function add(p,c){if(c)p.appendChild(c);return p}
function send(m){parent.postMessage(Object.assign({jsonrpc:"2.0"},m),"*")}
function request(method,params){return new Promise(function(res,rej){var id=nextId++;pending[id]={res:res,rej:rej};send({id:id,method:method,params:params})})}
function size(){send({method:"ui/notifications/size-changed",params:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight}})}
function theme(ctx){if(ctx&&ctx.theme)document.documentElement.setAttribute("data-theme",ctx.theme)}
function btn(label,fn){var b=el("button","btn",label);b.type="button";b.addEventListener("click",fn);return b}
function head(t,s){var h=el("div","hd");add(h,el("h1",null,t));if(s)add(h,el("div","sub",s));return h}
function pic(src,fb){if(src){var i=el("img");i.src=src;i.alt="";i.onerror=function(){i.replaceWith(el("div","ph",fb))};return i}return el("div","ph",fb)}
function tile(it,call){var t=el("div","tile");t.tabIndex=0;add(t,pic(it.img,it.video?"▶":"🖼"));if(it.flag)add(t,el("span","fl",it.flag));add(t,el("div","cap",it.cap||""));
 if(call){t.addEventListener("click",function(){go(call,true)});t.addEventListener("keydown",function(e){if(e.key==="Enter")go(call,true)})}return t}
function tiles(d){var c=el("div","card");add(c,head(d.title,d.sub));var g=el("div","grid");(d.items||[]).forEach(function(it){add(g,tile(it,it.call))});if(!(d.items||[]).length)add(c,el("div","empty","Nothing found."));else add(c,g);if(d.note)add(c,el("div","note",d.note));return c}
function photo(d){var c=el("div","card");add(c,head(d.title,d.sub));if(d.img){var b=el("div","big");add(b,pic(d.img,"🖼"));add(c,b)}var dl=el("dl","rows");(d.rows||[]).forEach(function(r){if(r[1]==null||r[1]==="")return;add(dl,el("dt",null,r[0]));add(dl,el("dd",null,String(r[1])))});add(c,dl);
 if(d.actions&&d.actions.length){var bar=el("div","bar");d.actions.forEach(function(a){add(bar,btn(a.label,function(){go(a.call,false)}))});add(c,bar)}return c}
function bars(d){var c=el("div","card");add(c,head(d.title,d.sub));var mx=Math.max.apply(null,(d.rows||[]).map(function(r){return r.value}).concat([1]));(d.rows||[]).forEach(function(r){var row=el("div","row");add(row,el("span","lb",r.label));var tr=el("div","tr"),i=el("i");i.style.width=Math.round(100*r.value/mx)+"%";add(tr,i);add(row,tr);add(row,el("span","v",r.text));add(c,row)});if(d.note)add(c,el("div","note",d.note));return c}
function text(d){var c=el("div","card"),b=el("div","note");b.style.whiteSpace="pre-wrap";b.textContent=d.text;add(c,b);return c}
function data(d){var c=el("div","card");add(c,head(d.title,d.sub));(d.sections||[]).forEach(function(s){
 if(s.title)add(c,el("div","sec",s.title));
 if(s.rows){var dl=el("dl","rows");s.rows.forEach(function(r){add(dl,el("dt",null,r[0]));add(dl,el("dd",null,r[1]))});add(c,dl)}
 if(s.table){var w=el("div","tw"),t=el("table"),hr=el("tr");s.cols.forEach(function(x){add(hr,el("th",null,x))});add(t,hr);
  s.table.forEach(function(r){var tr=el("tr");r.forEach(function(x){add(tr,el("td",null,x))});add(t,tr)});add(w,t);add(c,w);
  if(s.more)add(c,el("div","note","... and "+s.more+" more rows"))}});
 if(!(d.sections||[]).length)add(c,el("div","empty","No data."));return c}
var KINDS={tiles:tiles,photo:photo,bars:bars,text:text,data:data};
function draw(){root.textContent="";if(stack.length){var bk=btn("← Back",function(){show(stack.pop(),false)});bk.style.marginBottom="8px";add(root,bk)}var f=KINDS[cur&&cur.kind];if(!f){add(root,el("div","empty","Nothing to display."));return}add(root,f(cur));setTimeout(size,30)}
function show(d,push){if(push&&cur)stack.push(cur);cur=d;draw()}
function textOf(r){return((r&&r.content)||[]).map(function(c){return c.text||""}).join("\n")}
function go(call,push){if(busy)return;busy=true;document.body.classList.add("busy");
 request("tools/call",call).then(function(r){busy=false;document.body.classList.remove("busy");var d=r&&r.structuredContent;if(!d||r.isError)d={kind:"text",text:textOf(r)||"No result."};show(d,push)},function(e){busy=false;document.body.classList.remove("busy");show({kind:"text",text:"Could not load: "+(e&&e.message||"error")},push)})}
window.addEventListener("message",function(e){var m=e.data;if(!m||m.jsonrpc!=="2.0")return;
 if(m.id!=null&&pending[m.id]&&(m.result!==undefined||m.error)){var p=pending[m.id];delete pending[m.id];if(m.error)p.rej(m.error);else p.res(m.result);return}
 if(m.method==="ui/notifications/tool-result"){var r=m.params||{};stack=[];show(r.structuredContent||{kind:"text",text:textOf(r)||"No visual data for this result."},false)}
 else if(m.method==="ui/notifications/host-context-changed"){theme(m.params)}
 else if(m.method==="ui/resource-teardown"&&m.id!=null){send({id:m.id,result:{}})}
});
if(window.ResizeObserver)new ResizeObserver(size).observe(document.body);
request("ui/initialize",{appInfo:{name:"photag-card",version:"1.0.0"},appCapabilities:{},protocolVersion:"2026-01-26"}).then(function(r){if(r)theme(r.hostContext);send({method:"ui/notifications/initialized",params:{}});size()},function(){});
})();
</script></body></html>"""
