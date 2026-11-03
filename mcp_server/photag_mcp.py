"""photag as an MCP server (stdio) for Claude: one tool per endpoint of photag's local server, built live from its /openapi.json,
so every feature of the program (library, search, people, albums, Develop edits, analysis, import, export, backup, settings ...) is reachable.

No dependency outside the standard library. It talks to the running photag (http://127.0.0.1:8756 ...), or starts the server headless
when nothing answers (PHOTAG_AUTOSTART=0 turns that off). PHOTAG_URL forces an address.

Approval: every call that changes something (except light catalog edits such as albums and tags) opens a real Windows yes/no dialog
on the user's screen, on top of Claude's own permission prompt. Rule number one (CLAUDE.md): the export option "write_original" (the
one exception, writes into the original files) works only after its own dialog, every time; photag itself keeps its limits (JPEG/PNG,
backup copy in <library>/original-backups). Large images / videos are saved to %TEMP%/photag-mcp and the path is returned.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from card_view import VIEW_HTML, VIEW_MIME, VIEW_URI  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE_PORT = int(os.environ.get("PHOTAG_BASE_PORT", 8756))
PROTOCOL = "2024-11-05"
MAX_IMAGE = 1_500_000

_base = None
_tools = {}          # name -> (method, path, operation)
_server_proc = None


def _log(msg):
    print(f"[photag-mcp] {msg}", file=sys.stderr, flush=True)


def _alive(base):
    try:
        with urllib.request.urlopen(base + "/api/status", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def find_base():
    global _base, _server_proc
    if _base and _alive(_base):
        return _base
    forced = os.environ.get("PHOTAG_URL")
    if forced:
        _base = forced.rstrip("/")
        return _base
    for port in range(BASE_PORT, BASE_PORT + 50):
        b = f"http://127.0.0.1:{port}"
        if _alive(b):
            _base = b
            return b
    if os.environ.get("PHOTAG_AUTOSTART", "1") == "0":
        raise RuntimeError("photag is not running. Start photag, or set PHOTAG_URL.")
    _log(f"photag is not running, starting its server on port {BASE_PORT}")
    _server_proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--host", "127.0.0.1",
                                     "--port", str(BASE_PORT), "--log-level", "warning"], cwd=str(ROOT),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    b = f"http://127.0.0.1:{BASE_PORT}"
    for _ in range(80):
        if _alive(b):
            _base = b
            return b
        time.sleep(0.5)
    raise RuntimeError("the photag server did not start")


def _http(method, path, query=None, body=None, timeout=300, raw=None):
    url = find_base() + path
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    data, headers = None, {}
    if raw is not None:
        data, headers = raw, {"Content-Type": "application/octet-stream"}
    elif body is not None:
        data, headers = json.dumps(body).encode(), {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


# ---- tools from the OpenAPI schema --------------------------------------------------------------------------------------------

def _resolve(node, comps, depth=0):
    """Inline $ref (the client needs one self-contained JSON schema per tool)."""
    if depth > 6:
        return {}
    if isinstance(node, dict):
        if "$ref" in node:
            return _resolve(comps.get(node["$ref"].rsplit("/", 1)[-1], {}), comps, depth + 1)
        return {k: _resolve(v, comps, depth) for k, v in node.items() if k not in ("title", "examples")}
    if isinstance(node, list):
        return [_resolve(v, comps, depth) for v in node]
    return node


def _tool_name(method, path):
    p = re.sub(r"\{(\w+)\}", r"by_\1", path.strip("/"))
    p = re.sub(r"^api/", "", p)
    p = re.sub(r"[^a-zA-Z0-9]+", "_", p).strip("_").lower()
    return f"photag_{method.lower()}_{p}"[:64]


# Endpoints that open a window / dialog on the user's screen or change the program itself; still reachable, but marked.
_DESTRUCTIVE = ("delete-forever", "/restore", "/update/install", "/disconnect", "/clear", "/cancel")


def build_tools():
    status, _, raw = _http("GET", "/openapi.json")
    spec = json.loads(raw)
    comps = spec.get("components", {}).get("schemas", {})
    tools = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            name = _tool_name(method, path)
            props, required = {}, []
            for prm in op.get("parameters", []):
                if prm.get("in") not in ("path", "query"):
                    continue
                sch = _resolve(prm.get("schema", {}), comps)
                sch["description"] = f"{prm['in']} parameter"
                props[prm["name"]] = sch
                if prm.get("required"):
                    required.append(prm["name"])
            rb = op.get("requestBody", {}).get("content", {})
            if "application/json" in rb:
                bsch = _resolve(rb["application/json"].get("schema", {}), comps)
                props["body"] = {**bsch, "description": "JSON request body"}
                if op["requestBody"].get("required"):
                    required.append("body")
            if path == "/api/import-upload":
                props["file_path"] = {"type": "string", "description": "absolute path of a local file to upload into the library (copied, never moved)"}
                required.append("file_path")
            doc = (op.get("description") or op.get("summary") or "").strip().split("\n\n")[0].replace("\n", " ")
            desc = f"{method.upper()} {path}. {doc}".strip()[:900]
            destructive = method == "delete" or any(k in path for k in _DESTRUCTIVE)
            _tools[name] = (method, path, op)
            meta = {"ui": {"resourceUri": VIEW_URI}, "ui/resourceUri": VIEW_URI}       # every result is shown as a card, never as raw JSON
            tools.append({"_meta": meta,
                "name": name, "description": desc,
                "inputSchema": {"type": "object", "properties": props, "required": required},
                "annotations": {"title": f"{method.upper()} {path}", "readOnlyHint": method == "get", "destructiveHint": destructive,
                                "idempotentHint": method in ("get", "put", "delete"), "openWorldHint": False},
            })
    return tools


# ---- approval by the person at the keyboard -----------------------------------------------------------------------------------
# Besides Claude's own "allow this tool" prompt, every call that changes something (except the light catalog edits below) opens a
# real Windows dialog on the user's screen. The model cannot click it. PHOTAG_MCP_CONFIRM=0 turns the extra dialogs off (never the
# one for the original files).

_LIGHT = re.compile(r"^/api/(albums$|album/\{\w+\}/(add|remove|rename)$|tag-links|keywords$|searches$|smart/|series|person/\{\w+\}/rename$|"
                    r"cluster/\{\w+\}/name$|photo/\{\w+\}/(edit|rotate|auto|preview|revert|snapshots|face|frame|trim|mask-overlay|compress)|"
                    r"photos$|photo/\{\w+\}$|dev-presets$|catalog-edits$|dates/from-names$|geotag/suggest$|analysis/(run|rank)$|"
                    r"semantic/index$|faces$|aitag$|map$|ai/pick$|ai/rank-mode$|ai/models$|auto-score$)")


def needs_confirm(method, path):
    if method == "get" or os.environ.get("PHOTAG_MCP_CONFIRM", "1") == "0":
        return False
    if method == "patch" and _LIGHT.match(path):
        return False
    return not (method == "post" and _LIGHT.match(path))


def ask_user(title, text):
    """A yes/no dialog on the user's screen, default No. Anything but a clear Yes (or no dialog available) is a refusal."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        # MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_SETFOREGROUND | MB_TOPMOST
        return ctypes.windll.user32.MessageBoxW(0, text, title, 0x4 | 0x30 | 0x100 | 0x10000 | 0x40000) == 6
    except Exception:
        return False


def _summary(args, body):
    s = json.dumps({**args, **({"body": body} if body is not None else {})}, ensure_ascii=False)
    return s if len(s) < 700 else s[:700] + " ..."


def _save_file(data, ctype, tag):
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "video/mp4": ".mp4"}.get(ctype.split(";")[0], ".bin")
    d = Path(os.environ.get("TEMP", ".")) / "photag-mcp"
    d.mkdir(exist_ok=True)
    f = d / f"{tag}-{int(time.time() * 1000)}{ext}"
    f.write_bytes(data)
    return str(f)


# ---- inline cards (MCP Apps), see card_view.py ----------------------------------------------------------------------------------

CARD_GET = {"/api/photos", "/api/photo/{pid}", "/api/albums", "/api/people", "/api/storage/breakdown", "/api/search/smart",
            "/api/semantic/search"}
_OPEN_PHOTO = _tool_name("get", "/api/photo/{pid}")
_OPEN_EXIF = _tool_name("get", "/api/photo/{pid}/exif")
_MAX_TILES = 24


def _thumb(pid, side=112, quality=55):
    """A tiny data: URI (about 3 KB): the host drops a card whose data is too big, so the pictures are shrunk here."""
    try:
        st, ct, d = _http("GET", f"/thumb/{pid}", timeout=20)
        if st != 200 or not ct.startswith("image/"):
            return None
        import base64
        try:
            import io
            from PIL import Image
            im = Image.open(io.BytesIO(d)).convert("RGB")
            im.thumbnail((side, side))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True)
            d, ct = buf.getvalue(), "image/jpeg"
        except Exception:
            if len(d) > 8000:
                return None
        return f"data:{ct.split(';')[0]};base64,{base64.b64encode(d).decode()}"
    except Exception:
        return None


def _thumbs(ids):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(6) as ex:
        return list(ex.map(_thumb, ids))


def _size(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.1f} {u}" if u != "B" else f"{int(n)} B"
        n /= 1024


def _date(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def _photo_call(pid):
    return {"name": _OPEN_PHOTO, "arguments": {"pid": pid}}


def build_card(tpl, data, args):
    """structuredContent for the card of a GET result, or None when the result has no useful picture."""
    if tpl == "/api/photos" and isinstance(data, list):
        rows = data[:_MAX_TILES]
        th = _thumbs([r["id"] for r in rows])
        return {"kind": "tiles", "title": "Photos", "sub": f"{len(data)} photos" + (f" (showing {_MAX_TILES})" if len(data) > _MAX_TILES else ""),
                "items": [{"img": t, "cap": r.get("filename", ""), "video": bool(r.get("is_video")), "flag": "★" if r.get("favorited") else "",
                           "call": _photo_call(r["id"])} for r, t in zip(rows, th)]}
    if tpl in ("/api/search/smart", "/api/semantic/search") and isinstance(data, dict | list):
        res = data.get("results") if isinstance(data, dict) else data
        res = [r for r in (res or []) if isinstance(r, dict) and "id" in r]
        rows = res[:_MAX_TILES]
        th = _thumbs([r["id"] for r in rows])
        return {"kind": "tiles", "title": "Search results", "sub": f"{len(res)} matches" + (f" (showing {_MAX_TILES})" if len(res) > _MAX_TILES else ""),
                "items": [{"img": t, "cap": f"#{r['id']}" + (f"  {r['score']:.2f}" if isinstance(r.get("score"), int | float) else ""),
                           "call": _photo_call(r["id"])} for r, t in zip(rows, th)]}
    if tpl == "/api/photo/{pid}" and isinstance(data, dict) and "id" in data:
        keys = [("taken_at", "Taken"), ("width", "Width"), ("height", "Height"), ("bytes", "Size"), ("camera_make", "Camera make"),
                ("camera_model", "Camera"), ("lens", "Lens"), ("lat", "Latitude"), ("lng", "Longitude"), ("rating", "Rating"),
                ("folder", "Folder"), ("imported_at", "Imported")]
        rows = []
        for k, label in keys:
            v = data.get(k)
            rows.append([label, _date(v) if k in ("taken_at", "imported_at") else _size(v) if k == "bytes" else v])
        return {"kind": "photo", "title": data.get("filename", f"#{data['id']}"), "sub": f"photo #{data['id']}" + ("  ★" if data.get("favorited") else ""),
                "img": _thumb(data["id"], 360, 70), "rows": rows,
                "actions": [{"label": "EXIF", "call": {"name": _OPEN_EXIF, "arguments": {"pid": data["id"]}}}]}
    if tpl == "/api/albums" and isinstance(data, list):
        rows = data[:_MAX_TILES]
        th = _thumbs([r.get("cover") or r.get("cover_photo_id") or 0 for r in rows])
        return {"kind": "tiles", "title": "Albums", "sub": f"{len(data)} albums" + (f" (showing {_MAX_TILES})" if len(data) > _MAX_TILES else ""),
                "items": [{"img": t, "cap": f"{r.get('name', '')} ({r.get('n', 0)})"} for r, t in zip(rows, th)]}
    if tpl == "/api/people" and isinstance(data, list):
        rows = data[:_MAX_TILES]
        th = _thumbs([r.get("cover_photo") or 0 for r in rows])
        return {"kind": "tiles", "title": "People", "sub": f"{len(data)} people",
                "items": [{"img": t, "cap": f"{r.get('name', '')} ({r.get('tag_photos', 0)})"} for r, t in zip(rows, th)]}
    if tpl == "/api/storage/breakdown" and isinstance(data, dict):
        return {"kind": "bars", "title": "Storage by year", "sub": f"{data.get('total_n', 0)} items, {_size(data.get('total_bytes'))}",
                "rows": [{"label": str(r["name"]), "value": r["bytes"], "text": f"{r['n']} · {_size(r['bytes'])}"} for r in data.get("by_year", [])],
                "note": f"Trash: {data.get('trash_n', 0)} items, {_size(data.get('trash_bytes'))}"}
    return None


def _scal(v, n=120):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else str(v)
    return s if len(s) <= n else s[:n] + "..."


def _table(title, rows):
    cols = list(rows[0].keys())[:8]
    return {"title": title, "cols": cols, "table": [[_scal(r.get(c), 80) for c in cols] for r in rows[:50]],
            "more": max(0, len(rows) - 50)}


def generic_card(method, tpl, parsed, status):
    """Any JSON result as a readable card (key/value rows and tables)."""
    sections = []
    if isinstance(parsed, dict):
        rows = [[k, _scal(v)] for k, v in parsed.items() if not (isinstance(v, list) and v and isinstance(v[0], dict))]
        if rows:
            sections.append({"rows": rows[:80]})
        for k, v in parsed.items():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                sections.append(_table(k, v))
    elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        sections.append(_table("", parsed))
    elif isinstance(parsed, list):
        sections.append({"rows": [[str(i + 1), _scal(v)] for i, v in enumerate(parsed[:80])]})
    else:
        sections.append({"rows": [["result", _scal(parsed)]]})
    return {"kind": "data", "title": f"{method.upper()} {tpl}", "sub": "OK" if status < 400 else f"Error HTTP {status}", "sections": sections}


def call_tool(name, args, out=None):
    """`out` (a dict) receives "structuredContent" when the result has a card."""
    if name not in _tools:
        raise ValueError(f"unknown tool {name}")
    method, path, _op = _tools[name]
    tpl = path
    args = dict(args or {})
    body = args.pop("body", None)
    local_file = args.pop("file_path", None) if path == "/api/import-upload" else None
    raw = None
    if local_file:
        raw = Path(local_file).read_bytes()
        args.setdefault("name", Path(local_file).name)
        args.setdefault("modified", Path(local_file).stat().st_mtime)
    if path == "/api/export" and isinstance(body, dict) and body.get("write_original"):
        # The one exception to rule number one: allowed only when the person at the keyboard says yes in a real dialog, every time.
        # photag itself still limits it to JPEG/PNG and keeps a copy of every file in <library>/original-backups.
        if not ask_user("photag MCP - ORIGINAL FILES", "Claude asks to WRITE THE EDITS AND METADATA INTO THE ORIGINAL FILES of "
                        f"{len(body.get('ids') or [])} photo(s) (JPEG/PNG only).\n\nA copy of each file goes to <library>\\original-backups "
                        "first, but the files themselves are replaced.\n\nAllow it?"):
            raise ValueError("The user did not approve writing into the original files. Nothing was changed.")
    elif needs_confirm(method, path):
        if not ask_user("photag MCP - approval", f"Claude wants to run:\n{method.upper()} {path}\n\n{_summary(args, body)}\n\nAllow it?"):
            raise ValueError("The user did not approve this action in the dialog. Nothing was changed.")
    query = {}
    for k, v in args.items():
        token = "{" + k + "}"
        if token in path:
            path = path.replace(token, urllib.parse.quote(str(v), safe=""))
        elif v is not None:
            query[k] = v
    status, ctype, data = _http(method.upper(), path, query, body, raw=raw)
    if ctype.startswith("image/") and len(data) <= MAX_IMAGE:
        import base64
        return [{"type": "image", "data": base64.b64encode(data).decode(), "mimeType": ctype.split(";")[0]}], status >= 400
    if ctype.startswith(("image/", "video/", "audio/", "application/octet-stream")):
        f = _save_file(data, ctype, name)
        return [{"type": "text", "text": f"{ctype}, {len(data)} bytes, saved to {f} (open it with a file-reading tool)"}], status >= 400
    if ctype.startswith(("application/json", "text/")):
        text = data.decode("utf-8", "replace")
        try:
            parsed = json.loads(text)
            text = json.dumps(parsed, ensure_ascii=False, indent=1)
            if out is not None and method == "get" and tpl in CARD_GET and status < 400:
                try:
                    card = build_card(tpl, parsed, args)
                    if card:
                        out["structuredContent"] = card
                except Exception as e:
                    _log(f"card failed for {tpl}: {e}")
            if out is not None and "structuredContent" not in out:
                out["structuredContent"] = generic_card(method, tpl, parsed, status)
        except ValueError:
            pass
        if len(text) > 60000:
            text = text[:60000] + f"\n... [cut, {len(text)} characters in all; narrow the query with limit / offset / filters]"
        return [{"type": "text", "text": text if status < 400 else f"HTTP {status}: {text}"}], status >= 400
    return [{"type": "text", "text": f"{ctype or 'binary'}, {len(data)} bytes (HTTP {status})"}], status >= 400


# ---- the card window ----------------------------------------------------------------------------------------------------------
# The model gets the JSON; the person gets the card in a real window on this PC (Edge app window), independent of whether the host
# (Claude Desktop / a device bridge) renders MCP Apps. One window, updated by every call; clicks in it call the tools again (the
# approval dialogs still apply). Served on 127.0.0.1 with a random token in the address. PHOTAG_MCP_WINDOW=0 turns it off.

_card = {"seq": 0, "sc": None, "poll": 0.0, "url": None, "token": None}

_HOST_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>photag</title>
<style>html,body{margin:0;height:100%;background:#f6f7f9}@media(prefers-color-scheme:dark){html,body{background:#15171c}}iframe{border:0;width:100%;height:100%}</style></head>
<body><iframe id="f"></iframe><script>
var T=__TOKEN__,f=document.getElementById("f"),ready=false,seq=0,last=null;
f.srcdoc=__VIEW__;
function post(m){f.contentWindow.postMessage(Object.assign({jsonrpc:"2.0"},m),"*")}
function push(){if(ready&&last)post({method:"ui/notifications/tool-result",params:{structuredContent:last}})}
window.addEventListener("message",function(e){if(e.source!==f.contentWindow)return;var m=e.data;if(!m||m.jsonrpc!=="2.0")return;
 if(m.method==="ui/initialize"){post({id:m.id,result:{hostContext:{theme:matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}}})}
 else if(m.method==="ui/notifications/initialized"){ready=true;push()}
 else if(m.method==="tools/call"){fetch("/"+T+"/call",{method:"POST",body:JSON.stringify(m.params)}).then(function(r){return r.json()}).then(function(r){post({id:m.id,result:r})},function(x){post({id:m.id,error:{message:String(x)}})})}});
setInterval(function(){fetch("/"+T+"/latest?seq="+seq).then(function(r){return r.json()}).then(function(d){if(d.seq!==seq){seq=d.seq;last=d.sc;push()}}).catch(function(){})},700);
</script></body></html>"""


def _start_card_server():
    import secrets
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    token = secrets.token_urlsafe(16)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            b = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def _ok(self):
            return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost") and self.path.startswith(f"/{token}/")

        def do_GET(self):
            if not self._ok():
                return self._send(404, "not found", "text/plain")
            if self.path.startswith(f"/{token}/latest"):
                _card["poll"] = time.time()
                return self._send(200, json.dumps({"seq": _card["seq"], "sc": _card["sc"]}, ensure_ascii=False))
            page = _HOST_PAGE.replace("__TOKEN__", json.dumps(token)).replace("__VIEW__", json.dumps(VIEW_HTML).replace("</", "<\\/"))
            self._send(200, page, "text/html; charset=utf-8")

        def do_POST(self):
            if not self._ok() or self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}"):
                return self._send(403, "refused", "text/plain")
            try:
                p = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                extra = {}
                content, is_err = call_tool(p["name"], p.get("arguments"), extra)
                if extra.get("structuredContent"):
                    _card["seq"] += 1
                    _card["sc"] = extra["structuredContent"]
                self._send(200, json.dumps({"content": content, "isError": is_err, **extra}, ensure_ascii=False))
            except Exception as e:
                self._send(200, json.dumps({"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}))

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _card["token"] = token
    _card["url"] = f"http://127.0.0.1:{srv.server_address[1]}/{token}/"


def show_card(sc):
    """Publish a card to the window; open the window when it is not open (nobody polled for a while)."""
    if os.environ.get("PHOTAG_MCP_WINDOW", "0") != "1" or not sc:
        return
    try:
        if not _card["url"]:
            _start_card_server()
        _card["seq"] += 1
        _card["sc"] = sc
        _log("card window: " + _card["url"])
        if time.time() - _card["poll"] > 6:
            _card["poll"] = time.time() + 10          # give the new window time to start polling
            edge = next((p for p in (shutil.which("msedge"), r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                                     r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if p and os.path.exists(p)), None)
            if edge:
                subprocess.Popen([edge, f"--app={_card['url']}", "--window-size=900,720"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                import webbrowser
                webbrowser.open(_card["url"])
    except Exception as e:
        _log(f"card window failed: {e}")


# ---- JSON-RPC over stdio --------------------------------------------------------------------------------------------------------

def send(obj):
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def handle(msg):
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:
        return                                  # notifications need no answer
    try:
        if method == "initialize":
            result = {"protocolVersion": msg["params"].get("protocolVersion", PROTOCOL), "capabilities": {"tools": {}, "resources": {}},
                      "serverInfo": {"name": "photag", "version": "1.0.0"},
                      "instructions": "Tools for photag, the user's photo catalog. Every tool maps to one endpoint of the program's local server. "
                                      "Never touch the user's original photo files: work on the catalog or on exported copies."}
        elif method == "tools/list":
            result = {"tools": build_tools()}
        elif method == "tools/call":
            p = msg["params"]
            try:
                if not _tools:
                    build_tools()
                extra = {}
                content, is_err = call_tool(p["name"], p.get("arguments"), extra)
                result = {"content": content, "isError": is_err, **extra}
                show_card(extra.get("structuredContent"))
            except Exception as e:
                result = {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
        elif method == "resources/list":
            result = {"resources": [{"uri": VIEW_URI, "name": "photag card", "mimeType": VIEW_MIME}]}
        elif method == "resources/read":
            result = {"contents": [{"uri": VIEW_URI, "mimeType": VIEW_MIME, "text": VIEW_HTML, "_meta": {"ui": {"prefersBorder": False}}}]}
        elif method == "ping":
            result = {}
        else:
            return send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}})
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    except Exception as e:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}})


def main():
    for line in sys.stdin.buffer:
        line = line.strip()
        if line:
            try:
                handle(json.loads(line.decode("utf-8")))
            except ValueError:
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
    if _server_proc:
        _server_proc.terminate()


if __name__ == "__main__":
    main()
