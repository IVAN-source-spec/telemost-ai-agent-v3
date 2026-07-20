import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel


class MeetingRequest(BaseModel):
    meeting_url: str
    title: str | None = None
    source: str | None = "node-dashboard"
    external_event_id: str | None = None
    scheduled_start_at: str | None = None
    scheduled_end_at: str | None = None
    organizer: str | None = None


class BotEndpoint(BaseModel):
    id: str
    name: str
    base_url: str
    token: str = ""


APP_TITLE = os.getenv("NODE_DASHBOARD_TITLE", "Telemost Node Dashboard")
REQUEST_TIMEOUT = float(os.getenv("NODE_DASHBOARD_REQUEST_TIMEOUT_SECONDS", "5"))
ENDPOINTS_ENV = os.getenv(
    "NODE_DASHBOARD_ENDPOINTS",
    "bot-1|Bot 1|http://telemost-bot-1:8000,"
    "bot-2|Bot 2|http://telemost-bot-2:8000,"
    "bot-3|Bot 3|http://telemost-bot-3:8000",
)


def parse_endpoints(value: str) -> list[BotEndpoint]:
    endpoints: list[BotEndpoint] = []
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            continue
        parts = [part.strip() for part in item.split("|")]
        if len(parts) < 3:
            continue
        endpoints.append(
            BotEndpoint(
                id=parts[0],
                name=parts[1],
                base_url=parts[2].rstrip("/"),
                token=parts[3] if len(parts) > 3 else "",
            )
        )
    return endpoints


BOT_ENDPOINTS = parse_endpoints(ENDPOINTS_ENV)
app = FastAPI(title=APP_TITLE)


def request_json(endpoint: BotEndpoint, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if endpoint.token:
        headers["Authorization"] = f"Bearer {endpoint.token}"
        headers["X-API-Token"] = endpoint.token
    req = urllib.request.Request(f"{endpoint.base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=error.code, detail=body or str(error)) from error
    except Exception as error:
        raise RuntimeError(str(error)) from error


async def fetch_node(endpoint: BotEndpoint) -> dict[str, Any]:
    try:
        data = await asyncio.to_thread(request_json, endpoint, "/api/v1/node/status")
        data["base_url"] = endpoint.base_url
        data["configured_node"] = endpoint.model_dump()
        return data
    except Exception as error:
        return {
            "node_id": endpoint.id,
            "node_name": endpoint.name,
            "status": "unavailable",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "bots_total": 0,
            "bots_idle": 0,
            "bots_busy": 0,
            "bots": [],
            "base_url": endpoint.base_url,
            "configured_node": endpoint.model_dump(),
            "error": str(error),
        }


async def collect_nodes() -> list[dict[str, Any]]:
    return await asyncio.gather(*(fetch_node(endpoint) for endpoint in BOT_ENDPOINTS))


def summarize(nodes: list[dict[str, Any]]) -> dict[str, int]:
    bots = [bot for node in nodes for bot in node.get("bots", [])]
    return {
        "nodes_total": len(nodes),
        "nodes_online": sum(1 for node in nodes if node.get("status") == "online"),
        "nodes_unavailable": sum(1 for node in nodes if node.get("status") != "online"),
        "bots_total": len(bots),
        "bots_idle": sum(1 for bot in bots if bot.get("status") == "idle"),
        "bots_busy": sum(1 for bot in bots if bot.get("status") == "busy"),
    }


def endpoint_for_bot(nodes: list[dict[str, Any]], global_bot_id: str) -> BotEndpoint | None:
    for node in nodes:
        endpoint_data = node.get("configured_node") or {}
        endpoint = BotEndpoint(**endpoint_data)
        for bot in node.get("bots", []):
            identities = {
                str(bot.get("global_bot_id") or ""),
                str(bot.get("bot_id") or ""),
                f"{node.get('node_id')}:{bot.get('bot_id')}",
            }
            if global_bot_id in identities:
                return endpoint
    return None


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard/ui", response_class=HTMLResponse)
async def dashboard_ui() -> str:
    return HTML


@app.get("/api/v1/node/status")
async def node_status() -> dict[str, Any]:
    nodes = await collect_nodes()
    return {
        "node_id": os.getenv("NODE_DASHBOARD_ID", "local-node-dashboard"),
        "node_name": APP_TITLE,
        "status": "online",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarize(nodes),
        "nodes": nodes,
    }


@app.get("/api/v1/bots/")
async def bots_status() -> dict[str, Any]:
    nodes = await collect_nodes()
    bots = [bot for node in nodes for bot in node.get("bots", [])]
    return {"summary": summarize(nodes), "nodes": nodes, "bots": bots}


@app.post("/api/v1/bots/meetings")
async def start_any_bot(req: MeetingRequest) -> JSONResponse:
    if not req.meeting_url.strip():
        raise HTTPException(status_code=400, detail="meeting_url is required")
    nodes = await collect_nodes()
    for node in nodes:
        if node.get("status") != "online":
            continue
        if not any(bot.get("status") == "idle" for bot in node.get("bots", [])):
            continue
        endpoint = BotEndpoint(**(node.get("configured_node") or {}))
        data = await asyncio.to_thread(
            request_json,
            endpoint,
            "/api/v1/bots/meetings",
            "POST",
            req.model_dump(),
        )
        return JSONResponse({"node_id": node.get("node_id"), "node_name": node.get("node_name"), **data})
    raise HTTPException(status_code=409, detail="No idle bot available on this VM")


@app.post("/api/v1/bots/{global_bot_id}/meetings")
async def start_specific_bot(global_bot_id: str, req: MeetingRequest) -> JSONResponse:
    nodes = await collect_nodes()
    endpoint = endpoint_for_bot(nodes, urllib.parse.unquote(global_bot_id))
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    data = await asyncio.to_thread(
        request_json,
        endpoint,
        "/api/v1/bots/meetings",
        "POST",
        req.model_dump(),
    )
    return JSONResponse(data)


HTML = r'''<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Telemost Node Dashboard</title>
  <style>
    :root { color-scheme: light; font-family: Arial, sans-serif; }
    body { margin: 0; background: #f3f6fa; color: #111827; }
    header { background: #111827; color: white; padding: 18px 28px; display:flex; align-items:center; justify-content:space-between; }
    main { padding: 24px 28px; max-width: 1280px; margin: 0 auto; }
    h1 { margin: 0; font-size: 24px; }
    .muted { color: #64748b; }
    .summary { display:grid; grid-template-columns: repeat(4, minmax(140px, 1fr)); gap: 12px; margin-bottom: 18px; }
    .metric, .panel, .bot { background:white; border:1px solid #d8e0ea; border-radius:8px; box-shadow:0 1px 2px rgba(15,23,42,.05); }
    .metric { padding: 14px 16px; }
    .metric b { display:block; font-size:28px; margin-top:6px; }
    .panel { padding: 18px 20px; margin-bottom: 18px; }
    form { display:grid; grid-template-columns: 1.6fr 1fr auto; gap: 10px; align-items:center; }
    input { border:1px solid #cbd5e1; border-radius:8px; padding: 11px 12px; font-size:15px; }
    button { border:0; border-radius:8px; padding: 11px 14px; font-weight:700; background:#2563eb; color:white; cursor:pointer; }
    button.secondary { background:#0f172a; }
    button:disabled { background:#94a3b8; cursor:not-allowed; }
    .grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(310px, 1fr)); gap: 12px; }
    .bot { padding: 16px; border-left: 6px solid #94a3b8; }
    .bot.idle { border-left-color:#16a34a; }
    .bot.busy { border-left-color:#dc2626; }
    .bot.unavailable { border-left-color:#f59e0b; }
    .bot h3 { margin:0 0 8px; font-size:18px; }
    .row { display:flex; justify-content:space-between; gap:10px; margin:5px 0; }
    .actions { display:flex; gap:8px; margin-top:12px; }
    code { font-size:12px; background:#eef2f7; padding:2px 4px; border-radius:4px; }
    .error { color:#b91c1c; white-space:pre-wrap; }
    @media (max-width: 800px) { .summary, form { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <header><div><h1 id="title">Telemost Node Dashboard</h1><div class="muted">Локальная панель виртуальной машины</div></div><button class="secondary" onclick="load()">Обновить</button></header>
  <main>
    <section class="summary">
      <div class="metric"><span>Ботов всего</span><b id="botsTotal">0</b></div>
      <div class="metric"><span>Свободно</span><b id="botsIdle">0</b></div>
      <div class="metric"><span>Занято</span><b id="botsBusy">0</b></div>
      <div class="metric"><span>Узлов онлайн</span><b id="nodesOnline">0</b></div>
    </section>
    <section class="panel">
      <h2>Быстрый старт</h2>
      <form id="quickForm">
        <input name="meeting_url" placeholder="Ссылка на встречу" required>
        <input name="title" placeholder="Название, необязательно">
        <button type="submit">Запустить свободного бота</button>
      </form>
      <p id="message" class="muted"></p>
    </section>
    <section class="panel">
      <h2>Боты этой VM</h2>
      <div id="bots" class="grid"></div>
    </section>
  </main>
  <script>
    let state = null;
    const fmt = value => value || '—';
    const esc = value => String(value ?? '').replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
    async function postJson(url, payload) {
      const res = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      const text = await res.text();
      let data = {};
      try { data = text ? JSON.parse(text) : {}; } catch { data = {detail:text}; }
      if (!res.ok) throw new Error(data.detail || text || res.statusText);
      return data;
    }
    function payloadFromInputs(prefix='') {
      return {
        meeting_url: document.querySelector(prefix + '[name="meeting_url"]').value.trim(),
        title: document.querySelector(prefix + '[name="title"]').value.trim() || null,
        source: 'node-dashboard'
      };
    }
    async function startAny(ev) {
      ev.preventDefault();
      const msg = document.getElementById('message');
      msg.className = 'muted'; msg.textContent = 'Отправляю задачу...';
      try {
        const data = await postJson('/api/v1/bots/meetings', payloadFromInputs('#quickForm '));
        msg.textContent = `Задача создана: ${data.task_id || 'ok'}`;
        await load();
      } catch (e) { msg.className = 'error'; msg.textContent = e.message; }
    }
    async function startBot(globalId) {
      const url = prompt('Ссылка на встречу');
      if (!url) return;
      const title = prompt('Название встречи, необязательно') || null;
      await postJson('/api/v1/bots/' + encodeURIComponent(globalId) + '/meetings', {meeting_url:url, title, source:'node-dashboard'});
      await load();
    }
    function render(data) {
      state = data;
      document.getElementById('title').textContent = data.node_name || 'Telemost Node Dashboard';
      const s = data.summary || {};
      botsTotal.textContent = s.bots_total || 0;
      botsIdle.textContent = s.bots_idle || 0;
      botsBusy.textContent = s.bots_busy || 0;
      nodesOnline.textContent = `${s.nodes_online || 0}/${s.nodes_total || 0}`;
      const cards = [];
      for (const node of data.nodes || []) {
        if (node.status !== 'online') {
          cards.push(`<article class="bot unavailable"><h3>${esc(node.node_name)}</h3><div class="error">${esc(node.error || 'Недоступен')}</div><code>${esc(node.base_url)}</code></article>`);
          continue;
        }
        for (const bot of node.bots || []) {
          const status = bot.status || 'unknown';
          const gid = bot.global_bot_id || `${node.node_id}:${bot.bot_id}`;
          cards.push(`<article class="bot ${esc(status)}"><h3>${esc(node.node_name)}</h3>
            <div class="row"><span>Статус</span><b>${esc(status)}</b></div>
            <div class="row"><span>Бот</span><code>${esc(bot.bot_id)}</code></div>
            <div class="row"><span>Встреча</span><span>${esc(fmt(bot.title || bot.session_id))}</span></div>
            <div class="row"><span>Ссылка</span><span>${bot.meeting_url ? `<a href="${esc(bot.meeting_url)}" target="_blank">открыть</a>` : '—'}</span></div>
            <div class="actions"><button ${status === 'idle' ? '' : 'disabled'} onclick="startBot('${esc(gid)}')">Запустить здесь</button></div>
          </article>`);
        }
      }
      bots.innerHTML = cards.join('') || '<p class="muted">Боты не найдены</p>';
    }
    async function load() {
      try {
        const res = await fetch('/api/v1/node/status');
        render(await res.json());
      } catch (e) {
        bots.innerHTML = `<p class="error">${esc(e.message)}</p>`;
      }
    }
    quickForm.addEventListener('submit', startAny);
    load();
    setInterval(load, 10000);
  </script>
</body>
</html>'''


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
