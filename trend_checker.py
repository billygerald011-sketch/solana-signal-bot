"""
Trend Checker
Checks trending status via:
1. pump.fun front page / king of hill
2. Nitter search for recent tweets mentioning the token
3. Same-meta detection from our own signals data
4. Google Trends (pytrends)
"""

import asyncio
import aiohttp
import logging
import re
from datetime import datetime, timezone, timedelta
from tracker import get_all_signals

log = logging.getLogger(__name__)


async def check_pumpfun_trending(session, ca: str) -> dict:
    """Check if token appears on pump.fun trending/front page."""
    result = {'on_front_page': False, 'king_of_hill': False, 'front_page_rank': None}
    try:
        # Check king of hill
        url = f"https://frontend-api.pump.fun/coins/{ca}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                d = await r.json()
                result['king_of_hill'] = d.get('is_currently_live', False)

        # Check front page (recently created coins sorted by volume)
        front_url = "https://frontend-api.pump.fun/coins?offset=0&limit=50&sort=volume&order=DESC&includeNsfw=false"
        async with session.get(front_url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                coins = await r.json()
                for i, coin in enumerate(coins):
                    if coin.get('mint') == ca:
                        result['on_front_page'] = True
                        result['front_page_rank'] = i + 1
                        break
    except Exception as e:
        log.warning(f"pump.fun trending check failed: {e}")
    return result


async def check_twitter_mentions(session, name: str, ticker: str) -> dict:
    """Search nitter for recent mentions of token name/ticker."""
    result = {'recent_tweet_count': 0, 'trending_on_twitter': False}
    try:
        query = ticker if ticker else name
        query = query.replace(' ', '+')
        nitter_instances = [
            f"https://nitter.net/search?q={query}&f=tweets",
            f"https://nitter.privacydev.net/search?q={query}&f=tweets",
        ]
        html = None
        for url in nitter_instances:
            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=8),
                    headers={'User-Agent': 'Mozilla/5.0'}
                ) as r:
                    if r.status == 200:
                        html = await r.text()
                        break
            except Exception:
                continue

        if html:
            # Count tweet items on page
            tweet_count = len(re.findall(r'class="tweet-content', html))
            result['recent_tweet_count'] = tweet_count
            result['trending_on_twitter'] = tweet_count >= 5
    except Exception as e:
        log.warning(f"Twitter mention check failed for {name}: {e}")
    return result


async def check_google_trends(name: str) -> dict:
    """Check Google Trends for the token name."""
    result = {'google_trending': False, 'google_interest': 0}
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl='en-US', tz=360)
        pytrends.build_payload([name], timeframe='now 1-H')
        data = pytrends.interest_over_time()
        if not data.empty and name in data.columns:
            latest = int(data[name].iloc[-1])
            result['google_interest']  = latest
            result['google_trending']  = latest >= 50
    except Exception as e:
        log.warning(f"Google Trends check failed for {name}: {e}")
    return result


def check_same_meta(signal: dict) -> dict:
    """
    Check if similar coins are pumping today in our data.
    Detects meta themes like: animals, countries, AI, memes, etc.
    """
    result = {'meta_hot': False, 'meta_theme': None, 'meta_win_rate': 0}
    try:
        name = signal.get('name', '').lower()

        # Detect theme
        themes = {
            'animal':   ['cat', 'dog', 'frog', 'ape', 'bear', 'bull', 'shark', 'wolf', 'fox', 'fish', 'bird', 'pepe', 'doge', 'shib'],
            'country':  ['usa', 'uk', 'nigeria', 'brazil', 'china', 'india', 'france', 'germany', 'japan', 'korea', 'mexico', 'country', 'nation'],
            'ai':       ['ai', 'gpt', 'llm', 'robot', 'neural', 'agent', 'claude', 'openai'],
            'meme':     ['chad', 'wojak', 'npc', 'based', 'cope', 'sigma', 'rizz', 'slay', 'bussin', 'brainrot'],
            'food':     ['pizza', 'burger', 'taco', 'sushi', 'noodle', 'coffee', 'beer', 'wine'],
            'space':    ['moon', 'mars', 'rocket', 'galaxy', 'star', 'solar', 'cosmic', 'nebula'],
        }

        detected_theme = None
        for theme, keywords in themes.items():
            if any(kw in name for kw in keywords):
                detected_theme = theme
                break

        if not detected_theme:
            return result

        result['meta_theme'] = detected_theme

        # Check how same-theme coins performed today
        today = datetime.now(timezone.utc).date().isoformat()
        all_signals = get_all_signals()
        same_theme_today = []

        for s in all_signals:
            if not s['timestamp'].startswith(today):
                continue
            s_name = s.get('name', '').lower()
            keywords = themes[detected_theme]
            if any(kw in s_name for kw in keywords):
                same_theme_today.append(s)

        if len(same_theme_today) >= 3:
            winners = [s for s in same_theme_today if s.get('hit_2x')]
            win_rate = round(len(winners) / len(same_theme_today) * 100)
            result['meta_win_rate'] = win_rate
            result['meta_hot']      = win_rate >= 40  # 40%+ of same-theme coins 2x'd today
            result['meta_sample']   = len(same_theme_today)

    except Exception as e:
        log.warning(f"Same-meta check failed: {e}")
    return result


async def check_volume_velocity(session, ca: str, current_volume: int) -> dict:
    """Check if volume is accelerating by comparing to pump.fun data."""
    result = {'volume_accelerating': False, 'volume_velocity_score': 0}
    try:
        url = f"https://frontend-api.pump.fun/coins/{ca}/trades?limit=20&offset=0"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                trades = await r.json()
                if trades and len(trades) >= 5:
                    # Count trades in last 5 mins vs 5 mins before that
                    now = datetime.now(timezone.utc)
                    recent = sum(1 for t in trades
                                if (now - datetime.fromisoformat(
                                    t.get('timestamp','').replace('Z','')
                                )).total_seconds() < 300
                                ) if trades else 0
                    result['recent_trade_count']     = recent
                    result['volume_accelerating']    = recent >= 5
                    result['volume_velocity_score']  = min(recent * 10, 100)
    except Exception as e:
        log.warning(f"Volume velocity check failed: {e}")
    return result


async def check_all_trends(signal: dict, session: aiohttp.ClientSession) -> dict:
    """Run all trend checks concurrently."""
    ca     = signal.get('ca', '')
    name   = signal.get('name', '')
    ticker = name.upper().replace(' ', '')

    tasks = [
        check_pumpfun_trending(session, ca),
        check_twitter_mentions(session, name, ticker),
        check_volume_velocity(session, ca, signal.get('volume', 0)),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    pumpfun_trend = results[0] if isinstance(results[0], dict) else {}
    twitter_trend = results[1] if isinstance(results[1], dict) else {}
    vol_velocity  = results[2] if isinstance(results[2], dict) else {}

    # Google trends is sync so run separately (won't block long)
    google_trend  = await asyncio.to_thread(check_google_trends, name)
    same_meta     = check_same_meta(signal)

    return {
        'on_front_page':        pumpfun_trend.get('on_front_page', False),
        'front_page_rank':      pumpfun_trend.get('front_page_rank'),
        'king_of_hill':         pumpfun_trend.get('king_of_hill', False),
        'recent_tweet_count':   twitter_trend.get('recent_tweet_count', 0),
        'trending_on_twitter':  twitter_trend.get('trending_on_twitter', False),
        'google_interest':      google_trend.get('google_interest', 0) if isinstance(google_trend, dict) else 0,
        'google_trending':      google_trend.get('google_trending', False) if isinstance(google_trend, dict) else False,
        'meta_hot':             same_meta.get('meta_hot', False),
        'meta_theme':           same_meta.get('meta_theme'),
        'meta_win_rate':        same_meta.get('meta_win_rate', 0),
        'volume_accelerating':  vol_velocity.get('volume_accelerating', False),
        'volume_velocity_score':vol_velocity.get('volume_velocity_score', 0),
        'recent_trade_count':   vol_velocity.get('recent_trade_count', 0),
    }
