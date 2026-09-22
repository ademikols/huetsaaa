import asyncio
import json
import sqlite3
import os
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

# ---------- База данных ----------
def init_db():
    conn = sqlite3.connect("movies.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS sessions
                 (id INTEGER PRIMARY KEY, user_id INTEGER, link TEXT, created_at TIMESTAMP, status TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, created_at TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins
                 (user_id INTEGER PRIMARY KEY)''')
    conn.commit()
    conn.close()

def add_user(user_id, username):
    conn = sqlite3.connect("movies.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?)",
              (user_id, username, datetime.now()))
    conn.commit()
    conn.close()

# ---------- API для Mini App ----------
async def handle_catalog(request):
    q = request.query.get("q", "").strip()
    genre = request.query.get("genre", "").strip()
    page = int(request.query.get("page", "1"))
    try:
        if q:
            data = kino.search(q)
            films = data.get("films", [])
        else:
            params = {"page": page}
            if genre:
                params["genre"] = genre
            data = kino.catalog(**params)
            films = data.get("films", [])
        return web.json_response({"ok": True, "films": films})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def handle_film(request):
    film_id = request.match_info.get("film_id")
    try:
        data = kino.get_film(film_id)
        return web.json_response({"ok": True, "film": data.get("film", {})})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

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
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🔧 Веб-сервер слушает 0.0.0.0:{PORT}", flush=True)

# ---------- Бот ----------
@dp.message(Command("start"))
async def start(message: types.Message):
    add_user(message.from_user.id, message.from_user.username or "unknown")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎬 Открыть кинотеатр",
            web_app=WebAppInfo(url=f"{WEB_APP_URL}/app.html")
        )],
        [InlineKeyboardButton(text="📋 Мои сессии", callback_data="my_sessions")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")]
    ])
    await message.answer(
        "🎭 Добро пожаловать в кинотеатр!\n\n"
        "Выбирай фильм из каталога и смотри вместе с друзьями.\n\n"
        "Жми кнопку ниже 👇",
        reply_markup=kb
    )

@dp.callback_query(lambda c: c.data == "my_sessions")
async def my_sessions(callback: types.CallbackQuery):
    conn = sqlite3.connect("movies.db")
    c = conn.cursor()
    c.execute("SELECT id, link, created_at FROM sessions WHERE user_id=? LIMIT 10", (callback.from_user.id,))
    sessions = c.fetchall()
    conn.close()
    if not sessions:
        await callback.message.answer("У тебя еще нет сессий 😔")
    else:
        text = "📋 МОИ СЕССИИ:\n━━━━━━━━━━━━━━━━\n"
        for s in sessions:
            text += f"ID: {s[0]} | {str(s[1])[:30]}... | {str(s[2])[:10]}\n"
        await callback.message.answer(text)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "help")
async def help_handler(callback: types.CallbackQuery):
    await callback.message.answer(
        "❓ КАК ПОЛЬЗОВАТЬСЯ:\n\n"
        "1️⃣ Открой кинотеатр\n"
        "2️⃣ Найди фильм в каталоге\n"
        "3️⃣ Нажми «Смотреть вместе»\n"
        "4️⃣ Поделись ссылкой с друзьями"
    )
    await callback.answer()

async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await start_web()
    print("✅ Бот запущен!", flush=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
