"""
Price Tracker
Uses PumpPortal WebSocket to stream live trades for tracked tokens.
Calculates real-time mcap from each trade event.
No pump.fun HTTP needed — websocket is freely accessible from servers.

Also uses Helius RPC as fallback for bonding curve math.
"""

import asyncio
import aiohttp
import logging
import json
import os
import websockets
from datetime import datetime, timezone

log = logging.getLogger(__name__)

HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "2842e504-2c6d-41a1-b013-962ee1263e23")
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
PUMPPORTAL_WS  = "wss://pumpportal.fun/api/data"

# Global state — tracks live prices from websocket
_live_prices: dict = {}   # ca -> {mcap, trade_count, last_update}
_ws_subscriptions: set = set()
_ws_connection = None


async def get_sol_price(session: aiohttp.ClientSession) -> float:
    """Get SOL/USD price from CoinGecko."""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                d = await r.json()
                return float(d.get('solana', {}).get('usd', 150))
    except Exception as e:
        log.warning(f"SOL price fetch failed: {e}")
    return 150.0


async def pumpportal_ws_loop(get_active_signals_fn, update_price_fn, milestone_callback_fn):
    """
    Main WebSocket loop — connects to PumpPortal and streams trades.
    Auto-subscribes to new tokens as they come in.
    Auto-reconnects on disconnect.
    """
    global _ws_connection, _ws_subscriptions

    while True:
        try:
            log.info("Connecting to PumpPortal WebSocket...")
            async with websockets.connect(
                PUMPPORTAL_WS,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5
            ) as ws:
                _ws_connection = ws
                log.info("PumpPortal WebSocket connected ✅")

                # Subscribe to all currently tracked tokens
                active = get_active_signals_fn()
                for sig in active:
                    ca = sig['ca']
                    if ca not in _ws_subscriptions:
                        await ws.send(json.dumps({
                            "method": "subscribeTokenTrade",
                            "keys": [ca]
                        }))
                        _ws_subscriptions.add(ca)
                        log.info(f"Subscribed to trades for {sig.get('name','?')}")

                async with aiohttp.ClientSession() as session:
                    sol_price = await get_sol_price(session)

                async for message in ws:
                    try:
                        data = json.loads(message)

                        # Skip non-trade messages
                        if 'mint' not in data or 'marketCapSol' not in data:
                            continue

                        ca         = data['mint']
                        mcap_sol   = float(data.get('marketCapSol', 0))
                        mcap_usd   = mcap_sol * sol_price
                        is_buy     = data.get('txType') == 'buy'

                        # Update live prices
                        if ca not in _live_prices:
                            _live_prices[ca] = {
                                'mcap':        mcap_usd,
                                'trade_count': 0,
                                'buy_count':   0,
                                'sell_count':  0,
                                'last_update': datetime.now(timezone.utc).isoformat()
                            }
                        _live_prices[ca]['mcap']        = mcap_usd
                        _live_prices[ca]['trade_count'] += 1
                        _live_prices[ca]['last_update'] = datetime.now(timezone.utc).isoformat()
                        if is_buy:
                            _live_prices[ca]['buy_count']  += 1
                        else:
                            _live_prices[ca]['sell_count'] += 1

                        # Find the signal and update price
                        active = get_active_signals_fn()
                        for sig in active:
                            if sig['ca'] == ca:
                                entry_mcap = sig['marketcap']
                                if entry_mcap > 0:
                                    multiplier = mcap_usd / entry_mcap
                                    update_price_fn(ca, mcap_usd, multiplier)
                                    # Check milestones
                                    prev = sig.get('peak_multiplier', 0)
                                    for milestone in [2, 5, 10, 20, 50, 100]:
                                        if multiplier >= milestone and prev < milestone:
                                            entry_time   = datetime.fromisoformat(sig['timestamp'])
                                            mins_elapsed = (datetime.now(timezone.utc) - entry_time).total_seconds() / 60
                                            await milestone_callback_fn(sig, milestone, mcap_usd, round(mins_elapsed))
                                break

                    except Exception as e:
                        log.warning(f"Trade message processing failed: {e}")

        except Exception as e:
            log.warning(f"PumpPortal WebSocket error: {e} — reconnecting in 5s")
            _ws_connection = None
            await asyncio.sleep(5)


async def subscribe_token(ca: str):
    """Subscribe to live trades for a new token."""
    global _ws_connection, _ws_subscriptions
    if ca in _ws_subscriptions:
        return
    _ws_subscriptions.add(ca)
    if _ws_connection:
        try:
            await _ws_connection.send(json.dumps({
                "method": "subscribeTokenTrade",
                "keys": [ca]
            }))
            log.info(f"Subscribed to trades: {ca[:8]}...")
        except Exception as e:
            log.warning(f"Failed to subscribe to {ca[:8]}: {e}")
            _ws_subscriptions.discard(ca)


def get_live_trade_count(ca: str) -> dict:
    """Get live trade stats for a token from websocket data."""
    return _live_prices.get(ca, {
        'mcap': 0, 'trade_count': 0,
        'buy_count': 0, 'sell_count': 0
    })


async def get_token_market_cap_helius(mint: str) -> float | None:
    """
    Fallback: read bonding curve directly from Solana blockchain via Helius.
    Used when websocket hasn't received data for a token yet.
    """
    import struct, base64
    try:
        async with aiohttp.ClientSession() as session:
            sol_price = await get_sol_price(session)

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getProgramAccounts",
                "params": [
                    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
                    {
                        "encoding": "base64",
                        "filters": [
                            {"dataSize": 49},
                            {"memcmp": {"offset": 8, "bytes": mint}}
                        ]
                    }
                ]
            }
            async with session.post(
                HELIUS_RPC_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    accounts = data.get('result', [])
                    if accounts:
                        raw = base64.b64decode(accounts[0]['account']['data'][0])
                        if len(raw) >= 49:
                            offset = 8
                            vtr = struct.unpack_from('<Q', raw, offset)[0]; offset += 8
                            vsr = struct.unpack_from('<Q', raw, offset)[0]
                            if vtr > 0:
                                price_sol = (vsr / 1e9) / (vtr / 1e6)
                                mcap      = price_sol * 1_000_000_000 * sol_price
                                return round(mcap, 2)
    except Exception as e:
        log.warning(f"Helius fallback failed for {mint}: {e}")
    return None
