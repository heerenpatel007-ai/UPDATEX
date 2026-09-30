import json, asyncio, os, time
from curl_cffi import requests
from datetime import datetime
from dotenv import load_dotenv
import pytz

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────
JSON_FILE = os.path.join(os.path.dirname(__file__), "nse_service.json")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# Rotate User-Agents to reduce bot detection
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

# NSE requires cookies from the homepage before API calls work
COOKIE_SEED_URLS = [
    "https://www.nseindia.com/",
    "https://www.nseindia.com/market-data/live-equity-market",
    "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
]

# ── Session Factory ────────────────────────────────────────────────────────────
def make_session(ua_index: int = 0):
    """Create a fresh session impersonating Chrome to bypass Akamai."""
    # DO NOT override headers manually when using impersonate, it breaks the fingerprint
    session = requests.Session(impersonate="chrome")
    return session


def init_cookies(session: requests.Session) -> bool:
    """
    Seed the session with NSE cookies by visiting the homepage + secondary pages.
    Tries all seed URLs (not just the first). Returns True if cookies were set.
    NOTE: This is a blocking call — always run via asyncio.to_thread().
    """
    got_cookies = False
    for url in COOKIE_SEED_URLS:
        try:
            session.headers["Referer"] = "https://www.nseindia.com/"
            r = session.get(url, timeout=15)
            if session.cookies:
                print(f"[NSE] Cookies seeded from {url} (status {r.status_code}) — cookies: {list(session.cookies.keys())}")
                got_cookies = True
        except Exception as e:
            print(f"[NSE] Cookie seed failed for {url}: {e}")
    if not got_cookies:
        print("[NSE] Warning: no cookies received from any seed URL.")
    return got_cookies


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_item_id(item: dict) -> str:
    att = item.get("attchmntFile", "").strip()
    if att:
        return att
    return f"{item.get('symbol')}_{item.get('desc')}_{item.get('an_dt')}"


def enrich_item(item: dict, market_type: str) -> dict:
    """Add market_type and a clean pdf_link to every item."""
    item["market_type"] = market_type
    attach = item.get("attchmntFile", "").strip()
    if attach:
        item["pdf_link"] = attach if attach.startswith("http") else f"https://nsearchives.nseindia.com/corporate/{attach}"
    else:
        item["pdf_link"] = ""
    return item


def load_json() -> list:
    if not os.path.exists(JSON_FILE):
        return []
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_json(data: list):
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def clear_json():
    save_json([])
    print("[NSE] JSON file cleared for new day.")


def is_today(item: dict) -> bool:
    """Return True if the item's an_dt is from today."""
    an_dt = item.get("an_dt", "")
    today = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%d-%b-%Y")
    return an_dt.startswith(today)


def extract_list_from_response(data) -> list:
    """
    Robustly extract a list of announcements from NSE API response.
    NSE sometimes returns a raw list, sometimes a dict with a 'data' key.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Try common keys NSE uses
        for key in ("data", "announcements", "results", "Table"):
            if key in data and isinstance(data[key], list):
                print(f"[NSE] Extracted list from response key: '{key}'")
                return data[key]
        print(f"[NSE] Unexpected dict response keys: {list(data.keys())}")
    return []


# ── Main Worker ───────────────────────────────────────────────────────────────
async def run_nse_worker():
    print("[NSE] Service starting...")

    # ── Startup: clear stale data from previous days ──────────────────────────
    existing = load_json()
    if existing and not any(is_today(i) for i in existing):
        print(f"[NSE] Stale data detected (last record: {existing[-1].get('an_dt', '?')}). Clearing JSON.")
        clear_json()
    elif not existing:
        save_json([])  # ensure file exists

    last_reset_date = datetime.now(pytz.timezone("Asia/Kolkata")).date()

    # ── Session setup (NON-BLOCKING via asyncio.to_thread) ────────────────────
    ua_index = 0
    session  = make_session(ua_index)

    # FIX: Run blocking cookie seeding in a thread so it doesn't block the event loop
    await asyncio.to_thread(init_cookies, session)

    last_cookie_time = time.time()
    COOKIE_REFRESH   = 480  # refresh every 8 minutes

    # ── Inner fetch (always runs in a thread) ─────────────────────────────────
    def fetch_nse():
        nonlocal last_cookie_time, session, ua_index

        current_time = time.time()

        # Refresh cookies on schedule
        if (current_time - last_cookie_time) > COOKIE_REFRESH:
            ua_index += 1
            session = make_session(ua_index)
            ok = init_cookies(session)
            last_cookie_time = current_time
            if ok:
                print("[NSE] Session & cookies refreshed successfully.")
            else:
                print("[NSE] Session refreshed but no cookies — API may reject calls.")

        # Load current data for dedup
        existing     = load_json()
        existing_ids = {get_item_id(i) for i in existing}

        today_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%d-%m-%Y")
        all_raw   = []

        for idx in ["equities", "sme"]:
            market_type = "Mainboard" if idx == "equities" else "SME"
            url = (
                f"https://www.nseindia.com/api/corporate-announcements"
                f"?index={idx}&from_date={today_str}&to_date={today_str}"
            )
            session.headers["Referer"] = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
            try:
                resp = session.get(url, timeout=20)

                if resp.status_code == 200:
                    try:
                        raw_data = resp.json()
                        # FIX: Robustly extract list regardless of response shape
                        data = extract_list_from_response(raw_data)
                        for item in data:
                            enrich_item(item, market_type)
                        all_raw.extend(data)
                        print(f"[NSE] {idx}: {len(data)} records fetched.")
                    except Exception as je:
                        print(f"[NSE JSON Error] {idx}: {je}")

                elif resp.status_code in (401, 403):
                    print(f"[NSE] {idx}: Got {resp.status_code} — forcing session & cookie recreation.")
                    ua_index += 1
                    session = make_session(ua_index)
                    init_cookies(session)
                    last_cookie_time = time.time()

                else:
                    print(f"[NSE API Error] {idx}: HTTP {resp.status_code} — body: {resp.text[:200]}")

            except requests.exceptions.Timeout:
                print(f"[NSE Timeout] {idx}: request timed out.")
            except Exception as req_err:
                print(f"[NSE Request Error] {idx}: {req_err}")

        # Dedup and persist new items
        new_items = [i for i in all_raw if get_item_id(i) not in existing_ids]

        if new_items:
            try:
                new_items.sort(
                    key=lambda x: datetime.strptime(x.get("an_dt", "01-Jan-2000 00:00:00"), "%d-%b-%Y %H:%M:%S")
                )
            except Exception:
                pass

            existing.extend(new_items)
            save_json(existing)
            print(f"[NSE] ✅ Saved {len(new_items)} new item(s). Total today: {len(existing)}")
        else:
            print(f"[NSE] No new items. Total stored: {len(existing)}")

    # ── Poll loop ─────────────────────────────────────────────────────────────
    try:
        while True:
            # Daily midnight reset — clear JSON when date rolls over
            current_date = datetime.now(pytz.timezone("Asia/Kolkata")).date()
            if current_date != last_reset_date:
                clear_json()
                last_reset_date = current_date
                # Also recreate session on new day
                ua_index += 1
                session = make_session(ua_index)
                await asyncio.to_thread(init_cookies, session)
                last_cookie_time = time.time()

            try:
                await asyncio.to_thread(fetch_nse)
            except Exception as e:
                print(f"[NSE Loop Error] {e}")

            await asyncio.sleep(15)   # poll every 15s

    except asyncio.CancelledError:
        print("[NSE] Worker cancelled. Shutting down cleanly.")


if __name__ == "__main__":
    asyncio.run(run_nse_worker())
