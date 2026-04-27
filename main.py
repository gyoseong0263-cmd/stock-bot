import os
import yfinance as yf
import pandas as pd
import ta
import requests
import time
import datetime
import threading
import pytz
from flask import Flask

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

app = Flask(__name__)

@app.route("/")
def home():
    return "Stock Bot Running"


def send_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text}

    for i in range(3):
        try:
            response = requests.post(url, data=data, timeout=20)

            if response.status_code == 200:
                print("📩 텔레그램 전송 성공")
                return True
            else:
                print("❌ 텔레그램 응답 오류:", response.text)

        except Exception as e:
            print(f"❌ 전송 실패 {i + 1}/3", e)
            time.sleep(3)

    return False


def is_market_open():
    ny = pytz.timezone("America/New_York")
    now_ny = datetime.datetime.now(ny)

    weekday = now_ny.weekday()
    hour = now_ny.hour
    minute = now_ny.minute

    if weekday >= 5:
        return False

    if (hour > 9 or (hour == 9 and minute >= 30)) and hour < 16:
        return True

    return False


leverage_stocks = [
    "TQQQ","SOXL","FNGU","SPXL","TECL","SMCX","CONL","AMDL","OKLL",
    "UPRO","TNA","LABU","DFEN","NUGT","IONL","TSLL","PLTU","MUU",
    "INTW","SMU","IRE","HIMZ","ORCX","SNXX","CRCA","MSFU","MSFL","TSMX"
]

normal_stocks = [
    "AAPL","MSFT","NVDA","TSLA","AMZN","META","GOOGL","AMD","AVGO","NFLX",
    "PLTR","COIN","UBER","CRM","ADBE","ORCL","NOW","PANW","FTNT","WDAY",
    "INTC","QCOM","TXN","MU","ASML","LRCX","KLAC","SNOW","MDB","DDOG",
    "CRWD","NET","ZS","OKTA","TEAM","DOCU","SHOP","XYZ","PYPL","ROKU",
    "JPM","BAC","GS","MS","V","MA","XOM","CVX","COP","SLB",
    "UNH","JNJ","LLY","PFE","ABBV","MRNA","BABA","JD","PDD","BIDU",
    "TCEHY","RIVN","LCID","NIO","XPEV","LI","HOOD","ZM","F","GM",
    "BA","CAT","GE","SPY","QQQ","DIA","IWM","VOO","VTI","XLK",
    "XLF","XLE","XLV","XLI","XLP","XLU","SOXX","SMH","ARKK","ARKW",
    "ARKQ","GLD","SLV","USO","UNG","BITO","MARA","RIOT"
]

CHECK_INTERVAL = 300
PERIOD = "60d"
INTERVAL = "5m"
CHUNK_SIZE = 50

sent_today = set()
last_day = datetime.date.today()
market_block_alerted = False
market_was_open = None


def is_market_ok():
    try:
        qqq = yf.download("QQQ", period="6mo", interval="1d", progress=False)

        if qqq.empty:
            print("시장 데이터 없음")
            return False

        if isinstance(qqq.columns, pd.MultiIndex):
            qqq.columns = qqq.columns.droplevel(1)

        qqq["MA20"] = qqq["Close"].rolling(20).mean()
        qqq["MA60"] = qqq["Close"].rolling(60).mean()
        qqq = qqq.dropna()

        if qqq.empty:
            print("시장 필터 계산 데이터 없음")
            return False

        qqq_close = qqq["Close"].iloc[-1]
        qqq_ma20 = qqq["MA20"].iloc[-1]
        qqq_ma60 = qqq["MA60"].iloc[-1]

        market_ok = (qqq_close > qqq_ma60) and (qqq_ma20 > qqq_ma60)

        print("시장 상태:", "상승장" if market_ok else "하락장")
        return market_ok

    except Exception as e:
        print("시장 필터 오류:", e)
        return False


def download_group(symbols):
    try:
        return yf.download(
            symbols,
            period=PERIOD,
            interval=INTERVAL,
            group_by="ticker",
            threads=True,
            progress=False
        )
    except Exception as e:
        print("묶음 다운로드 오류:", e)
        return None


def chunks(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def prepare_data(data):
    data = data.copy()

    data["MA20"] = data["Close"].rolling(20).mean()
    data["MA60"] = data["Close"].rolling(60).mean()
    data["RSI"] = ta.momentum.RSIIndicator(data["Close"]).rsi()
    data["Vol_MA"] = data["Volume"].rolling(20).mean()

    return data.dropna()


def is_buy_timing(data, stock_type):
    if data.empty or len(data) < 80:
        return False

    data = prepare_data(data)

    if data.empty or len(data) < 10:
        return False

    close_now = data["Close"].iloc[-1]
    close_prev = data["Close"].iloc[-2]

    ma20_now = data["MA20"].iloc[-1]
    ma20_prev = data["MA20"].iloc[-2]

    volume_now = data["Volume"].iloc[-1]
    volume_ma = data["Vol_MA"].iloc[-1]

    rsi_now = data["RSI"].iloc[-1]

    trend_ok = close_now > ma20_now
    ma20_up = ma20_now > ma20_prev
    price_up = close_now > close_prev
    volume_up = volume_now > volume_ma

    if stock_type == "leverage":
        rsi_ok = 50 <= rsi_now <= 60
    else:
        rsi_ok = 50 <= rsi_now <= 65

    recent_high_prev = data["Close"].rolling(5).max().iloc[-2]

    pullback = close_prev < recent_high_prev
    rebound = close_now > close_prev

    return (
        trend_ok and
        ma20_up and
        price_up and
        volume_up and
        rsi_ok and
        pullback and
        rebound
    )


def make_message(stock_type_label, stock, data, stop_rate, target_rate):
    data = prepare_data(data)

    entry = data["Close"].iloc[-1]
    stop_loss = entry * stop_rate
    take_profit = entry * target_rate

    rsi_now = data["RSI"].iloc[-1]
    volume_now = data["Volume"].iloc[-1]
    volume_ma = data["Vol_MA"].iloc[-1]

    volume_percent = (volume_now / volume_ma) * 100

    msg = f"""
[{stock_type_label}]
🔥 매수 타이밍 포착

종목: {stock}
진입가: {round(entry, 2)}
손절가: {round(stop_loss, 2)}
목표가: {round(take_profit, 2)}
RSI: {round(rsi_now, 2)}
거래량: {int(volume_now):,} (평균 대비 {round(volume_percent)}%)
"""
    return msg


def run_stock_bot():
    global last_day, market_block_alerted, market_was_open

    print("🚀 주식 알림 봇 시작")
    send_message("✅ 주식 알림 봇이 시작되었습니다.")

    while True:
        try:
            today = datetime.date.today()

            if today != last_day:
                sent_today.clear()
                market_block_alerted = False
                last_day = today

            market_open = is_market_open()

            if market_was_open is None:
                market_was_open = market_open

            if market_open and market_was_open is False:
                send_message("🟢 미국 장 시작")
                market_was_open = True

            if not market_open and market_was_open is True:
                send_message("🔴 미국 장 종료")
                market_was_open = False

            if not market_open:
                print("⏰ 장외시간 (미국 뉴욕 기준)")
                time.sleep(CHECK_INTERVAL)
                continue

            print("🔄 검사 시작")

            if not is_market_ok():
                print("🚫 하락장 → 매매 금지")

                if not market_block_alerted:
                    send_message("🚫 하락장 감지 → 매매 중지")
                    market_block_alerted = True

                time.sleep(CHECK_INTERVAL)
                continue

            results = []

            for group in chunks(leverage_stocks, CHUNK_SIZE):
                data_all = download_group(group)

                if data_all is None or data_all.empty:
                    continue

                for stock in group:
                    try:
                        data = data_all[stock].copy()

                        if is_buy_timing(data, "leverage") and stock not in sent_today:
                            msg = make_message(
                                stock_type_label="레버리지",
                                stock=stock,
                                data=data,
                                stop_rate=0.97,
                                target_rate=1.05
                            )

                            results.append(msg)
                            sent_today.add(stock)

                    except Exception as e:
                        print(f"{stock} 처리 오류:", e)
                        continue

            for group in chunks(normal_stocks, CHUNK_SIZE):
                data_all = download_group(group)

                if data_all is None or data_all.empty:
                    continue

                for stock in group:
                    try:
                        data = data_all[stock].copy()

                        if is_buy_timing(data, "normal") and stock not in sent_today:
                            msg = make_message(
                                stock_type_label="일반",
                                stock=stock,
                                data=data,
                                stop_rate=0.96,
                                target_rate=1.06
                            )

                            results.append(msg)
                            sent_today.add(stock)

                    except Exception as e:
                        print(f"{stock} 처리 오류:", e)
                        continue

            if results:
                message = "🔥 매수 타이밍 알림\n\n" + "\n".join(results)
                send_message(message)
            else:
                print("신호 없음")

            print("⏱ 5분 대기...\n")
            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            print("전체 루프 오류:", e)
            send_message(f"⚠️ 봇 오류 발생: {e}")
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_stock_bot, daemon=True)
    bot_thread.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
