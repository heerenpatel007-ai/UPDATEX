import os
import asyncio
import aiohttp
from dotenv import load_dotenv

load_dotenv()

async def test_telegram():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        print("Missing credentials in .env")
        return
        
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id, 
        "text": "✅ <b>Updatex Backend is Live!</b>\n\nYour Telegram integration is working perfectly. You will now receive automated market updates here in 12-hour format.",
        "parse_mode": "HTML"
    }
    
    print(f"Sending test message to {chat_id}...")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, timeout=10, ssl=False) as resp:
                data = await resp.json()
                if data.get("ok"):
                    print("Success! Message delivered to Telegram.")
                else:
                    print(f"Failed to send: {data}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_telegram())
