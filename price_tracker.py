"""
Price Tracker
Uses PumpPortal WebSocket with asyncio.Queue architecture.
- Queue-based subscriptions (thread-safe, instant, crash-proof)
- Auto-reconnects on disconnect
- No global WS connection touching from outside
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

# Global queue — bot.py puts CAs here, WS loop subscribes instantly
subscription_queue: asyncio.Queue = asyncio.Queue()

# Live trade data per CA
_live_prices: dict = {}


async def get_sol_price(session: aiohttp.ClientSession) -> float:
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
    Main WebSocket loop with queue-based subscription architecture.
    Two concurrent tasks:
      1. subscribe_loop — drains the queue and subscribes instantly
      2. read_loop — processes incoming trade events
    Auto-reconnects on any error.
    """
    while True:
        try:
            log.info("Connecting to PumpPortal WebSocket...")
            async with websockets.connect(
                PUMPPORTAL_WS,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5
            ) as ws:
                log.info("PumpPortal WebSocket connected ✅")

                # Re-subscribe to all active tokens on reconnect
                active = get_active_signals_fn()
                for sig in active:
                    await subscription_queue.put(sig['ca'])

                async with aiohttp.ClientSession() as session:
                    sol_price = await get_sol_price(session)
                sol_price_updated = datetime.now(timezone.utc)

                async def subscribe_loop():
                    """Drain subscription queue and subscribe instantly."""
                    while True:
                        ca = await subscription_queue.get()
                        try:
                            await ws.send(json.dumps({
                                "method": "subscribeTokenTrade",
                                "keys": [ca]
                            }))
                            log.info(f"⚡ Subscribed to {ca[:8]}...")
                        except Exception as e:
                            log.warning(f"Subscribe failed for {ca[:8]}: {e}")
                            # Put back in queue for retry after reconnect
                            await subscription_queue.put(ca)

                async def read_loop():
                    """Process incoming trade events."""
                    nonlocal sol_price, sol_price_updated
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            if 'mint' not in data or 'marketCapSol' not in data:
                                continue

                            ca       = data['mint']
                            mcap_sol = float(data.get('marketCapSol', 0))
                            is_buy   = data.get('txType') == 'buy'

                            # Refresh SOL price every 5 mins
                            now = datetime.now(timezone.utc)
                            if (now - sol_price_updated).total_seconds() > 300:
                                async with aiohttp.ClientSession() as s:
                                    sol_price = await get_sol_price(s)
                                sol_price_updated = now

                            mcap_usd = mcap_sol * sol_price

                            # Update live prices
                            if ca not in _live_prices:
                                _live_prices[ca] = {
                                    'mcap': mcap_usd,
                                    'trade_count': 0,
                                    'buy_count': 0,
                                    'sell_count': 0,
                                    'last_update': now.isoformat()
                                }
                            p = _live_prices[ca]
                            p['mcap']        = mcap_usd
                            p['trade_count'] += 1
                            p['last_update'] = now.isoformat()
                            if is_buy: p['buy_count']  += 1
                            else:      p['sell_count'] += 1

                            # Update tracker and check milestones
                            active = get_active_signals_fn()
                            for sig in active:
                                if sig['ca'] == ca:
                                    entry_mcap = sig['marketcap']
                                    if entry_mcap > 0:
                                        multiplier = mcap_usd / entry_mcap
                                        update_price_fn(ca, mcap_usd, multiplier)
                                        prev = sig.get('peak_multiplier', 0)
                                        for milestone in [2, 5, 10, 20, 50, 100]:
                                            if multiplier >= milestone and prev < milestone:
                                                entry_time   = datetime.fromisoformat(sig['timestamp'])
                                                mins_elapsed = (now - entry_time).total_seconds() / 60
                                                await milestone_callback_fn(sig, milestone, mcap_usd, round(mins_elapsed))
                                    break

                        except Exception as e:
                            log.warning(f"Trade message error: {e}")

                # Run both loops concurrently
                await asyncio.gather(subscribe_loop(), read_loop())

        except Exception as e:
            log.warning(f"PumpPortal WebSocket error: {e} — reconnecting in 5s")
            await asyncio.sleep(5)


async def subscribe_token(ca: str):
    """
    Instantly queue a CA for WebSocket subscription.
    Called the moment a CA is parsed — before any safety checks run.
    """
    await subscription_queue.put(ca)
    log.info(f"⚡ Queued subscription for {ca[:8]}...")


def get_live_trade_count(ca: str) -> dict:
    return _live_prices.get(ca, {
        'mcap': 0, 'trade_count': 0,
        'buy_count': 0, 'sell_count': 0
    })


async def get_token_market_cap_helius(mint: str) -> float | None:
    """Fallback: read bonding curve from Solana blockchain via Helius."""
    import struct, base64
    try:
        async with aiohttp.ClientSession() as session:
            sol_price = await get_sol_price(session)
            payload = {
                "jsonrpc": "2.0", "id": 1,
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
            async with session.post(HELIUS_RPC_URL, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=10)) as r:
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
                                return round(price_sol * 1_000_000_000 * sol_price, 2)
    except Exception as e:
        log.warning(f"Helius fallback failed for {mint}: {e}")
    return None
