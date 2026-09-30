import os
import pyotp
import asyncio
import aiohttp
import re
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from dotenv import load_dotenv
import pytz

load_dotenv()

class AngelOneService:
    def __init__(self):
        self.api_key = os.getenv("ANGEL_API_KEY")
        self.client_id = os.getenv("ANGEL_CLIENT_ID")
        self.password = os.getenv("ANGEL_PASSWORD")
        self.totp_key = os.getenv("ANGEL_TOTP_KEY")
        
        self.smart_api = SmartConnect(api_key=self.api_key)
        self.sws = None
        self.is_connected = False
        self.data_queue = asyncio.Queue()
        self.active_subscriptions = set() # Set of tokens
        self.token_map = {}
        self.subscribers = {} # token (str) -> set of asyncio.Queue

    async def load_tokens(self):
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    data = await resp.json()
                    for item in data:
                        exch = item.get('exch_seg')
                        symbol_str = item.get('symbol', '')
                        name_str = item.get('name', '')
                        token_val = item.get('token')
                        
                        if exch == 'NSE' and symbol_str.endswith('-EQ'):
                            clean_name = symbol_str.replace('-EQ', '')
                            self.token_map[clean_name] = {"token": token_val, "exch": "NSE", "tradingsymbol": symbol_str}
                        
                        elif exch == 'BSE':
                            entry = {"token": token_val, "exch": "BSE", "tradingsymbol": symbol_str}
                            if symbol_str: self.token_map[symbol_str.upper()] = entry
                            if name_str: self.token_map[name_str.upper()] = entry
                            if token_val: self.token_map[str(token_val)] = entry
                            
                        elif exch == 'NFO' and item.get('instrumenttype') in ['OPTIDX', 'OPTSTK']:
                            # e.g. name="NIFTY", symbol="NIFTY25MAY23000CE"
                            if not hasattr(self, 'nfo_options'):
                                self.nfo_options = []
                            try:
                                strike = float(item.get('strike', 0)) / 100.0  # Angel stores as 2250000.000000
                                opt_type = "CE" if symbol_str.endswith("CE") else ("PE" if symbol_str.endswith("PE") else "")
                                self.nfo_options.append({
                                    "name": name_str.upper(),
                                    "symbol": symbol_str,
                                    "token": token_val,
                                    "strike": strike,
                                    "type": opt_type,
                                    "expiry": item.get('expiry'),
                                    "lotsize": int(item.get('lotsize', 0) or 0)
                                })
                            except: pass
                    print(f"[AngelOne] Loaded {len(self.token_map)} equity/BSE symbols and {len(getattr(self, 'nfo_options', []))} NFO options.")
                    
                    # Hardcode primary indices since they lack -EQ suffix in master
                    self.token_map["NIFTY"] = {"token": "26000", "exch": "NSE", "tradingsymbol": "Nifty 50"}
                    self.token_map["BANKNIFTY"] = {"token": "26009", "exch": "NSE", "tradingsymbol": "Nifty Bank"}
                    self.token_map["FINNIFTY"] = {"token": "26037", "exch": "NSE", "tradingsymbol": "Nifty Fin Service"}
                    
        except Exception as e:
            print(f"[AngelOne] Error loading tokens: {e}")

    async def get_ltp(self, symbol_or_code):
        """Fetch LTP using Angel One REST API."""
        def _fetch():
            target = str(symbol_or_code).upper()
            # Normalization helper
            def normalize(s):
                return re.sub(r'[^A-Z0-9]', '', str(s).upper())

            norm_target = normalize(symbol_or_code)
            entry = self.token_map.get(target) or self.token_map.get(norm_target)
            
            # Fuzzy fallback
            if not entry:
                for k, v in self.token_map.items():
                    if norm_target in normalize(k):
                        entry = v
                        break
                        
            if not entry:
                return None
            
            try:
                # Ensure session
                if not hasattr(self, 'auth_token'):
                    self.generate_session()
                
                # Using getMarketData instead of ltpData to avoid needing tradingsymbol
                exchangeTokens = {entry["exch"]: [str(entry["token"])]}
                res = self.smart_api.getMarketData("LTP", exchangeTokens)
                
                if res and res.get('status') and res.get('data'):
                    fetched = res['data'].get('fetched', [])
                    if fetched:
                        return float(fetched[0].get('ltp', 0))
            except Exception as e:
                print(f"[AngelOne] get_ltp error: {e}")
            return None

        return await asyncio.to_thread(_fetch)

    async def get_historical_data(self, symbol_or_code, interval="ONE_DAY", days=60, from_date=None, to_date=None):
        """Fetch Historical Candle Data from Angel One"""
        def _fetch():
            target = str(symbol_or_code).upper()
            # Normalization helper
            def normalize(s):
                return re.sub(r'[^A-Z0-9]', '', str(s).upper())

            norm_target = normalize(symbol_or_code)
            entry = self.token_map.get(target) or self.token_map.get(norm_target)
            
            if not entry:
                for k, v in self.token_map.items():
                    if norm_target in normalize(k):
                        entry = v
                        break
            
            if not entry:
                for opt in getattr(self, 'nfo_options', []):
                    if normalize(opt['symbol']) == norm_target or opt['symbol'].upper() == target:
                        entry = {"token": opt['token'], "exch": "NFO"}
                        break
            
            if not entry:
                return None
            
            try:
                if not hasattr(self, 'auth_token'):
                    self.generate_session()
                
                from datetime import datetime, timedelta
                
                if from_date and to_date:
                    from_str = from_date
                    to_str = to_date
                else:
                    now = datetime.now(pytz.timezone("Asia/Kolkata")).replace(tzinfo=None)
                    past = now - timedelta(days=days)
                    from_str = past.strftime("%Y-%m-%d %H:%M")
                    to_str = now.strftime("%Y-%m-%d %H:%M")
                
                historicParam = {
                    "exchange": entry["exch"],
                    "symboltoken": str(entry["token"]),
                    "interval": interval,
                    "fromdate": from_str,
                    "todate": to_str
                }
                
                res = self.smart_api.getCandleData(historicParam)
                if res and res.get('status') and res.get('data'):
                    import pandas as pd
                    # data columns: timestamp, open, high, low, close, volume
                    df = pd.DataFrame(res['data'], columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
                    df['Date'] = pd.to_datetime(df['Date'])
                    return df
            except Exception as e:
                print(f"[AngelOne] get_historical_data error: {e}")
            return None

        return await asyncio.to_thread(_fetch)

    def generate_session(self):
        """Authenticates and starts a session with Angel One."""
        try:
            totp = pyotp.TOTP(self.totp_key).now()
            data = self.smart_api.generateSession(self.client_id, self.password, totp)
            
            if data.get('status'):
                print(f"[AngelOne] Session generated for {self.client_id}")
                self.auth_token = data['data']['jwtToken']
                self.feed_token = self.smart_api.getfeedToken()
                return True
            else:
                print(f"[AngelOne] Session failed: {data.get('message')}")
                return False
        except Exception as e:
            print(f"[AngelOne] Auth Error: {e}")
            return False

    def on_ticks(self, ticks):
        """Callback for incoming market data."""
        # Bridge sync callback to async queue
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(self.data_queue.put_nowait, ticks)
        
        # Dispatch to active queue subscribers
        for tick in ticks:
            token = str(tick.get("token"))
            if token in self.subscribers:
                for q in self.subscribers[token]:
                    try:
                        loop.call_soon_threadsafe(q.put_nowait, tick)
                    except:
                        pass

    async def subscribe_queue(self, symbol_token, exchange="NSE"):
        """Creates an async queue and subscribes it to receive ticks for a token."""
        token_str = str(symbol_token)
        if token_str not in self.subscribers:
            self.subscribers[token_str] = set()
            
        q = asyncio.Queue()
        self.subscribers[token_str].add(q)
        
        if len(self.subscribers[token_str]) == 1:
            # First subscriber, trigger exchange subscription
            def _sub():
                try:
                    self.subscribe(token_str, exchange)
                except Exception as e:
                    print(f"[AngelOne] Subscribe failed: {e}")
            await asyncio.to_thread(_sub)
            
        return q

    async def unsubscribe_queue(self, symbol_token, q, exchange="NSE"):
        """Removes an async queue and unsubscribes from the exchange if no subscribers left."""
        token_str = str(symbol_token)
        if token_str in self.subscribers and q in self.subscribers[token_str]:
            self.subscribers[token_str].remove(q)
            if not self.subscribers[token_str]:
                del self.subscribers[token_str]
                # No more subscribers, unsubscribe from the exchange
                def _unsub():
                    try:
                        self.unsubscribe(token_str, exchange)
                    except Exception as e:
                        print(f"[AngelOne] Unsubscribe failed: {e}")
                await asyncio.to_thread(_unsub)

    def on_connect(self, ws, response):
        print("[AngelOne Socket] Connected")
        self.is_connected = True

    def on_close(self, ws, code, reason):
        print(f"[AngelOne Socket] Closed: {reason}")
        self.is_connected = False

    def connect_websocket(self):
        """Starts the SmartWebSocketV2 connection."""
        if not hasattr(self, 'auth_token'):
            if not self.generate_session():
                return
        
        self.sws = SmartWebSocketV2(self.auth_token, self.api_key, self.client_id, self.feed_token)
        self.sws.on_ticks = self.on_ticks
        self.sws.on_open = self.on_connect
        self.sws.on_close = self.on_close
        
        # Start in a separate thread because sws.connect() is blocking
        import threading
        threading.Thread(target=self.sws.connect, daemon=True).start()

    def subscribe(self, symbol_token, exchange="NSE"):
        """Subscribe to a specific symbol for live ticks."""
        if not self.sws or not self.is_connected:
            self.connect_websocket()
            # Give it a second to connect
            import time
            time.sleep(2)
        
        correlation_id = f"sub_{symbol_token}"
        mode = 3 # Changed to 3 for Full Quote (Depth)
        
        # In V2, we use action 1 for subscribe
        payload = [{
            "exchangeType": 1 if exchange == "NSE" else 3,
            "tokens": [str(symbol_token)]
        }]
        
        try:
            self.sws.subscribe(correlation_id, mode, payload)
            self.active_subscriptions.add(symbol_token)
            print(f"[AngelOne] Subscribed to {symbol_token}")
        except Exception as e:
            print(f"[AngelOne] Subscription error: {e}")

    def unsubscribe(self, symbol_token, exchange="NSE"):
        """Unsubscribe from a symbol."""
        if self.sws and self.is_connected:
            correlation_id = f"unsub_{symbol_token}"
            mode = 1
            payload = [{
                "exchangeType": 1 if exchange == "NSE" else 3,
                "tokens": [str(symbol_token)]
            }]
            self.sws.unsubscribe(correlation_id, mode, payload)
            if symbol_token in self.active_subscriptions:
                self.active_subscriptions.remove(symbol_token)

# Singleton instance
angel_service = AngelOneService()
