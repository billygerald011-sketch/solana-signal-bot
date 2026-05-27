"""
Social Checker - Phase 1
Checks Twitter, website, pump.fun replies, and Telegram group activity.
All results saved to signal data for pattern analysis later.
"""

import asyncio
import aiohttp
import logging
import re

log = logging.getLogger(__name__)


async def check_url_live(session, url: str) -> bool:
    if not url:
        return False
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=6), allow_redirects=True) as r:
            return r.status == 200
    except Exception:
        return False


async def check_twitter(session, twitter_url: str) -> dict:
    result = {
        'has_twitter': False,
        'twitter_followers': 0,
        'tweet_count': 0,
        'is_active': False,
    }
    if not twitter_url:
        return result
    try:
        username = re.search(r'(?:twitter|x)\.com/([A-Za-z0-9_]+)', twitter_url)
        if not username:
            return result
        handle = username.group(1)

        nitter_instances = [
            f"https://nitter.net/{handle}",
            f"https://nitter.privacydev.net/{handle}",
            f"https://nitter.poast.org/{handle}",
        ]
        html = None
        for url in nitter_instances:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8),
                                       headers={'User-Agent': 'Mozilla/5.0'}) as r:
                    if r.status == 200:
                        html = await r.text()
                        break
            except Exception:
                continue

        if not html or 'User not found' in html:
            return result

        result['has_twitter'] = True
        followers = re.search(r'(\d[\d,]*)\s*Followers', html)
        tweets    = re.search(r'(\d[\d,]*)\s*(?:Tweets|Posts)', html)
        if followers:
            result['twitter_followers'] = int(followers.group(1).replace(',', ''))
        if tweets:
            result['tweet_count'] = int(tweets.group(1).replace(',', ''))
        result['is_active'] = result['twitter_followers'] > 0 or result['tweet_count'] > 0

    except Exception as e:
        log.warning(f"Twitter check failed: {e}")
    return result


async def check_pumpfun(session, ca: str) -> dict:
    result = {
        'pumpfun_live': False,
        'reply_count': 0,
        'has_description': False,
        'king_of_hill': False,
        'telegram_url': '',
        'twitter_url': '',
        'website_url': '',
    }
    if not ca:
        return result
    try:
        url = f"https://frontend-api.pump.fun/coins/{ca}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                d = await r.json()
                result['pumpfun_live']    = True
                result['reply_count']     = d.get('reply_count', 0)
                result['king_of_hill']    = d.get('is_currently_live', False)
                result['has_description'] = len(d.get('description', '')) > 20
                
                # Extract all socials directly from pump.fun
                result['telegram_url'] = d.get('telegram', '') or ''
                result['twitter_url']  = d.get('twitter', '') or ''
                result['website_url']  = d.get('website', '') or ''
    except Exception as e:
        log.warning(f"pump.fun check failed: {e}")
    return result


async def check_telegram_group(client, telegram_url: str) -> dict:
    """Use our existing Telethon client to check Telegram group size."""
    result = {'telegram_members': 0, 'telegram_active': False}
    if not telegram_url:
        return result
    try:
        # Extract username from t.me/username
        match = re.search(r't\.me/([A-Za-z0-9_]+)', telegram_url)
        if not match:
            return result
        username = match.group(1)
        entity = await client.get_entity(username)
        if hasattr(entity, 'participants_count') and entity.participants_count:
            result['telegram_members'] = entity.participants_count
            result['telegram_active']  = entity.participants_count > 50
    except Exception as e:
        log.warning(f"Telegram group check failed for {telegram_url}: {e}")
    return result


async def check_socials(signal: dict, tg_client=None) -> dict:
    """Run all social checks and return combined data."""
    ca = signal.get('ca', '')

    async with aiohttp.ClientSession() as session:
        # 1. Fetch pump.fun data FIRST to extract fallback URLs
        pumpfun_data = await check_pumpfun(session, ca)
        if not isinstance(pumpfun_data, dict):
            pumpfun_data = {'pumpfun_live': False, 'reply_count': 0, 'telegram_url': '', 'twitter_url': '', 'website_url': ''}

        # 2. Consolidate URLs: Prefer signal URLs, fallback to pump.fun URLs
        twitter_url = signal.get('twitter_url') or pumpfun_data.get('twitter_url', '')
        website_url = signal.get('website_url') or pumpfun_data.get('website_url', '')
        tg_url      = signal.get('telegram_url') or pumpfun_data.get('telegram_url', '')

        # 3. Now run the Website and Twitter checks concurrently with our final URLs
        twitter_task = check_twitter(session, twitter_url)
        website_task = check_url_live(session, website_url)

        twitter_data, website_live = await asyncio.gather(
            twitter_task, website_task,
            return_exceptions=True
        )

    # 4. Handle any exceptions from gather
    twitter_data  = twitter_data  if isinstance(twitter_data, dict)  else {'has_twitter': False, 'twitter_followers': 0, 'tweet_count': 0, 'is_active': False}
    website_live  = website_live  if isinstance(website_live, bool)   else False

    # 5. Get Telegram group info if we have a client and a final URL
    tg_data = {'telegram_members': 0, 'telegram_active': False}
    if tg_client and tg_url:
        tg_data = await check_telegram_group(tg_client, tg_url)

    # If we found a twitter URL on pump.fun but Nitter failed, let's still mark has_twitter as True
    # so we know the project actually linked one. Nitter is notoriously flaky.
    has_twitter_final = twitter_data.get('has_twitter', False) or bool(twitter_url)

    return {
        'has_twitter':       has_twitter_final,
        'twitter_followers': twitter_data.get('twitter_followers', 0),
        'tweet_count':       twitter_data.get('tweet_count', 0),
        'twitter_active':    twitter_data.get('is_active', False),
        'website_live':      website_live,
        'pumpfun_replies':   pumpfun_data.get('reply_count', 0),
        'has_description':   pumpfun_data.get('has_description', False),
        'king_of_hill':      pumpfun_data.get('king_of_hill', False),
        'telegram_url':      tg_url,
        'telegram_members':  tg_data.get('telegram_members', 0),
        'telegram_active':   tg_data.get('telegram_active', False),
    }