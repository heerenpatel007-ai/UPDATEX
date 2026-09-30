import json, asyncio, os, aiohttp
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot
from services.utils import strip_tags
import pytz

load_dotenv()

# Configuration
JSON_FILE   = os.path.join(os.path.dirname(__file__), "bse_service.json")

BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID")
HEADERS     = {
    "Referer": "https://www.bseindia.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}



async def send_telegram(bot: Bot, text: str):
    if not bot: return
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        print(f"[BSE Telegram Error] {e}")

async def run_bse_worker():
    print("[BSE] Service starting...")
    last_reset_date = datetime.now(pytz.timezone("Asia/Kolkata")).date()
    first_run = True
    
    if not BOT_TOKEN:
        print("[BSE Warning] TELEGRAM_BOT_TOKEN not found. Telegram alerts will be skipped.")
        bot = None
    else:
        bot = Bot(token=BOT_TOKEN)
    
    # Ensure JSON file exists
    if not os.path.exists(JSON_FILE):
        with open(JSON_FILE, "w") as f: json.dump([], f)

    try:
        async with aiohttp.ClientSession() as session:
            while True:
                # Daily midnight reset
                current_date = datetime.now(pytz.timezone("Asia/Kolkata")).date()
                if current_date != last_reset_date:
                    with open(JSON_FILE, "w") as f: json.dump([], f)
                    last_reset_date = current_date

                today_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y%m%d")

                try:
                    # Load existing IDs for dedup
                    existing = []
                    if os.path.exists(JSON_FILE):
                        try:
                            with open(JSON_FILE, "r") as f: existing = json.load(f)
                        except:
                            existing = []
                    existing_ids = {item.get("NEWSID") for item in existing if item.get("NEWSID")}

                    # Paginated fetch — 10 pages on startup, then just 2 pages for speed
                    all_new = []
                    max_pages = 10 if first_run else 2
                    for page in range(1, max_pages + 1):
                        url = (f"https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
                               f"?pageno={page}&strCat=-1&strPrevDate={today_str}&strScrip=&strSearch=P"
                               f"&strToDate={today_str}&strType=C&subcategory=-1")
                        async with session.get(url, headers=HEADERS, timeout=10) as resp:
                            if resp.status == 200:
                                try:
                                    res  = await resp.json()
                                    rows = res.get("Table", [])
                                    if not rows:
                                        break  # No more pages
                                    new_on_page = [r for r in rows if r.get("NEWSID") not in existing_ids]
                                    all_new.extend(new_on_page)
                                    if len(new_on_page) == 0:
                                        break
                                except Exception as json_err:
                                    print(f"[BSE JSON Error] Page {page}: {json_err}")
                                    break
                            else:
                                print(f"[BSE API Error] Page {page}: Status {resp.status}")
                                break

                    if all_new:
                        # Save all new items to JSON
                        existing.extend(all_new)
                        with open(JSON_FILE, "w") as f: json.dump(existing, f, indent=2)
                        print(f"[BSE] Detected {len(all_new)} new items.")
                        first_run = False

                except Exception as e:
                    print(f"[BSE Error] {e}")

                await asyncio.sleep(5)
    finally:
        if bot:
            await bot.session.close()

if __name__ == "__main__":
    asyncio.run(run_bse_worker())
