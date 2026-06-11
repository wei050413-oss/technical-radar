import os

import pandas as pd
import requests
import yfinance as yf


SHEET_ID = "1DM7x4sQP2Mt7Tiohf2wGLt_l1dizbhAk-sZEIMleqlc"
SHEET_NAME = "watchlist"
MAX_MESSAGE_LENGTH = 4500
TRUNCATION_NOTICE = "訊息過長，已截斷。"


def get_tickers_from_sheets():
    url = (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq"
        f"?tqx=out:csv&sheet={SHEET_NAME}"
    )

    try:
        df = pd.read_csv(url)
        required_columns = {"ticker", "active"}
        missing_columns = required_columns - set(df.columns)
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"缺少必要欄位：{missing}")

        active = df["active"].astype(str).str.strip().str.lower()
        active_df = df[active.isin({"true", "1", "yes", "y"})]
        return (
            active_df["ticker"]
            .dropna()
            .astype(str)
            .str.strip()
            .loc[lambda values: values.ne("")]
            .tolist()
        )
    except Exception as exc:
        print(f"Google Sheets 讀取失敗：{exc}")
        raise


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def get_price_data(ticker):
    df = yf.download(
        ticker,
        period="1y",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    if df.empty:
        raise ValueError("抓不到價格資料")

    if isinstance(df.columns, pd.MultiIndex):
        try:
            close = df["Close"][ticker]
            volume = df["Volume"][ticker]
        except KeyError:
            close = df["Close"].iloc[:, 0]
            volume = df["Volume"].iloc[:, 0]
    else:
        close = df["Close"]
        volume = df["Volume"]

    price_data = pd.DataFrame({"close": close, "volume": volume}).dropna(
        subset=["close"]
    )
    if len(price_data) < 2:
        raise ValueError("價格資料不足，至少需要兩個交易日")

    return price_data


def analyze_ticker(ticker):
    result = {
        "ticker": ticker,
        "close": None,
        "change_pct": None,
        "high_alerts": [],
        "medium_alerts": [],
        "error": None,
    }

    try:
        df = get_price_data(ticker)
        close = df["close"]
        volume = df["volume"]

        ma20 = close.rolling(20).mean()
        ma50 = close.rolling(50).mean()
        rsi = calculate_rsi(close)
        avg_volume_20 = volume.rolling(20).mean()

        latest_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        change_pct = (latest_close - prev_close) / prev_close * 100

        result["close"] = latest_close
        result["change_pct"] = change_pct

        previous_close = close.iloc[:-1]
        previous_20 = previous_close.tail(20)
        previous_52_week = previous_close.tail(252)

        if len(previous_20) >= 20:
            if latest_close > float(previous_20.max()):
                result["high_alerts"].append("突破近20日高點")
            if latest_close < float(previous_20.min()):
                result["high_alerts"].append("跌破近20日低點")

        if not previous_52_week.empty:
            if latest_close > float(previous_52_week.max()):
                result["high_alerts"].append("創52週新高")
            if latest_close < float(previous_52_week.min()):
                result["high_alerts"].append("創52週新低")

        latest_ma50 = ma50.iloc[-1]
        prev_ma50 = ma50.iloc[-2]
        if pd.notna(latest_ma50) and pd.notna(prev_ma50):
            if prev_close > prev_ma50 and latest_close < latest_ma50:
                result["high_alerts"].append("剛跌破50MA")
            if prev_close < prev_ma50 and latest_close > latest_ma50:
                result["high_alerts"].append("剛站上50MA")

        latest_avg_volume = avg_volume_20.iloc[-1]
        latest_volume = volume.iloc[-1]
        if (
            pd.notna(latest_volume)
            and pd.notna(latest_avg_volume)
            and latest_avg_volume > 0
            and latest_volume > latest_avg_volume * 2
        ):
            volume_multiple = latest_volume / latest_avg_volume
            result["high_alerts"].append(f"成交量放大 {volume_multiple:.1f}倍")

        latest_ma20 = ma20.iloc[-1]
        prev_ma20 = ma20.iloc[-2]
        if pd.notna(latest_ma20) and pd.notna(prev_ma20):
            if prev_close > prev_ma20 and latest_close < latest_ma20:
                result["medium_alerts"].append("剛跌破20MA")
            if prev_close < prev_ma20 and latest_close > latest_ma20:
                result["medium_alerts"].append("剛站上20MA")

        latest_rsi = rsi.iloc[-1]
        if pd.notna(latest_rsi):
            if latest_rsi > 80:
                result["medium_alerts"].append(
                    f"RSI {latest_rsi:.1f}，極度過熱"
                )
            if latest_rsi < 30:
                result["medium_alerts"].append(f"RSI {latest_rsi:.1f}，超賣")

        if abs(change_pct) >= 5:
            result["medium_alerts"].append(
                f"當日波動較大 {change_pct:+.2f}%"
            )
    except Exception as exc:
        result["error"] = str(exc)
        print(f"{ticker} 資料處理失敗：{exc}")

    return result


def build_daily_watchlist(results):
    lines = ["📈 Daily Watchlist"]
    for result in results:
        ticker = result["ticker"]
        if result.get("error"):
            lines.append(f"{ticker}: 資料錯誤")
            continue

        lines.append(
            f"{ticker}: {result['close']:.2f} "
            f"({result['change_pct']:+.2f}%)"
        )

    return "\n".join(lines)


def build_technical_alerts(results):
    high_priority = []
    medium_priority = []

    for result in results:
        if result.get("error"):
            continue

        high_alerts = result["high_alerts"][:4]
        remaining_slots = 4 - len(high_alerts)
        medium_alerts = result["medium_alerts"][:remaining_slots]

        if high_alerts:
            high_priority.append((result["ticker"], high_alerts))
        if medium_alerts:
            medium_priority.append((result["ticker"], medium_alerts))

    if not high_priority and not medium_priority:
        return "🚨 Technical Alerts\n今日無重大技術訊號。"

    lines = ["🚨 Technical Alerts"]
    if high_priority:
        lines.extend(["", "🔥 High Priority"])
        for ticker, alerts in high_priority:
            lines.append(ticker)
            lines.extend(f"- {alert}" for alert in alerts)

    if medium_priority:
        lines.extend(["", "⚠️ Medium Priority"])
        for ticker, alerts in medium_priority:
            lines.append(ticker)
            lines.extend(f"- {alert}" for alert in alerts)

    return "\n".join(lines)


def build_message():
    try:
        tickers = get_tickers_from_sheets()
    except Exception:
        return None

    if not tickers:
        print("Google Sheets 中沒有 active=True 的 ticker，不送出 LINE 訊息。")
        return None

    results = [analyze_ticker(ticker) for ticker in tickers]
    message = (
        f"{build_daily_watchlist(results)}\n\n"
        f"{build_technical_alerts(results)}"
    )

    if len(message) > MAX_MESSAGE_LENGTH:
        keep_length = MAX_MESSAGE_LENGTH - len(TRUNCATION_NOTICE) - 1
        message = f"{message[:keep_length].rstrip()}\n{TRUNCATION_NOTICE}"

    return message


def send_line_message(text):
    if not text or not text.strip():
        print("LINE 訊息為空，不送出。")
        return

    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.getenv("LINE_USER_ID")

    if not token or not user_id:
        print("LINE_CHANNEL_ACCESS_TOKEN 或 LINE_USER_ID 沒有設定，不送出訊息。")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}],
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        print(f"LINE 訊息發送成功，status={response.status_code}")
    except requests.RequestException as exc:
        print(f"LINE 訊息發送失敗：{exc}")
        if getattr(exc, "response", None) is not None:
            print(exc.response.text)


def main():
    message = build_message()
    if not message:
        return

    print(message)
    send_line_message(message)


if __name__ == "__main__":
    main()
