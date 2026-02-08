import ccxt
import pandas as pd
import asyncio
import time
from telegram import Bot
from flask import Flask
from threading import Thread

# ================= CONFIG =================
TOKEN = "8281155907:AAEtRbRaLO1jA6EZxAGZ74whQdFxEHetxJg"
CHAT_ID = -1003523855047

SYMBOLS = [
    "BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","BNB/USDT:USDT",
    "XRP/USDT:USDT","TRX/USDT:USDT","LINK/USDT:USDT","DOGE/USDT:USDT"
]

TIMEFRAME_TREND = "1h"
TIMEFRAME_ENTRY = "5m"

CHECK_INTERVAL = 60
COOLDOWN = 3600
MIN_STOP_PCT = 0.005   # 0.5%
MIN_RR = 2.5
# =========================================

bot = Bot(token=TOKEN)
exchange = ccxt.bybit({"options": {"defaultType": "future"}})
last_signal_time = {}
open_trades = {}

# ================= UTILS =================
def get_data(symbol, tf, limit=200):
    ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
    return df

def trend_tf(symbol):
    df = get_data(symbol, TIMEFRAME_TREND, 200)
    ema50 = df["close"].ewm(span=50).mean()
    ema200 = df["close"].ewm(span=200).mean()
    return "UP" if ema50.iloc[-1] > ema200.iloc[-1] else "DOWN"

def find_fvg(df):
    for i in range(2, len(df)):
        if df["low"].iloc[i] > df["high"].iloc[i-2]:
            return ("bull", df["high"].iloc[i-2], df["low"].iloc[i])
        if df["high"].iloc[i] < df["low"].iloc[i-2]:
            return ("bear", df["low"].iloc[i-2], df["high"].iloc[i])
    return None

def calc_atr(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

# ================= SIGNAL LOGIC =================
def analyze(symbol):
    trend = trend_tf(symbol)
    df = get_data(symbol, TIMEFRAME_ENTRY, 200)
    fvg = find_fvg(df)
    if not fvg:
        return None

    price = df["close"].iloc[-1]
    atr = calc_atr(df)
    fvg_type, fvg_low, fvg_high = fvg
    entry = (fvg_low + fvg_high) / 2

    min_stop_dist = max(entry * MIN_STOP_PCT, atr*2)

    # ===== LONG =====
    if trend == "UP" and fvg_type == "bull" and price < fvg_high:
        stop = entry - min_stop_dist
        risk = entry - stop
        takes = [entry + risk*m for m in [3,4,5,6]]
        rr = risk and (takes[0]-entry)/risk
        if rr < MIN_RR:
            return None
        return build_signal(symbol,"LONG",entry,stop,takes)

    # ===== SHORT =====
    if trend == "DOWN" and fvg_type == "bear" and price > fvg_low:
        stop = entry + min_stop_dist
        risk = stop - entry
        takes = [entry - risk*m for m in [3,4,5,6]]
        rr = risk and (entry-takes[0])/risk
        if rr < MIN_RR:
            return None
        return build_signal(symbol,"SHORT",entry,stop,takes)

    return None

def build_signal(symbol, side, entry, stop, takes):
    clean_symbol = symbol.replace(":USDT","")
    rr = round(abs((takes[0]-entry)/(entry-stop)),2)

    entry = round(entry,6)
    stop = round(stop,6)
    takes = [round(t,6) for t in takes]

    text = f"""🔊 Signal for {clean_symbol}
Type: {"🟩 LONG" if side=="LONG" else "🟥 SHORT"}
⏰ Market: {entry}
"""
    for i,t in enumerate(takes):
        text += f"🎯{i+1} Take: {t}\n"
    text += f"🛑 Stop: {stop}\nRR: 1:{rr}"
    return {"text":text, "symbol":symbol, "side":side, "takes":takes, "stop":stop}

# ================= TELEGRAM =================
async def send_signal(data):
    msg = await bot.send_message(chat_id=CHAT_ID, text=data["text"])
    open_trades[data["symbol"]] = {
        "msg_id": msg.message_id,
        "side": data["side"],
        "takes": data["takes"],
        "stop": data["stop"]
    }
    last_signal_time[data["symbol"]] = time.time()

async def check_tp_sl(symbol):
    if symbol not in open_trades:
        return
    df = get_data(symbol, TIMEFRAME_ENTRY, 5)
    price = df["close"].iloc[-1]
    trade = open_trades[symbol]
    clean_symbol = symbol.replace(":USDT","")

    for i,t in enumerate(trade["takes"]):
        if trade["side"]=="LONG" and price >= t:
            await bot.send_message(CHAT_ID, f"✅ {clean_symbol} Take {i+1} hit!", reply_to_message_id=trade["msg_id"])
            trade["takes"][i] = 999999999
        if trade["side"]=="SHORT" and price <= t:
            await bot.send_message(CHAT_ID, f"✅ {clean_symbol} Take {i+1} hit!", reply_to_message_id=trade["msg_id"])
            trade["takes"][i] = -999999999

    if trade["side"]=="LONG" and price <= trade["stop"]:
        await bot.send_message(CHAT_ID, f"❌ {clean_symbol} STOP LOSS!", reply_to_message_id=trade["msg_id"])
        del open_trades[symbol]
    if trade["side"]=="SHORT" and price >= trade["stop"]:
        await bot.send_message(CHAT_ID, f"❌ {clean_symbol} STOP LOSS!", reply_to_message_id=trade["msg_id"])
        del open_trades[symbol]

# ================= MAIN LOOP =================
async def main():
    print("✅ Bot started...")
    while True:
        for sym in SYMBOLS:
            try:
                if sym in last_signal_time and time.time()-last_signal_time[sym] < COOLDOWN:
                    await check_tp_sl(sym)
                    continue
                if sym in open_trades:
                    await check_tp_sl(sym)
                    continue
                data = analyze(sym)
                if data:
                    await send_signal(data)
            except Exception as e:
                print("ERROR", sym, e)
        await asyncio.sleep(CHECK_INTERVAL)

# ================= FLASK KEEP ALIVE =================
app = Flask('')
@app.route('/')
def home():
    return "Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8080)

Thread(target=run).start()

if __name__ == "__main__":
    asyncio.run(main())
