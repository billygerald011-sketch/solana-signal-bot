import asyncio
import re
import os
import logging
import aiohttp
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telegram import Bot
from telegram.constants import ParseMode
from tracker import save_signal, update_price, get_all_signals, get_stats
from social_checker import check_socials
from trend_checker import check_all_trends
from safety_checker import run_safety_checks

# ── Config ────────────────────────────────────────────────────────────────────
API_ID         = int(os.environ.get("API_ID", "22062932"))
API_HASH       = os.environ.get("API_HASH", "fa408cb00846e274bd4f79d219493923")
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "8978544822:AAE3vbDBZeCYSNPjJnOrJTMLZ-Ue5_WMzWk")
OWNER_ID       = int(os.environ.get("OWNER_ID", "6514156935"))
CHANNEL        = os.environ.get("CHANNEL", "pumpfunvolumeby4AM")
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

        # Extract dev wallet address
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
        data['timestamp'] = datetime.now(datetime.UTC).isoformat()        
        return data
    except Exception as e:
        log.error(f"Parse error: {e}")
        return None


# ── Notify ────────────────────────────────────────────────────────────────────
async def notify_signal(signal: dict, socials: dict, trends: dict, safety: dict, signal_id: int):
    # Social strings
    twitter_str = f"✅ {socials.get('twitter_followers',0):,} followers" if socials.get('has_twitter') else '❌'
    website_str = '✅ Live' if socials.get('website_live') else '❌'
    tg_str      = f"✅ {socials.get('telegram_members',0):,} members" if socials.get('telegram_members',0) > 0 else ('✅' if socials.get('telegram_url') else '❌')

    # Trend strings
    twitter_buzz  = f"🔥 {trends.get('recent_tweet_count',0)} tweets" if trends.get('trending_on_twitter') else f"💤 {trends.get('recent_tweet_count',0)} tweets"
    meta_str      = f"🔥 {trends.get('meta_theme','').upper()} meta hot ({trends.get('meta_win_rate',0)}% win rate)" if trends.get('meta_hot') else (f"Theme: {trends.get('meta_theme','none')}" if trends.get('meta_theme') else 'No theme detected')
    velocity_str  = f"🚀 Accelerating ({trends.get('recent_trade_count',0)} trades/5m)" if trends.get('volume_accelerating') else f"📊 {trends.get('recent_trade_count',0)} trades/5m"

    # Safety strings
    honeypot_str  = "🚨 HONEYPOT" if safety.get('is_honeypot') else "✅ Clean"
    rugcheck_str  = safety.get('rugcheck_rating', 'unknown')
    dev_str       = f"🚨 Serial rugger ({safety.get('dev_rug_rate',0)}% rug rate)" if safety.get('dev_is_serial_rugger') else f"✅ {safety.get('dev_previous_tokens',0)} prev tokens"

    # Red flags
    red_flags = safety.get('red_flags', [])
    flags_str = '\n'.join(red_flags) if red_flags else '✅ None'

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
        f"💬 Telegram: {tg_str}\n"
        f"💭 pump.fun replies: {socials.get('pumpfun_replies',0)}\n\n"
        f"🔥 *TRENDING*\n"
        f"Twitter: {twitter_buzz}\n"
        f"Meta: {meta_str}\n"
        f"Volume: {velocity_str}\n"
        f"Front page: {'✅ #'+str(trends.get('front_page_rank')) if trends.get('on_front_page') else '❌'}\n\n"
        f"🛡 *SAFETY*\n"
        f"Honeypot: {honeypot_str}\n"
        f"Rugcheck: {rugcheck_str}\n"
        f"Dev history: {dev_str}\n\n"
        f"🚩 *RED FLAGS*\n{flags_str}\n\n"
        f"📝 *Paper: $10 entered @ ${signal.get('marketcap',0):,}*\n"
        f"🎯 2x target: ${signal.get('marketcap',0)*2:,}\n"
        f"⏳ Tracking 48hrs\n\n"
        f"🔗 [pump.fun](https://pump.fun/{signal.get('ca','')})"
    )
    try:
        await alert_bot.send_message(chat_id=OWNER_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.error(f"Alert failed: {e}")


# ── Price Checker ─────────────────────────────────────────────────────────────
async def check_prices_loop():
    while True:
        await asyncio.sleep(300)
        signals = get_all_signals(active_only=True)
        for sig in signals:
            try:
                entry_time   = datetime.fromisoformat(sig['timestamp'])
                mins_elapsed = (datetime.utcnow() - entry_time).total_seconds() / 60

                if   mins_elapsed <= 120:  check_now = True
                elif mins_elapsed <= 360:  check_now = int(mins_elapsed) % 15 < 5
                elif mins_elapsed <= 1440: check_now = int(mins_elapsed) % 30 < 5
                else:                      check_now = int(mins_elapsed) % 60 < 5

                if not check_now:
                    continue

                ca = sig['ca']
                async with aiohttp.ClientSession() as session:
                    url = f"https://frontend-api.pump.fun/coins/{ca}"
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                        if r.status == 200:
                            d            = await r.json()
                            current_mcap = d.get('usd_market_cap', 0)
                            entry_mcap   = sig['marketcap']
                            if entry_mcap > 0:
                                multiplier = current_mcap / entry_mcap
                                update_price(ca, current_mcap, multiplier)
                                prev = sig.get('peak_multiplier', 0)
                                for milestone in [2, 5, 10, 20, 50, 100]:
                                    if multiplier >= milestone and prev < milestone:
                                        await alert_bot.send_message(
                                            chat_id=OWNER_ID,
                                            text=(
                                                f"🎯 *{sig['name']}* hit *{milestone}x!*\n"
                                                f"Entry: ${entry_mcap:,} → Now: ${current_mcap:,}\n"
                                                f"Time: {round(mins_elapsed)}m after signal\n"
                                                f"`{ca}`\n"
                                                f"[pump.fun](https://pump.fun/{ca})"
                                            ),
                                            parse_mode=ParseMode.MARKDOWN
                                        )
            except Exception as e:
                log.warning(f"Price check failed for {sig.get('ca','?')}: {e}")


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
            "📡 Catching ALL signals\n"
            "💵 Paper trading $10 per signal\n"
            "🌐 Socials: Twitter + Website + Telegram\n"
            "🔥 Trends: Twitter buzz + Meta + Volume velocity\n"
            "🛡 Safety: Honeypot + Rugcheck + Dev history\n"
            "⏳ Tracking prices for 48hrs\n"
            "💾 All data saved to persistent storage\n\n"
            "Every stone is being turned 🚀"
        ),
        parse_mode=ParseMode.MARKDOWN
    )

    @client.on(events.NewMessage(chats=CHANNEL))
    async def handler(event):
        text = event.message.message
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

        # Merge all data into signal for storage
        signal.update({
            'telegram_url':          socials.get('telegram_url', signal.get('telegram_url', '')),
            'telegram_members':      socials.get('telegram_members', 0),
            'pumpfun_replies':       socials.get('pumpfun_replies', 0),
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
        log.info(f"Saved signal #{signal_id}: {signal.get('name')} — honeypot:{safety.get('is_honeypot')} meta:{trends.get('meta_theme')} tweets:{trends.get('recent_tweet_count')}")
        await notify_signal(signal, socials, trends, safety, signal_id)

    asyncio.create_task(check_prices_loop())
    asyncio.create_task(daily_summary_loop())

    log.info(f"Listening to: {CHANNEL}")
    await client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
