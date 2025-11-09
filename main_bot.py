import os
import asyncio
import threading
from twitchio.ext import commands
from flask import Flask
import socketio

# =========================
#  Beállítások
# =========================
TOKEN = os.getenv("TOKEN")
CHANNEL = os.getenv("CHANNEL")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

loop = asyncio.get_event_loop()
HTTP_PORT = 8000

# =========================
#  Flask + SocketIO
# =========================
app = Flask(__name__)
sio = socketio.Server(async_mode="threading", cors_allowed_origins="*")
app.wsgi_app = socketio.WSGIApp(sio, app.wsgi_app)

@app.route("/")
def home():
    return "Bot online és fut a Renderen!"

# =========================
#  Twitch Bot
# =========================
bot = commands.Bot(
    token=TOKEN,
    client_id=CLIENT_ID,
    nick=CHANNEL,
    prefix="!",
    initial_channels=[CHANNEL]
)

# =========================
#  Modulok betöltése
# =========================
try:
    for folder in os.listdir("games"):
        if os.path.exists(f"games/{folder}/bot.py"):
            bot.load_module(f"games.{folder}.bot")
            print(f"[✅] {folder} betöltve.")
except Exception as e:
    print(f"[⚠️] Hiba a modulok betöltésénél: {e}")

# =========================
#  Heartbeat
# =========================
async def heartbeat():
    while True:
        print("💓 Bot él és fut Renderen...")
        await asyncio.sleep(15)

# =========================
#  Indítás
# =========================
async def main():
    print("✅ main_bot.py elindult Renderen")

    # Flask külön szálon (a működő rész!)
    threading.Thread(
        target=lambda: sio.run(app, host="0.0.0.0", port=HTTP_PORT, allow_unsafe_werkzeug=True),
        daemon=True
    ).start()

    # Heartbeat
    loop.create_task(heartbeat())

    print("🚀 Bot indul, Twitch kapcsolat kezdeményezése...")
    await bot.start()

if __name__ == "__main__":
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("🛑 Leállítás...")
    except Exception as e:
        print(f"❌ Hiba a főindítás során: {e}")
