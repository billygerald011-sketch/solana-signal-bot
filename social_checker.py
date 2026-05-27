"""
Social Checker - 4 Layer Architecture
Layer 1: IPFS metadata from PumpPortal WebSocket stream (instant T+0)
Layer 2: Helius getAsset IPFS fetch (T+1s)
Layer 3: DuckDuckGo search (fallback T+5s)
Layer 4: Telegram global SearchRequest (community finder T+3s)
"""

import asyncio
import aiohttp
import logging
import re
import os
import json

log = logging.getLogger(__name__)

HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "2842e504-2c6d-41a1-b013-962ee1263e23")

# Global store for metadata URIs extracted from WebSocket stream
_ws_metadata_cache: dict = {}  # ca -> {twitter, telegram, website, image}


def store_ws_metadata(ca: str, metadata: dict):
    """Called by price_tracker when WebSocket stream contains token metadata."""
    if ca and metadata:
        _ws_metadata_cache[ca] = metadata
        log.info(f"Cached WS metadata for {ca[:8]}: twitter={bool(metadata.get('twitter'))} tg={bool(metadata.get('telegram'))}")


# ── Layer 1: WebSocket stream metadata ───────────────────────────────────────
def get_ws_metadata(ca: str) -> dict:
    """Get socials from WebSocket stream cache (instant, T+0)."""
    return _ws_metadata_cache.get(ca, {})


# ── Layer 2: Helius getAsset + IPFS fetch ────────────────────────────────────
async def get_helius_ipfs_socials(session: aiohttp.ClientSession, ca: str) -> dict:
    """Fetch token metadata URI via Helius DAS API then fetch IPFS JSON."""
    result = {'twitter': '', 'telegram': '', 'website': '', 'image': ''}
    try:
        # Step 1: Get metadata URI from Helius
        url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getAsset",
            "params": {"id": ca}
        }
        async with session.post(url, json=payload,
                                timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                data = await r.json()
                uri = data.get('result', {}).get('content', {}).get('json_uri', '')

                if not uri:
                    return result

                # Step 2: Fetch IPFS metadata JSON
                # Try multiple IPFS gateways for reliability
                ipfs_hash = uri.replace('https://ipfs.io/ipfs/', '').replace('https://cf-ipfs.com/ipfs/', '').replace('ipfs://', '')
                gateways = [
                    uri,  # original URI first
                    f"https://cf-ipfs.com/ipfs/{ipfs_hash}",
                    f"https://ipfs.io/ipfs/{ipfs_hash}",
                    f"https://gateway.pinata.cloud/ipfs/{ipfs_hash}",
                ]

                for gateway_url in gateways:
                    try:
                        async with session.get(
                            gateway_url,
                            timeout=aiohttp.ClientTimeout(total=6)
                        ) as meta_r:
                            if meta_r.status == 200:
                                meta = await meta_r.json(content_type=None)
                                twitter  = meta.get('twitter', '')  or meta.get('twitter_url', '')  or ''
                                telegram = meta.get('telegram', '') or meta.get('telegram_url', '') or ''
                                website  = meta.get('website', '')  or meta.get('website_url', '')  or ''
                                image    = meta.get('image', '') or ''

                                # Normalize URLs
                                if twitter and not twitter.startswith('http'):
                                    twitter = f"https://x.com/{twitter.lstrip('@')}"
                                if telegram and not telegram.startswith('http'):
                                    telegram = f"https://t.me/{telegram.lstrip('@')}"
                                if website and not website.startswith('http'):
                                    website = f"https://{website}"

                                if twitter or telegram or website:
                                    result.update({
                                        'twitter':  twitter,
                                        'telegram': telegram,
                                        'website':  website,
                                        'image':    image,
                                    })
                                    log.info(f"Helius IPFS socials found for {ca[:8]}: tw={bool(twitter)} tg={bool(telegram)} web={bool(website)}")
                                    return result
                    except Exception:
                        continue

    except Exception as e:
        log.warning(f"Helius IPFS fetch failed for {ca}: {e}")
    return result


# ── Layer 3: DuckDuckGo search ────────────────────────────────────────────────
async def search_ddg_socials(name: str, ca: str) -> dict:
    """Search DuckDuckGo for token socials. Runs in thread to avoid blocking."""
    result = {'twitter': '', 'telegram': '', 'website': ''}
    try:
        def _search():
            from duckduckgo_search import DDGS
            found = {'twitter': '', 'telegram': '', 'website': ''}
            query = f'"{name}" solana memecoin (site:twitter.com OR site:x.com OR site:t.me)'
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=5))
                    for res in results:
                        url = res.get('href', '')
                        if not found['twitter'] and ('twitter.com' in url or 'x.com' in url):
                            found['twitter'] = url
                        elif not found['telegram'] and 't.me' in url:
                            found['telegram'] = url
                        elif not found['website'] and 'pump.fun' not in url and 'dexscreener' not in url:
                            found['website'] = url
            except Exception as e:
                log.warning(f"DDG search failed: {e}")
            return found

        result = await asyncio.wait_for(
            asyncio.to_thread(_search),
            timeout=8.0
        )
        if result.get('twitter') or result.get('telegram'):
            log.info(f"DDG found socials for {name}: {result}")

    except asyncio.TimeoutError:
        log.warning(f"DDG search timed out for {name}")
    except Exception as e:
        log.warning(f"DDG search error for {name}: {e}")
    return result


# ── Layer 4: Telegram global search ──────────────────────────────────────────
async def search_telegram_global(client, name: str) -> dict:
    """Search Telegram globally for token community groups."""
    result = {'telegram': '', 'telegram_members': 0}
    if not client:
        return result
    try:
        from telethon.tl.functions.contacts import SearchRequest
        search_result = await client(SearchRequest(q=name, limit=3))
        for chat in search_result.chats:
            username = getattr(chat, 'username', '')
            members  = getattr(chat, 'participants_count', 0) or 0
            if username and members > 10:
                result['telegram']         = f"https://t.me/{username}"
                result['telegram_members'] = members
                log.info(f"Telegram global search found {username} ({members} members) for {name}")
                break
    except Exception as e:
        log.warning(f"Telegram global search failed for {name}: {e}")
    return result


# ── Website liveness check ────────────────────────────────────────────────────
async def check_url_live(session: aiohttp.ClientSession, url: str) -> bool:
    if not url:
        return False
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=6),
                               allow_redirects=True) as r:
            return r.status == 200
    except Exception:
        return False


# ── Telegram group member count ───────────────────────────────────────────────
async def get_telegram_members(client, telegram_url: str) -> int:
    if not client or not telegram_url:
        return 0
    try:
        match = re.search(r't\.me/([A-Za-z0-9_]+)', telegram_url)
        if not match:
            return 0
        entity = await client.get_entity(match.group(1))
        return getattr(entity, 'participants_count', 0) or 0
    except Exception:
        return 0


# ── Main entry point ──────────────────────────────────────────────────────────
async def check_socials(signal: dict, tg_client=None, safety_data: dict = None) -> dict:
    """
    Run all 4 layers concurrently and merge results.
    Priority: WS cache > Helius IPFS > pump.fun API > DDG search > TG search
    """
    ca   = signal.get('ca', '')
    name = signal.get('name', '')

    # Layer 1: Check WebSocket cache first (instant)
    ws_meta = get_ws_metadata(ca)

    async with aiohttp.ClientSession() as session:
        # Layer 2: Helius IPFS + Layer 3: DDG run concurrently
        helius_task = get_helius_ipfs_socials(session, ca)
        ddg_task    = search_ddg_socials(name, ca)
        tg_task     = search_telegram_global(tg_client, name) if tg_client else asyncio.sleep(0)

        helius_data, ddg_data, tg_search = await asyncio.gather(
            helius_task, ddg_task, tg_task,
            return_exceptions=True
        )

    helius_data = helius_data if isinstance(helius_data, dict) else {}
    ddg_data    = ddg_data    if isinstance(ddg_data,    dict) else {}
    tg_search   = tg_search   if isinstance(tg_search,   dict) else {}

    # Layer 4: pump.fun API data from safety_checker
    pump_data = safety_data or {}

    # Merge all sources — priority order
    twitter_url  = (ws_meta.get('twitter')  or helius_data.get('twitter')  or
                    pump_data.get('twitter_url') or ddg_data.get('twitter')  or
                    signal.get('twitter_url', ''))

    telegram_url = (ws_meta.get('telegram') or helius_data.get('telegram') or
                    pump_data.get('telegram_url') or ddg_data.get('telegram') or
                    tg_search.get('telegram') or signal.get('telegram_url', ''))

    website_url  = (ws_meta.get('website')  or helius_data.get('website')  or
                    pump_data.get('website_url') or ddg_data.get('website')  or
                    signal.get('website_url', ''))

    log.info(f"Final socials for {name}: tw={bool(twitter_url)} tg={bool(telegram_url)} web={bool(website_url)}")

    # Check website liveness + Telegram members concurrently
    async with aiohttp.ClientSession() as session:
        website_live_task = check_url_live(session, website_url)
        tg_members_task   = (get_telegram_members(tg_client, telegram_url)
                             if tg_client and telegram_url else asyncio.sleep(0))

        website_live, tg_members = await asyncio.gather(
            website_live_task, tg_members_task,
            return_exceptions=True
        )

    website_live = website_live if isinstance(website_live, bool) else False
    tg_members   = tg_members   if isinstance(tg_members,   int)  else tg_search.get('telegram_members', 0)

    return {
        'twitter_url':       twitter_url,
        'telegram_url':      telegram_url,
        'website_url':       website_url,
        'has_twitter':       bool(twitter_url),
        'has_telegram':      bool(telegram_url),
        'has_website':       bool(website_url),
        'website_live':      website_live,
        'twitter_active':    bool(twitter_url),
        'twitter_followers': 0,
        'telegram_members':  tg_members,
        'telegram_active':   tg_members > 50,
        'pumpfun_replies':   pump_data.get('reply_count', 0),
        # Source tracking for analysis
        'social_source':     ('ws_cache' if ws_meta.get('twitter') or ws_meta.get('telegram')
                              else 'helius' if helius_data.get('twitter') or helius_data.get('telegram')
                              else 'pump_api' if pump_data.get('twitter_url') or pump_data.get('telegram_url')
                              else 'ddg' if ddg_data.get('twitter') or ddg_data.get('telegram')
                              else 'tg_search' if tg_search.get('telegram')
                              else 'none'),
    }
