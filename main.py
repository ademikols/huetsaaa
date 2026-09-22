import os
import json
import asyncio
import random
import string
import math
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiogram.filters import CommandStart
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEB_APP_URL = os.getenv("WEB_APP_URL", "http://localhost:3000").rstrip("/")
PORT = int(os.getenv("PORT", "3000"))
BIND_HOST = "0.0.0.0"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class Side(str, Enum):
    BOTTOM = "bottom"
    TOP = "top"

class CardType(str, Enum):
    HOG = "hog"
    MUSKETEER = "musketeer"
    ICE_SPIRIT = "ice_spirit"
    SKELETONS = "skeletons"
    ICE_GOLEM = "ice_golem"
    CANNON = "cannon"
    FIREBALL = "fireball"
    LOG = "log"

CARD_STATS = {
    CardType.HOG: {"cost": 4, "hp": 800, "damage": 150, "hit_speed": 1.6, "speed": 2.0, "range": 0.8, "target": ["ground"], "special": "building_only"},
    CardType.MUSKETEER: {"cost": 4, "hp": 340, "damage": 103, "hit_speed": 1.1, "speed": 1.0, "range": 6.0, "target": ["ground", "air"]},
    CardType.ICE_SPIRIT: {"cost": 1, "hp": 90, "damage": 43, "hit_speed": 0.3, "speed": 2.0, "range": 2.5, "target": ["ground", "air"], "special": "freeze_on_hit"},
    CardType.SKELETONS: {"cost": 1, "hp": 32, "damage": 32, "hit_speed": 1.0, "speed": 1.6, "range": 0.5, "target": ["ground", "air"], "count": 3},
    CardType.ICE_GOLEM: {"cost": 2, "hp": 565, "damage": 40, "hit_speed": 2.5, "speed": 0.8, "range": 0.6, "target": ["ground"], "special": "building_only"},
    CardType.CANNON: {"cost": 3, "hp": 1000, "damage": 60, "hit_speed": 1.0, "speed": 0, "range": 5.5, "target": ["ground"], "special": "building", "duration": 30},
    CardType.FIREBALL: {"cost": 4, "hp": 0, "damage": 325, "radius": 2.5, "special": "spell"},
    CardType.LOG: {"cost": 2, "hp": 0, "damage": 240, "radius": 3.9, "distance": 10.1, "special": "spell"},
}

DECK = [CardType.HOG, CardType.MUSKETEER, CardType.ICE_SPIRIT, CardType.SKELETONS, CardType.ICE_GOLEM, CardType.CANNON, CardType.FIREBALL, CardType.LOG]

@dataclass
class Unit:
    id: str
    owner: Side
    card_type: CardType
    x: float
    y: float
    hp: int
    max_hp: int
    frozen_until: float = 0.0
    target_id: Optional[str] = None
    next_attack_time: float = 0.0

@dataclass
class Tower:
    kind: str
    x: float
    y: float
    hp: int
    max_hp: int
    alive: bool = True
    next_attack_time: float = 0.0

@dataclass
class Effect:
    type: str
    x: float
    y: float
    radius: float
    duration: float
    start_time: float

@dataclass
class PlayerState:
    user_id: int
    username: str
    elixir: float = 0.0
    max_elixir: int = 10
    hand: List[CardType] = field(default_factory=lambda: [DECK[i % 8] for i in range(4)])
    deck_index: int = 4
    crowns: int = 0
    surrender: bool = False

@dataclass
class GameRoom:
    code: str
    players: Dict[Side, Optional[PlayerState]] = field(default_factory=dict)
    units: List[Unit] = field(default_factory=list)
    effects: List[Effect] = field(default_factory=list)
    towers: Dict[Side, Dict[str, Tower]] = field(default_factory=dict)
    start_time: float = 0.0
    last_tick_time: float = 0.0
    game_over: bool = False
    winner: Optional[Side] = None
    connections: Set[web.WebSocketResponse] = field(default_factory=set)

    def __post_init__(self):
        self.players[Side.BOTTOM] = None
        self.players[Side.TOP] = None
        self._init_towers()

    def _init_towers(self):
        self.towers[Side.BOTTOM] = {
            "princess_left": Tower("princess", 3, 28, 1400, 1400),
            "princess_right": Tower("princess", 14, 28, 1400, 1400),
            "king": Tower("king", 8.5, 30, 2400, 2400),
        }
        self.towers[Side.TOP] = {
            "princess_left": Tower("princess", 3, 3, 1400, 1400),
            "princess_right": Tower("princess", 14, 3, 1400, 1400),
            "king": Tower("king", 8.5, 1, 2400, 2400),
        }

games: Dict[str, GameRoom] = {}

def generate_code() -> str:
    return "".join(random.choices(string.digits, k=6))

def get_opponent(side: Side) -> Side:
    return Side.TOP if side == Side.BOTTOM else Side.BOTTOM

def distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)

def is_in_river(y: float) -> bool:
    return 15 <= y <= 16

def get_closest_bridge(x: float) -> Tuple[float, float]:
    bridge1_x = 3.5
    bridge2_x = 13.5
    if abs(x - bridge1_x) < abs(x - bridge2_x):
        return bridge1_x, 15.5
    else:
        return bridge2_x, 15.5

def can_place_card(side: Side, card_type: CardType, x: float, y: float) -> bool:
    if card_type == CardType.FIREBALL or card_type == CardType.LOG:
        return True
    if side == Side.BOTTOM and y <= 16:
        return True
    if side == Side.TOP and y >= 15:
        return True
    return False

def process_game_tick(game: GameRoom, current_time: float):
    dt = 0.2

    for player in game.players.values():
        if player:
            if current_time - game.start_time < 120:
                player.elixir = min(player.elixir + dt / 2.8, player.max_elixir)
            else:
                player.elixir = min(player.elixir + dt / 1.4, player.max_elixir)

    for unit in game.units[:]:
        if unit.hp <= 0:
            game.units.remove(unit)
            continue

        if unit.frozen_until > current_time:
            continue

        stats = CARD_STATS[unit.card_type]
        if unit.card_type == CardType.CANNON:
            continue

        target = None
        closest_dist = float('inf')

        if "building_only" in stats.get("special", ""):
            for side in [Side.BOTTOM, Side.TOP]:
                if side == unit.owner:
                    continue
                for tower in game.towers[side].values():
                    if tower.alive:
                        d = distance(unit.x, unit.y, tower.x, tower.y)
                        if d < closest_dist:
                            closest_dist = d
                            target = tower
        else:
            for other in game.units:
                if other.owner != unit.owner and other.hp > 0:
                    d = distance(unit.x, unit.y, other.x, other.y)
                    if d < closest_dist:
                        closest_dist = d
                        target = other

            if not target:
                for side in [Side.BOTTOM, Side.TOP]:
                    if side == unit.owner:
                        continue
                    for tower in game.towers[side].values():
                        if tower.alive:
                            d = distance(unit.x, unit.y, tower.x, tower.y)
                            if d < closest_dist:
                                closest_dist = d
                                target = tower

        if target:
            if isinstance(target, Unit):
                target_x, target_y = target.x, target.y
            else:
                target_x, target_y = target.x, target.y

            dist_to_target = distance(unit.x, unit.y, target_x, target_y)

            if dist_to_target <= stats["range"]:
                if unit.next_attack_time <= current_time:
                    if isinstance(target, Unit):
                        target.hp -= stats["damage"]
                    else:
                        tower_damage = stats["damage"]
                        if unit.card_type in [CardType.FIREBALL, CardType.LOG]:
                            tower_damage = int(stats["damage"] * 0.4)
                        target.hp -= tower_damage
                    unit.next_attack_time = current_time + stats["hit_speed"]
            else:
                angle = math.atan2(target_y - unit.y, target_x - unit.x)
                new_x = unit.x + stats["speed"] * dt * math.cos(angle)
                new_y = unit.y + stats["speed"] * dt * math.sin(angle)

                if is_in_river(new_y) and not (3 <= new_x <= 5 or 13 <= new_x <= 15):
                    bridge_x, bridge_y = get_closest_bridge(unit.x)
                    angle = math.atan2(bridge_y - unit.y, bridge_x - unit.x)
                    new_x = unit.x + stats["speed"] * dt * math.cos(angle)
                    new_y = unit.y + stats["speed"] * dt * math.sin(angle)

                unit.x = max(0, min(18, new_x))
                unit.y = max(0, min(32, new_y))

    for effect in game.effects[:]:
        if current_time - effect.start_time > effect.duration:
            game.effects.remove(effect)

    for side in [Side.BOTTOM, Side.TOP]:
        opp_side = get_opponent(side)
        for tower in game.towers[side].values():
            if not tower.alive:
                continue

            target = None
            closest_dist = float('inf')

            for unit in game.units:
                if unit.owner == opp_side and unit.hp > 0:
                    d = distance(tower.x, tower.y, unit.x, unit.y)
                    if d <= 7.5 if tower.kind == "princess" else d <= 7.0:
                        if d < closest_dist:
                            closest_dist = d
                            target = unit

            if target and tower.next_attack_time <= current_time:
                target.hp -= 90
                tower.next_attack_time = current_time + (0.8 if tower.kind == "princess" else 1.0)

        for tower in game.towers[side].values():
            if tower.hp <= 0 and tower.alive:
                tower.alive = False

    for side in [Side.BOTTOM, Side.TOP]:
        towers = game.towers[side]
        princess_alive = any(t.alive for k, t in towers.items() if k != "king")

        if not towers["king"].alive:
            opp = get_opponent(side)
            game.game_over = True
            game.winner = opp
            game.players[opp].crowns += 1

        if not princess_alive and towers["king"].alive:
            towers["king"].alive = True

def get_game_state(game: GameRoom) -> dict:
    return {
        "type": "state",
        "state": {
            "players": {
                "bottom": {"elixir": round(game.players[Side.BOTTOM].elixir, 1), "hand": [str(c) for c in game.players[Side.BOTTOM].hand]} if game.players[Side.BOTTOM] else None,
                "top": {"elixir": round(game.players[Side.TOP].elixir, 1), "hand": [str(c) for c in game.players[Side.TOP].hand]} if game.players[Side.TOP] else None,
            },
            "units": [{"id": u.id, "owner": u.owner, "card": str(u.card_type), "x": round(u.x, 2), "y": round(u.y, 2), "hp": u.hp, "max_hp": u.max_hp, "frozen": u.frozen_until > asyncio.get_event_loop().time()} for u in game.units],
            "effects": [{"type": e.type, "x": round(e.x, 2), "y": round(e.y, 2), "radius": e.radius} for e in game.effects],
            "towers": {
                "bottom": {k: {"kind": v.kind, "x": round(v.x, 2), "y": round(v.y, 2), "hp": v.hp, "max_hp": v.max_hp, "alive": v.alive} for k, v in game.towers[Side.BOTTOM].items()},
                "top": {k: {"kind": v.kind, "x": round(v.x, 2), "y": round(v.y, 2), "hp": v.hp, "max_hp": v.max_hp, "alive": v.alive} for k, v in game.towers[Side.TOP].items()},
            },
            "timer": max(0, 180 - int(asyncio.get_event_loop().time() - game.start_time)),
            "crowns": {"bottom": game.players[Side.BOTTOM].crowns if game.players[Side.BOTTOM] else 0, "top": game.players[Side.TOP].crowns if game.players[Side.TOP] else 0},
        }
    }

async def game_loop(game: GameRoom):
    loop = asyncio.get_event_loop()
    while not game.game_over:
        current_time = loop.time()
        process_game_tick(game, current_time)

        if len(game.connections) > 0:
            state_msg = get_game_state(game)
            disconnected = set()
            for ws in game.connections:
                try:
                    await ws.send_json(state_msg)
                except:
                    disconnected.add(ws)
            game.connections -= disconnected

        await asyncio.sleep(0.2)

    for ws in game.connections:
        try:
            await ws.send_json({"type": "gameover", "winner": game.winner, "reason": "king_destroyed"})
        except:
            pass

async def handle_http_request(request):
    path = request.path

    if path == "/" or path == "/app.html":
        app_html = open("/mnt/user-data/outputs/app.html", "r").read()
        return web.Response(text=app_html, content_type="text/html")

    if path == "/api/game/create":
        code = generate_code()
        while code in games:
            code = generate_code()
        games[code] = GameRoom(code=code)
        return web.json_response({"ok": True, "code": code})

    if path == "/api/game/join":
        data = await request.json()
        code = data.get("code")
        user_id = data.get("user_id")
        username = data.get("username")

        if code not in games:
            return web.json_response({"ok": False, "error": "game_not_found"}, status=404)

        game = games[code]
        if game.players[Side.BOTTOM] is None:
            game.players[Side.BOTTOM] = PlayerState(user_id=user_id, username=username)
            side = Side.BOTTOM
        elif game.players[Side.TOP] is None:
            game.players[Side.TOP] = PlayerState(user_id=user_id, username=username)
            side = Side.TOP
            game.start_time = asyncio.get_event_loop().time()
            asyncio.create_task(game_loop(game))
        else:
            return web.json_response({"ok": False, "error": "game_full"}, status=400)

        return web.json_response({"ok": True, "side": side})

    return web.Response(status=404)

async def websocket_handler(request):
    code = request.match_info.get("code")
    if code not in games:
        return web.Response(status=404)

    game = games[code]
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    game.connections.add(ws)

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get("type")

                    if msg_type == "play_card":
                        side = Side(data.get("side"))
                        card_id = data.get("card_id", 0)
                        x = float(data.get("x", 9))
                        y = float(data.get("y", 16))

                        player = game.players[side]
                        if not player or card_id >= len(player.hand):
                            continue

                        card_type = player.hand[card_id]
                        cost = CARD_STATS[card_type]["cost"]

                        if player.elixir < cost:
                            continue

                        if not can_place_card(side, card_type, x, y):
                            continue

                        player.elixir -= cost
                        next_card = DECK[player.deck_index % 8]
                        player.hand[card_id] = next_card
                        player.deck_index += 1

                        unit_id = f"{side}_{len(game.units)}_{int(asyncio.get_event_loop().time())}"
                        if card_type == CardType.CANNON:
                            new_unit = Unit(id=unit_id, owner=side, card_type=card_type, x=x, y=y, hp=CARD_STATS[card_type]["hp"], max_hp=CARD_STATS[card_type]["hp"])
                        else:
                            new_unit = Unit(id=unit_id, owner=side, card_type=card_type, x=x, y=y, hp=CARD_STATS[card_type]["hp"], max_hp=CARD_STATS[card_type]["hp"])
                        game.units.append(new_unit)

                except:
                    pass
    finally:
        game.connections.discard(ws)

async def start_bot():
    dp.message.register(start_command, CommandStart())
    await dp.start_polling(bot, allowed_updates=None)

@dp.message()
async def start_command(message):
    await message.answer(f"Click the button below to play!", reply_markup={"inline_keyboard": [[{"text": "Play Game", "web_app": {"url": WEB_APP_URL}}]]})

async def main():
    app = web.Application()
    app.router.add_get("/", handle_http_request)
    app.router.add_get("/app.html", handle_http_request)
    app.router.add_post("/api/game/create", handle_http_request)
    app.router.add_post("/api/game/join", handle_http_request)
    app.router.add_get("/ws/game/{code}", websocket_handler)

    await bot.delete_webhook(drop_pending_updates=True)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, BIND_HOST, PORT)
    await site.start()

    logger.info(f"Web server started on {BIND_HOST}:{PORT}")

    await asyncio.sleep(float('inf'))

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
