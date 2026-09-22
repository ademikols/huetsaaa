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

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://yourdomain.com").rstrip("/")
PORT = int(os.getenv("PORT", "3000"))

bot = Bot(token=TOKEN)
dp = Dispatcher()

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

def create_session(user_id, link):
    conn = sqlite3.connect("movies.db")
    c = conn.cursor()
    c.execute("INSERT INTO sessions (user_id, link, created_at, status) VALUES (?, ?, ?, ?)",
              (user_id, link, datetime.now(), "active"))
    conn.commit()
    session_id = c.lastrowid
    conn.close()
    return session_id

def get_session(session_id):
    conn = sqlite3.connect("movies.db")
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE id=?", (session_id,))
    result = c.fetchone()
    conn.close()
    return result

# ---------- Веб-сервер для Mini App ----------
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
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🔧 Веб-сервер слушает 0.0.0.0:{PORT}", flush=True)

# ---------- Хендлеры бота ----------
@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "unknown"
    add_user(user_id, username)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎬 Открыть кинотеатр",
            web_app=WebAppInfo(url=f"{WEB_APP_URL}/app.html")
        )],
        [InlineKeyboardButton(text="📋 Мои сессии", callback_data="my_sessions")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")]
    ])

    await message.answer(
        "🎭 Добро пожаловать в кинотеатр!\n\n"
        "Смотрите фильмы вместе с друзьями через ссылку из YouTube, ВКонтакте или RuTube.\n\n"
        "Жми кнопку ниже 👇",
        reply_markup=keyboard
    )

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещен")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="🎬 Сессии", callback_data="admin_sessions")],
        [InlineKeyboardButton(text="📤 Рассылка", callback_data="admin_broadcast")]
    ])

    await message.answer("⚙ Админ-панель:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    conn = sqlite3.connect("movies.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    users_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM sessions WHERE status='active'")
    sessions_count = c.fetchone()[0]
    conn.close()

    stats = f"""
📊 СТАТИСТИКА:
━━━━━━━━━━━━━━━━
👥 Всего пользователей: {users_count}
🎬 Активных сессий: {sessions_count}
⏰ Дата: {datetime.now().strftime("%d.%m.%Y %H:%M")}
"""
    await callback.message.answer(stats)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_users")
async def admin_users(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    conn = sqlite3.connect("movies.db")
    c = conn.cursor()
    c.execute("SELECT user_id, username, created_at FROM users LIMIT 20")
    users = c.fetchall()
    conn.close()

    text = "👥 ПОСЛЕДНИЕ ПОЛЬЗОВАТЕЛИ:\n━━━━━━━━━━━━━━━━\n"
    for user in users:
        text += f"ID: {user[0]} | @{user[1]} | {str(user[2])[:10]}\n"

    await callback.message.answer(text)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "my_sessions")
async def my_sessions(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    conn = sqlite3.connect("movies.db")
    c = conn.cursor()
    c.execute("SELECT id, link, created_at FROM sessions WHERE user_id=? LIMIT 10", (user_id,))
    sessions = c.fetchall()
    conn.close()

    if not sessions:
        await callback.message.answer("У тебя еще нет сессий 😔")
        await callback.answer()
        return

    text = "📋 МОИ СЕССИИ:\n━━━━━━━━━━━━━━━━\n"
    for session in sessions:
        text += f"ID: {session[0]} | {str(session[1])[:30]}... | {str(session[2])[:10]}\n"

    await callback.message.answer(text)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "help")
async def help_handler(callback: types.CallbackQuery):
    help_text = """
❓ КАК ПОЛЬЗОВАТЬСЯ:

1⃣ Жми "🎬 Открыть кинотеатр"
2⃣ Вставь ссылку на видео (YouTube/VK/RuTube)
3⃣ Получишь код сессии - поделись с друзьями
4⃣ Друзья введут код и вы будете смотреть вместе

🎬 ПОДДЕРЖИВАЕМЫЕ ИСТОЧНИКИ:
• YouTube (youtube.com, youtu.be)
• ВКонтакте (vk.com/video)
• RuTube (rutube.ru)

💡 СОВЕТЫ:
✓ Один человек создает сессию
✓ Остальные присоединяются по коду
✓ Синхронизация видео в реальном времени
✓ Чат для комментариев (скоро)

❓ ВОПРОСЫ?
Пишите @support
"""
    await callback.message.answer(help_text)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_broadcast")
async def admin_broadcast(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    await callback.message.answer("📤 Введи сообщение для рассылки (или /cancel):")
    await callback.answer()

# ---------- Запуск ----------
async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await start_web()
    print("✅ Бот запущен!", flush=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
