import json, asyncio, os, aiohttp
from datetime import datetime
from services.utils import strip_tags

# Configuration
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))


def parse_any_date(date_str):
    """Convert any exchange date format into a sortable datetime object"""
    if not date_str: return datetime.min
    try:
        # NSE: 14-May-2026 15:02:59
        if "-" in date_str and " " in date_str:
            return datetime.strptime(date_str, "%d-%b-%Y %H:%M:%S")
        # BSE: 2026-05-14T16:06:29
        if "T" in date_str:
            return datetime.fromisoformat(date_str.split(".")[0])
        # Default
        return datetime.fromisoformat(date_str)
    except:
        return datetime.min



async def send_telegram(bot_token, chat_id, message):
    if not bot_token or not chat_id: return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, timeout=10, ssl=False) as resp: return await resp.json()
        except: pass

class SocketManager:
    def __init__(self):
        self.active_connections = []
        self.pushed_ids = set()
        # On boot, we remember what we've already seen to prevent double-sending
        self._initialize_history()

    def _initialize_history(self):
        for filename, id_key in [("nse_service.json", "attchmntFile"), ("bse_service.json", "NEWSID")]:
            path = os.path.join(BASE_DIR, filename)
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for item in json.load(f):
                            if item.get(id_key): self.pushed_ids.add(item[id_key])
                except: pass

    async def connect(self, websocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket):
        if websocket in self.active_connections: self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        # Use default=str to handle datetime objects added during sorting
        payload = json.dumps(message, default=str)
        for connection in self.active_connections:
            try: await connection.send_text(payload)
            except: pass

    async def watch_and_broadcast(self):
        print("[Dispatcher] Final Chronological Order System Active.")
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id   = os.getenv("TELEGRAM_CHAT_ID")

        while True:
            try:

                unsent_items = []

                # 1. Gather all new items from all sources
                sources = {
                    "NSE": (os.path.join(BASE_DIR, "nse_service.json"), "attchmntFile", "an_dt"),
                    "BSE": (os.path.join(BASE_DIR, "bse_service.json"), "NEWSID", "NEWS_DT")
                }

                for source, (path, id_key, time_key) in sources.items():
                    if not os.path.exists(path): continue
                    with open(path, "r", encoding="utf-8") as f:
                        try:
                            raw = json.load(f)
                        except:
                            continue
                        items = []
                        items = raw if isinstance(raw, list) else []

                        for item in items:
                            # Use primary ID if available, otherwise fallback to robust fingerprint
                            primary_id = item.get(id_key)
                            if primary_id:
                                uid = f"{source}_{primary_id}"
                            else:
                                from services.utils import generate_uid
                                headline = item.get("desc") or item.get("HEADLINE") or item.get("headline") or ""
                                uid = generate_uid(source, item.get("symbol") or "", headline, item.get(time_key) or "")

                            if uid not in self.pushed_ids:
                                item["_type"] = source
                                item["_id"]   = uid
                                item["_dt"]   = parse_any_date(item.get(time_key))
                                unsent_items.append(item)

                # 2. THE CRITICAL SORT (Oldest First)
                unsent_items.sort(key=lambda x: x["_dt"])

                # 3. Batch Dispatch
                for item in unsent_items:
                    source = item["_type"]
                    
                    # Normalize for UI
                    if source == "NSE":
                        item["sm_name"] = item.get("sm_name") or item.get("companyName") or "NSE"
                        item["symbol"]  = item.get("symbol") or "NSE"
                        item["desc"]    = strip_tags(item.get("desc", "Announcement"))
                    elif source == "BSE":
                        item["SLONGNAME"] = item.get("SLONGNAME") or item.get("COMPANY_NAME") or "BSE"
                        item["SCRIP_NAME"] = item.get("SCRIP_NAME") or str(item.get("SCRIP_CD", "BSE"))
                        item["HEADLINE"]   = strip_tags(item.get("HEADLINE", "Announcement"))

                    # A. Send to UI
                    item["source"] = source
                    await self.broadcast(item)

                    # B. Send to Telegram (Only NSE/BSE)
                    if source in ["NSE", "BSE"] and bot_token and chat_id:
                        msg = self._build_telegram_msg(item, source)
                        await send_telegram(bot_token, chat_id, msg)
                        await asyncio.sleep(0.5) # Protect Telegram limits

                    self.pushed_ids.add(item["_id"])

            except Exception as e:
                print(f"[Dispatcher Error] {e}")

            await asyncio.sleep(2)

    def _build_telegram_msg(self, item, source):
        def to_12_hour(t_str):
            try:
                # Expecting HH:MM:SS format
                t = datetime.strptime(t_str, "%H:%M:%S")
                return t.strftime("%I:%M:%S %p")
            except:
                return t_str

        if source == "NSE":
            symbol, name = item.get("symbol", "-"), item.get("sm_name", "-")
            an_dt = str(item.get("an_dt", "-"))
            time_str = an_dt.split(" ")[-1] if " " in an_dt else an_dt
            time_str = to_12_hour(time_str)
            pdf_html = f'\n📄 <a href="{item.get("pdf_link", "")}">View PDF</a>' if item.get("pdf_link") else ""
            return f"🏢 <b>NSE | {name}</b> ({symbol})\n📋 {item.get('desc', '-')}\n🕒 {time_str}\n📊 {item.get('market_type', '-')}{pdf_html}"
        else:
            symbol, name = item.get("SCRIP_NAME") or str(item.get("SCRIP_CD", "-")), item.get("SLONGNAME", "-")
            dt_str = str(item.get("NEWS_DT", "-"))
            time_str = dt_str.split("T")[1].split(".")[0] if "T" in dt_str else dt_str
            time_str = to_12_hour(time_str)
            category = item.get("SUBCATNAME") or item.get("CATEGORYNAME") or "-"
            pdf_html = f'\n📄 <a href="{item.get("pdf_link", "")}">View PDF</a>' if item.get("pdf_link") else ""
            return f"🏢 <b>BSE | {name}</b> ({symbol})\n📋 {item.get('HEADLINE', '-')}\n🕒 {time_str}\n📊 {category}{pdf_html}"

socket_manager = SocketManager()
