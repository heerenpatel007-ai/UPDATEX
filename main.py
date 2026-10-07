from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import asyncio
import json
import os
import re
import shutil
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from datetime import datetime, timedelta

from services.socket_manager  import socket_manager
from services.nse_service     import run_nse_worker
from services.bse_service     import run_bse_worker
from services.Extra.angel_one import angel_service
import aiohttp
from pydantic import BaseModel
from typing import List
import pytz
import yfinance as yf

load_dotenv()

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load Angel One Token Map and Session
    try:
        print("[AngelOne] Loading tokens on boot...")
        await angel_service.load_tokens()
        # Fire session connect in background so boot doesn't hang if credentials are wrong
        asyncio.create_task(asyncio.to_thread(angel_service.connect_websocket))
    except Exception as ae:
        print(f"[AngelOne Boot Warning] {ae}")

    # Start all background services on boot
    tasks = [
        asyncio.create_task(socket_manager.watch_and_broadcast()),
        asyncio.create_task(run_nse_worker()),
        asyncio.create_task(run_bse_worker()),
        asyncio.create_task(keep_alive_task()),
    ]
    print("[UPDATEX] All services started (including Keep-Alive).")
    yield
    # Shutdown — cancel all background tasks
    for t in tasks:
        t.cancel()
    print("[UPDATEX] All services stopped.")

app = FastAPI(
    title="UPDATEX API",
    description="Unified Market Intelligence Backend",
    version="6.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def keep_alive_task():
    """Pings the app itself every 10 mins to prevent Render sleep"""
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        print("[Keep-Alive] No RENDER_EXTERNAL_URL found in .env. Skipping self-ping.")
        return
    
    print(f"[Keep-Alive] Started. Pinging {url} every 10 mins.")
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(600) # 10 minutes
            try:
                async with session.get(f"{url}/api/ping", timeout=10) as resp:
                    print(f"[Keep-Alive] Ping sent to {url}. Status: {resp.status}")
            except Exception as e:
                print(f"[Keep-Alive Error] {e}")

@app.get("/api/ping")
async def ping():
    return {"status": "pong"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await socket_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        socket_manager.disconnect(websocket)

@app.get("/api/status")
async def get_status():
    services_dir = os.path.join(os.path.dirname(__file__), "services")
    services = {}
    for name, fname in [("NSE", "nse_service.json"), ("BSE", "bse_service.json")]:
        fpath = os.path.join(services_dir, fname)
        count = 0
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f: data = json.load(f)
                count = len(data)
            except: pass
        services[name] = {"count": count}
    return {"status": "online", "websockets": len(socket_manager.active_connections), "services": services}

@app.get("/api/feed")
async def get_feed():
    services_dir = os.path.join(os.path.dirname(__file__), "services")
    result = []
    
    # Mapping of filename to source tag
    sources = {
        "nse_service.json": "NSE",
        "bse_service.json": "BSE"
    }
    
    for fname, source in sources.items():
        fpath = os.path.join(services_dir, fname)
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            item["source"] = source
                        result.extend(data)
                    elif isinstance(data, dict):
                        # Handle cases where data is a single dict or has tables
                        if "Table" in data:
                            for item in data["Table"]:
                                item["source"] = source
                                result.append(item)
                        else:
                            data["source"] = source
                            result.append(data)
            except: pass
    return result

@app.get("/api/scripts")
async def get_scripts():
    if not angel_service.token_map:
        return {"status": "loading", "message": "Scripts are still loading...", "scripts": []}
    
    # Extract unique symbols from the token map
    # Some keys are numeric tokens or lowercase, we want uppercase alphabetic symbols mostly.
    scripts = sorted([k for k in angel_service.token_map.keys() if isinstance(k, str) and not k.isdigit()])
    return {"status": "success", "scripts": scripts}

@app.get("/api/analyze/{symbol}")
async def analyze_script(symbol: str):
    try:
        def fetch_yf():
            # Append .NS to search on NSE via Yahoo Finance
            ticker = yf.Ticker(f"{symbol.upper()}.NS")
            hist_df = ticker.history(period="3y")
            return hist_df

        # Run the synchronous yfinance call in a thread
        hist_df = await asyncio.to_thread(fetch_yf)
        
        historical_data = []
        ltp = None
        analysis = {}
        
        if not hist_df.empty:
            import pandas as pd
            # Current/last close price is our LTP
            ltp = float(hist_df.iloc[-1]['Close'])
            
            # --- 3-Year Data Analysis ---
            # 52 Week High/Low (approx 252 trading days)
            last_year_df = hist_df.tail(252)
            high_52w = float(last_year_df['High'].max())
            low_52w = float(last_year_df['Low'].min())
            
            # Moving Averages
            sma_50 = float(hist_df['Close'].rolling(window=50).mean().iloc[-1]) if len(hist_df) >= 50 else ltp
            sma_200 = float(hist_df['Close'].rolling(window=200).mean().iloc[-1]) if len(hist_df) >= 200 else ltp
            
            # Trend Analysis
            trend = "Neutral"
            if ltp > sma_200 and sma_50 > sma_200:
                trend = "Bullish 🟢"
            elif ltp < sma_200 and sma_50 < sma_200:
                trend = "Bearish 🔴"
            elif ltp > sma_50:
                trend = "Short-term Bullish ↗️"
            else:
                trend = "Short-term Bearish ↘️"
                
            # Basic Support/Resistance (Fibonacci approximate)
            diff = high_52w - low_52w
            resistance = high_52w - (diff * 0.236)
            support = low_52w + (diff * 0.236)
            
            analysis = {
                "trend": trend,
                "sma_50": round(sma_50, 2),
                "sma_200": round(sma_200, 2),
                "high_52w": round(high_52w, 2),
                "low_52w": round(low_52w, 2),
                "support": round(support, 2),
                "resistance": round(resistance, 2)
            }
            
            # --- Instant Momentum Factors ---
            try:
                # RSI (14-period)
                delta = hist_df['Close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                analysis["rsi"] = round(float(rsi.iloc[-1]), 2) if not rsi.isna().all() else 50.0
                
                # Volume Spike
                vol_10d_avg = float(hist_df['Volume'].tail(11).head(10).mean())
                current_vol = float(hist_df['Volume'].iloc[-1])
                analysis["vol_spike"] = round(current_vol / vol_10d_avg, 2) if vol_10d_avg > 0 else 1.0
                
                # MACD
                ema_12 = hist_df['Close'].ewm(span=12, adjust=False).mean()
                ema_26 = hist_df['Close'].ewm(span=26, adjust=False).mean()
                macd_line = ema_12 - ema_26
                signal_line = macd_line.ewm(span=9, adjust=False).mean()
                macd_hist = float(macd_line.iloc[-1] - signal_line.iloc[-1])
                if macd_hist > 0 and macd_hist > float(macd_line.iloc[-2] - signal_line.iloc[-2]):
                    analysis["macd_status"] = "Bullish Crossover 🚀"
                elif macd_hist < 0:
                    analysis["macd_status"] = "Bearish 🔻"
                else:
                    analysis["macd_status"] = "Neutral"
                    
                # % Change Today
                prev_close = float(hist_df['Close'].iloc[-2]) if len(hist_df) > 1 else ltp
                analysis["pct_change"] = round(((ltp - prev_close) / prev_close) * 100, 2)
                
                # Distance from High
                today_high = float(hist_df['High'].iloc[-1])
                analysis["dist_from_high"] = round(((today_high - ltp) / ltp) * 100, 2)
            except Exception as e:
                print(f"Momentum calculation error: {e}")
                pass
            # ----------------------------
            
            # For the table, we only want to send the last 30 days
            table_df = hist_df.tail(30).copy()
            
            # Move Date from index to column
            table_df = table_df.reset_index()
            
            # Format date as YYYY-MM-DD
            table_df['Date'] = table_df['Date'].dt.strftime('%Y-%m-%d')
            
            # Extract only the needed columns to avoid JSON errors
            table_df = table_df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
            historical_data = table_df.to_dict(orient='records')
            
        return {
            "status": "success",
            "symbol": symbol.upper(),
            "ltp": ltp,
            "analysis": analysis,
            "historical": historical_data
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}




app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    # Disable reload because workers write JSON files to the disk
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
