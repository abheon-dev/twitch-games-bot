import os
import sys
import json
import asyncio
import importlib
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer
from twitchio.ext import commands
from websockets import serve

# =========================
#  KONFIGURÁCIÓ
# =========================
CONFIG_PATH = "config.json"
if not os.path.exists(CONFIG_PATH):
    print("❌ Nincs config.json!")
    input()
    raise SystemExit

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

TOKEN = os.getenv("TWITCH_TOKEN") or CONFIG.get("TWITCH_TOKEN")
CHANNEL = os.getenv("TWITCH_CHANNEL") or CONFIG.get("TWITCH_CHANNEL")

# 🔽 Ide jönnek az újak:
CLIENT_ID = os.getenv("CLIENT_ID") or CONFIG.get("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET") or CONFIG.get("CLIENT_SECRET")
BOT_ID = os.getenv("BOT_ID") or CONFIG.get("BOT_ID")

GENERAL = CONFIG.get("general", {})
HTTP_PORT = int(GENERAL.get("HTTP_PORT", 8000))
WS_PORT = int(GENERAL.get("WS_PORT", 8765))

if not TOKEN or not CHANNEL:
    print("❌ Hiányzik a TWITCH_TOKEN vagy TWITCH_CHANNEL (env vagy config.json)!")
    input()
    raise SystemExit

print(f"🔹 TOKEN és CHANNEL betöltve: {CHANNEL}")

# =========================
#  EVENT LOOP FIX (Win)
# =========================
try:
    asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

loop = asyncio.get_event_loop()

# =========================
#  HOST API a játékoknak
# =========================
class HostAPI:
    def __init__(self, ws_queue, overlay_root="overlay"):
        self.ws_queue = ws_queue
        self.overlay_root = overlay_root
        os.makedirs(self.overlay_root, exist_ok=True)

    def _ensure_game_dir(self, game_name: str):
        path = os.path.join(self.overlay_root, game_name)
        os.makedirs(path, exist_ok=True)
        return path

    def overlay_write(self, game_name: str, data: dict, trigger_refresh: bool = True):
        """data.json kiírása a megfelelő játék overlay mappájába + opcionális refresh jelzés."""
        dirp = self._ensure_game_dir(game_name)
        datap = os.path.join(dirp, "data.json")
        with open(datap, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if trigger_refresh:
            self.ws_broadcast({"event": "refresh", "game": game_name})

    def ws_broadcast(self, payload):
        try:
            msg = payload if isinstance(payload, str) else json.dumps(payload)
            self.ws_queue.put_nowait(msg)
        except Exception:
            pass


# =========================
#  WS szerver (központi)
# =========================
_ws_clients = set()
_ws_queue: asyncio.Queue[str] = asyncio.Queue()

async def _ws_handler(ws):
    _ws_clients.add(ws)
    try:
        while True:
            # Ez a szerver csak "push"-ra szolgál; itt nem várunk bejövő üzeneteket.
            await asyncio.sleep(60)
    finally:
        _ws_clients.discard(ws)

async def _ws_broadcaster():
    while True:
        msg = await _ws_queue.get()
        for c in list(_ws_clients):
            try:
                await c.send(msg)
            except Exception:
                _ws_clients.discard(c)

async def _ws_server():
    async with serve(_ws_handler, "0.0.0.0", WS_PORT):
        print(f"🧩 WebSocket fut: ws://127.0.0.1:{WS_PORT}/")
        await asyncio.Future()

# =========================
#  HTTP szerver (központi)
#   – gyökér: /overlay
#   – pl.:  http://127.0.0.1:8000/temeto.html
# =========================
def _http_server():
    root = os.path.join(os.getcwd(), "overlay")
    if os.path.exists(root):
        os.chdir(root)
    httpd = HTTPServer(("0.0.0.0", HTTP_PORT), SimpleHTTPRequestHandler)
    print(f"🌐 HTTP fut: http://127.0.0.1:{HTTP_PORT}/  (gyökér: {root})")
    httpd.serve_forever()

# =========================
#  Twitch bot
# =========================
bot = commands.Bot(
    token=TOKEN,
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    bot_id=BOT_ID,
    prefix="!",
    loop=loop
)

# ✅ Debug logoló event — EZT adtuk hozzá
@bot.event
async def event_ready():
    print(f"✅ Bot bejelentkezett: {bot.nick}")
    print(f"📡 Csatlakozott csatornák: {bot.connected_channels}")

# Host API példány, amit a játékok megkapnak a boton keresztül
bot.host = HostAPI(_ws_queue)

LOADED_GAMES = {}

@bot.command(name="jatekok")
async def list_games(ctx):
    available = ", ".join(LOADED_GAMES.keys()) or "Nincsenek betöltött játékok."
    await ctx.send(f"🎮 Elérhető játékok: {available}")

@bot.command(name="indit")
async def start_game(ctx, game_name: str):
    game_name = game_name.strip().lower()
    if game_name not in LOADED_GAMES:
        await ctx.send(f"❌ Nincs ilyen játék betöltve: {game_name}")
        return
    game_module = LOADED_GAMES[game_name]
    try:
        if hasattr(game_module, "prepare"):
            # Itt kapja meg a játék a host API-t (bot.host) és itt regisztráljuk a parancsokat
            game_module.prepare(bot)
            await ctx.send(f"✅ {game_name.capitalize()} játék elindítva!")
        elif hasattr(game_module, "run_game"):
            asyncio.create_task(game_module.run_game(bot, ctx, CONFIG.get(game_name, {})))
            await ctx.send(f"✅ {game_name.capitalize()} játék elindítva!")
        else:
            await ctx.send(f"⚠️ A {game_name} modulban nincs 'prepare' vagy 'run_game'!")
    except Exception as e:
        await ctx.send(f"💥 Hiba a {game_name} indításakor: {e}")
        print(f"[HIBA] {e}")

# =========================
#  Játékok betöltése
# =========================
GAMES_DIR = "games"
for folder in os.listdir(GAMES_DIR):
    path = os.path.join(GAMES_DIR, folder)
    if not os.path.isdir(path) or folder == "__pycache__":
        continue
    try:
        module_path = f"{GAMES_DIR}.{folder}.bot"
        game = importlib.import_module(module_path)
        LOADED_GAMES[folder] = game
        print(f"[✅] {folder} betöltve.")
    except Exception as e:
        print(f"[⚠️] Nem sikerült betölteni: {folder} → {e}")
# =========================
#  Indítás
# =========================
async def heartbeat():
    while True:
        print("💓 Bot él és fut Renderen...")
        await asyncio.sleep(15)

async def main():
    print("✅ main_bot.py elindult Renderen")

    # HTTP szerver külön szálon
    loop.create_task(asyncio.to_thread(_http_server))

    # Heartbeat üzenetek
    loop.create_task(heartbeat())

    # Twitch bot indítása
    print("🚀 Bot indul, Twitch kapcsolat kezdeményezése...")
    await bot.start()

if __name__ == "__main__":
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("🛑 Leállítás...")
    except Exception as e:
        print(f"❌ Hiba a főindítás során: {e}")
