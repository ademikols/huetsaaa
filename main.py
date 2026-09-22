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

# Поле: 8 в ширину, 14 в высоту. Река на строках 6-7.
FIELD_W = 8
FIELD_H = 14

# Карты Hog 2.6
# target: 'all' | 'buildings' | 'ground'
# range в клетках. speed в клетках/сек.
CARDS = {
    "hog":       {"name": "Хог Райдер",   "cost": 4, "type": "troop", "target": "buildings", "hp": 800, "atk": 150, "speed": 1.6, "range": 0.6, "hit_speed": 1.6, "count": 1},
    "musketeer": {"name": "Мушкетёр",     "cost": 4, "type": "troop", "target": "all",       "hp": 500, "atk": 110, "speed": 1.0, "range": 5.5, "hit_speed": 1.0, "count": 1},
    "ice_spirit":{"name": "Ледяной Дух",  "cost": 1, "type": "troop", "target": "all",       "hp": 100, "atk": 50,  "speed": 2.2, "range": 0.5, "hit_speed": 0.1, "count": 1, "freeze": 1.5},
    "skeletons": {"name": "Скелеты",      "cost": 1, "type": "troop", "target": "all",       "hp": 60,  "atk": 30,  "speed": 1.6, "range": 0.5, "hit_speed": 1.0, "count": 3},
    "ice_golem": {"name": "Ледяной Голем","cost": 2, "type": "troop", "target": "buildings", "hp": 600, "atk": 70,  "speed": 1.1, "range": 0.6, "hit_speed": 2.5, "count": 1, "death_slow": 2.0},
    "cannon":    {"name": "Пушка",        "cost": 3, "type": "building","target": "ground",   "hp": 700, "atk": 130, "speed": 0,   "range": 5.5, "hit_speed": 1.0, "count": 1, "lifetime": 30},
    "fireball":  {"name": "Огн. Шар",     "cost": 4, "type": "spell",  "target": "all",       "atk": 300, "radius": 2.5},
    "log":       {"name": "Бревно",       "cost": 2, "type": "spell",  "target": "ground",    "atk": 100, "radius": 1.0},
}

def new_game(code, host_id, host_name):
    return {
        "code": code,
        "players": {
            "bottom": {"user_id": host_id, "username": host_name, "elixir": 5.0, "towers": make_towers("bottom")},
            "top": None,
        },
        "units": [],
        "effects": [],
        "next_id": 1,
        "clients": set(),
        "last_tick": datetime.now().timestamp(),
        "ended": False,
    }

def make_towers(side):
    if side == "bottom":
        return [
            {"kind": "princess", "x": 2, "y": 11.5, "hp": 1400, "max_hp": 1400, "atk": 80, "range": 5.5, "hit_speed": 0.8, "last_hit": 0},
            {"kind": "princess", "x": 5, "y": 11.5, "hp": 1400, "max_hp": 1400, "atk": 80, "range": 5.5, "hit_speed": 0.8, "last_hit": 0},
        ]
    return [
        {"kind": "princess", "x": 2, "y": 2.5, "hp": 1400, "max_hp": 1400, "atk": 80, "range": 5.5, "hit_speed": 0.8, "last_hit": 0},
        {"kind": "princess", "x": 5, "y": 2.5, "hp": 1400, "max_hp": 1400, "atk": 80, "range": 5.5, "hit_speed": 0.8, "last_hit": 0},
    ]

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
    if g["players"]["top"]:
        return web.json_response({"ok": False, "error": "Игра заполнена"}, status=400)
    g["players"]["top"] = {"user_id": d.get("user_id"), "username": d.get("username") or "guest", "elixir": 5.0, "towers": make_towers("top")}
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
            t = data.get("type")
            if t == "play_card":
                await handle_play_card(g, data)
    finally:
        g["clients"].discard(ws)
    return ws

async def handle_play_card(g, data):
    side = data.get("side")
    card_id = data.get("card_id")
    x = float(data.get("x", 0))
    y = float(data.get("y", 0))
    p = g["players"].get(side)
    card = CARDS.get(card_id)
    if not p or not card:
        return
    if p["elixir"] < card["cost"]:
        return
    # Проверка зоны: bottom играет в нижней половине, top — в верхней
    if side == "bottom" and y < FIELD_H / 2 - 0.5:
        return
    if side == "top" and y > FIELD_H / 2 + 0.5:
        return
    p["elixir"] -= card["cost"]

    if card["type"] == "spell":
        # Мгновенный урон
        radius = card["radius"]
        for u in list(g["units"]):
            dx = u["x"] - x
            dy = u["y"] - y
            if dx * dx + dy * dy <= radius * radius:
                u["hp"] -= card["atk"]
        g["effects"].append({"type": "spell", "card": card_id, "x": x, "y": y, "radius": radius, "life": 0.6})
        await broadcast(g, {"type": "state", "state": public_state(g)})
        return

    # Войска / здание
    count = card.get("count", 1)
    for i in range(count):
        offset = 0
        if count > 1:
            offset = (i - (count - 1) / 2) * 0.5
        g["units"].append({
            "id": g["next_id"],
            "owner": side,
            "card": card_id,
            "x": x + offset,
            "y": y,
            "hp": card["hp"],
            "max_hp": card["hp"],
            "atk": card.get("atk", 0),
            "speed": card.get("speed", 0),
            "range": card.get("range", 0.5),
            "hit_speed": card.get("hit_speed", 1.0),
            "target": card.get("target", "all"),
            "type": card.get("type", "troop"),
            "lifetime": card.get("lifetime", 0),
            "frozen": 0,
            "last_hit": 0,
        })
        g["next_id"] += 1

    await broadcast(g, {"type": "state", "state": public_state(g)})

def public_state(g):
    return {
        "players": {
            "bottom": g["players"]["bottom"] and {
                "username": g["players"]["bottom"]["username"],
                "elixir": round(g["players"]["bottom"]["elixir"], 1),
                "towers": g["players"]["bottom"]["towers"],
            },
            "top": g["players"]["top"] and {
                "username": g["players"]["top"]["username"],
                "elixir": round(g["players"]["top"]["elixir"], 1),
                "towers": g["players"]["top"]["towers"],
            },
        },
        "units": g["units"],
        "effects": g["effects"],
        "ended": g["ended"],
    }

async def broadcast(g, data, exclude=None):
    for c in list(g["clients"]):
        if c is exclude or c.closed:
            continue
        try:
            await c.send_json(data)
        except Exception:
            g["clients"].discard(c)

def dist(a, b):
    dx = a["x"] - b["x"]
    dy = a["y"] - b["y"]
    return (dx * dx + dy * dy) ** 0.5

def find_target(g, u):
    """Найти ближайшую цель для юнита с учётом его target-фильтра."""
    enemies = []
    opp = "top" if u["owner"] == "bottom" else "bottom"
    if g["players"][opp]:
        for t in g["players"][opp]["towers"]:
            enemies.append(t)
    for other in g["units"]:
        if other["owner"] == u["owner"]:
            continue
        if other["type"] == "building":
            enemies.append(other)
            continue
        # Юнит-цель
        if u["target"] == "buildings":
            continue
        if u["target"] == "ground" and other.get("air"):
            continue
        enemies.append(other)
    if not enemies:
        return None
    return min(enemies, key=lambda e: dist(u, e))

async def tick_game(g, dt):
    # Эликсир
    for side in ["bottom", "top"]:
        p = g["players"][side]
        if p:
            p["elixir"] = min(10.0, p["elixir"] + dt * 1 / 2.8)  # ~2.8 сек за 1 эликсир

    # Обновление эффектов
    for e in g["effects"]:
        e["life"] -= dt
    g["effects"] = [e for e in g["effects"] if e["life"] > 0]

    # Юниты
    alive = []
    for u in g["units"]:
        if u["hp"] <= 0:
            continue
        if u.get("lifetime"):
            u["lifetime"] -= dt
            if u["lifetime"] <= 0:
                continue
        if u["frozen"] > 0:
            u["frozen"] -= dt
            alive.append(u)
            continue
        if u["speed"] == 0:
            # Здание — только атакует
            u["last_hit"] -= dt
            if u["last_hit"] <= 0:
                tgt = find_target(g, u)
                if tgt and dist(u, tgt) <= u["range"]:
                    tgt["hp"] -= u["atk"]
                    u["last_hit"] = u["hit_speed"]
            alive.append(u)
            continue

        # Движение
        tgt = find_target(g, u)
        if tgt:
            d = dist(u, tgt)
            if d > u["range"]:
                # Идём к цели
                dx = tgt["x"] - u["x"]
                dy = tgt["y"] - u["y"]
                if d > 0:
                    step = u["speed"] * dt
                    u["x"] += dx / d * step
                    u["y"] += dy / d * step
            else:
                # Атака
                u["last_hit"] -= dt
                if u["last_hit"] <= 0:
                    tgt["hp"] -= u["atk"]
                    u["last_hit"] = u["hit_speed"]
                    if u["card"] == "ice_spirit":
                        tgt["frozen"] = 1.5
                        u["hp"] = 0  # умирает при атаке
        alive.append(u)

    # Смерть ледяного голема — слоу
    for u in g["units"]:
        if u["hp"] <= 0 and u["card"] == "ice_golem":
            g["effects"].append({"type": "slow", "x": u["x"], "y": u["y"], "radius": 2.0, "life": 0.5})
            for other in g["units"]:
                if other["owner"] != u["owner"] and other["hp"] > 0:
                    if dist(u, other) <= 2.0:
                        other["frozen"] = max(other.get("frozen", 0), 2.0)

    g["units"] = [u for u in alive if u["hp"] > 0]

    # Башни стреляют
    for side in ["bottom", "top"]:
        p = g["players"][side]
        if not p:
            continue
        for t in p["towers"]:
            if t["hp"] <= 0:
                continue
            t["last_hit"] -= dt
            if t["last_hit"] <= 0:
                # Найти ближайшего врага в радиусе
                opp = "top" if side == "bottom" else "bottom"
                targets = [u for u in g["units"] if u["owner"] == opp and u["hp"] > 0]
                targets = [u for u in targets if dist(t, u) <= t["range"]]
                if targets:
                    closest = min(targets, key=lambda u: dist(t, u))
                    closest["hp"] -= t["atk"]
                    t["last_hit"] = t["hit_speed"]

    # Проверка конца
    b_alive = any(t["hp"] > 0 for t in g["players"]["bottom"]["towers"]) if g["players"]["bottom"] else True
    t_alive = any(t["hp"] > 0 for t in g["players"]["top"]["towers"]) if g["players"]["top"] else True
    if not b_alive or not t_alive:
        g["ended"] = True
        winner = "top" if not b_alive else "bottom"
        await broadcast(g, {"type": "gameover", "winner": winner})
        return False
    return True

async def game_loop():
    while True:
        await asyncio.sleep(0.2)
        now = datetime.now().timestamp()
        for code in list(games.keys()):
            g = games.get(code)
            if not g or g["ended"]:
                continue
            dt = now - g["last_tick"]
            g["last_tick"] = now
            if dt > 1.0:
                dt = 0.2
            cont = await tick_game(g, dt)
            if g["clients"]:
                await broadcast(g, {"type": "state", "state": public_state(g)})
            if not cont:
                games.pop(code, None)

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
        InlineKeyboardButton(text="⚔ Hog 2.6", web_app=WebAppInfo(url=f"{WEB_APP_URL}/app.html"))
    ]])
    await message.answer("⚔ Hog 2.6 Mini\n\nСоздай игру или присоединись по коду.", reply_markup=kb)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await start_web()
    print("✅ Бот запущен!", flush=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
