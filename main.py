import json
import os
from datetime import date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, quote_plus
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf


SHEET_ID = "1DM7x4sQP2Mt7Tiohf2wGLt_l1dizbhAk-sZEIMleqlc"
MAX_MESSAGE_LENGTH = 4500
TRUNCATION_NOTICE = "訊息過長，已截斷。"
EVENT_STATE_PATH = Path(__file__).with_name("event_state.json")
VOLUME_EXPANSION_THRESHOLD = 1.5
VOLUME_SHRINK_THRESHOLD = 1.0
BIG_MOVE_THRESHOLD = 5.0
TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")
MAX_ALERTS_PER_TICKER = 4
DEFAULT_MARKET = "us"
DEFAULT_REPORT_TYPE = "daily"
WEEKLY_RECAP_FALLBACK = (
    "📊 Weekly Market Recap\n\nWeekly Market Recap 暫時無法產生。"
)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
WEEKLY_NEWS_LIMIT = 12
WEEKLY_WATCHLIST_LIMIT = 5
WEEKLY_INDEX_TICKERS = {
    "Dow Jones": "^DJI",
    "S&P500": "^GSPC",
    "NASDAQ": "^IXIC",
    "PHLX": "^SOX",
}
WEEKLY_SECTOR_ETFS = {
    "XLK": "科技",
    "XLE": "能源",
    "XLF": "金融",
    "XLV": "醫療",
    "XLI": "工業",
    "XLU": "公用事業",
    "XLY": "非必需消費",
    "XLP": "必需消費",
    "SMH": "半導體",
    "SOXX": "半導體",
    "IGV": "軟體",
    "XSD": "半導體設備 / 小型半導體",
    "URA": "鈾 / 核能",
    "NLR": "核能",
    "PAVE": "基建",
    "ITA": "國防",
    "TAN": "太陽能",
    "ICLN": "潔淨能源",
}
WEEKLY_NEWS_KEYWORDS = [
    "Federal Reserve",
    "inflation",
    "CPI",
    "PPI",
    "jobs report",
    "oil price",
    "Middle East",
    "Iran",
    "tariffs",
    "Nvidia",
    "AI infrastructure",
    "semiconductor",
    "data center power",
    "nuclear energy",
]
WATCHLIST_CONFIGS = {
    "us": {
        "sheet_name": "US Watchlist",
        "display": "ticker",
        "title": "📈 Daily Watchlist",
        "include_event_radar": True,
        "event_scope": "us",
    },
    "tw": {
        "sheet_name": "TW Watchlist",
        "display": "name",
        "title": "📈 台股 Watchlist",
        "include_event_radar": True,
        "event_scope": "tw",
    },
}

MACRO_EVENTS = [
    {
        "id": "CPI_2026-06-12",
        "type": "macro",
        "name": "CPI",
        "date": "2026-06-12",
        "session": "before_market",
    },
    {
        "id": "PPI_2026-06-15",
        "type": "macro",
        "name": "PPI",
        "date": "2026-06-15",
        "session": "before_market",
    },
    {
        "id": "FOMC_2026-06-18",
        "type": "macro",
        "name": "FOMC",
        "date": "2026-06-18",
        "session": "after_market",
    },
    {
        "id": "NFP_2026-07-05",
        "type": "macro",
        "name": "非農就業",
        "date": "2026-07-05",
        "session": "before_market",
    },
    {
        "id": "PCE_2026-07-26",
        "type": "macro",
        "name": "PCE",
        "date": "2026-07-26",
        "session": "before_market",
    },
]


def get_watchlist_config(market=None):
    market_key = (market or os.getenv("WATCHLIST_MARKET") or DEFAULT_MARKET).lower()
    if market_key not in WATCHLIST_CONFIGS:
        raise ValueError(f"不支援的 WATCHLIST_MARKET：{market_key}")
    return WATCHLIST_CONFIGS[market_key]


def get_watchlist_from_sheets(market=None):
    config = get_watchlist_config(market)
    sheet_name = quote(config["sheet_name"])
    url = (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq"
        f"?tqx=out:csv&sheet={sheet_name}"
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
        watchlist = []
        for _, row in active_df.iterrows():
            ticker = str(row.get("ticker", "")).strip()
            if not ticker:
                continue

            name = row.get("name", "")
            if pd.isna(name):
                name = ""
            name = str(name).strip()
            display_name = name if config["display"] == "name" and name else ticker
            watchlist.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "display_name": display_name,
                }
            )

        return watchlist
    except Exception as exc:
        print(f"Google Sheets 讀取失敗：{exc}")
        raise


def get_tickers_from_sheets(market=None):
    return [item["ticker"] for item in get_watchlist_from_sheets(market)]


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_volume_ratio(volume, avg_volume_20d):
    if (
        pd.isna(volume)
        or pd.isna(avg_volume_20d)
        or avg_volume_20d <= 0
    ):
        return None

    return volume / avg_volume_20d


def get_volume_price_signal(change_pct, volume_ratio):
    if change_pct is None or volume_ratio is None:
        return {"label": None, "priority": "none"}
    if pd.isna(change_pct) or pd.isna(volume_ratio):
        return {"label": None, "priority": "none"}

    ratio_label = f"{volume_ratio:.1f}x Avg Volume"

    if change_pct == 0:
        return {
            "label": f"➖ 價格持平（{ratio_label}）",
            "priority": "none",
        }

    if volume_ratio >= VOLUME_EXPANSION_THRESHOLD:
        if change_pct >= BIG_MOVE_THRESHOLD:
            return {
                "label": f"🔥 強勢放量上漲（{ratio_label}）",
                "priority": "high",
            }
        if change_pct > 0:
            return {
                "label": f"📈 放量上漲（{ratio_label}）",
                "priority": "high",
            }
        if change_pct <= -BIG_MOVE_THRESHOLD:
            return {
                "label": f"🚨 強勢放量下跌（{ratio_label}）",
                "priority": "high",
            }
        return {
            "label": f"⚠️ 放量下跌（{ratio_label}）",
            "priority": "high",
        }

    if volume_ratio < VOLUME_SHRINK_THRESHOLD:
        if change_pct >= BIG_MOVE_THRESHOLD:
            return {
                "label": f"⚠️ 強勢量縮上漲（{ratio_label}）",
                "priority": "medium",
            }
        if change_pct <= -BIG_MOVE_THRESHOLD:
            return {
                "label": f"📉 強勢量縮下跌（{ratio_label}）",
                "priority": "medium",
            }
        return {"label": None, "priority": "none"}

    if change_pct > 0:
        return {
            "label": f"➖ 正常量能上漲（{ratio_label}）",
            "priority": "none",
        }

    return {
        "label": f"➖ 正常量能下跌（{ratio_label}）",
        "priority": "none",
    }


def is_volume_price_signal(alert):
    return isinstance(alert, str) and "Avg Volume" in alert


def limit_alerts_prioritizing_volume_signal(alerts, limit=MAX_ALERTS_PER_TICKER):
    if limit <= 0:
        return [alert for alert in alerts if is_volume_price_signal(alert)][:1]

    if len(alerts) <= limit:
        return alerts

    visible_alerts = alerts[:limit]
    if any(is_volume_price_signal(alert) for alert in visible_alerts):
        return visible_alerts

    for alert in alerts[limit:]:
        if is_volume_price_signal(alert):
            return visible_alerts[: limit - 1] + [alert]

    return visible_alerts


def get_today_taipei():
    return datetime.now(TAIPEI_TIMEZONE).date()


def parse_event_date(event):
    current_year = get_today_taipei().year
    value = event.get("date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        value = value.strip()
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            pass

        for separator in ("/", "-"):
            if separator not in value:
                continue
            try:
                month, day = (int(part) for part in value.split(separator, 1))
                return date(current_year, month, day)
            except ValueError:
                continue
    raise ValueError(f"無效事件日期：{value}")


def format_event_date(event_date):
    if isinstance(event_date, datetime):
        event_date = event_date.date()
    return f"{event_date.month}/{event_date.day}"


def format_event_session(session):
    session_labels = {
        "before_market": "盤前公布",
        "after_market": "盤後公布",
        "during_market": "盤中公布",
        "unknown": "時間待確認",
    }
    return session_labels.get(session, "時間待確認")


def format_event_line(event):
    event_date = parse_event_date(event)
    session = format_event_session(event.get("session", "unknown"))
    return f"{event['name']}：{format_event_date(event_date)} {session}"


def _flatten_calendar_values(value):
    if isinstance(value, pd.DataFrame):
        return value.to_numpy().ravel().tolist()
    if isinstance(value, (pd.Series, pd.Index)):
        return value.tolist()
    if isinstance(value, (list, tuple, set)):
        flattened = []
        for item in value:
            flattened.extend(_flatten_calendar_values(item))
        return flattened
    return [value]


def _extract_earnings_values(calendar):
    if calendar is None:
        return []

    if isinstance(calendar, dict):
        for key, value in calendar.items():
            if "earnings date" in str(key).strip().lower():
                return _flatten_calendar_values(value)
        return []

    if isinstance(calendar, pd.Series):
        for key, value in calendar.items():
            if "earnings date" in str(key).strip().lower():
                return _flatten_calendar_values(value)
        return []

    if isinstance(calendar, pd.DataFrame):
        for label in calendar.index:
            if "earnings date" in str(label).strip().lower():
                return _flatten_calendar_values(calendar.loc[label])
        for label in calendar.columns:
            if "earnings date" in str(label).strip().lower():
                return _flatten_calendar_values(calendar[label])

    return []


def _parse_earnings_datetime(value):
    if value is None or pd.isna(value):
        return None

    try:
        if isinstance(value, (int, float)):
            timestamp = pd.to_datetime(value, unit="s")
        else:
            timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if pd.isna(timestamp):
        return None
    return timestamp.to_pydatetime()


def _get_earnings_session(earnings_datetime):
    if not isinstance(earnings_datetime, datetime):
        return "unknown"

    event_time = earnings_datetime.time()
    if event_time == time.min:
        return "unknown"

    eastern = ZoneInfo("America/New_York")
    if earnings_datetime.tzinfo is None:
        eastern_datetime = earnings_datetime.replace(tzinfo=eastern)
    else:
        eastern_datetime = earnings_datetime.astimezone(eastern)

    event_time = eastern_datetime.time().replace(tzinfo=None)
    if event_time < time(9, 30):
        return "before_market"
    if event_time >= time(16, 0):
        return "after_market"
    if time(9, 30) <= event_time < time(16, 0):
        return "during_market"
    return "unknown"


def get_earnings_events(watchlist):
    events = []
    today = get_today_taipei()

    for item in watchlist:
        ticker = item["ticker"] if isinstance(item, dict) else item
        display_name = (
            item.get("display_name", ticker)
            if isinstance(item, dict)
            else ticker
        )
        try:
            calendar = yf.Ticker(ticker).calendar
            values = _extract_earnings_values(calendar)
            earnings_datetimes = [
                parsed
                for value in values
                if (parsed := _parse_earnings_datetime(value)) is not None
            ]
            upcoming = [
                value for value in earnings_datetimes if value.date() >= today
            ]
            if not upcoming:
                continue

            earnings_datetime = min(upcoming, key=lambda value: value.date())
            earnings_date = earnings_datetime.date().isoformat()
            events.append(
                {
                    "id": f"{ticker}_earnings_{earnings_date}",
                    "type": "earnings",
                    "name": f"{display_name} 財報",
                    "ticker": ticker,
                    "date": earnings_date,
                    "session": _get_earnings_session(earnings_datetime),
                }
            )
        except Exception as exc:
            print(f"Warning: {ticker} 財報日期讀取失敗，已跳過：{exc}")

    return events


def get_macro_events():
    events = []
    for event in MACRO_EVENTS:
        try:
            parse_event_date(event)
            events.append(event.copy())
        except (TypeError, ValueError) as exc:
            print(
                f"Warning: macro event {event.get('id', 'unknown')} "
                f"日期格式錯誤，已跳過：{exc}"
            )
    return events


def get_tw_market_events(today=None):
    today = today or get_today_taipei()
    events = []

    for month_offset in range(3):
        month = today.month + month_offset
        year = today.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        revenue_deadline = date(year, month, 10)
        events.append(
            {
                "id": f"TW_monthly_revenue_{year}-{month:02d}",
                "type": "tw_market",
                "name": "台股月營收公告截止",
                "date": revenue_deadline.isoformat(),
                "session": "unknown",
            }
        )

    return events


def get_upcoming_events(events, today):
    upcoming_events = []
    for event in events:
        try:
            event_date = parse_event_date(event)
        except (TypeError, ValueError) as exc:
            print(
                f"Warning: event {event.get('id', 'unknown')} "
                f"日期格式錯誤，已跳過：{exc}"
            )
            continue

        if event_date >= today:
            upcoming_events.append(event)

    return upcoming_events


def load_event_state():
    empty_state = {"notified_event_ids": []}
    if not EVENT_STATE_PATH.exists():
        try:
            save_event_state(empty_state)
        except OSError as exc:
            print(f"Warning: event_state.json 建立失敗：{exc}")
        return empty_state

    try:
        with EVENT_STATE_PATH.open("r", encoding="utf-8") as state_file:
            state = json.load(state_file)
        notified_ids = state.get("notified_event_ids")
        if not isinstance(notified_ids, list):
            raise ValueError("notified_event_ids 必須是 list")
        return {"notified_event_ids": list(dict.fromkeys(notified_ids))}
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        print(f"Warning: event_state.json 讀取失敗，使用空狀態：{exc}")
        return empty_state


def save_event_state(state):
    normalized_state = {
        "notified_event_ids": list(
            dict.fromkeys(state.get("notified_event_ids", []))
        )
    }
    with EVENT_STATE_PATH.open("w", encoding="utf-8") as state_file:
        json.dump(normalized_state, state_file, ensure_ascii=False, indent=2)
        state_file.write("\n")

    # GitHub Actions runner is ephemeral. Persisting this state there later
    # requires committing it back to the repo or using external storage such
    # as Google Sheets, GitHub Gist, Firebase, or Supabase.


def get_new_events(events, state):
    notified_ids = set(state.get("notified_event_ids", []))
    return [event for event in events if event["id"] not in notified_ids]


def update_event_state(state, new_events):
    notified_ids = list(state.get("notified_event_ids", []))
    notified_ids.extend(event["id"] for event in new_events)
    state["notified_event_ids"] = list(dict.fromkeys(notified_ids))
    return state


def get_this_week_events(events, today):
    end_date = today + timedelta(days=7)
    this_week = []

    for event in events:
        try:
            event_date = parse_event_date(event)
        except (TypeError, ValueError) as exc:
            print(
                f"Warning: event {event.get('id', 'unknown')} "
                f"日期格式錯誤，已跳過：{exc}"
            )
            continue

        if today <= event_date <= end_date:
            this_week.append(event)

    return this_week


def _sort_events(events):
    return sorted(events, key=lambda event: (parse_event_date(event), event["name"]))


def build_event_radar(watchlist, event_scope="us"):
    today = get_today_taipei()
    earnings_events = get_earnings_events(watchlist)
    if event_scope == "tw":
        market_events = get_tw_market_events(today)
    else:
        market_events = get_macro_events()

    events_by_id = {
        event["id"]: event for event in earnings_events + market_events
    }
    events = _sort_events(get_upcoming_events(events_by_id.values(), today))

    state = load_event_state()
    new_events = get_new_events(events, state)[:10]
    new_event_ids = {event["id"] for event in new_events}
    this_week_events = [
        event
        for event in get_this_week_events(events, today)
        if event["id"] not in new_event_ids
    ][:10]

    if new_events:
        update_event_state(state, new_events)
        try:
            save_event_state(state)
        except OSError as exc:
            print(f"Warning: event_state.json 更新失敗：{exc}")

    if not new_events and not this_week_events:
        return "📅 Event Radar\n今日無新的或當週重要事件。"

    lines = ["📅 Event Radar"]
    if new_events:
        lines.extend(["", "🆕 New Events"])
        lines.extend(format_event_line(event) for event in new_events)

    if this_week_events:
        lines.extend(["", "📌 This Week"])
        lines.extend(format_event_line(event) for event in this_week_events)

    return "\n".join(lines)


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


def analyze_ticker(ticker, display_name=None):
    result = {
        "ticker": ticker,
        "display_name": display_name or ticker,
        "close": None,
        "change_pct": None,
        "volume_ratio": None,
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
        volume_ratio = calculate_volume_ratio(latest_volume, latest_avg_volume)
        result["volume_ratio"] = volume_ratio
        volume_signal = get_volume_price_signal(change_pct, volume_ratio)
        if volume_signal["priority"] == "high":
            result["high_alerts"].append(volume_signal["label"])
        if volume_signal["priority"] == "medium":
            result["medium_alerts"].append(volume_signal["label"])

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

    except Exception as exc:
        result["error"] = str(exc)
        print(f"{ticker} 資料處理失敗：{exc}")

    return result


def build_daily_watchlist(results, title="📈 Daily Watchlist"):
    lines = [title]
    for result in results:
        display_name = result.get("display_name") or result["ticker"]
        if result.get("error"):
            lines.append(f"{display_name}: 資料錯誤")
            continue

        lines.append(
            f"{display_name}: {result['close']:.2f} "
            f"({result['change_pct']:+.2f}%)"
        )

    return "\n".join(lines)


def build_technical_alerts(results):
    ticker_alerts = []

    for result in results:
        if result.get("error"):
            continue

        high_alerts = limit_alerts_prioritizing_volume_signal(
            result["high_alerts"]
        )
        remaining_slots = MAX_ALERTS_PER_TICKER - len(high_alerts)
        medium_alerts = limit_alerts_prioritizing_volume_signal(
            result["medium_alerts"],
            remaining_slots,
        )
        alerts = high_alerts + medium_alerts

        if alerts:
            display_name = result.get("display_name") or result["ticker"]
            ticker_alerts.append((display_name, alerts))

    if not ticker_alerts:
        return "🚨 Technical Alerts\n\nNo alerts today."

    lines = ["🚨 Technical Alerts"]
    for ticker, alerts in ticker_alerts:
        lines.extend(["", ticker])
        lines.extend(f"- {alert}" for alert in alerts)

    return "\n".join(lines)


def build_message(market=None):
    try:
        config = get_watchlist_config(market)
    except ValueError as exc:
        print(exc)
        return None

    try:
        watchlist = get_watchlist_from_sheets(market)
    except Exception:
        return None

    if not watchlist:
        print("Google Sheets 中沒有 active=True 的 ticker，不送出 LINE 訊息。")
        return None

    results = [
        analyze_ticker(item["ticker"], item["display_name"])
        for item in watchlist
    ]

    sections = [
        build_daily_watchlist(results, config["title"]),
        build_technical_alerts(results),
    ]

    if config["include_event_radar"]:
        try:
            event_radar = build_event_radar(
                watchlist,
                config.get("event_scope", "us"),
            )
        except Exception as exc:
            print(f"Warning: Event Radar 建立失敗：{exc}")
            event_radar = "📅 Event Radar\n今日無新的或當週重要事件。"
        sections.append(event_radar)

    message = "\n\n".join(sections)

    if len(message) > MAX_MESSAGE_LENGTH:
        keep_length = MAX_MESSAGE_LENGTH - len(TRUNCATION_NOTICE) - 1
        message = f"{message[:keep_length].rstrip()}\n{TRUNCATION_NOTICE}"

    return message


def should_run_weekly_recap(today=None):
    today = today or get_today_taipei()
    return today.weekday() == 0


def get_previous_us_week_range(today=None):
    today = today or get_today_taipei()
    days_since_previous_monday = today.weekday() + 7
    week_start = today - timedelta(days=days_since_previous_monday)
    week_end = week_start + timedelta(days=4)
    return week_start, week_end


def calculate_period_return(price_data, start_date=None, end_date=None):
    if price_data is None or price_data.empty:
        return None

    data = price_data.copy()
    if not isinstance(data.index, pd.DatetimeIndex):
        data.index = pd.to_datetime(data.index)

    if "close" in data.columns:
        close = data["close"]
    elif "Close" in data.columns:
        close = data["Close"]
    else:
        raise ValueError("缺少 close 欄位")

    close = close.dropna()
    if start_date is not None:
        close = close[close.index.date >= start_date]
    if end_date is not None:
        close = close[close.index.date <= end_date]
    if len(close) < 2:
        return None

    start_price = float(close.iloc[0])
    end_price = float(close.iloc[-1])
    if start_price <= 0:
        return None

    return (end_price - start_price) / start_price * 100


def get_period_price_data(ticker, start_date, end_date):
    download_end = end_date + timedelta(days=3)
    df = yf.download(
        ticker,
        start=start_date.isoformat(),
        end=download_end.isoformat(),
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    if df.empty:
        raise ValueError("抓不到價格資料")

    if isinstance(df.columns, pd.MultiIndex):
        try:
            close = df["Close"][ticker]
        except KeyError:
            close = df["Close"].iloc[:, 0]
    else:
        close = df["Close"]

    return pd.DataFrame({"close": close}).dropna(subset=["close"])


def get_weekly_returns(tickers, start_date, end_date):
    returns = {}
    for label, ticker in tickers.items():
        try:
            price_data = get_period_price_data(ticker, start_date, end_date)
            period_return = calculate_period_return(
                price_data,
                start_date,
                end_date,
            )
            if period_return is not None:
                returns[label] = round(period_return, 2)
        except Exception as exc:
            print(f"Warning: {label} 週報酬率讀取失敗，已跳過：{exc}")

    return returns


def get_watchlist_weekly_movers(watchlist, start_date, end_date):
    movers = []
    for item in watchlist:
        ticker = item["ticker"]
        display_name = item.get("display_name") or ticker
        try:
            price_data = get_period_price_data(ticker, start_date, end_date)
            period_return = calculate_period_return(
                price_data,
                start_date,
                end_date,
            )
            if period_return is None:
                continue
            movers.append(
                {
                    "ticker": ticker,
                    "name": display_name,
                    "return_pct": round(period_return, 2),
                }
            )
        except Exception as exc:
            print(f"Warning: {ticker} watchlist 週報酬率讀取失敗，已跳過：{exc}")

    gainers = sorted(movers, key=lambda item: item["return_pct"], reverse=True)[
        :WEEKLY_WATCHLIST_LIMIT
    ]
    losers = sorted(movers, key=lambda item: item["return_pct"])[:WEEKLY_WATCHLIST_LIMIT]
    big_moves = [
        item for item in movers if abs(item["return_pct"]) >= BIG_MOVE_THRESHOLD
    ]
    big_moves = sorted(
        big_moves,
        key=lambda item: abs(item["return_pct"]),
        reverse=True,
    )

    return {
        "gainers": format_movers(gainers),
        "losers": format_movers(losers),
        "big_moves": format_movers(big_moves),
    }


def format_movers(movers):
    return [
        f"{item['name']} {item['return_pct']:+.1f}%"
        for item in movers
    ]


def parse_rss_items(xml_text, start_date, end_date, limit):
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    items = []
    seen_titles = set()
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "Google News").strip()
        published_text = (item.findtext("pubDate") or "").strip()
        if not title or title.lower() in seen_titles:
            continue

        published_at = ""
        published_date = None
        if published_text:
            try:
                published = parsedate_to_datetime(published_text)
                published_date = published.date()
                published_at = published_date.isoformat()
            except (TypeError, ValueError):
                published_at = published_text

        if published_date and not (start_date <= published_date <= end_date):
            continue

        seen_titles.add(title.lower())
        items.append(
            {
                "title": title,
                "source": source,
                "published_at": published_at,
                "url": link,
            }
        )
        if len(items) >= limit:
            break

    return items


def fetch_weekly_market_news(start_date, end_date, limit=WEEKLY_NEWS_LIMIT):
    query = " OR ".join(f'"{keyword}"' for keyword in WEEKLY_NEWS_KEYWORDS)
    query = f"({query}) after:{start_date.isoformat()} before:{(end_date + timedelta(days=1)).isoformat()}"
    url = (
        "https://news.google.com/rss/search"
        f"?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        return parse_rss_items(response.text, start_date, end_date, limit)
    except requests.RequestException as exc:
        print(f"Warning: Weekly news 讀取失敗，已跳過：{exc}")
        return []


def collect_weekly_market_data(today=None):
    week_start, week_end = get_previous_us_week_range(today)
    indices = get_weekly_returns(WEEKLY_INDEX_TICKERS, week_start, week_end)
    sector_tickers = {
        ticker: ticker
        for ticker in WEEKLY_SECTOR_ETFS
    }
    sector_returns = get_weekly_returns(sector_tickers, week_start, week_end)

    try:
        watchlist = get_watchlist_from_sheets("us")
    except Exception as exc:
        print(f"Warning: US Watchlist 讀取失敗，週報略過 watchlist：{exc}")
        watchlist = []

    watchlist_top_movers = get_watchlist_weekly_movers(
        watchlist,
        week_start,
        week_end,
    )
    news_headlines = fetch_weekly_market_news(week_start, week_end)

    return {
        "week_range": f"{week_start.isoformat()} to {week_end.isoformat()}",
        "indices": format_return_map(indices),
        "sector_etfs": format_return_map(sector_returns),
        "sector_etf_labels": WEEKLY_SECTOR_ETFS,
        "watchlist_top_movers": watchlist_top_movers,
        "news_headlines": news_headlines,
    }


def format_return_map(returns):
    return {
        label: f"{value:+.1f}%"
        for label, value in returns.items()
    }


def build_weekly_recap_prompt(summary):
    system_prompt = (
        "你是謹慎的市場摘要助手。只能根據使用者提供的 structured data "
        "整理每週市場回顧，不要給買賣建議，不要硬編原因。"
    )
    user_prompt = f"""
請根據以下 JSON 產生適合 LINE 閱讀的繁體中文美股週報。

規則：
1. 不要給買賣建議。
2. 不要硬編原因。
3. 如果新聞與市場表現無法明確連結，請寫「市場反應較分散，未出現單一明確主線」。
4. 本週主線最多 3 點。
5. 類股輪動最多列 3 個強勢、3 個弱勢。
6. 內容要短。
7. 固定使用以下格式：

📊 Weekly Market Recap

本週主線：

1. ...
2. ...
3. ...

市場反應：

✓ ...
✓ ...
✗ ...

類股輪動：

↑ ...
↑ ...
↑ ...

↓ ...
↓ ...

指數表現：

Dow Jones +x.x%
S&P500 +x.x%
NASDAQ +x.x%
PHLX +x.x%

Structured data:
{json.dumps(summary, ensure_ascii=False, indent=2)}
""".strip()
    return system_prompt, user_prompt


def call_openai_weekly_recap(summary):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 沒有設定")

    system_prompt, user_prompt = build_weekly_recap_prompt(summary)
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 600,
        },
        timeout=45,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


def build_weekly_market_recap_message(today=None):
    try:
        summary = collect_weekly_market_data(today)
        recap = call_openai_weekly_recap(summary)
    except Exception as exc:
        print(f"Warning: Weekly Market Recap 建立失敗：{exc}")
        return WEEKLY_RECAP_FALLBACK

    if not recap.startswith("📊 Weekly Market Recap"):
        recap = f"📊 Weekly Market Recap\n\n{recap}"

    if len(recap) > MAX_MESSAGE_LENGTH:
        keep_length = MAX_MESSAGE_LENGTH - len(TRUNCATION_NOTICE) - 1
        recap = f"{recap[:keep_length].rstrip()}\n{TRUNCATION_NOTICE}"

    return recap


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
    report_type = os.getenv("REPORT_TYPE", DEFAULT_REPORT_TYPE).lower()
    if report_type == "weekly":
        message = build_weekly_market_recap_message()
    else:
        message = build_message()

    if not message:
        return

    print(message)
    send_line_message(message)


if __name__ == "__main__":
    main()
