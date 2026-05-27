import asyncio
import re
import os
import logging
import aiohttp
from datetime import datetime, timezone
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telegram import Bot
from telegram.constants import ParseMode
from tracker import save_signal, update_price, get_all_signals, get_stats
from social_checker import check_socials
from trend_checker import check_all_trends
from safety_checker import run_safety_checks
from price_tracker import pumpportal_ws_loop, subscribe_token, get_live_trade_count

# ── Config ────────────────────────────────────────────────────────────────────
API_ID         = int(os.environ.get("API_ID", "22062932"))
API_HASH       = os.environ.get("API_HASH", "fa408cb00846e274bd4f79d219493923")
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "8978544822:AAE3vbDBZeCYSNPjJnOrJTMLZ-Ue5_WMzWk")
OWNER_ID       = int(os.environ.get("OWNER_ID", "6514156935"))
CHANNEL        = os.environ.get("CHANNEL", "pumpfunvolumeby4AM")
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "2842e504-2c6d-41a1-b013-962ee1263e23")
SESSION_STRING = os.environ.get("SESSION_STRING", "1BJWap1wBu6lWuGrvMz3YfdlyzqpKN-kjP2iG4zWB2XC_eeLjNKYMA61n0aZHynzwv75SFBayEywEbzSE3994Iiaxpunc3jSWnJM709w91TBHIMUExs_aMIjMxsJY5xNK12wigG80wRmEJUzZ5koDFg0HjGl28gsVo-MwSzwnZLGF0oQpLRsV97jHpv2z-vwyHiGZE-8cd72FdMdo2a8xzWz7QI1EYGhlzOjgKDYAJPra3i-E759-GJKfTW6evJyWFIaRaNszXwCANWV75O-7Mdh4uWJ-3uqcDp_2vbVQ7J9PMfKRdQsakeGRIKFlLpoAMuHHK_HNn9hdgveJkoK-zK6Zb3c8ANo=")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
alert_bot = Bot(token=BOT_TOKEN)


# ── Signal Parser ─────────────────────────────────────────────────────────────
def parse_signal(text: str) -> dict | None:
    try:
        data = {}
        name_match = re.search(r'🔔\s*(.+?)\s*\|', text)
        ca_match   = re.search(r'([A-Za-z0-9]{32,44})pump', text)
        if name_match: data['name'] = name_match.group(1).strip()
        if ca_match:   data['ca']   = ca_match.group(1) + 'pump'

        mc = re.search(r'Marketcap[:\s]*\$([0-9,]+)', text)
        if mc: data['marketcap'] = int(mc.group(1).replace(',', ''))

        age = re.search(r'Age[:\s]*(\d+)\s*(m|h|s)', text)
        if age:
            val, unit = int(age.group(1)), age.group(2)
            data['age_minutes'] = val if unit == 'm' else (val * 60 if unit == 'h' else val / 60)

        dev = re.search(r'Dev[:\s].*?\(\s*💰\s*(\d+(?:\.\d+)?)%\)', text)
        if dev: data['dev_pct'] = float(dev.group(1))

        dev_wallet = re.search(r'Dev[:\s]+([A-Za-z0-9]{32,44})', text)
        if dev_wallet: data['dev_wallet'] = dev_wallet.group(1)

        holders = re.search(r'Holders[:\s]*(\d+)', text)
        if holders: data['holders'] = int(holders.group(1))

        top10 = re.search(r'Top 10 holders[:\s]*(\d+(?:\.\d+)?)%', text)
        if top10: data['top10_pct'] = float(top10.group(1))

        vol = re.search(r'Volume[:\s]*\$([0-9,]+)', text)
        if vol: data['volume'] = int(vol.group(1).replace(',', ''))

        liq = re.search(r'Liquidity[:\s]*\$([0-9,]+)', text)
        if liq: data['liquidity'] = int(liq.group(1).replace(',', ''))

        bc = re.search(r'Bonding Curve[:\s]*(\d+(?:\.\d+)?)%', text)
        if bc: data['bonding_curve'] = float(bc.group(1))

        x_profile = re.search(r'(https?://(?:twitter|x)\.com/\S+)', text)
        website   = re.search(r'(https?://(?!t\.me|pump\.fun|twitter|x\.com)\S+\.\S+)', text)
        tg_link   = re.search(r'(https?://t\.me/\S+)', text)
        if x_profile: data['twitter_url']  = x_profile.group(1)
        if website:   data['website_url']   = website.group(1)
        if tg_link:   data['telegram_url']  = tg_link.group(1)

        if 'ca' not in data or 'marketcap' not in data:
            return None

        data['raw_text']  = text
        data['timestamp'] = datetime.now(timezone.utc).isoformat()
        return data
    except Exception as e:
        log.error(f"Parse error: {e}")
        return None


# ── Milestone callback ────────────────────────────────────────────────────────
async def on_milestone(sig: dict, milestone: int, current_mcap: float, mins_elapsed: int):
    ca         = sig['ca']
    entry_mcap = sig['marketcap']
    await alert_bot.send_message(
        chat_id=OWNER_ID,
        text=(
            f"🎯 *{sig['name']}* hit *{milestone}x!*\n"
            f"Entry: ${entry_mcap:,} → Now: ${current_mcap:,.0f}\n"
            f"Time: {mins_elapsed}m after signal\n"
            f"`{ca}`\n"
            f"[pump.fun](https://pump.fun/{ca})"
        ),
        parse_mode=ParseMode.MARKDOWN
    )


# ── Notify ────────────────────────────────────────────────────────────────────
async def notify_signal(signal: dict, socials: dict, trends: dict, safety: dict, signal_id: int):
    twitter_str = f"✅ {socials.get('twitter_followers',0):,} followers" if socials.get('has_twitter') else '❌'
    website_str = '✅ Live' if socials.get('website_live') else '❌'
    tg_str      = f"✅ {socials.get('telegram_members',0):,} members" if socials.get('telegram_members',0) > 0 else ('✅' if socials.get('telegram_url') else '❌')
    twitter_buzz = f"🔥 {trends.get('recent_tweet_count',0)} tweets" if trends.get('trending_on_twitter') else f"💤 {trends.get('recent_tweet_count',0)} tweets"
    meta_str    = f"🔥 {trends.get('meta_theme','').upper()} meta hot ({trends.get('meta_win_rate',0)}% win rate)" if trends.get('meta_hot') else (f"Theme: {trends.get('meta_theme','none')}" if trends.get('meta_theme') else 'No theme')
    buys  = trends.get('buy_count', 0)
    sells = trends.get('sell_count', 0)
    trade_total = trends.get('recent_trade_count', 0)
    velocity_str = f"🚀 {trade_total} trades (🟢{buys} buys / 🔴{sells} sells)" if trends.get('volume_accelerating') else f"📊 {trade_total} trades (🟢{buys} / 🔴{sells})"
    honeypot_str = "🚨 HONEYPOT" if safety.get('is_honeypot') else "✅ Clean"
    dev_str     = f"🚨 Serial rugger ({safety.get('dev_rug_rate',0)}% rug rate)" if safety.get('dev_is_serial_rugger') else f"✅ {safety.get('dev_previous_tokens',0)} prev tokens"
    flags_str   = '\n'.join(safety.get('red_flags', [])) if safety.get('red_flags') else '✅ None'

    msg = (
        f"📡 *SIGNAL #{signal_id} CAUGHT*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 *{signal.get('name','Unknown')}*\n"
        f"`{signal.get('ca','')}`\n\n"
        f"📊 *ON-CHAIN*\n"
        f"💰 Mcap: *${signal.get('marketcap',0):,}*\n"
        f"⏱ Age: *{signal.get('age_minutes',0):.0f}m*\n"
        f"👥 Holders: *{signal.get('holders',0)}* (+{safety.get('unique_buyers_5m',0)}/5m)\n"
        f"🏆 Top10: *{signal.get('top10_pct',0)}%*\n"
        f"🚀 Volume: *${signal.get('volume',0):,}*\n"
        f"💧 Liquidity: *${signal.get('liquidity',0):,}*\n"
        f"📈 Bonding: *{signal.get('bonding_curve',0)}%*\n"
        f"👨‍💻 Dev: *{signal.get('dev_pct',0)}%*\n\n"
        f"🌐 *SOCIALS*\n"
        f"🐦 Twitter: {twitter_str}\n"
        f"🌍 Website: {website_str}\n"
        f"💬 Telegram: {tg_str}\n\n"
        f"🔥 *TRENDING*\n"
        f"Twitter: {twitter_buzz}\n"
        f"Meta: {meta_str}\n"
        f"Volume: {velocity_str}\n"
        f"Front page: {'✅ #'+str(trends.get('front_page_rank')) if trends.get('on_front_page') else '❌'}\n\n"
        f"🛡 *SAFETY*\n"
        f"Honeypot: {honeypot_str}\n"
        f"Rugcheck: {safety.get('rugcheck_rating','unknown')}\n"
        f"Dev history: {dev_str}\n\n"
        f"🚩 *RED FLAGS*\n{flags_str}\n\n"
        f"📝 *Paper: $10 entered @ ${signal.get('marketcap',0):,}*\n"
        f"🎯 2x target: ${signal.get('marketcap',0)*2:,}\n"
        f"⏳ Tracking live via WebSocket\n\n"
        f"🔗 [pump.fun](https://pump.fun/{signal.get('ca','')})"
    )
    try:
        await alert_bot.send_message(chat_id=OWNER_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.error(f"Alert failed: {e}")


# ── Daily Summary ─────────────────────────────────────────────────────────────
async def daily_summary_loop():
    while True:
        await asyncio.sleep(86400)
        stats = get_stats()
        msg = (
            f"📊 *DAILY SUMMARY*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 Total signals: *{stats['total']}*\n"
            f"2️⃣  Hit 2x: *{stats['hit_2x']}* ({stats['rate_2x']}%)\n"
            f"5️⃣  Hit 5x: *{stats['hit_5x']}* ({stats['rate_5x']}%)\n"
            f"🔟 Hit 10x: *{stats['hit_10x']}* ({stats['rate_10x']}%)\n"
            f"💀 Rugged: *{stats['rugged']}*\n"
            f"⏱ Avg time to 2x: *{stats['avg_time_to_2x']}*\n"
            f"💵 Paper P&L: *${stats['paper_profit']:.2f}*\n"
            f"🏆 Best pick: *{stats['best_pick']}*"
        )
        await alert_bot.send_message(chat_id=OWNER_ID, text=msg, parse_mode=ParseMode.MARKDOWN)


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    log.info("Starting Solana Signal Bot — PHASE 1 FULL DATA COLLECTION")
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.start(bot_token=None)
    log.info("Telegram client connected.")

    await alert_bot.send_message(
        chat_id=OWNER_ID,
        text=(
            "🤖 *Solana Signal Bot LIVE — Phase 1 Full*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📡 Catching ALL signals from 4AM channel\n"
            "💵 Paper trading $10 per signal\n"
            "🔌 Live price tracking via PumpPortal WebSocket\n"
            "🌐 Socials via Helius IPFS metadata\n"
            "🔥 Trends: Twitter + Meta + Volume velocity\n"
            "🛡 Safety: Honeypot + Rugcheck + Dev history\n"
            "💾 All data saved to persistent storage\n\n"
            "Every stone is being turned 🚀"
        ),
        parse_mode=ParseMode.MARKDOWN
    )

    @client.on(events.NewMessage())
    async def handler(event):
        text = event.message.message
        # Filter to only our target channel
        try:
            chat = await event.get_chat()
            chat_username = getattr(chat, 'username', '') or ''
            chat_id = getattr(chat, 'id', 0)
            if chat_username.lower() != CHANNEL.lower() and str(chat_id) != '2692939230':
                return
        except Exception:
            pass

        if 'Marketcap' not in text:
            return
        log.info("Signal received, parsing...")
        signal = parse_signal(text)
        if not signal:
            log.warning("Could not parse signal")
            return

        async with aiohttp.ClientSession() as session:
            socials, trends, safety = await asyncio.gather(
                check_socials(signal, tg_client=client),
                check_all_trends(signal, session),
                run_safety_checks(signal, session),
                return_exceptions=True
            )

        socials = socials if isinstance(socials, dict) else {}
        trends  = trends  if isinstance(trends,  dict) else {}
        safety  = safety  if isinstance(safety,  dict) else {}

        signal.update({
            'telegram_url':          socials.get('telegram_url', signal.get('telegram_url', '')),
            'telegram_members':      socials.get('telegram_members', 0),
            'pumpfun_replies':       0,  # replaced by live trade count
            'twitter_followers':     socials.get('twitter_followers', 0),
            'website_live':          socials.get('website_live', False),
            'trending_on_twitter':   trends.get('trending_on_twitter', False),
            'recent_tweet_count':    trends.get('recent_tweet_count', 0),
            'meta_hot':              trends.get('meta_hot', False),
            'meta_theme':            trends.get('meta_theme'),
            'meta_win_rate':         trends.get('meta_win_rate', 0),
            'volume_accelerating':   trends.get('volume_accelerating', False),
            'volume_velocity_score': trends.get('volume_velocity_score', 0),
            'recent_trade_count':    trends.get('recent_trade_count', 0),
            'on_front_page':         trends.get('on_front_page', False),
            'front_page_rank':       trends.get('front_page_rank'),
            'king_of_hill':          trends.get('king_of_hill', False),
            'google_interest':       trends.get('google_interest', 0),
            'is_honeypot':           safety.get('is_honeypot', False),
            'rugcheck_score':        safety.get('rugcheck_score'),
            'rugcheck_rating':       safety.get('rugcheck_rating', 'unknown'),
            'rugcheck_risks':        safety.get('rugcheck_risks', []),
            'dev_previous_tokens':   safety.get('dev_previous_tokens', 0),
            'dev_rug_rate':          safety.get('dev_rug_rate', 0),
            'dev_is_serial_rugger':  safety.get('dev_is_serial_rugger', False),
            'unique_buyers_5m':      safety.get('unique_buyers_5m', 0),
            'holder_growth_fast':    safety.get('holder_growth_fast', False),
            'red_flags':             safety.get('red_flags', []),
        })

        signal_id = save_signal(signal, {'total': 0, 'red_flags': safety.get('red_flags', [])})
        log.info(f"Saved signal #{signal_id}: {signal.get('name')}")

        # Subscribe to live price tracking via WebSocket
        await subscribe_token(signal['ca'])

        await notify_signal(signal, socials, trends, safety, signal_id)

    # Start background tasks
    asyncio.create_task(pumpportal_ws_loop(
        get_active_signals_fn=lambda: get_all_signals(active_only=True),
        update_price_fn=update_price,
        milestone_callback_fn=on_milestone
    ))
    asyncio.create_task(daily_summary_loop())

    log.info(f"Listening to: {CHANNEL}")
    await client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
