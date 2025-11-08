import asyncio
import json
import random
import time
from pathlib import Path
from twitchio.ext import commands

# ===============================
# 🔧 Könnyen módosítható beállítások
# ===============================
CHALLENGE_TIMEOUT = 180       # kihívás érvényessége (mp)
AI_REPLY_WINDOW   = 30        # kihívás lejárta után ennyi ideig írhat a kihívó: !igen (mp)
MOVE_TIMEOUT      = 60        # egy játékos ennyi ideig léphet (mp) – utána automatikus lépés
OVERLAY_CLEAR_DELAY = 8       # játék vége után ennyi idővel ürítjük az overlay-t (mp)
AI_THINK_DELAY      = (1.0, 2.0)  # AI "gondolkodási" idő (mp) min/max

# játéktípusok: (mode, size_or_rows, win_cond)
# - "amoeba": size x size tábla, 5 kell
# - "connect4": 6x7 tábla fix, 4 kell
BOARD_TYPES = [
    ("amoeba",   13, 5),
    ("amoeba",   19, 5),
    ("connect4",  6, 4),  # 6x7 fix a kódban
]

# ===============================
# Overlay + HostAPI
# ===============================
OVERLAY_DIR = Path(__file__).resolve().parents[2] / "overlay"
OVERLAY_DATA = OVERLAY_DIR / "data.json"
_host_api = None  # main_bot.prepare() injektálja

def _ws_send(event_name: str):
    """WebSocket-trigger az overlaynek (ha a főbot biztosít HostAPI-t)."""
    if _host_api:
        try:
            _host_api.ws_broadcast({"event": event_name, "game": "amoeba"})
        except Exception:
            pass

def _overlay_write(payload: dict, do_refresh: bool = True):
    OVERLAY_DIR.mkdir(parents=True, exist_ok=True)
    with open(OVERLAY_DATA, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    if do_refresh:
        _ws_send("refresh")

def _clear_overlay():
    _overlay_write({}, do_refresh=True)

# ===============================
# Tábla és játékmenet
# ===============================
class GameBoard:
    def __init__(self, mode="amoeba", size_or_rows=13, win_cond=5):
        self.mode = mode
        if mode == "connect4":
            self.rows, self.cols = 6, 7
        else:
            self.rows, self.cols = size_or_rows, size_or_rows

        self.win_cond = win_cond
        self.board = [[" " for _ in range(self.cols)] for _ in range(self.rows)]

        self.player1 = ""
        self.player2 = ""
        self.current_player = ""
        self.winner = ""
        self.active = False
        self.is_ai = False
        self.last_move_ts = 0.0

    def start(self, p1, p2, ai=False):
        self.player1 = p1
        self.player2 = p2
        self.current_player = p1
        self.winner = ""
        self.is_ai = ai
        self.active = True
        self.last_move_ts = time.time()
        self._save()
        _ws_send("new_game")

    def _save(self):
        _overlay_write(self.to_dict())

    def to_dict(self):
        return {
            "game": "amoeba",
            "mode": self.mode,
            "board": self.board,
            "player1": self.player1,
            "player2": self.player2,
            "current_player": self.current_player,
            "winner": self.winner or ""
        }

    # --------- lépés ----------
    def make_move(self, player, coord):
        if not self.active or player != self.current_player:
            return None

        mark = "☠️" if player == self.player1 else "🩸"

        if self.mode == "connect4":
            # coord itt egy oszlop index (0..6)
            col = coord
            if col < 0 or col >= self.cols:
                return "❌ Érvénytelen oszlop!"
            row = None
            for r in range(self.rows-1, -1, -1):
                if self.board[r][col] == " ":
                    row = r
                    break
            if row is None:
                return "❌ Ez az oszlop tele van!"
        else:
            # coord itt (row, col)
            row, col = coord
            if not (0 <= row < self.rows and 0 <= col < self.cols):
                return "❌ Ez a mező kívül esik a táblán!"
            if self.board[row][col] != " ":
                return "❌ Ez a mező már foglalt!"

        # beírjuk a lépést
        self.board[row][col] = mark

        # győzelem?
        if self._check_victory(row, col, mark):
            self.active = False
            self.winner = player
            self._save()
            asyncio.create_task(self._auto_clear_overlay())
            return f"🏆 {player} nyert! ({mark})"

        # döntetlen?
        if all(cell != " " for r in self.board for cell in r):
            self.active = False
            self.winner = "Döntetlen"
            self._save()
            asyncio.create_task(self._auto_clear_overlay())
            return "🤝 Döntetlen!"

        # következő játékos
        self.current_player = self.player2 if self.current_player == self.player1 else self.player1
        self.last_move_ts = time.time()
        self._save()
        return f"✅ {mark} — {self.current_player} következik."

    # --------- győzelem ellenőrzés ----------
    def _check_victory(self, row, col, mark):
        directions = [(1,0), (0,1), (1,1), (1,-1)]
        for dr, dc in directions:
            count = 1
            # előre
            rr, cc = row + dr, col + dc
            while 0 <= rr < self.rows and 0 <= cc < self.cols and self.board[rr][cc] == mark:
                count += 1; rr += dr; cc += dc
            # vissza
            rr, cc = row - dr, col - dc
            while 0 <= rr < self.rows and 0 <= cc < self.cols and self.board[rr][cc] == mark:
                count += 1; rr -= dr; cc -= dc
            if count >= self.win_cond:
                return True
        return False

    async def _auto_clear_overlay(self):
        await asyncio.sleep(OVERLAY_CLEAR_DELAY)
        _clear_overlay()

    # --------- AI (heurisztikus) ----------
    def _count_dir(self, r, c, dr, dc, mark):
        cnt = 0
        for d in (1, -1):
            rr, cc = r + dr*d, c + dc*d
            while 0 <= rr < self.rows and 0 <= cc < self.cols and self.board[rr][cc] == mark:
                cnt += 1
                rr += dr*d; cc += dc*d
        return cnt

    def smart_ai_move(self):
        """Amoeba: támadás+védekezés; Connect4: egyszerű pontozás oszlopokra."""
        if self.mode == "connect4":
            return self._connect4_best_column()

        best_score = -1
        best_moves = []
        mark_ai = "🩸"
        mark_pl = "☠️"

        for r in range(self.rows):
            for c in range(self.cols):
                if self.board[r][c] != " ":
                    continue
                score = 0
                for dr, dc in ((1,0),(0,1),(1,1),(1,-1)):
                    ai_cnt = self._count_dir(r, c, dr, dc, mark_ai)
                    pl_cnt = self._count_dir(r, c, dr, dc, mark_pl)
                    score += ai_cnt ** 2
                    score += (pl_cnt ** 2) * 1.5
                if score > best_score:
                    best_score = score
                    best_moves = [(r, c)]
                elif score == best_score:
                    best_moves.append((r, c))
        return random.choice(best_moves) if best_moves else None

    def _connect4_best_column(self):
        """Egyszerű Connect4 heurisztika:
           - preferálja a középső oszlopokat
           - kerüli a tele oszlopot
        """
        scores = []
        center = self.cols // 2
        for c in range(self.cols):
            # megtaláljuk a legalsó szabad sort
            rr = None
            for r in range(self.rows-1, -1, -1):
                if self.board[r][c] == " ":
                    rr = r
                    break
            if rr is None:
                continue
            # pontozás: közép preferencia + kis védekező/támadó ösztön
            center_bias = (self.cols - abs(c - center))
            scores.append((center_bias + random.random()*0.1, c))
        if not scores:
            return None
        scores.sort(reverse=True)
        return scores[0][1]

# ===============================
# Twitch Cog
# ===============================
class AmoebaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.challenge = None             # {"type":"open"|"direct","challenger":str,"target":str|None,"since":ts}
        self.ai_offer_for = None          # kihívó neve, ha AI felajánlás aktív
        self.ai_offer_deadline = 0.0
        self.game: GameBoard | None = None
        self._move_timer_task = None

    # --------- segéd: jogosultság ---------
    def is_streamer_or_mod(self, ctx):
        badges = getattr(ctx.author, "badges", {}) or {}
        channel_name = self.bot.connected_channels[0].name.lower() if self.bot.connected_channels else ""
        return ("moderator" in badges) or ("broadcaster" in badges) or (ctx.author.name.lower() == channel_name)

    # --------- lépésidő figyelő ---------
    async def _move_timer(self, ctx):
        while self.game and self.game.active:
            await asyncio.sleep(2)
            if time.time() - self.game.last_move_ts >= MOVE_TIMEOUT:
                # automatikus (okos) lépés a soron következő játékosnak
                who = self.game.current_player
                if self.game.mode == "connect4":
                    col = self.game._connect4_best_column()
                    if col is None:
                        # minden oszlop tele – döntetlen felé, de próbálunk még egy randomot
                        free_cols = [c for c in range(self.game.cols) if self.game.board[0][c] == " "]
                        if not free_cols:
                            return
                        col = random.choice(free_cols)
                    await ctx.send(f"⏰ {who} nem lépett időben — automatikus lépés: oszlop {col+1}")
                    res = self.game.make_move(who, col)
                    if res:
                        await ctx.send(res)
                else:
                    mv = self.game.smart_ai_move()
                    if not mv:
                        return
                    r, c = mv
                    await ctx.send(f"⏰ {who} nem lépett időben — automatikus lépés: {chr(65+c)}{r+1}")
                    res = self.game.make_move(who, (r, c))
                    if res:
                        await ctx.send(res)

    def _restart_move_timer(self, ctx):
        if self._move_timer_task and not self._move_timer_task.done():
            self._move_timer_task.cancel()
        self._move_timer_task = asyncio.create_task(self._move_timer(ctx))

    # --------- parancsok ---------
    @commands.command(name="kihívás", aliases=["kihivas","kihív","kihiv"])
    async def kihivas(self, ctx, target: str = None):
        if self.challenge:
            await ctx.send("⚠️ Már van függőben lévő kihívás!")
            return
        if self.game and self.game.active:
            await ctx.send("❌ Már fut egy játék.")
            return
        if target is not None and not target.strip():
            await ctx.send("❌ Adj meg érvényes játékosnevet, vagy használd: !kihívás nyílt")
            return

        if not target or target.lower() == "nyílt":
            self.challenge = {"type": "open", "challenger": ctx.author.name, "target": None, "since": time.time()}
            await ctx.send("📢 Nyílt kihívás indítva! Használd: !elfogad")
        else:
            self.challenge = {"type": "direct", "challenger": ctx.author.name, "target": target, "since": time.time()}
            await ctx.send(f"🎯 {ctx.author.name} kihívta {target}-ot egy játékra! Elfogadod? (!elfogad)")

        async def expire():
            await asyncio.sleep(CHALLENGE_TIMEOUT)
            if self.challenge and (time.time() - self.challenge["since"]) >= CHALLENGE_TIMEOUT:
                challenger = self.challenge["challenger"]
                self.challenge = None
                self.ai_offer_for = challenger
                self.ai_offer_deadline = time.time() + AI_REPLY_WINDOW
                await ctx.send("⏳ Senki sem fogadta el a kihívást. Szeretnél AI ellen játszani? Írd: !igen")
                await asyncio.sleep(AI_REPLY_WINDOW)
                if self.ai_offer_for and time.time() > self.ai_offer_deadline:
                    await ctx.send("⌛ Az AI-ajánlat lejárt.")
                    self.ai_offer_for = None
                    self.ai_offer_deadline = 0.0
        asyncio.create_task(expire())

    @commands.command(name="elfogad", aliases=["accept"])
    async def elfogad(self, ctx):
        if not self.challenge:
            await ctx.send("❌ Nincs függőben kihívás.")
            return
        ch = self.challenge
        if ch["type"] == "direct" and ctx.author.name.lower() != ch["target"].lower():
            await ctx.send("❌ Ezt a kihívást nem neked szánták.")
            return

        mode, size_or_rows, win = random.choice(BOARD_TYPES)
        p1 = ch["challenger"]
        p2 = ctx.author.name
        self.challenge = None
        self.ai_offer_for = None
        self.ai_offer_deadline = 0.0

        self.game = GameBoard(mode, size_or_rows, win)
        self.game.start(p1, p2, ai=False)
        await ctx.send(f"🎮 Játék indult: {p1} ☠️ vs {p2} 🩸 — {p1} kezd!")
        self._restart_move_timer(ctx)

    @commands.command(name="igen")
    async def igen(self, ctx):
        if not self.ai_offer_for or ctx.author.name != self.ai_offer_for:
            return
        if time.time() > self.ai_offer_deadline:
            await ctx.send("⌛ Az AI-ajánlat lejárt.")
            self.ai_offer_for = None
            self.ai_offer_deadline = 0.0
            return

        mode, size_or_rows, win = random.choice(BOARD_TYPES)
        p1 = self.ai_offer_for
        p2 = "🤖 AI_BOT"
        self.ai_offer_for = None
        self.ai_offer_deadline = 0.0

        self.game = GameBoard(mode, size_or_rows, win)
        self.game.start(p1, p2, ai=True)
        await ctx.send(f"🎮 Játék indult: {p1} ☠️ vs 🤖 AI_BOT 🩸 — {p1} kezd!")
        self._restart_move_timer(ctx)

    @commands.command(name="lép", aliases=["lep"])
    async def lep(self, ctx, coord: str = None):
        if not self.game or not self.game.active:
            await ctx.send("❌ Nincs aktív játék.")
            return
        if not coord:
            await ctx.send("Használat: !lép A1 (amoeba) vagy !lép 3 / !lép C (negyedelő)")
            return

        # játékos lépése
        if self.game.mode == "connect4":
            token = coord.strip()
            # engedjük: szám (1..7) vagy betű (A..G)
            if token.isdigit():
                col = int(token) - 1
            else:
                col = ord(token[0].upper()) - 65
            result = self.game.make_move(ctx.author.name, col)
            if result:
                await ctx.send(result)
        else:
            try:
                col = ord(coord[0].upper()) - 65
                row = int(coord[1:]) - 1
            except Exception:
                await ctx.send("❌ Érvénytelen koordináta! Pl: A1, B7, H12")
                return
            result = self.game.make_move(ctx.author.name, (row, col))
            if result:
                await ctx.send(result)

        # újraindítjuk a lépésidő-figyelőt
        self._restart_move_timer(ctx)

        # AI lép, ha ő következik
        if self.game and self.game.active and self.game.is_ai and self.game.current_player == "🤖 AI_BOT":
            await asyncio.sleep(random.uniform(*AI_THINK_DELAY))
            if self.game.mode == "connect4":
                col = self.game._connect4_best_column()
                if col is None:
                    # fallback: bármelyik nem tele oszlop
                    free_cols = [c for c in range(self.game.cols) if self.game.board[0][c] == " "]
                    if not free_cols:
                        return
                    col = random.choice(free_cols)
                ai_res = self.game.make_move("🤖 AI_BOT", col)
                await ctx.send(f"🤖 AI lép oszlop: {col+1}")
                if ai_res:
                    await ctx.send(ai_res)
            else:
                mv = self.game.smart_ai_move()
                if mv:
                    r, c = mv
                    ai_res = self.game.make_move("🤖 AI_BOT", (r, c))
                    await ctx.send(f"🤖 AI lép: {chr(65+c)}{r+1}")
                    if ai_res:
                        await ctx.send(ai_res)
            # AI után is indítjuk a lépésidő-figyelőt
            self._restart_move_timer(ctx)

    @commands.command(name="stop", aliases=["leallit","leállít"])
    async def stop_cmd(self, ctx):
        """Modul leállítása – csak streamer/mod."""
        if not self.is_streamer_or_mod(ctx):
            return
        # állapot nullázás
        self.challenge = None
        self.ai_offer_for = None
        self.ai_offer_deadline = 0.0
        if self._move_timer_task and not self._move_timer_task.done():
            self._move_timer_task.cancel()
        self._move_timer_task = None
        self.game = None
        # overlay ürítés
        _clear_overlay()
        await ctx.send("⚙️ Az Amoeba modul leállítva.")
        try:
            self.bot.remove_cog("AmoebaCog")
        except Exception:
            pass

# ===============================
# Modul belépési pont
# ===============================
def prepare(bot):
    global _host_api
    _host_api = getattr(bot, "host", None)
    bot.add_cog(AmoebaCog(bot))
    print("[✅] Amoeba modul csatlakoztatva a főbothoz.")
