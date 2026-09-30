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






app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    # Disable reload because workers write JSON files to the disk
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
