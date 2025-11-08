from abc import ABC, abstractmethod
import asyncio
import json
import os
import time
from typing import Dict, Set, List

class BaseGame(ABC):
    def __init__(self, channel: str, bot):
        self.channel = channel
        self.bot = bot
        self.active = False
        self.game_id = None
        self.game_starter = None
        self.last_tipp_times = {}
        self.last_tipp_time = 0
        
        # Overlay elérési útvonalak
        self.OVERLAY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "overlay")
        self.DATA_FILE = os.path.join(self.OVERLAY_DIR, "data.json")
        
        # Konfiguráció a fő botból
        self.config = bot.config
        
        # WebSocket értesítés a fő botból
        self._ws_notify = bot._ws_notify if hasattr(bot, '_ws_notify') else lambda x: None
    
    @abstractmethod
    def start(self):
        """Játék indítása"""
        pass
    
    @abstractmethod
    async def handle_message(self, user: str, message: str) -> bool:
        """Üzenet feldolgozása. Visszaadja, hogy a játék aktív-e"""
        pass
    
    @abstractmethod
    def stop(self):
        """Játék leállítása és takarítás"""
        pass
    
    def send_message(self, message: str):
        """Üzenet küldése a Twitch chatbe"""
        try:
            target_channel = None
            for ch in self.bot.connected_channels:
                if ch.name.lower() == self.channel.lower():
                    target_channel = ch
                    break
            
            if target_channel:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(target_channel.send(message))
                else:
                    loop.run_until_complete(target_channel.send(message))
            else:
                print(f"Hiba: Nem található a(z) {self.channel} csatorna")
        except Exception as e:
            print(f"Hiba az üzenetküldés során: {e}")
    
    def save_overlay_state(self, theme: str, category: str, word: str, 
                          wrong: List[str], lives_status: str, state: str):
        """Állapot mentése az overlay számára"""
        try:
            # Biztosítsuk, hogy az overlay könyvtár létezik
            os.makedirs(self.OVERLAY_DIR, exist_ok=True)
            
            # Adatstruktúra létrehozása
            data = {
                "theme": theme,
                "category": category,
                "word": word,
                "wrong": wrong,
                "lives_status": lives_status,
                "state": state
            }
            
            # Debug: kiírjuk a mentett adatokat
            print(f"💾 Overlay adatok mentése:")
            print(f"   Útvonal: {self.DATA_FILE}")
            print(f"   Téma: {theme}")
            print(f"   Kategória: {category}")
            print(f"   Szó: {word}")
            print(f"   Hibás betűk: {wrong}")
            print(f"   Élet állapot: {lives_status}")
            print(f"   Állapot: {state}")
            
            # Fájlba mentés
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # WebSocket értesítés küldése
            self._ws_notify("refresh")
            
            print("✅ Overlay adatok sikeresen mentve")
            
        except Exception as e:
            print(f"❌ Hiba az overlay frissítésekor: {e}")
            import traceback
            traceback.print_exc()
    
    async def clear_overlay_after(self, delay: float = 8.0):
        """Overlay törlése késleltetve"""
        await asyncio.sleep(delay)
        try:
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "theme": "",
                    "category": "",
                    "word": "",
                    "wrong": [],
                    "lives_status": "0/0",
                    "state": "normal"
                }, f, ensure_ascii=False, indent=2)
            self._ws_notify("game_over")
            print("✅ Overlay sikeresen törölve")
        except Exception as e:
            print(f"❌ Hiba az overlay törlésekor: {e}")
    
    def is_streamer_or_mod(self, user) -> bool:
        """Ellenőrzi, hogy a felhasználó streamer vagy mod-e"""
        return self.bot.is_streamer_or_mod(user)