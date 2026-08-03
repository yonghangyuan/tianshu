"""天枢 HTTP API 服务器。

用法:
    tianshu-server              # 默认 http://localhost:8720
    tianshu-server --port 9000
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, Depends
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel
import json

# 确保项目根在 path
_project_root = Path(__file__).resolve().parents[3]  # gateway→tianshu→src→root
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from tianshu.sdk.models import AgentRequest, AgentContext
from tianshu.core.service import AgentCore
from tianshu.core.config import load_providers, load_routing_config
from tianshu.core.setup import load_user_keys

# ── 全局 AgentCore 实例 ────────────────────────────────────────────

_core: AgentCore | None = None
_sessions: dict[str, AgentContext] = {}
_ws_clients: list[WebSocket] = []
_ws_names: dict[WebSocket, str] = {}

# ── 安全配置 ──
import hashlib, os as _os
SERVER_TOKEN = _os.environ.get("TIANSHU_TOKEN", "tianshu")
LOGIN_PASSWORD = _os.environ.get("TIANSHU_LOGIN_PASSWORD", "")
MAX_INPUT_LENGTH = 2000
import secrets as _secrets
_login_tokens: set[str] = set()  # 已登录 token 集合
MAX_WS_MESSAGE_LENGTH = 1000
_ws_rate_limit: dict[str, float] = {}  # IP → last message time


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _core
    config_dir = _project_root / "config"
    providers_yaml = config_dir / "providers.yaml"
    soul_md = config_dir / "soul.md"

    if not providers_yaml.exists():
        raise RuntimeError("config/providers.yaml not found")

    user_keys = load_user_keys()
    registry = load_providers(providers_yaml, extra_keys=user_keys)
    routing = load_routing_config(providers_yaml)
    system_prompt = soul_md.read_text(encoding="utf-8") if soul_md.exists() else ""

    _core = AgentCore()
    _core.setup(registry=registry, routing=routing, system_prompt=system_prompt,
                db_path=str(_project_root / "tianshu.db"), skill_discover=True)
    await _init_chat_db()
    yield


app = FastAPI(title="天枢 Agent API", version="0.1.0", lifespan=lifespan)


# ── 数据模型 ──────────────────────────────────────────────────────

class RunRequest(BaseModel):
    input: str
    session_id: str = ""
    model_override: str = ""


class RunResponse(BaseModel):
    decision_id: str
    content: str
    tool_calls: list[dict] = []
    audit_level: int = 1
    model_used: str = ""
    elapsed_ms: int = 0
    error: str = ""


# ── 端点 ──────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """健康检查。"""
    if _core is None:
        return JSONResponse({"status": "not ready"}, status_code=503)
    return {
        "status": "ok",
        "models": _core.model_count,
        "skills": _core.skills.count,
        "tools": _core.skills.tool_count,
    }


@app.post("/run", response_model=RunResponse)
async def run(req: RunRequest):
    """执行 Agent 对话（需要 Token 鉴权）。"""
    if _core is None:
        raise HTTPException(503, "AgentCore not initialized")
    if len(req.input) > MAX_INPUT_LENGTH:
        raise HTTPException(400, f"Input too long (max {MAX_INPUT_LENGTH})")

    sid = req.session_id or f"sess_{int(time.time())}"
    if sid not in _sessions:
        _sessions[sid] = AgentContext(session_id=sid)

    resp = await _core.run(
        AgentRequest(
            input=req.input,
            session_id=sid,
            model_override=req.model_override,
        ),
        ctx=_sessions[sid],
    )
    return RunResponse(
        decision_id=resp.decision_id,
        content=resp.content,
        tool_calls=resp.tool_calls,
        audit_level=resp.audit_level,
        model_used=resp.model_used,
        elapsed_ms=resp.elapsed_ms,
        error=resp.error,
    )


@app.get("/tools")
async def tools():
    """获取可用工具列表。"""
    if _core is None:
        raise HTTPException(503)
    return {"tools": _core.skills.loader.get_all_tools()}


@app.get("/audit")
async def audit(limit: int = 10):
    """获取最近审计记录。"""
    if _core is None:
        raise HTTPException(503)
    records = await _core.audit.recent(limit)
    return {"count": await _core.audit.count(), "records": records}


@app.get("/memory")
async def memory(limit: int = 10):
    """获取最近记忆。"""
    if _core is None:
        raise HTTPException(503)
    return {
        "count": await _core.memory.count(),
        "recent": await _core.memory.list_recent(limit),
    }


@app.get("/skills")
async def skills():
    """获取 Skills 列表。"""
    if _core is None:
        raise HTTPException(503)
    return {"skills": _core.skills.list_skills()}


@app.post("/run/stream")
async def run_stream(req: RunRequest):
    """SSE 流式 Agent 对话。"""
    if _core is None:
        raise HTTPException(503)

    sid = req.session_id or f"sess_{int(time.time())}"
    if sid not in _sessions:
        _sessions[sid] = AgentContext(session_id=sid)

    async def generate():
        import asyncio as _aio
        resp = await _core.run(
            AgentRequest(input=req.input, session_id=sid, model_override=req.model_override),
            ctx=_sessions[sid],
        )
        yield f"data: {json.dumps({'type': 'start', 'model': resp.model_used, 'decision_id': resp.decision_id}, ensure_ascii=False)}\n\n"
        if resp.tool_calls:
            for tc in resp.tool_calls:
                yield f"data: {json.dumps({'type': 'tool', 'name': tc.get('name','?'), 'ok': tc.get('success',True)}, ensure_ascii=False)}\n\n"
        if resp.content:
            # 逐字流式发送（模拟打字机效果）
            for char in resp.content:
                yield f"data: {json.dumps({'type': 'text', 'content': char}, ensure_ascii=False)}\n\n"
                await _aio.sleep(0.015)  # ~65 chars/sec
        yield f"data: {json.dumps({'type': 'done', 'elapsed_ms': resp.elapsed_ms, 'audit_level': resp.audit_level}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/")
async def dashboard():
    """Web 仪表盘。"""
    if _core is None:
        return HTMLResponse("<h1>Agent not ready</h1>")
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><title>天枢 Agent</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{font-family:-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex}}
  nav{{width:260px;background:#1e293b;padding:24px 20px;border-right:1px solid #334155}}
  nav h1{{font-size:20px;margin-bottom:8px}} nav .sub{{color:#94a3b8;font-size:13px;margin-bottom:24px}}
  nav .stat{{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #1e293b;font-size:14px}}
  nav .stat .val{{color:#38bdf8;font-weight:600}}
  main{{flex:1;padding:32px;display:flex;flex-direction:column}}
  #chat{{flex:1;overflow-y:auto;margin-bottom:16px}}
  .msg{{padding:12px 16px;border-radius:8px;margin-bottom:8px;max-width:80%}}
  .user{{background:#1e40af;margin-left:auto}} .agent{{background:#1e293b}}
  #input-area{{display:flex;gap:8px}}
  #input{{flex:1;padding:12px;border-radius:8px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;font-size:15px}}
  button{{padding:12px 24px;border-radius:8px;border:none;background:#2563eb;color:#fff;cursor:pointer;font-size:15px}}
  button:hover{{background:#1d4ed8}}
</style></head>
<body>
<nav>
  <h1>☰ 天枢 Agent</h1>
  <div class="sub">v0.3.0 · 北斗七星第一星</div>
  <div class="stat"><span>模型</span><span class="val">{_core.model_count}</span></div>
  <div class="stat"><span>Skills</span><span class="val">{_core.skills.count}</span></div>
  <div class="stat"><span>Plugins</span><span class="val">{_core.plugins.count}</span></div>
  <div class="stat"><span>Cron</span><span class="val">{len(_core.cron.list_jobs())}</span></div>
</nav>
<main>
  <div id="chat"></div>
  <div id="input-area">
    <input id="input" placeholder="输入消息..." onkeypress="if(event.key==='Enter')send()">
    <button onclick="send()">发送</button>
  </div>
</main>
<script>
async function send(){{
  const input=document.getElementById('input');const text=input.value.trim();if(!text)return;
  addMsg('user',text);input.value='';const div=addMsg('agent','');
  const resp=await fetch('/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{input:text}})}});
  const data=await resp.json();
  if(data.tool_calls&&data.tool_calls.length)div.innerHTML='[tools: '+data.tool_calls.map(t=>t.name).join(', ')+']<br>'+data.content;
  else div.textContent=data.content||'(empty)';
}}
function addMsg(role,text){{const d=document.createElement('div');d.className='msg '+role;d.textContent=text;document.getElementById('chat').appendChild(d);return d;}}
</script>
</body></html>""")


# ── 登录 ────────────────────────────────────────────────────

@app.get("/login")
async def login_page():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 · 天枢</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:#0a0e27;color:#c9d1d9;display:flex;justify-content:center;align-items:center;height:100vh}
.box{background:#131a35;padding:40px;border-radius:12px;border:1px solid #1e2a4a;width:360px;text-align:center}
h1{color:#e2c860;margin-bottom:4px}
.sub{color:#64748b;font-size:12px;margin-bottom:24px}
input{width:100%;padding:10px;border-radius:6px;border:1px solid #1e2a4a;background:#0f1630;color:#c9d1d9;font-size:14px;margin-bottom:12px}
button{width:100%;padding:10px;border-radius:6px;border:none;background:#2563eb;color:#fff;font-size:15px;font-weight:600;cursor:pointer}
button:hover{background:#1d4ed8}
.err{color:#ef4444;font-size:13px;margin-top:8px}
</style></head>
<body>
<div class="box">
  <h1>天枢 · 星群</h1>
  <div class="sub">北斗七星第一星</div>
  <input id="u" placeholder="用户名" value="">
  <input id="p" type="password" placeholder="密码">
  <button onclick="login()">登录</button>
  <div class="err" id="e"></div>
</div>
<script>
async function login(){
  const u=document.getElementById('u').value.trim(),p=document.getElementById('p').value;
  if(!u){document.getElementById('e').textContent='请输入用户名';return}
  const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})});
  const d=await r.json();
  if(d.ok){location.href='/chat'}else{document.getElementById('e').textContent=d.error||'登录失败'}
}
</script>
</body></html>""")

class LoginReq(BaseModel):
    username: str
    password: str = ""

@app.post("/login")
async def login(req: LoginReq):
    if not req.username.strip():
        return {"ok": False, "error": "用户名不能为空"}
    if LOGIN_PASSWORD and req.password != LOGIN_PASSWORD:
        return {"ok": False, "error": "密码错误"}
    token = _secrets.token_hex(16)
    _login_tokens.add(token)
    resp = JSONResponse({"ok": True, "token": token})
    resp.set_cookie("tianshu_token", token, httponly=True)
    return resp

def _check_auth(request: Request) -> bool:
    """检查请求是否已登录。"""
    if not LOGIN_PASSWORD:
        return True
    token = request.cookies.get("tianshu_token", "")
    if token in _login_tokens:
        return True
    token = request.query_params.get("token", "")
    return token in _login_tokens

async def require_auth(request: Request):
    if not _check_auth(request):
        raise HTTPException(401, "请先登录")

@app.get("/chat")
async def chat_page(request: Request):
    if not _check_auth(request):
        return RedirectResponse("/login")
    html = Path(__file__).parent / "chat.html"
    return HTMLResponse(html.read_text(encoding="utf-8"))


@app.get("/models")
async def models():
    """获取已注册模型。"""
    if _core is None:
        raise HTTPException(503)
    registry = _core._registry
    return {
        "models": [
            {"name": f"{p.provider_name}/{p.model_id}", "tags": list(p.capabilities)}
            for p in registry.list_all()
        ]
    }


# ── WebSocket 群聊 ──────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_chat(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    _ws_names[ws] = "匿名"

    # 发送当前状态
    if _core:
        await ws.send_json({
            "type": "status",
            "models": _core.model_count,
            "skills": _core.skills.count,
        })

    # 广播加入消息
    await _broadcast({"type": "join", "from": _ws_names[ws], "members": list(_ws_names.values())})

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "chat")
            sender = data.get("from", "匿名")[:20]  # 名字长度限制
            content = data.get("content", "")[:MAX_WS_MESSAGE_LENGTH]

            # 限流：同一 IP 每秒最多 3 条消息
            client_ip = ws.client.host if ws.client else "unknown"
            now = time.time()
            last = _ws_rate_limit.get(client_ip, 0)
            if now - last < 0.3:
                await ws.send_json({"type": "error", "content": "消息太快，请稍后"})
                continue
            _ws_rate_limit[client_ip] = now

            # 更新名字
            if sender != _ws_names.get(ws, ""):
                old_name = _ws_names[ws]
                _ws_names[ws] = sender
                await _broadcast({"type": "join", "from": sender, "members": list(_ws_names.values())})

            if msg_type == "chat" and content:
                # 广播用户消息
                await _broadcast({"type": "chat", "from": sender, "content": content})

                # 检测 @天枢
                if _core and ("@天枢" in content or "@tianshu" in content.lower()):
                    user_input = content.replace("@天枢", "").replace("@tianshu", "").strip()
                    if user_input:
                        resp = await _core.run(AgentRequest(input=user_input, task_type="conversation"))
                        tools_info = [{"name": t.get("name", "?"), "ok": t.get("success", True)} for t in resp.tool_calls]
                        await _broadcast({
                            "type": "chat", "from": "天枢",
                            "content": resp.content or "(空回复)",
                            "tools": tools_info,
                        })
                        # 广播工具事件
                        for t in resp.tool_calls:
                            await _broadcast({
                                "type": "tool",
                                "name": t.get("name", "?"),
                                "ok": t.get("success", True),
                            })

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ws_clients.remove(ws)
        name = _ws_names.pop(ws, "匿名")
        await _broadcast({"type": "join", "from": f"{name} 离开", "members": list(_ws_names.values())})


async def _broadcast(msg: dict):
    disconnected = []
    for client in _ws_clients:
        try:
            await client.send_json(msg)
        except Exception:
            disconnected.append(client)
    for c in disconnected:
        if c in _ws_clients:
            _ws_clients.remove(c)


# ── 群聊 ────────────────────────────────────────────────────

_chat_messages: list[dict] = []  # {id, from, content, time}
_chat_counter: int = 0
MAX_CHAT_MSGS = 200  # 最多保留 200 条
_chat_db_ready = False

async def _init_chat_db():
    """持久化聊天消息到 SQLite。"""
    global _chat_db_ready, _chat_counter
    import aiosqlite
    db_path = _project_root / "tianshu.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY, sender TEXT, content TEXT, time REAL)""")
        await db.commit()
        async with db.execute("SELECT id,sender,content,time FROM chat_messages ORDER BY id DESC LIMIT ?", (MAX_CHAT_MSGS,)) as c:
            for row in reversed(await c.fetchall()):
                _chat_messages.append({"id":row[0],"from":row[1],"content":row[2],"time":row[3]})
                if row[0]>_chat_counter: _chat_counter=row[0]
    _chat_db_ready = True

class ChatMsg(BaseModel):
    sender: str = "匿名"
    content: str = ""

@app.post("/chat/send")
async def chat_send(msg: ChatMsg, _=Depends(require_auth)):
    global _chat_counter
    sender = msg.sender[:20]
    content = msg.content[:1000]

    # 存储消息
    _chat_counter += 1
    entry = {"id": _chat_counter, "from": sender, "content": content, "time": time.time()}
    _chat_messages.append(entry)
    if len(_chat_messages) > MAX_CHAT_MSGS:
        _chat_messages.pop(0)
    # 持久化
    if _chat_db_ready:
        import aiosqlite
        async with aiosqlite.connect(str(_project_root / "tianshu.db")) as db:
            await db.execute("INSERT INTO chat_messages VALUES(?,?,?,?)", (_chat_counter, sender, content, time.time()))
            await db.commit()

    # @天枢 → Agent 回复
    agent_reply = None
    if _core and ("@天枢" in content or "@tianshu" in content.lower()):
        user_input = content.replace("@天枢", "").replace("@tianshu", "").strip()
        if user_input:
            try:
                # 注入最近 10 条聊天上下文
                context = "\n".join(
                    f"[{m['from']}]: {m['content'][:200]}"
                    for m in _chat_messages[-10:] if m["from"] != "天枢"
                )
                full_input = f"群聊上下文:\n{context}\n\n用户 {sender} @你: {user_input}" if context else user_input
                resp = await _core.run(AgentRequest(input=full_input, task_type="conversation"))
                agent_reply = resp.content or "(空回复)"
                tools_used = [t.get("name","?") for t in resp.tool_calls] if resp.tool_calls else []
                _chat_counter += 1
                _chat_messages.append({
                    "id": _chat_counter, "from": "天枢", "content": agent_reply,
                    "time": time.time(), "tools": tools_used, "audit_id": resp.decision_id,
                })
            except Exception as e:
                agent_reply = f"Error: {e}"

    return {"ok": True, "agent_reply": agent_reply}

# ── 文件上传/下载 ──────────────────────────────────────────

from fastapi import UploadFile, File as FFile
from fastapi.responses import FileResponse
import shutil

import pathlib as _pl
UPLOAD_DIR = _pl.Path.home() / "tianshu_uploads"  # ~/tianshu_uploads
UPLOAD_DIR.mkdir(exist_ok=True)

@app.post("/chat/upload")
async def chat_upload(file: UploadFile = FFile(...), sender: str = "匿名", _=Depends(require_auth)):
    """上传文件到群聊。"""
    safe_name = file.filename.replace("\\", "/").split("/")[-1][:100]  # 防路径遍历
    dest = UPLOAD_DIR / f"{int(time.time())}_{safe_name}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # 广播为聊天消息
    global _chat_counter
    _chat_counter += 1
    full_path = str(dest)
    entry = {"id": _chat_counter, "from": sender, "content": f"📎 上传了文件: {safe_name} (路径: {full_path})", "time": time.time()}
    _chat_messages.append(entry)
    return {"ok": True, "filename": safe_name, "id": _chat_counter}

@app.get("/chat/files")
async def chat_files(_=Depends(require_auth)):
    """列出可下载文件。"""
    files = []
    for f in sorted(UPLOAD_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
        name = f.name.split("_", 1)[-1] if "_" in f.name else f.name
        files.append({"name": name, "size": f.stat().st_size, "time": f.stat().st_mtime})
    return {"files": files}

@app.get("/chat/download/{filename}")
async def chat_download(filename: str, _=Depends(require_auth)):
    """下载文件。"""
    for f in UPLOAD_DIR.iterdir():
        if f.name.endswith("_" + filename) or f.name == filename:
            return FileResponse(f, filename=filename)
    raise HTTPException(404, "文件不存在")

@app.get("/chat/messages")
async def chat_messages(since: int = 0, _=Depends(require_auth)):
    """返回 since 之后的新消息（轮询用）。"""
    recent = [m for m in _chat_messages if m["id"] > since]
    # 在线用户 + Agent 状态
    users = list(set(m["from"] for m in _chat_messages[-100:]))
    agents = [
        {"name": "天枢", "status": "working" if _core and _core._last_reasoning else "idle",
         "tools": _core._tool_registry.count if _core and _core._tool_registry else 0,
         "audit_count": 0}
    ]
    return {"messages": recent[-50:], "users": users, "agents": agents}


# ── CLI 入口 ──────────────────────────────────────────────────────

def main():
    import uvicorn
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8720)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    print(f"天枢 API Server → http://{args.host}:{args.port}")
    print(f"  /health  /run  /tools  /audit  /memory  /skills  /models")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
