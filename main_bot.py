import os
import json
import asyncio
import threading
from twitchio.ext import commands
from flask import Flask, send_from_directory
from flask_socketio import SocketIO

# =========================
#  Konfiguráció
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
GENERAL = CONFIG.get("general", {})
HTTP_PORT = int(GENERAL.get("HTTP_PORT", 8000))

if not TOKEN or not CHANNEL:
    print("❌ Hiányzik a TWITCH_TOKEN vagy TWITCH_CHANNEL (env vagy config.json)!")
    input()
    raise SystemExit

print(f"🔹 TOKEN és CHANNEL betöltve: {CHANNEL}")

loop = asyncio.get_event_loop()

# =========================
#  Twitch bot
# =========================
bot = commands.Bot(
    token=TOKEN,
    prefix="!",
    initial_channels=[CHANNEL],
    loop=loop
)

@bot.command(name="jatekok")
async def games_list(ctx):
    await ctx.send("🎮 Elérhető játékok: akasztofa, amoeba")

# =========================
#  Flask + SocketIO szerver
# =========================
app = Flask(__name__, static_folder="overlay")
socketio = SocketIO(app, cors_allowed_origins="*")

@app.route('/')
def index():
    return "Twitch bot és overlay szerver fut 🎮"

@app.route('/<path:filename>')
def serve_overlay(filename):
    overlay_root = os.path.join(os.getcwd(), "overlay")
    return send_from_directory(overlay_root, filename)

@socketio.on('connect')
def handle_connect():
    print("🟢 Overlay csatlakozott WebSocketen")

# =========================
#  Heartbeat + fő indítás
# =========================
async def heartbeat():
    while True:
        print("💓 Bot él és fut Renderen...")
        await asyncio.sleep(15)

async def main():
    print("✅ main_bot.py elindult Renderen")

    # Flask szerver külön szálon
    threading.Thread(
        target=lambda: socketio.run(app, host="0.0.0.0", port=HTTP_PORT, allow_unsafe_werkzeug=True),
        daemon=True
    ).start()

    # Heartbeat elindítása
    loop.create_task(heartbeat())

    # Automatikus modulbetöltés a /games könyvtárból
    import importlib
    try:
        for folder in os.listdir("games"):
            module_path = f"games.{folder}.bot"
            if os.path.exists(f"games/{folder}/bot.py"):
                try:
                    bot.load_module(module_path)
                    print(f"[✅] {folder} modul automatikusan betöltve.")
                except Exception as e:
                    print(f"[⚠️] Hiba a {folder} modul betöltésénél: {e}")
    except Exception as e:
        print(f"[❌] Modulok automatikus betöltése nem sikerült: {e}")

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
