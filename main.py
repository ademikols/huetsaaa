import asyncio
import json
import sqlite3
import os
import random
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv
from kino_is import KinoIs

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://yourdomain.com").rstrip("/")
PORT = int(os.getenv("PORT", "3000"))

bot = Bot(token=TOKEN)
dp = Dispatcher()
kino = KinoIs(lang="ru")

# ---------- База ----------
def init_db():
    conn = sqlite3.connect("movies.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS sessions
                 (id INTEGER PRIMARY KEY, user_id INTEGER, link TEXT, created_at TIMESTAMP, status TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, created_at TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
    conn.commit()
    conn.close()

def add_user(user_id, username):
    conn = sqlite3.connect("movies.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?)", (user_id, username, datetime.now()))
    conn.commit()
    conn.close()

# ---------- Комнаты (в памяти) ----------
rooms_store = {}

def gen_code():
    while True:
        code = str(random.randint(100000, 999999))
        if code not in rooms_store:
            return code

# ---------- API ----------
async def handle_catalog(request):
    q = request.query.get("q", "").strip()
    genre = request.query.get("genre", "").strip()
    page = int(request.query.get("page", "1"))
    try:
        if q:
            data = kino.search(q)
            films = data.get("films", []) if isinstance(data, dict) else data
        else:
            params = {"page": page}
            if genre:
                params["genre"] = genre
            data = kino.catalog(**params)
            films = data.get("films", []) if isinstance(data, dict) else data
        return web.json_response({"ok": True, "films": films})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def handle_film(request):
    fid = request.match_info.get("film_id")
    try:
        data = kino.get_film(fid)
        film = data.get("film", {}) if isinstance(data, dict) else data
        return web.json_response({"ok": True, "film": film})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def handle_session_create(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    code = gen_code()
    session = {
        "code": code,
        "name": data.get("name") or "Комната",
        "film": data.get("film"),
        "host_id": data.get("user_id"),
        "created_at": datetime.now().isoformat(),
        "participants": [{
            "user_id": data.get("user_id"),
            "username": data.get("username") or "guest",
            "joined_at": datetime.now().isoformat()
        }]
    }
    rooms_store[code] = session
    print(f"🎬 Комната {code} создана: {session['name']}", flush=True)
    return web.json_response({"ok": True, "code": code, "session": session})

async def handle_session_join(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    code = str(data.get("code", "")).strip()
    session = rooms_store.get(code)
    if not session:
        return web.json_response({"ok": False, "error": "Комната не найдена"}, status=404)
    uid = data.get("user_id")
    if not any(p.get("user_id") == uid for p in session["participants"]):
        session["participants"].append({
            "user_id": uid,
            "username": data.get("username") or "guest",
            "joined_at": datetime.now().isoformat()
        })
    return web.json_response({"ok": True, "session": session})

async def handle_session_get(request):
    code = request.match_info.get("code")
    session = rooms_store.get(code)
    if not session:
        return web.json_response({"ok": False, "error": "Комната не найдена"}, status=404)
    return web.json_response({"ok": True, "session": session})

async def handle_index(request):
    if os.path.exists("app.html"):
        return web.FileResponse("app.html")
    return web.Response(text="app.html not found", status=404)

async def handle_health(request):
    return web.json_response({"ok": True})

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/app.html", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/catalog", handle_catalog)
    app.router.add_get("/api/film/{film_id}", handle_film)
    app.router.add_post("/api/session/create", handle_session_create)
    app.router.add_post("/api/session/join", handle_session_join)
    app.router.add_get("/api/session/{code}", handle_session_get)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"🔧 Веб-сервер слушает 0.0.0.0:{PORT}", flush=True)

# ---------- Бот ----------
@dp.message(Command("start"))
async def start(message: types.Message):
    add_user(message.from_user.id, message.from_user.username or "unknown")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎬 Открыть кинотеатр",
            web_app=WebAppInfo(url=f"{WEB_APP_URL}/app.html")
        )]
    ])
    await message.answer(
        "🎭 Кинотеатр\n\nВыбирай фильм из каталога и создавай комнату для совместного просмотра.",
        reply_markup=kb
    )

async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await start_web()
    print("✅ Бот запущен!", flush=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
