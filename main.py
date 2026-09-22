import asyncio
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    WebAppInfo,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# ---------------------------------------------------------------------------
# НАСТРОЙКИ
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")

# Домен бота на Bothost БЕЗ /app.html на конце.
# Например: https://bot-1790100069-3777-kolsademi.bothost.tech
WEB_APP_URL = (os.getenv("WEB_APP_URL") or os.getenv("WEBAPP_URL") or "https://example.com").rstrip("/")
PORT = int(os.getenv("PORT", "3000"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def webapp_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚔️ Играть (Hog 2.6)",
                    web_app=WebAppInfo(url=f"{WEB_APP_URL}/app.html"),
                )
            ]
        ]
    )


def webapp_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚔️ Открыть арену", web_app=WebAppInfo(url=f"{WEB_APP_URL}/app.html"))]
        ],
        resize_keyboard=True,
    )


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Это мини-игра в стиле Clash Royale "
        "(колода Hog 2.6, 3D-поле).\n\n"
        "Нажми кнопку ниже, чтобы открыть арену.",
        reply_markup=webapp_reply_keyboard(),
    )


@dp.message(F.text == "/play")
async def cmd_play(message: Message) -> None:
    await message.answer("Запусти арену:", reply_markup=webapp_inline_keyboard())


@dp.message(F.web_app_data)
async def on_webapp_data(message: Message) -> None:
    data = message.web_app_data.data
    logger.info("Получены данные из Mini App: %s", data)
    await message.answer(f"Результат боя получен:\n<code>{data}</code>", parse_mode="HTML")


# ---------------------------------------------------------------------------
# ВЕБ-СЕРВЕР: отдаёт app.html на Bothost
# ---------------------------------------------------------------------------
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
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info("Веб-сервер слушает 0.0.0.0:%s", PORT)


async def main() -> None:
    logger.info("Бот запускается...")
    await bot.delete_webhook(drop_pending_updates=True)
    await start_web()
    logger.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
