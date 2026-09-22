import asyncio
import json
import os
import random
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://yourdomain.com").rstrip("/")
PORT = int(os.getenv("PORT", "3000"))

bot = Bot(token=TOKEN)
dp = Dispatcher()

games = {}

CARDS = [
    {"id": 1, "name": "Гоблин", "cost": 2, "atk": 8,  "hp": 15, "speed": 1},
    {"id": 2, "name": "Лучник", "cost": 3, "atk": 12, "hp": 20, "speed": 1},
    {"id": 3, "name": "Рыцарь", "cost": 4, "atk": 18, "hp": 35, "speed": 1},
    {"id": 4, "name": "Гигант", "cost": 5, "atk": 25, "hp": 60, "speed": 1},
]

def new_game(code, host_id, host_name):
    return {
        "code": code,
        "players": {
            "white": {"user_id": host_id, "username": host_name, "elixir": 5.0, "tower_hp": 100},
            "black": None,
        },
        "units": [],
        "clients": set(),
        "last_tick": datetime.now().timestamp(),
    }

def gen_code():
    while True:
        c = str(random.randint(100000, 999999))
        if c not in games:
            return c

async def api_create(request):
    d = await request.json()
    code = gen_code()
    games[code] = new_game(code, d.get("user_id"), d.get("username") or "guest")
    return web.json_response({"ok": True, "code": code})

async def api_join(request):
    d = await request.json()
    code = str(d.get("code", "")).strip()
    g = games.get(code)
    if not g:
        return web.json_response({"ok": False, "error": "Игра не найдена"}, status=404)
    if g["players"]["black"]:
        return web.json_response({"ok": False, "error": "Игра заполнена"}, status=400)
    g["players"]["black"] = {
        "user_id": d.get("user_id"),
        "username": d.get("username") or "guest",
        "elixir": 5.0,
        "tower_hp": 100,
    }
    return web.json_response({"ok": True, "code": code})

async def ws_game(request):
    code = request.match_info.get("code")
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    g = games.get(code)
    if not g:
        await ws.send_json({"type": "error", "error": "Игра не найдена"})
        await ws.close()
        return ws
    g["clients"].add(ws)
    await ws.send_json({"type": "state", "state": public_state(g)})
    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except Exception:
                continue
            if data.get("type") == "play_card":
                player = data.get("player")
                card_id = data.get("card_id")
                p = g["players"].get(player)
                if not p:
                    continue
                card = None
                for c in CARDS:
                    if c["id"] == card_id:
                        card = c
                        break
                if not card:
                    continue
                if p["elixir"] < card["cost"]:
                    continue
                p["elixir"] -= card["cost"]
                start_pos = 1 if player == "white" else 8
                g["units"].append({
                    "owner": player,
                    "pos": start_pos,
                    "hp": card["hp"],
                    "atk": card["atk"],
                    "speed": card["speed"],
                    "name": card["name"],
                })
                await broadcast(g, {"type": "state", "state": public_state(g)})
    finally:
        g["clients"].discard(ws)
    return ws

def public_state(g):
    white_p = g["players"]["white"]
    black_p = g["players"]["black"]
    return {
        "players": {
            "white": {"username": white_p["username"], "elixir": round(white_p["elixir"], 1), "tower_hp": white_p["tower_hp"]} if white_p else None,
            "black": {"username": black_p["username"], "elixir": round(black_p["elixir"], 1), "tower_hp": black_p["tower_hp"]} if black_p else None,
        },
        "units": g["units"],
    }

async def broadcast(g, data, exclude=None):
    for c in list(g["clients"]):
        if c is exclude or c.closed:
            continue
        try:
            await c.send_json(data)
        except Exception:
            g["clients"].discard(c)

async def game_loop():
    sides = ["white", "black"]
    while True:
        await asyncio.sleep(0.5)
        now = datetime.now().timestamp()
        for code in list(games.keys()):
            g = games.get(code)
            if not g:
                continue
            dt = now - g["last_tick"]
            g["last_tick"] = now
            # Эликсир
            for side in sides:
                p = g["players"][side]
                if p:
                    p["elixir"] = min(10.0, p["elixir"] + dt * 0.5)
            # Движение
            for u in g["units"]:
                if u["owner"] == "white":
                    u["pos"] += u["speed"]
                else:
                    u["pos"] -= u["speed"]
            # Бой
            dead = set()
            n = len(g["units"])
            for i in range(n):
                if i in dead:
                    continue
                a = g["units"][i]
                for j in range(i + 1, n):
                    if j in dead:
                        continue
                    b = g["units"][j]
                    if a["owner"] == b["owner"]:
                        continue
                    if abs(a["pos"] - b["pos"]) <= 1:
                        b["hp"] -= a["atk"]
                        a["hp"] -= b["atk"]
                        if b["hp"] <= 0:
                            dead.add(j)
                        if a["hp"] <= 0:
                            dead.add(i)
                            break
            # Башни
            for i in range(len(g["units"])):
                if i in dead:
                    continue
                u = g["units"][i]
                if u["owner"] == "white" and u["pos"] >= 9:
                    if g["players"]["black"]:
                        g["players"]["black"]["tower_hp"] -= u["atk"]
                    dead.add(i)
                elif u["owner"] == "black" and u["pos"] <= 0:
                    if g["players"]["white"]:
                        g["players"]["white"]["tower_hp"] -= u["atk"]
                    dead.add(i)
            g["units"] = [u for i, u in enumerate(g["units"]) if i not in dead]
            # Проверка конца
            w_hp = g["players"]["white"]["tower_hp"] if g["players"]["white"] else 100
            b_hp = g["players"]["black"]["tower_hp"] if g["players"]["black"] else 100
            if w_hp <= 0 or b_hp <= 0:
                winner = "black" if w_hp <= 0 else "white"
                await broadcast(g, {"type": "gameover", "winner": winner})
                games.pop(code, None)
                continue
            if g["clients"]:
                await broadcast(g, {"type": "state", "state": public_state(g)})

async def handle_index(request):
    if os.path.exists("app.html"):
        return web.FileResponse("app.html")
    return web.Response(text="app.html not found", status=404)

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/app.html", handle_index)
    app.router.add_post("/api/game/create", api_create)
    app.router.add_post("/api/game/join", api_join)
    app.router.add_get("/ws/game/{code}", ws_game)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"🔧 Веб-сервер слушает 0.0.0.0:{PORT}", flush=True)
    asyncio.create_task(game_loop())

@dp.message(Command("start"))
async def start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚔ Играть", web_app=WebAppInfo(url=f"{WEB_APP_URL}/app.html"))
    ]])
    await message.answer("⚔ Clash Mini\n\nСоздай игру или присоединись по коду.", reply_markup=kb)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await start_web()
    print("✅ Бот запущен!", flush=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
