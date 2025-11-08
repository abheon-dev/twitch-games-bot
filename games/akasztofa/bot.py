import asyncio
import json
import random
import time
import uuid
from pathlib import Path
from twitchio.ext import commands

# ===============================
# Beállítások / konstansok
# ===============================

THEMES = {
    "temeto": 5,
    "gyertya": 7,
    "akasztofa": 6,
    "szorny": 4,
    "zombik": 8,
}

# Meglévő overlay-struktúrához igazodunk (root overlay/)
OVERLAY_DIR = Path(__file__).resolve().parents[2] / "overlay"
OVERLAY_DATA = OVERLAY_DIR / "data.json"

GAME_DURATION_SECONDS = 1200  # 20 perc

DEFAULT_CONFIG = {
    "PERSONAL_TIPP_COOLDOWN": 5,
    "GLOBAL_TIPP_COOLDOWN": 3,
    "NEW_GAME_COOLDOWN": 120,
    "GAME_DURATION": 1200,
    "ANGEL_CHANCE": 8,   # %
    "DEVIL_CHANCE": 1    # %
}

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    cfg.update(data)
        except Exception:
            pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ===============================
# Állapot
# ===============================
game_active = False
game_id = None
game_starter = ""
current_theme = "temeto"
STAGES_MAX = 6

secret_word = ""
category = ""
guessed_letters = set()
wrong_items = []
last_wrong_guesser = ""
hint_used = False
state = "normal"   # 'normal' | 'angel' | stb.
bonus_life = 0

# A main_bot HostAPI-ja (itt lesz beállítva a prepare(bot)-ban)
_host_api = None


# ===============================
# Szókatalógus
# ===============================
def load_catalog():
    """
    Betölti a games/akasztofa/data/words.json fájlt.
    - JSON: {kategória: [szavak]}
    - nem JSON: "Kategória:" sorok + alattuk szavak
    """
    path = Path(__file__).resolve().parent / "data" / "words.json"
    if not path.exists():
        print(f"[❌] Nem található a szókatalógus: {path}")
        return {}

    # JSON próbálkozás
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and data:
                return data
    except json.JSONDecodeError:
        pass

    # Kategória-formátum
    print("[ℹ️] A words.json nem JSON – kategória formátumban olvasom.")
    catalog = {}
    current_cat = None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.endswith(":"):
                current_cat = line[:-1].strip()
                catalog.setdefault(current_cat, [])
            else:
                if current_cat:
                    catalog[current_cat].append(line)

    # Üres kategóriák kiszűrése
    return {k: v for k, v in catalog.items() if v}


# ===============================
# Segédfüggvények
# ===============================
def mask_word(word: str, revealed: set[str]) -> str:
    return "".join([c if (c.lower() in revealed or not c.isalpha()) else "_" for c in word])


def lives_status() -> str:
    return f"{len(wrong_items)}/{STAGES_MAX + bonus_life}"


def _ws_send(event_name: str):
    """Esemény továbbítása az overlay felé a főbot HostAPI-ján át."""
    if _host_api:
        _host_api.ws_broadcast({"event": event_name, "game": "akasztofa"})
        # print(f"[Overlay] Esemény elküldve: {event_name}")
    else:
        print(f"[Overlay] HostAPI nincs inicializálva – kihagyva: {event_name}")


def _overlay_write(payload: dict, do_refresh: bool = True):
    """data.json kiírás + opcionális azonnali refresh."""
    OVERLAY_DIR.mkdir(parents=True, exist_ok=True)
    with open(OVERLAY_DATA, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    if do_refresh:
        _ws_send("refresh")


def _save_overlay():
    """Jelenlegi állapot kiírása és frissítés."""
    data = {
        "theme": current_theme,
        "category": category,
        "word": mask_word(secret_word, guessed_letters),
        "wrong": wrong_items,
        "lives_status": lives_status(),
        "state": state,
    }
    _overlay_write(data, do_refresh=True)


async def _clear_overlay_after(delay: float = 8.0):
    """Végállapot megjelenítése után takarítás & overlay törlés."""
    await asyncio.sleep(delay)

    # állapot-változók nullázása
    global game_active, game_id, game_starter, current_theme, STAGES_MAX
    global secret_word, category, guessed_letters, wrong_items, last_wrong_guesser
    global hint_used, state, bonus_life

    game_active = False
    game_id = None
    game_starter = ""
    current_theme = ""
    STAGES_MAX = 0
    secret_word = ""
    category = ""
    guessed_letters.clear()
    wrong_items.clear()
    last_wrong_guesser = ""
    hint_used = False
    state = "normal"
    bonus_life = 0

    # overlay ürítése + refresh + game_over jelzés
    empty = {
        "theme": "",
        "category": "",
        "word": "",
        "wrong": [],
        "lives_status": "0/0",
        "state": "normal"
    }
    _overlay_write(empty, do_refresh=True)
    _ws_send("game_over")


# ===============================
# Játéklogika
# ===============================
def _roll_theme_and_word(catalog: dict):
    global current_theme, STAGES_MAX, category, secret_word
    current_theme = random.choice(list(THEMES.keys()))
    STAGES_MAX = THEMES[current_theme]
    category = random.choice(list(catalog.keys()))
    secret_word = random.choice(catalog[category]).lower()


def start_new_game(catalog: dict, starter: str):
    """Új játék inicializálása. Csak akkor hívd, ha a bot már prepare-ölve van!"""
    global game_active, game_id, game_starter
    global guessed_letters, wrong_items, hint_used, state, bonus_life

    if not catalog:
        print("[⚠️] Üres katalógus – nem indítok játékot.")
        return

    # tiszta overlay állapot
    reset_overlay_state()
    _ws_send("new_game")

    game_active = True
    game_id = str(uuid.uuid4())
    game_starter = starter

    guessed_letters = set()
    wrong_items = []
    hint_used = False
    state = "normal"
    bonus_life = 0

    _roll_theme_and_word(catalog)
    _save_overlay()


async def game_timer(bot, my_id: str):
    """Automatikus timeout a játékra."""
    await asyncio.sleep(GAME_DURATION_SECONDS)
    global game_active
    if game_active and my_id == game_id:
        game_active = False
        try:
            await bot.connected_channels[0].send(f"!akasztas_to {game_starter}")
            await bot.connected_channels[0].send(f"❌ Vesztettetek! A szó: {secret_word.upper()}")
        except Exception:
            pass
        _save_overlay()
        asyncio.create_task(_clear_overlay_after(8.0))


# ===============================
# Parancsok
# ===============================
class HangmanCog(commands.Cog):
    def __init__(self, bot: commands.Bot, catalog: dict):
        self.bot = bot
        self.catalog = catalog
        self.config = load_config()
        self.last_global_tip = 0.0
        self.last_user_tip = {}
        self.last_newgame = 0.0


    def is_streamer_or_mod(self, ctx):
        badges = getattr(ctx.author, "badges", {}) or {}
        channel_name = self.bot.connected_channels[0].name.lower()
        return (
            "moderator" in badges
            or "broadcaster" in badges
            or ctx.author.name.lower() == channel_name
        )

    async def check_cooldowns(self, ctx, action="tipp") -> bool:
        """Globális/személyes/új játék cooldown ellenőrzés."""
        now = time.time()

        if action == "tipp":
            gcd = int(self.config.get("GLOBAL_TIPP_COOLDOWN", 0))
            pcd = int(self.config.get("PERSONAL_TIPP_COOLDOWN", 0))

            # globális cooldown
            if gcd > 0:
                delta = now - self.last_global_tip
                if delta < gcd:
                    await ctx.send(f"⏳ Várj még {round(gcd-delta,1)} mp-et a következő tipphez (globális cooldown).")
                    return False

            # személyes cooldown
            if pcd > 0:
                user = ctx.author.name.lower()
                delta = now - self.last_user_tip.get(user, 0.0)
                if delta < pcd:
                    await ctx.send(f"⏳ {ctx.author.name}, várj még {round(pcd-delta,1)} mp-et a következő tipphez.")
                    return False

            # ha engedett → időbélyegek frissítése
            self.last_global_tip = now
            self.last_user_tip[ctx.author.name.lower()] = now
            return True

        elif action == "newgame":
            ngcd = int(self.config.get("NEW_GAME_COOLDOWN", 0))
            if ngcd > 0:
                delta = now - self.last_newgame
                if delta < ngcd:
                    await ctx.send(f"⏳ Új játék előtt várj még {round(ngcd-delta,1)} mp-et.")
                    return False
            self.last_newgame = now
            return True

    async def handle_special_events(self, ctx):
        """Angyal/ördög + veszteség logika, minden rossz tipp után hívódik."""
        global wrong_items, bonus_life, state, game_active

        # ANGEL: ha elértük a max hibát és még nincs extra élet
        if len(wrong_items) == STAGES_MAX and bonus_life == 0:
            if random.random() < self.config.get("ANGEL_CHANCE", 8) / 100.0:
                bonus_life += 1
                state = "angel"
                await ctx.send("😇 Az utolsó pillanatban megmentett titeket a mentőangyal! Még egy esély!")
                _save_overlay()
                _ws_send("angel")
                return

        # DEVIL: kis eséllyel bármelyik rossz tippnél
        if random.random() < self.config.get("DEVIL_CHANCE", 1) / 100.0:
            wrong_items.append("😈")
            await ctx.send(f"😈 Az ördög megjelent — {lives_status()}")
            _ws_send("devil")

            # veszteség?
            if len(wrong_items) >= STAGES_MAX + bonus_life:
                game_active = False
                _save_overlay()
                await ctx.send(f"!akasztas_to {ctx.author.name}")
                await ctx.send(f"❌ Vesztettetek! A szó: {secret_word.upper()}")
                asyncio.create_task(_clear_overlay_after(8.0))
                return
            else:
                _save_overlay()
                return

        # normál veszteség
        if len(wrong_items) >= STAGES_MAX + bonus_life:
            game_active = False
            _save_overlay()
            await ctx.send(f"!akasztas_to {ctx.author.name}")
            await ctx.send(f"❌ Vesztettetek! A szó: {secret_word.upper()}")
            asyncio.create_task(_clear_overlay_after(8.0))
            return

        # állapotmentés
        _save_overlay()

    @commands.command(name="akasztás")
    async def akasztas(self, ctx):
        if not await self.check_cooldowns(ctx, 'newgame'):
            return
        """Új játék indítása."""
        global game_active
        if game_active:
            await ctx.send("Már fut egy játék! Tippelj: !tipp X vagy !tipp <szó>")
            return

        start_new_game(self.catalog, ctx.author.name)
        if not game_active:
            await ctx.send("❌ Nem tudok játékot indítani – üres a szókatalógus.")
            return

        await ctx.send(f"🪦 Új játék! Kategória: {category} — Tippelj: !tipp X vagy !tipp <szó>")
        asyncio.create_task(game_timer(self.bot, game_id))

    @commands.command(name="tipp")
    async def tipp(self, ctx):
        """Betű- vagy szótipp."""
        global game_active, last_wrong_guesser, hint_used

        if not game_active:
            return

        if not await self.check_cooldowns(ctx, "tipp"):
            return

        parts = ctx.message.content.split(maxsplit=1)
        if len(parts) < 2:
            return
        guess = parts[1].strip().lower()

        async def handle_win():
            global game_active
            game_active = False
            _save_overlay()
            _ws_send("victory")
            await ctx.send(f"🎉 Nyertetek! A szó: {secret_word.upper()}")
            asyncio.create_task(_clear_overlay_after(8.0))

        # teljes szó tipp
        if len(guess) > 1:
            if guess == secret_word.lower():
                await handle_win()
                return
            wrong_items.append("🧩")
            last_wrong_guesser = ctx.author.name
            await ctx.send(f"❌ Rossz szó tipp — {lives_status()}")
            await self.handle_special_events(ctx)
            return

        # egy betű
        if len(guess) == 1 and guess.isalpha():
            if guess in guessed_letters or guess.upper() in wrong_items:
                await ctx.send(f"❗ Már volt: {guess.upper()}")
                return

            if guess in secret_word.lower():
                guessed_letters.add(guess)
                await ctx.send(f"✅ Jó tipp: {guess.upper()}")
                _save_overlay()
                if mask_word(secret_word, guessed_letters) == secret_word:
                    await handle_win()
                return

            wrong_items.append(guess.upper())
            last_wrong_guesser = ctx.author.name
            await ctx.send(f"❌ Rossz tipp: {guess.upper()} — {lives_status()}")
            await self.handle_special_events(ctx)
            return
        # egyéb input: ignor

    @commands.command(name="hint")
    async def hint(self, ctx):
        """Felfed egy jó betűt, de +1 hiba (💡)."""
        global hint_used
        if not game_active:
            await ctx.send("❌ Nincs aktív játék!")
            return
        if hint_used:
            await ctx.send("💡 A segítséget már felhasználtátok ebben a játékban!")
            return

        hidden = [c.lower() for c in secret_word if c.isalpha() and c.lower() not in guessed_letters]
        if not hidden:
            await ctx.send("💡 Minden betű megvan, nincs mit segíteni!")
            return

        letter = random.choice(hidden)
        guessed_letters.add(letter)
        wrong_items.append("💡")
        hint_used = True

        await ctx.send(f"💡 Segítség: tartalmazza az „{letter.upper()}” betűt — de ez egy plusz hiba! ({lives_status()})")

        await self.handle_special_events(ctx)

        if mask_word(secret_word, guessed_letters) == secret_word:
            await self._win_after_hint(ctx)

    async def _win_after_hint(self, ctx):
        global game_active
        game_active = False
        _save_overlay()
        _ws_send("victory")
        await ctx.send(f"🎉 Nyertetek! A szó: {secret_word.upper()}")
        asyncio.create_task(_clear_overlay_after(8.0))

    # ----- Beállítás parancsok -----

    @commands.command(name="setangel")
    async def setangel(self, ctx):
        if not self.is_streamer_or_mod(ctx):
            return
        parts = ctx.message.content.split(maxsplit=1)
        if len(parts) < 2:
            await ctx.send("❌ Add meg az értéket! Példa: !setangel 0.5")
            return
        try:
            value = float(parts[1].replace(",", "."))
        except ValueError:
            await ctx.send("❌ Hibás számformátum! Példa: !setangel 0.5")
            return
        value = max(0.0, min(100.0, value))
        self.config["ANGEL_CHANCE"] = value
        save_config(self.config)
        await ctx.send(f"😇 Mentőangyal esélye beállítva: {value}%")

    @commands.command(name="setdevil")
    async def setdevil(self, ctx):
        if not self.is_streamer_or_mod(ctx):
            return
        parts = ctx.message.content.split(maxsplit=1)
        if len(parts) < 2:
            await ctx.send("❌ Add meg az értéket! Példa: !setdevil 0.25")
            return
        try:
            value = float(parts[1].replace(",", "."))
        except ValueError:
            await ctx.send("❌ Hibás számformátum! Példa: !setdevil 0.25")
            return
        value = max(0.0, min(100.0, value))
        self.config["DEVIL_CHANCE"] = value
        save_config(self.config)
        await ctx.send(f"😈 Ördög esélye beállítva: {value}%")

    @commands.command(name="setpersonal")
    async def setpersonal(self, ctx, value: int):
        if not self.is_streamer_or_mod(ctx):
            return
        self.config["PERSONAL_TIPP_COOLDOWN"] = max(0, value)
        save_config(self.config)
        await ctx.send(f"👤 Személyes tipp cooldown: {value} mp")

    @commands.command(name="setglobal")
    async def setglobal(self, ctx, value: int):
        if not self.is_streamer_or_mod(ctx):
            return
        self.config["GLOBAL_TIPP_COOLDOWN"] = max(0, value)
        save_config(self.config)
        await ctx.send(f"🌐 Globális tipp cooldown: {value} mp")

    @commands.command(name="setnewgame")
    async def setnewgame(self, ctx, value: int):
        if not self.is_streamer_or_mod(ctx):
            return
        self.config["NEW_GAME_COOLDOWN"] = max(0, value)
        save_config(self.config)
        await ctx.send(f"🎮 Új játék indítás közti idő: {value} mp")

    @commands.command(name="setduration")
    async def setduration(self, ctx, value: int):
        if not self.is_streamer_or_mod(ctx):
            return
        self.config["GAME_DURATION"] = max(60, value)
        save_config(self.config)
        await ctx.send(f"⏱️ Játékidő beállítva: {value} mp")

    @commands.command(name="status")
    async def status(self, ctx):
        if not self.is_streamer_or_mod(ctx):
            return
        msg = (
            "📊 **Játék beállítások:**\n"
            f"😇 Mentőangyal esély: {self.config['ANGEL_CHANCE']}%\n"
            f"😈 Ördög esély: {self.config['DEVIL_CHANCE']}%\n"
            f"👤 Személyes tipp cooldown: {self.config['PERSONAL_TIPP_COOLDOWN']} mp\n"
            f"🌐 Globális tipp cooldown: {self.config['GLOBAL_TIPP_COOLDOWN']} mp\n"
            f"🎮 Új játék indítás közti idő: {self.config['NEW_GAME_COOLDOWN']} mp\n"
            f"⏱️ Játékidő: {self.config['GAME_DURATION']} mp"
        )
        await ctx.send(msg)

    @commands.command(name="refresh")
    async def refresh_overlay(self, ctx):
        if not self.is_streamer_or_mod(ctx):
            return
        _save_overlay()
        await ctx.send("🔄 Overlay frissítve!")

    @commands.command(name="stop")
    async def stop_module(self, ctx):
        """Leállítja az akasztófa modult, hogy másik játék indítható legyen."""
        global game_active, game_id, game_starter

        if not self.is_streamer_or_mod(ctx):
            await ctx.send("❌ Nincs jogosultságod leállítani a modult.")
            return

        # ha fut épp játék, azt is lezárja
        game_active = False
        game_id = None
        game_starter = ""

        # overlay törlés
        reset_overlay_state()
        _ws_send("game_over")

        # saját cog eltávolítása a botból
        try:
            self.bot.remove_cog("HangmanCog")
            await ctx.send("🛑 Akasztófa modul leállítva. Új játék betölthető.")
        except Exception as e:
            await ctx.send(f"⚠️ Nem sikerült leállítani: {e}")


# ===============================
# Modul belépési pontok a fő botnak
# ===============================
def reset_overlay_state():
    """Overlay állapotának tiszta alaphelyzetbe hozása."""
    empty = {
        "theme": "",
        "category": "",
        "word": "",
        "wrong": [],
        "lives_status": "0/0",
        "state": "normal"
    }
    OVERLAY_DIR.mkdir(parents=True, exist_ok=True)
    with open(OVERLAY_DATA, "w", encoding="utf-8") as f:
        json.dump(empty, f, ensure_ascii=False, indent=2)
    print("[🧹] Overlay állapot alaphelyzetbe állítva.")


def prepare(bot):
    """
    A main_bot `!indit akasztofa` hívásában ezt futtatja:
      - beállítjuk a HostAPI-t
      - regisztráljuk a parancsokat (Cog)
    """
    global _host_api
    _host_api = getattr(bot, "host", None)
    if not _host_api:
        print("[❌] Nincs HostAPI a boton!")
    catalog = load_catalog()
    bot.add_cog(HangmanCog(bot, catalog))
    print("[✅] Akasztofa modul csatlakoztatva a főbothoz.")
