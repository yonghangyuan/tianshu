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
    from tianshu.core.db import get_db as _get_db
    await _get_db(str(_project_root / "tianshu.db")).init()
    await _init_chat_db()
    global _task_db_ready; _task_db_ready = True
    yield


from tianshu import __version__
app = FastAPI(title="天枢 Agent API", version=__version__, lifespan=lifespan)


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
async def dashboard(request: Request):
    """首页 —— 需要登录，登录后直接进入群聊。"""
    if not _check_auth(request):
        return RedirectResponse("/login")
    return RedirectResponse("/chat")

@app.get("/map/route")
async def map_route_api(from_lat: float, from_lng: float, to_lat: float, to_lng: float):
    """地图路径规划 API。"""
    import math
    R = 6371000
    dlat = math.radians(to_lat - from_lat)
    dlng = math.radians(to_lng - from_lng)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(from_lat))*math.cos(math.radians(to_lat))*math.sin(dlng/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    dist = R * c
    y = math.sin(dlng) * math.cos(math.radians(to_lat))
    x = math.cos(math.radians(from_lat))*math.sin(math.radians(to_lat)) - math.sin(math.radians(from_lat))*math.cos(math.radians(to_lat))*math.cos(dlng)
    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    return {"distance_m": round(dist, 1), "bearing": round(bearing, 1), "points": [[from_lat, from_lng], [to_lat, to_lng]]}

@app.get("/map")
async def map_page():
    """地图可视化页面。"""
    html = Path(__file__).parent / "map.html"
    return HTMLResponse(html.read_text(encoding="utf-8"))

@app.get("/welcome")
async def welcome():
    """导航入口 —— 显示已挂载 Agent 和入口链接。"""
    rows = []
    for name, cfg in _chat_agents.items():
        sp = cfg.get("system_prompt", "")[:60]
        md = cfg.get("model", "默认")
        rows.append(f'<tr><td>@{name}</td><td>{sp}</td><td>{md}</td></tr>')
    agent_list = "".join(rows)
    agent_count = len(_chat_agents)
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang=zh><head><meta charset=UTF-8><meta name=viewport content=\"width=device-width,initial-scale=1\">
<title>天枢 · 星群</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,sans-serif;background:#0a0e27;color:#c9d1d9;display:flex;justify-content:center;align-items:center;min-height:100vh}}
.box{{background:#131a35;padding:40px;border-radius:12px;border:1px solid #1e2a4a;max-width:500px;text-align:center}}
h1{{color:#e2c860;margin-bottom:4px}}
.sub{{color:#64748b;font-size:12px;margin-bottom:24px}}
a{{display:block;padding:12px;margin:8px 0;border-radius:6px;text-decoration:none;font-size:15px;font-weight:600}}
a.chat{{background:#2563eb;color:#fff}}
a.chat:hover{{background:#1d4ed8}}
a.agents{{background:#1e293b;color:#60a5fa;border:1px solid #334155}}
a.agents:hover{{background:#1e3a5f}}
table{{width:100%;margin-top:16px;border-collapse:collapse;font-size:13px}}
th,td{{padding:6px 8px;text-align:left;border-bottom:1px solid #1e2a4a}}
th{{color:#64748b}}
</style></head><body>
<div class=box>
<h1>天枢 · 星群</h1>
<div class=sub>北斗七星第一星</div>
<a class=chat href=/chat>💬 进入群聊</a>
<a class=agents href=/chat/agents>🤖 查看 Agent ({agent_count})</a>
<div style=\"margin-top:16px;text-align:left\">
<h3 style=color:#94a3b8;font-size:13px;margin-bottom:8px>已挂载 Agent</h3>
<table>{agent_list if agent_list else '<tr><td colspan=3 style=color:#475569>暂无——用 curl POST /chat/agents 添加</td></tr>'}</table>
</div>
</div></body></html>""")
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
    """检查请求是否已登录。未配置密码时拒绝所有访问。"""
    if not LOGIN_PASSWORD:
        return False  # 必须配密码，不放行
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


@app.post("/admin/reload")
async def admin_reload(request: Request):
    """热加载配置：重新读取 providers.yaml + skills，无需重启服务。"""
    if not _check_auth(request):
        raise HTTPException(401, detail="未登录")
    global _core
    config_dir = _project_root / "config"
    providers_yaml = config_dir / "providers.yaml"
    soul_md = config_dir / "soul.md"

    try:
        user_keys = load_user_keys()
        registry = load_providers(providers_yaml, extra_keys=user_keys)
        routing = load_routing_config(providers_yaml)
        system_prompt = soul_md.read_text(encoding="utf-8") if soul_md.exists() else ""

        _core = AgentCore()
        _core.setup(registry=registry, routing=routing, system_prompt=system_prompt,
                    db_path=str(_project_root / "tianshu.db"), skill_discover=True)
        return {
            "ok": True,
            "models": _core.model_count,
            "tools": _core._tool_registry.count if _core._tool_registry else 0,
        }
    except Exception as e:
        raise HTTPException(500, detail=f"重载失败: {e}")


# ── Agent 生命周期 API ──────────────────────────────────────────

class AgentCreate(BaseModel):
    name: str
    skills: list[str] = []
    model: str = "deepseek-v4-flash"

@app.post("/agents")
async def create_agent(req: AgentCreate, request: Request, _=Depends(require_auth)):
    """创建子 Agent。"""
    if _core is None:
        raise HTTPException(503, "AgentCore not ready")
    agent = await _core.orchestrator.create_agent(
        req.name, req.skills or ["web_search"], req.model,
    )
    return {
        "ok": True,
        "agent": {
            "id": agent.agent_id,
            "name": agent.name,
            "skills": agent.skills,
            "model": agent.model,
            "status": agent.status,
        },
    }

@app.get("/agents")
async def list_agents(request: Request, _=Depends(require_auth)):
    """列出所有活跃子 Agent。"""
    if _core is None:
        raise HTTPException(503)
    return {
        "count": _core.orchestrator.active_count,
        "agents": [
            {"id": a.agent_id, "name": a.name, "status": a.status, "model": a.model}
            for a in _core.orchestrator.active.values()
        ],
    }

@app.delete("/agents/{agent_name}")
async def delete_agent(agent_name: str, request: Request, _=Depends(require_auth)):
    """销毁子 Agent。"""
    if _core is None:
        raise HTTPException(503)
    agent = _core.orchestrator.by_name.get(agent_name)
    if not agent:
        raise HTTPException(404, f"Agent '{agent_name}' not found")
    await _core.orchestrator.destroy(agent)
    return {"ok": True, "deleted": agent_name}

@app.post("/agents/{agent_name}/dispatch")
async def dispatch_agent(agent_name: str, request: Request, _=Depends(require_auth)):
    """给子 Agent 派任务。"""
    if _core is None:
        raise HTTPException(503)
    body = await request.json()
    task = body.get("task", "")
    if not task:
        raise HTTPException(400, "task is required")
    agent = _core.orchestrator.by_name.get(agent_name)
    if not agent:
        raise HTTPException(404, f"Agent '{agent_name}' not found")
    msg = await _core.orchestrator.dispatch(agent, task)
    return {
        "ok": True,
        "agent": agent_name,
        "intent": str(msg.intent)[:200],
        "result": msg.payload.get("result", "") if msg.payload else "",
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
_chat_users: set[str] = {"天枢"}  # 所有发过言的人
_chat_agents: dict[str, dict] = {}  # 群聊级 Agent: name → {system_prompt, model}

async def _init_chat_db():
    """从统一数据库加载聊天历史 + 群聊 Agent。"""
    global _chat_db_ready, _chat_counter, _chat_agents
    from tianshu.core.db import get_db
    db = get_db(str(_project_root / "tianshu.db"))
    await db.init()
    conn = await db.connect()
    # 加载聊天历史
    c = await conn.execute("SELECT id,sender,content,time FROM chat_messages ORDER BY id DESC LIMIT ?", (MAX_CHAT_MSGS,))
    async for row in c:
        _chat_messages.insert(0, {"id":row[0],"from":row[1],"content":row[2],"time":row[3]})
        if row[0]>_chat_counter: _chat_counter=row[0]
    # 加载持久化 Agent
    c2 = await conn.execute("SELECT name,system_prompt,model FROM chat_agents")
    async for row in c2:
        _chat_agents[row[0]] = {"system_prompt": row[1], "model": row[2]}
        _chat_users.add(row[0])
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
    _chat_users.add(sender)
    entry = {"id": _chat_counter, "from": sender, "content": content, "time": time.time()}
    _chat_messages.append(entry)
    if len(_chat_messages) > MAX_CHAT_MSGS:
        _chat_messages.pop(0)
    if _chat_db_ready:
        import aiosqlite
        async with aiosqlite.connect(str(_project_root / "tianshu.db")) as db:
            await db.execute("INSERT INTO chat_messages VALUES(?,?,?,?)", (_chat_counter, sender, content, time.time()))
            await db.commit()

    # @Agent 检测（天枢 + 群聊自定义 Agent）
    import re as _re
    agent_reply = None
    mentioned = _re.findall(r'@(\S+)', content)
    for agent_name in mentioned:
        user_input = content.replace(f'@{agent_name}', '').strip()
        if not user_input or not _core: continue

        # 群聊上下文
        context = "\n".join(f"[{m['from']}]: {m['content'][:200]}" for m in _chat_messages[-10:] if m["from"] != agent_name)
        full_input = f"群聊上下文:\n{context}\n\n用户 {sender}: {user_input}" if context else user_input

        # 查找 Agent 定义
        is_default = agent_name in ("天枢", "tianshu")
        agent_cfg = _chat_agents.get(agent_name, {})
        if not is_default and not agent_cfg: continue  # 不存在的 Agent，跳过

        if agent_cfg.get("system_prompt"):
            full_input = f"{agent_cfg['system_prompt']}\n\n{full_input}"

        try:
            req = AgentRequest(input=full_input, task_type="conversation")
            if agent_cfg.get("model"): req.model_override = agent_cfg["model"]
            resp = await _core.run(req)
            agent_reply = resp.content or "(空回复)"
            tools_used = [t.get("name","?") for t in resp.tool_calls] if resp.tool_calls else []
            _chat_counter += 1
            _chat_messages.append({"id": _chat_counter, "from": agent_name, "content": agent_reply,
                "time": time.time(), "tools": tools_used, "audit_id": resp.decision_id})
        except Exception as e:
            agent_reply = f"Error: {e}"

    if not agent_reply:
        agent_reply = "抱歉，处理你的请求时遇到了问题。请稍后再试，或者换一种方式提问。"
    return {"ok": True, "agent_reply": agent_reply}

# ── 群聊 Agent 管理 ─────────────────────────────────────

@app.post("/chat/agents")
async def chat_add_agent(name: str = "", system_prompt: str = "", model: str = "", _=Depends(require_auth)):
    if not name or name in ("天枢","tianshu"): raise HTTPException(400, "name required, cannot be 天枢")
    _chat_agents[name] = {"system_prompt": system_prompt, "model": model}
    _chat_users.add(name)
    # 持久化
    from tianshu.core.db import get_db
    db = get_db(str(_project_root / "tianshu.db"))
    conn = await db.connect()
    await conn.execute("INSERT OR REPLACE INTO chat_agents VALUES(?,?,?,?,?)",
        (name, system_prompt, model, "", time.time()))
    await conn.commit()
    return {"ok": True, "agents": list(_chat_agents.keys())}

@app.delete("/chat/agents/{name}")
async def chat_remove_agent(name: str, _=Depends(require_auth)):
    _chat_agents.pop(name, None)
    from tianshu.core.db import get_db
    db = get_db(str(_project_root / "tianshu.db"))
    conn = await db.connect()
    await conn.execute("DELETE FROM chat_agents WHERE name=?", (name,))
    await conn.commit()
    return {"ok": True, "agents": list(_chat_agents.keys())}

@app.get("/chat/agents")
async def chat_list_agents(_=Depends(require_auth)):
    """浏览器 → HTML / API → JSON"""
    cards = []
    for name, cfg in _chat_agents.items():
        sp = cfg.get("system_prompt", "")[:80]
        md = cfg.get("model", "默认")
        cards.append(f'<div class=card><b>@{name}</b><br><span style=color:#64748b>{sp}</span><br><span style=color:#475569;font-size:12px>模型: {md}</span></div>')
    cards_html = "".join(cards)
    count = len(_chat_agents)
    html = f"""<!DOCTYPE html><html lang=zh><head><meta charset=UTF-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>星群 Agent</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,sans-serif;background:#0a0e27;color:#c9d1d9;padding:24px}}
h1{{color:#e2c860;font-size:18px;margin-bottom:16px}}
.card{{background:#131a35;border:1px solid #1e2a4a;border-radius:8px;padding:12px;margin-bottom:8px}}
a{{color:#60a5fa}}
</style></head><body>
<h1>星群 Agent ({count})</h1>
{cards_html if cards_html else '<p style=color:#475569>暂无 Agent。<br>添加: <code>curl -X POST "http://175.27.157.139:8720/chat/agents?name=Writer&system_prompt=写作助手"</code></p>'}
<p style=margin-top:16px><a href=/>← 返回首页</a></p>
</body></html>"""
    return HTMLResponse(html)

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

@app.post("/chat/share")
async def chat_share(path: str = "", _=Depends(require_auth)):
    """将服务器本地文件复制到上传目录，在群聊中可下载。"""
    import shutil as _shutil
    src = _pl.Path(path).expanduser()
    if not src.exists():
        raise HTTPException(404, f"文件不存在: {path}")
    if src.is_dir():
        raise HTTPException(400, "不能分享目录")
    safe_name = src.name[:100]
    dest = UPLOAD_DIR / f"{int(time.time())}_{safe_name}"
    _shutil.copy2(src, dest)
    # 广播到聊天
    global _chat_counter
    _chat_counter += 1
    entry = {"id": _chat_counter, "from": "天枢", "content": f"📎 已分享文件: {safe_name} ({src.stat().st_size:,} bytes)", "time": time.time()}
    _chat_messages.append(entry)
    return {"ok": True, "filename": safe_name, "size": src.stat().st_size}

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
    users = sorted(_chat_users)
    agents = [
        {"name": "天枢", "status": "working" if _core and _core._last_reasoning else "idle",
         "tools": _core._tool_registry.count if _core and _core._tool_registry else 0,
         "audit_count": 0}
    ]
    return {"messages": recent[-50:], "users": users, "agents": agents}


# ── 任务空间 ──────────────────────────────────────────────────

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    created_by: str = "匿名"

class TaskUpdate(BaseModel):
    status: str = ""
    title: str = ""
    description: str = ""
    updated_by: str = ""

class TaskMsg(BaseModel):
    sender: str = "匿名"
    content: str = ""

class TaskAgentCreate(BaseModel):
    name: str
    system_prompt: str = ""
    model: str = ""
    created_by: str = "匿名"

_task_db_ready = False

@app.post("/tasks")
async def create_task(req: TaskCreate, _=Depends(require_auth)):
    import aiosqlite
    now = time.time()
    db_path = _project_root / "tianshu.db"
    async with aiosqlite.connect(str(db_path)) as db:
        c = await db.execute("INSERT INTO tasks VALUES(NULL,?,?,?,?,?,?)",
            (req.title, req.description, "todo", req.created_by, now, now))
        await db.commit()
        tid = c.lastrowid
    return {"ok": True, "task": {"id": tid, "title": req.title, "description": req.description,
            "status": "todo", "created_by": req.created_by, "created_at": now, "updated_at": now}}

@app.get("/tasks")
async def list_tasks(status: str = "all", _=Depends(require_auth)):
    import aiosqlite
    db_path = _project_root / "tianshu.db"
    sql = "SELECT * FROM tasks"
    params = []
    if status and status != "all" and status != "archived":
        sql += " WHERE status != 'archived'"
    elif status == "archived":
        sql += " WHERE status = 'archived'"
    sql += " ORDER BY updated_at DESC LIMIT 50"
    async with aiosqlite.connect(str(db_path)) as db:
        c = await db.execute(sql, params)
        rows = await c.fetchall()
    tasks = [{"id": r[0], "title": r[1], "description": r[2], "status": r[3],
              "created_by": r[4], "created_at": r[5], "updated_at": r[6]} for r in rows]
    return {"tasks": tasks}

@app.get("/tasks/{task_id}")
async def get_task(task_id: int, _=Depends(require_auth)):
    import aiosqlite
    db_path = _project_root / "tianshu.db"
    async with aiosqlite.connect(str(db_path)) as db:
        c = await db.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
        row = await c.fetchone()
        if not row: raise HTTPException(404, "任务不存在")
        c2 = await db.execute("SELECT * FROM task_agents WHERE task_id=?", (task_id,))
        agents = [{"name": r[0], "task_id": r[1], "system_prompt": r[2], "model": r[3],
                    "created_by": r[4], "created_at": r[5]} for r in await c2.fetchall()]
    return {"task": {"id": row[0], "title": row[1], "description": row[2], "status": row[3],
            "created_by": row[4], "created_at": row[5], "updated_at": row[6]}, "agents": agents}

@app.patch("/tasks/{task_id}")
async def update_task(task_id: int, req: TaskUpdate, _=Depends(require_auth)):
    import aiosqlite
    db_path = _project_root / "tianshu.db"
    async with aiosqlite.connect(str(db_path)) as db:
        c = await db.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not await c.fetchone(): raise HTTPException(404, "任务不存在")
        if req.status:
            await db.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (req.status, time.time(), task_id))
        if req.title:
            await db.execute("UPDATE tasks SET title=?, updated_at=? WHERE id=?",
                (req.title, time.time(), task_id))
        await db.commit()
    return {"ok": True}

@app.get("/tasks/{task_id}/messages")
async def task_get_msgs(task_id: int, since: int = 0, _=Depends(require_auth)):
    import aiosqlite
    db_path = _project_root / "tianshu.db"
    async with aiosqlite.connect(str(db_path)) as db:
        c = await db.execute("SELECT * FROM task_messages WHERE task_id=? AND id>? ORDER BY id LIMIT 100",
            (task_id, since))
        rows = await c.fetchall()
    return {"messages": [{"id": r[0], "task_id": r[1], "from": r[2],
            "content": r[3], "time": r[4]} for r in rows]}

# ── P1: Task Agent 管理 ─────────────────────────────────────

@app.post("/tasks/{task_id}/agents")
async def task_add_agent(task_id: int, req: TaskAgentCreate, _=Depends(require_auth)):
    import aiosqlite
    db_path = _project_root / "tianshu.db"
    now = time.time()
    async with aiosqlite.connect(str(db_path)) as db:
        # 检查任务存在
        c = await db.execute("SELECT id FROM tasks WHERE id=?", (task_id,))
        if not await c.fetchone(): raise HTTPException(404, "任务不存在")
        await db.execute("INSERT OR REPLACE INTO task_agents VALUES(?,?,?,?,?,?)",
            (req.name, task_id, req.system_prompt, req.model, req.created_by, now))
        await db.commit()
    return {"ok": True, "agent": {"name": req.name, "task_id": task_id,
            "system_prompt": req.system_prompt, "model": req.model, "created_by": req.created_by}}

@app.delete("/tasks/{task_id}/agents/{name}")
async def task_remove_agent(task_id: int, name: str, _=Depends(require_auth)):
    import aiosqlite
    db_path = _project_root / "tianshu.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("DELETE FROM task_agents WHERE task_id=? AND name=?", (task_id, name))
        await db.commit()
    return {"ok": True}

# ── @Agent 自动回复 ────────────────────────────────────────

@app.post("/tasks/{task_id}/messages")
async def task_send_msg(task_id: int, msg: TaskMsg, _=Depends(require_auth)):
    import aiosqlite
    db_path = _project_root / "tianshu.db"
    now = time.time()
    async with aiosqlite.connect(str(db_path)) as db:
        # 存用户消息
        c = await db.execute("INSERT INTO task_messages VALUES(NULL,?,?,?,?)",
            (task_id, msg.sender, msg.content, now))
        await db.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now, task_id))
        await db.commit()
        mid = c.lastrowid

    agent_reply = None
    # 检测 @Agent
    import re
    mentioned = re.findall(r'@(\S+)', msg.content)
    if mentioned and _core:
        for agent_name in mentioned:
            async with aiosqlite.connect(str(db_path)) as db:
                c = await db.execute("SELECT * FROM task_agents WHERE task_id=? AND name=?",
                    (task_id, agent_name))
                row = await c.fetchone()
            if row:
                sp = row[2] or f"你是任务'{agent_name}'的智能助手。"
                try:
                    resp = await _core.run(AgentRequest(
                        input=f"{sp}\n\n用户 {msg.sender}: {msg.content.replace('@'+agent_name, '').strip()}",
                        task_type="conversation"))
                    agent_reply = resp.content or "(空)"
                    now2 = time.time()
                    async with aiosqlite.connect(str(db_path)) as db2:
                        await db2.execute("INSERT INTO task_messages VALUES(NULL,?,?,?,?)",
                            (task_id, agent_name, agent_reply, now2))
                        await db2.commit()
                except Exception as e:
                    agent_reply = f"Error: {e}"

    return {"ok": True, "message": {"id": mid, "task_id": task_id, "from": msg.sender,
            "content": msg.content, "time": now}, "agent_reply": agent_reply}

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
    print(f"  /chat   /admin/reload  /agents  /ws")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
