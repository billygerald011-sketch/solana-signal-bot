import asyncio
import re
import json
import os
import logging
import aiohttp
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telegram import Bot
from telegram.constants import ParseMode
from tracker import save_signal, update_price, get_all_signals, get_stats

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
        if x_profile: data['twitter_url'] = x_profile.group(1)
        if website:   data['website_url']  = website.group(1)

        if 'ca' not in data or 'marketcap' not in data:
            return None

        data['raw_text']  = text
        data['timestamp'] = datetime.utcnow().isoformat()
        return data
    except Exception as e:
        log.error(f"Parse error: {e}")
        return None


# ── Notify owner of every caught signal ──────────────────────────────────────
async def notify_signal(signal: dict, signal_id: int):
    has_twitter = '✅' if signal.get('twitter_url') else '❌'
    has_website = '✅' if signal.get('website_url') else '❌'
    msg = (
        f"📡 *SIGNAL #{signal_id} CAUGHT*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 *{signal.get('name', 'Unknown')}*\n"
        f"`{signal.get('ca', '')}`\n\n"
        f"💰 Mcap: *${signal.get('marketcap', 0):,}*\n"
        f"⏱ Age: *{signal.get('age_minutes', 0):.0f}m*\n"
        f"👥 Holders: *{signal.get('holders', 0)}*\n"
        f"🏆 Top10: *{signal.get('top10_pct', 0)}%*\n"
        f"🚀 Volume: *${signal.get('volume', 0):,}*\n"
        f"💧 Liquidity: *${signal.get('liquidity', 0):,}*\n"
        f"📈 Bonding: *{signal.get('bonding_curve', 0)}%*\n"
        f"👨‍💻 Dev: *{signal.get('dev_pct', 0)}%*\n\n"
        f"🐦 Twitter: {has_twitter}  🌐 Website: {has_website}\n\n"
        f"📝 *Paper trade: $10 entered*\n"
        f"🎯 Target 2x @ ${signal.get('marketcap', 0) * 2:,}\n\n"
        f"🔗 [View on pump.fun](https://pump.fun/{signal.get('ca', '')})"
    )
    try:
        await alert_bot.send_message(chat_id=OWNER_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.error(f"Failed to send alert: {e}")


# ── Price Checker ─────────────────────────────────────────────────────────────
async def check_prices_loop():
    while True:
        await asyncio.sleep(300)  # every 5 mins
        signals = get_all_signals(active_only=True)
        for sig in signals:
            try:
                ca = sig['ca']
                async with aiohttp.ClientSession() as session:
                    url = f"https://frontend-api.pump.fun/coins/{ca}"
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                        if r.status == 200:
                            d = await r.json()
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
            f"📡 Signals caught: *{stats['total']}*\n"
            f"2️⃣  Hit 2x: *{stats['hit_2x']}* ({stats['rate_2x']}%)\n"
            f"5️⃣  Hit 5x: *{stats['hit_5x']}* ({stats['rate_5x']}%)\n"
            f"🔟 Hit 10x: *{stats['hit_10x']}* ({stats['rate_10x']}%)\n"
            f"💀 Rugged: *{stats['rugged']}*\n"
            f"💵 Paper P&L: *${stats['paper_profit']:.2f}*\n"
            f"🏆 Best pick: *{stats['best_pick']}*"
        )
        await alert_bot.send_message(chat_id=OWNER_ID, text=msg, parse_mode=ParseMode.MARKDOWN)


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    log.info("Starting Solana Signal Bot — PHASE 1 DATA COLLECTION MODE")
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.start(bot_token=None)
    log.info("Telegram client connected.")

    await alert_bot.send_message(
        chat_id=OWNER_ID,
        text=(
            "🤖 *Solana Signal Bot LIVE — Phase 1*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📡 Listening to 4AM channel\n"
            "📝 Catching ALL signals — no filter\n"
            "💵 Paper trading $10 on every signal\n"
            "📊 Tracking 5m/15m/30m/1h/2h prices\n"
            "🎯 Alerting you on 2x/5x/10x milestones\n"
            "📈 Daily summary every 24hrs\n\n"
            "Let the data collection begin! 🚀"
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

        signal_id = save_signal(signal, {
            'onchain': 0, 'narrative': 0, 'social': 0,
            'total': 0, 'red_flags': []
        })
        log.info(f"Saved signal #{signal_id}: {signal.get('name')}")
        await notify_signal(signal, signal_id)

    asyncio.create_task(check_prices_loop())
    asyncio.create_task(daily_summary_loop())

    log.info(f"Listening to: {CHANNEL}")
    await client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
