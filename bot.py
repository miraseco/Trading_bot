import ccxt
import pandas as pd
import time
import requests
import os
import json
from datetime import datetime

# ========== تنظیمات ==========
GATE_API_KEY    = os.environ.get('GATE_API_KEY', '')
GATE_SECRET     = os.environ.get('GATE_SECRET', '')
TELEGRAM_TOKEN  = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID= os.environ.get('TELEGRAM_CHAT_ID', '')

SYMBOL          = 'BTC/USDT'
TIMEFRAME       = '1h'
EMA_FAST        = 9
EMA_SLOW        = 21
CHECK_INTERVAL  = 3600        # هر ۱ ساعت
STARTING_BALANCE= 1000.0      # موجودی مجازی اولیه (دلار)
TRADE_PERCENT   = 0.95        # ۹۵٪ موجودی رو استفاده می‌کنه

# ========== تلگرام ==========
def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML'
        }, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

# ========== اتصال ==========
def get_exchange():
    return ccxt.gateio({
        'apiKey': GATE_API_KEY,
        'secret': GATE_SECRET,
        'options': {'defaultType': 'spot'}
    })

# ========== داده ==========
def get_candles(exchange):
    ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=100)
    df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

# ========== اندیکاتورها ==========
def calculate_indicators(df):
    df['ema_fast'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()
    delta = df['close'].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = -delta.clip(upper=0).rolling(14).mean()
    df['rsi'] = 100 - (100 / (1 + gain/loss))
    return df

# ========== سیگنال ==========
def get_signal(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    cross_up   = prev['ema_fast'] <= prev['ema_slow'] and last['ema_fast'] > last['ema_slow']
    cross_down = prev['ema_fast'] >= prev['ema_slow'] and last['ema_fast'] < last['ema_slow']
    rsi = last['rsi']
    if cross_up   and rsi < 70: return 'BUY',  rsi
    if cross_down and rsi > 30: return 'SELL', rsi
    return None, rsi

# ========== Paper Trading Engine ==========
class PaperTrader:
    def __init__(self):
        self.balance_usdt  = STARTING_BALANCE
        self.balance_btc   = 0.0
        self.position      = None   # 'long' یا None
        self.entry_price   = 0.0
        self.entry_time    = None
        self.trades        = []
        self.trade_count   = 0
        self.win_count     = 0
        self.total_pnl_pct = 0.0

    def buy(self, price, now):
        amount_usdt = self.balance_usdt * TRADE_PERCENT
        self.balance_btc  = amount_usdt / price
        self.balance_usdt -= amount_usdt
        self.position    = 'long'
        self.entry_price = price
        self.entry_time  = now
        self.trade_count += 1

        msg = (
            f"🟢 <b>خرید اتوماتیک انجام شد!</b>\n\n"
            f"💰 قیمت خرید: <b>${price:,.2f}</b>\n"
            f"🪙 مقدار BTC: {self.balance_btc:.6f}\n"
            f"💵 موجودی USDT: ${self.balance_usdt:,.2f}\n"
            f"📊 معامله شماره: {self.trade_count}\n"
            f"⏰ {now}"
        )
        send_telegram(msg)
        print(f"[BUY]  ${price:,.2f} | BTC: {self.balance_btc:.6f}")

    def sell(self, price, now):
        amount_usdt       = self.balance_btc * price
        pnl_pct           = ((price - self.entry_price) / self.entry_price) * 100
        pnl_usdt          = amount_usdt - (self.balance_btc * self.entry_price)
        self.balance_usdt += amount_usdt
        self.balance_btc   = 0.0
        self.total_pnl_pct+= pnl_pct
        if pnl_pct > 0: self.win_count += 1

        self.trades.append({
            'entry': self.entry_price,
            'exit' : price,
            'pnl'  : pnl_pct,
            'time' : str(now)
        })

        emoji = "💚" if pnl_pct > 0 else "❤️"
        result = "سود" if pnl_pct > 0 else "ضرر"
        self.position  = None
        self.entry_price = 0.0

        msg = (
            f"🔴 <b>فروش اتوماتیک انجام شد!</b>\n\n"
            f"💰 قیمت فروش: <b>${price:,.2f}</b>\n"
            f"📥 قیمت خرید: ${self.entry_price if self.entry_price else self.trades[-1]['entry']:,.2f}\n"
            f"{emoji} {result}: <b>{pnl_pct:+.2f}%</b>  (${pnl_usdt:+.2f})\n"
            f"💵 موجودی کل: <b>${self.balance_usdt:,.2f}</b>\n"
            f"⏰ {now}"
        )
        send_telegram(msg)
        print(f"[SELL] ${price:,.2f} | PnL: {pnl_pct:+.2f}% | Balance: ${self.balance_usdt:,.2f}")

    def report(self, price, ema_f, ema_s, rsi, now):
        total_value = self.balance_usdt + (self.balance_btc * price)
        overall_pnl = ((total_value - STARTING_BALANCE) / STARTING_BALANCE) * 100
        win_rate    = (self.win_count / self.trade_count * 100) if self.trade_count > 0 else 0
        pos_status  = f"🟢 در پوزیشن از ${self.entry_price:,.2f}" if self.position else "⚪️ بدون پوزیشن"

        msg = (
            f"📋 <b>گزارش ۴ ساعته</b>\n\n"
            f"💱 BTC/USDT: <b>${price:,.2f}</b>\n"
            f"📈 EMA{EMA_FAST}: ${ema_f:,.2f}\n"
            f"📉 EMA{EMA_SLOW}: ${ema_s:,.2f}\n"
            f"📊 RSI: {rsi:.1f}\n\n"
            f"━━━━━━━━━━━━━━\n"
            f"💼 <b>وضعیت پورتفولیو</b>\n"
            f"💵 موجودی USDT: ${self.balance_usdt:,.2f}\n"
            f"🪙 موجودی BTC: {self.balance_btc:.6f}\n"
            f"💰 ارزش کل: <b>${total_value:,.2f}</b>\n"
            f"📌 وضعیت: {pos_status}\n\n"
            f"━━━━━━━━━━━━━━\n"
            f"📈 <b>آمار معاملات</b>\n"
            f"🔢 کل معاملات: {self.trade_count}\n"
            f"✅ معاملات سودده: {self.win_count}\n"
            f"🎯 نرخ موفقیت: {win_rate:.1f}%\n"
            f"💹 سود کل: <b>{overall_pnl:+.2f}%</b>\n"
            f"⏰ {now}"
        )
        send_telegram(msg)

# ========== اجرای اصلی ==========
def run():
    print("🤖 ربات Paper Trading شروع شد...")
    exchange = get_exchange()
    trader   = PaperTrader()
    report_counter = 0

    send_telegram(
        "🤖 <b>ربات Paper Trading فعال شد!</b>\n\n"
        f"📊 استراتژی: EMA {EMA_FAST}/{EMA_SLOW} + RSI\n"
        f"💱 جفت‌ارز: {SYMBOL}\n"
        f"⏱ تایم‌فریم: {TIMEFRAME}\n"
        f"💵 موجودی مجازی: ${STARTING_BALANCE:,.0f}\n\n"
        "🟢 در حال مانیتور بازار...\n"
        "⚠️ این Paper Trading است — پول واقعی درگیر نیست"
    )

    while True:
        try:
            df      = get_candles(exchange)
            df      = calculate_indicators(df)
            signal, rsi = get_signal(df)
            last    = df.iloc[-1]
            price   = last['close']
            ema_f   = last['ema_fast']
            ema_s   = last['ema_slow']
            now     = datetime.now().strftime('%H:%M - %Y/%m/%d')

            if signal == 'BUY'  and trader.position is None:
                trader.buy(price, now)
            elif signal == 'SELL' and trader.position == 'long':
                trader.sell(price, now)

            report_counter += 1
            if report_counter >= 4:
                report_counter = 0
                trader.report(price, ema_f, ema_s, rsi, now)

            print(f"[{now}] ${price:,.2f} | RSI:{rsi:.1f} | Signal:{signal} | Pos:{trader.position} | Balance:${trader.balance_usdt:,.2f}")
            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            print(f"Error: {e}")
            send_telegram(f"⚠️ <b>خطا:</b>\n{str(e)}")
            time.sleep(60)

if __name__ == '__main__':
    run()
