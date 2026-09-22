import asyncio
import json
import sqlite3
import os
import random
import aiohttp
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://yourdomain.com").rstrip("/")
PORT = int(os.getenv("PORT", "3000"))

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ---------- База ----------
def init_db():
    conn = sqlite3.connect("movies.db"); c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, created_at TIMESTAMP)')
    conn.commit(); conn.close()

def add_user(user_id, username):
    conn = sqlite3.connect("movies.db"); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?)", (user_id, username, datetime.now()))
    conn.commit(); conn.close()

# ---------- Комнаты ----------
rooms = {}

def gen_code():
    while True:
        c = str(random.randint(100000, 999999))
        if c not in rooms: return c

def room_public(r):
    return {"code": r["code"], "name": r["name"], "video_url": r["video_url"],
            "film": r["film"], "participants": r["participants"]}

# ---------- API IMDbOT ----------
IMDB_API = "https://imdb.iamidiotareyoutoo.com"

async def api_catalog(request):
    q = request.query.get("q", "").strip()
    if not q:
        return web.json_response({"ok": True, "films": []})
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{IMDB_API}/search", params={"q": q}) as resp:
                data = await resp.json()
        # IMDbOT возвращает список результатов в разных форматах.
        # Пробуем достать массив фильмов из типичных полей.
        films = data if isinstance(data, list) else (data.get("results") or data.get("titles") or data.get("data") or [])
        return web.json_response({"ok": True, "films": films})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def api_film(request):
    tt = request.match_info.get("film_id")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{IMDB_API}/search", params={"tt": tt}) as resp:
                data = await resp.json()
        film = data if isinstance(data, dict) else (data[0] if data else {})
        return web.json_response({"ok": True, "film": film})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

# ---------- API комнат ----------
async def api_create(request):
    d = await request.json()
    code = gen_code()
    session = {
        "code": code, "name": d.get("name") or "Комната",
        "video_url": d.get("video_url") or "", "film": d.get("film"),
        "host_id": d.get("user_id"), "created_at": datetime.now().isoformat(),
        "participants": [{"user_id": d.get("user_id"), "username": d.get("username") or "guest"}],
        "state": {"video_url": d.get("video_url") or "", "time": 0, "is_playing": False},
        "clients": set()
    }
    rooms[code] = session
    return web.json_response({"ok": True, "code": code, "session": room_public(session)})

async def api_join(request):
    d = await request.json()
    code = str(d.get("code", "")).strip()
    s = rooms.get(code)
    if not s: return web.json_response({"ok": False, "error": "Комната не найдена"}, status=404)
    uid = d.get("user_id")
    if not any(p.get("user_id") == uid for p in s["participants"]):
        s["participants"].append({"user_id": uid, "username": d.get("username") or "guest"})
    return web.json_response({"ok": True, "session": room_public(s)})

# ---------- WebSocket ----------
async def ws_handler(request):
    code = request.match_info.get("code")
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    s = rooms.get(code)
    if not s:
        await ws.send_json({"type": "error", "error": "Комната не найдена"})
        await ws.close(); return ws
    s["clients"].add(ws)
    await ws.send_json({"type": "state", "state": s["state"], "members": s["participants"]})
    await broadcast_members(s)
    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT: continue
            try: data = json.loads(msg.data)
            except: continue
            t = data.get("type")
            if t == "set_video":
                s["state"]["video_url"] = data.get("url", "")
                s["state"]["time"] = 0; s["state"]["is_playing"] = False
                await broadcast(s, data, exclude=ws)
            elif t == "play":
                s["state"]["is_playing"] = True
                s["state"]["time"] = data.get("time", 0)
                await broadcast(s, data, exclude=ws)
            elif t == "pause":
                s["state"]["is_playing"] = False
                s["state"]["time"] = data.get("time", 0)
                await broadcast(s, data, exclude=ws)
            elif t == "seek":
                s["state"]["time"] = data.get("time", 0)
                await broadcast(s, data, exclude=ws)
            elif t == "chat":
                await broadcast(s, {"type":"chat","username":data.get("username","guest"),"text":data.get("text","")}, exclude=ws)
            elif t == "join":
                uid = data.get("user_id")
                if not any(p.get("user_id") == uid for p in s["participants"]):
                    s["participants"].append({"user_id": uid, "username": data.get("username") or "guest"})
                await broadcast_members(s)
    finally:
        s["clients"].discard(ws)
        await broadcast_members(s)
    return ws

async def broadcast(s, data, exclude=None):
    for c in list(s["clients"]):
        if c is exclude or c.closed: continue
        try: await c.send_json(data)
        except: s["clients"].discard(c)

async def broadcast_members(s):
    await broadcast(s, {"type": "members", "members": s["participants"]})

# ---------- Файлы ----------
async def handle_index(request):
    if os.path.exists("app.html"): return web.FileResponse("app.html")
    return web.Response(text="app.html not found", status=404)

async def handle_health(request): return web.json_response({"ok": True})

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/app.html", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/catalog", api_catalog)
    app.router.add_get("/api/film/{film_id}", api_film)
    app.router.add_post("/api/session/create", api_create)
    app.router.add_post("/api/session/join", api_join)
    app.router.add_get("/ws/{code}", ws_handler)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"🔧 Веб-сервер слушает 0.0.0.0:{PORT}", flush=True)

# ---------- Бот ----------
@dp.message(Command("start"))
async def start(message: types.Message):
    add_user(message.from_user.id, message.from_user.username or "unknown")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎬 Открыть кинотеатр",
            web_app=WebAppInfo(url=f"{WEB_APP_URL}/app.html"))
    ]])
    await message.answer("🎭 Кинотеатр\n\nБиблиотека фильмов и совместный просмотр.", reply_markup=kb)

async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await start_web()
    print("✅ Бот запущен!", flush=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
