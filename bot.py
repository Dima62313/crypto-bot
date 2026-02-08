import ccxt
import pandas as pd
import asyncio
import time
from telegram import Bot

# ================= CONFIG =================

TOKEN = "8281155907:AAEtRbRaLO1jA6EZxAGZ74whQdFxEHetxJg"
CHAT_ID = -1003523855047   # твій канал або група

SYMBOLS = [
"BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","BNB/USDT:USDT",
"XRP/USDT:USDT","TRX/USDT:USDT","LINK/USDT:USDT","HYPE/USDT:USDT"
]

TIMEFRAME_TREND = "1h"
TIMEFRAME_ENTRY = "5m"

CHECK_INTERVAL = 60  # сек

# cooldown по монеті (щоб не спамив)
COOLDOWN = 3600  # 1 година

# =========================================

bot = Bot(token=TOKEN)

exchange = ccxt.bybit({
    "options": {"defaultType": "future"}
})

last_signal_time = {}
open_trades = {}   # symbol -> message_id


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

# -------- FVG detector (simple SMC) --------
def find_fvg(df):
    fvg = None
    for i in range(2, len(df)):
        if df["low"].iloc[i] > df["high"].iloc[i-2]:
            fvg = ("bull", df["high"].iloc[i-2], df["low"].iloc[i])
        if df["high"].iloc[i] < df["low"].iloc[i-2]:
            fvg = ("bear", df["low"].iloc[i-2], df["high"].iloc[i])
    return fvg


# ================= SIGNAL LOGIC =================

def analyze(symbol):
    trend = trend_tf(symbol)
    df = get_data(symbol, TIMEFRAME_ENTRY, 200)

    fvg = find_fvg(df)
    if not fvg:
        return None

    price = df["close"].iloc[-1]
    fvg_type, fvg_low, fvg_high = fvg
    entry = (fvg_low + fvg_high) / 2

    # ===== LONG =====
    if trend == "UP" and fvg_type == "bull" and price < fvg_high:
        stop = fvg_low
        risk = entry - stop
        t1 = entry + risk*2
        t2 = entry + risk*3
        t3 = entry + risk*4
        t4 = entry + risk*5
        return build_signal(symbol,"LONG",entry,stop,[t1,t2,t3,t4])

    # ===== SHORT =====
    if trend == "DOWN" and fvg_type == "bear" and price > fvg_low:
        stop = fvg_high
        risk = stop - entry
        t1 = entry - risk*2
        t2 = entry - risk*3
        t3 = entry - risk*4
        t4 = entry - risk*5
        return build_signal(symbol,"SHORT",entry,stop,[t1,t2,t3,t4])

    return None


def build_signal(symbol, side, entry, stop, takes):
    if stop <= 0 or entry <= 0:
        return None

    rr = abs((takes[0]-entry)/(entry-stop))

    if rr < 2:
        return None

    entry = round(entry, 6)
    stop = round(stop, 6)
    takes = [round(t,6) for t in takes]

    text = f"""🔊Signal for {symbol.replace(':USDT','')}
Type: {"🟩 LONG" if side=="LONG" else "🟥 SHORT"}
⏰Market: {entry}
☑️1 Take: {takes[0]}
☑️2 Take: {takes[1]}
☑️3 Take: {takes[2]}
☑️4 Take: {takes[3]}
🛑Stop: {stop}
RR: 1:{round(rr,2)}
"""
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

    # тейки
    for i,t in enumerate(trade["takes"]):
        if trade["side"]=="LONG" and price >= t:
            await bot.send_message(CHAT_ID, f"✅ {symbol} Take {i+1} hit!", reply_to_message_id=trade["msg_id"])
            trade["takes"][i] = 999999999

        if trade["side"]=="SHORT" and price <= t:
            await bot.send_message(CHAT_ID, f"✅ {symbol} Take {i+1} hit!", reply_to_message_id=trade["msg_id"])
            trade["takes"][i] = -999999999

    # стоп
    if trade["side"]=="LONG" and price <= trade["stop"]:
        await bot.send_message(CHAT_ID, f"❌ {symbol} STOP LOSS!", reply_to_message_id=trade["msg_id"])
        del open_trades[symbol]

    if trade["side"]=="SHORT" and price >= trade["stop"]:
        await bot.send_message(CHAT_ID, f"❌ {symbol} STOP LOSS!", reply_to_message_id=trade["msg_id"])
        del open_trades[symbol]


# ================= MAIN LOOP =================

async def main():
    print("✅ Bot started...")

    while True:
        for sym in SYMBOLS:
            try:
                # cooldown
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


if __name__ == "__main__":
    asyncio.run(main())
