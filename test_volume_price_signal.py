import unittest
from datetime import date, datetime
from unittest.mock import Mock, patch

import pandas as pd

import main
from main import (
    TAIPEI_TIMEZONE,
    analyze_ticker,
    build_weekly_market_recap_message,
    build_weekly_recap_prompt,
    build_daily_watchlist,
    build_event_radar,
    build_message,
    build_technical_alerts,
    calculate_period_return,
    calculate_volume_ratio,
    get_expected_price_date,
    get_price_data,
    get_earnings_events,
    get_macro_events,
    get_previous_us_week_range,
    get_watchlist_from_sheets,
    get_upcoming_events,
    get_volume_price_signal,
    parse_bls_release_events,
    should_run_weekly_recap,
)


class VolumePriceSignalTest(unittest.TestCase):
    def assertSignal(self, change_pct, volume_ratio, label, priority):
        self.assertEqual(
            get_volume_price_signal(change_pct, volume_ratio),
            {"label": label, "priority": priority},
        )

    def test_strong_volume_expansion_up(self):
        self.assertSignal(
            5.0,
            2.1,
            "🔥 強勢放量上漲（2.1x Avg Volume）",
            "high",
        )

    def test_volume_expansion_up(self):
        self.assertSignal(
            3.0,
            2.1,
            "📈 放量上漲（2.1x Avg Volume）",
            "high",
        )

    def test_strong_volume_expansion_down(self):
        self.assertSignal(
            -5.0,
            2.1,
            "🚨 強勢放量下跌（2.1x Avg Volume）",
            "high",
        )

    def test_volume_expansion_down(self):
        self.assertSignal(
            -3.0,
            2.1,
            "⚠️ 放量下跌（2.1x Avg Volume）",
            "high",
        )

    def test_strong_volume_shrink_up(self):
        self.assertSignal(
            5.0,
            0.7,
            "⚠️ 強勢量縮上漲（0.7x Avg Volume）",
            "medium",
        )

    def test_volume_shrink_up_is_not_alerted(self):
        self.assertSignal(
            3.0,
            0.7,
            None,
            "none",
        )

    def test_strong_volume_shrink_down(self):
        self.assertSignal(
            -5.0,
            0.7,
            "📉 強勢量縮下跌（0.7x Avg Volume）",
            "medium",
        )

    def test_volume_shrink_down_is_not_alerted(self):
        self.assertSignal(
            -3.0,
            0.7,
            None,
            "none",
        )

    def test_normal_volume_up_is_not_alerted(self):
        self.assertSignal(
            3.0,
            1.2,
            "➖ 正常量能上漲（1.2x Avg Volume）",
            "none",
        )

    def test_normal_volume_down_is_not_alerted(self):
        self.assertSignal(
            -3.0,
            1.2,
            "➖ 正常量能下跌（1.2x Avg Volume）",
            "none",
        )

    def test_flat_price_is_not_alerted(self):
        self.assertSignal(
            0.0,
            2.1,
            "➖ 價格持平（2.1x Avg Volume）",
            "none",
        )

    def test_missing_avg_volume_does_not_create_signal(self):
        volume_ratio = calculate_volume_ratio(100, None)
        self.assertIsNone(volume_ratio)
        self.assertSignal(None, volume_ratio, None, "none")

    def test_zero_avg_volume_does_not_create_signal(self):
        volume_ratio = calculate_volume_ratio(100, 0)
        self.assertIsNone(volume_ratio)
        self.assertSignal(3.0, volume_ratio, None, "none")


class TechnicalAlertsTest(unittest.TestCase):
    def build_alerts(self, high_alerts=None, medium_alerts=None):
        return build_technical_alerts(
            [
                {
                    "ticker": "TEST",
                    "high_alerts": high_alerts or [],
                    "medium_alerts": medium_alerts or [],
                }
            ]
        )

    def test_volume_expansion_up_is_displayed_without_priority_heading(self):
        message = self.build_alerts(
            high_alerts=[
                "突破近20日高點",
                "創52週新高",
                "剛站上50MA",
                "RSI breakout",
                "📈 放量上漲（2.1x Avg Volume）",
            ]
        )

        self.assertNotIn("🔥 High Priority", message)
        self.assertNotIn("⚠️ Medium Priority", message)
        self.assertIn("TEST", message)
        self.assertIn("- 📈 放量上漲（2.1x Avg Volume）", message)

    def test_volume_expansion_down_is_displayed(self):
        message = self.build_alerts(
            high_alerts=["⚠️ 放量下跌（2.1x Avg Volume）"]
        )

        self.assertIn("- ⚠️ 放量下跌（2.1x Avg Volume）", message)

    def test_strong_volume_shrink_up_is_displayed(self):
        message = self.build_alerts(
            medium_alerts=["⚠️ 強勢量縮上漲（0.7x Avg Volume）"]
        )

        self.assertIn("- ⚠️ 強勢量縮上漲（0.7x Avg Volume）", message)

    def test_strong_volume_shrink_down_is_displayed(self):
        message = self.build_alerts(
            medium_alerts=["📉 強勢量縮下跌（0.7x Avg Volume）"]
        )

        self.assertIn("- 📉 強勢量縮下跌（0.7x Avg Volume）", message)

    def test_high_and_medium_alerts_are_grouped_under_same_ticker_once(self):
        message = self.build_alerts(
            high_alerts=["跌破近20日低點"],
            medium_alerts=["RSI 23.1，超賣"],
        )

        self.assertEqual(message.count("TEST"), 1)
        self.assertIn("TEST\n- 跌破近20日低點\n- RSI 23.1，超賣", message)

    def test_alerts_use_display_name_when_available(self):
        message = build_technical_alerts(
            [
                {
                    "ticker": "2330.TW",
                    "display_name": "台積電",
                    "high_alerts": ["📈 放量上漲（2.1x Avg Volume）"],
                    "medium_alerts": [],
                }
            ]
        )

        self.assertIn("台積電\n- 📈 放量上漲（2.1x Avg Volume）", message)
        self.assertNotIn("2330.TW", message)

    def test_regular_volume_shrink_up_is_not_displayed(self):
        signal = get_volume_price_signal(3.0, 0.7)
        message = self.build_alerts(
            medium_alerts=[] if signal["priority"] == "none" else [signal["label"]]
        )

        self.assertEqual(signal, {"label": None, "priority": "none"})
        self.assertNotIn("量縮上漲", message)

    def test_regular_volume_shrink_down_is_not_displayed(self):
        signal = get_volume_price_signal(-3.0, 0.7)
        message = self.build_alerts(
            medium_alerts=[] if signal["priority"] == "none" else [signal["label"]]
        )

        self.assertEqual(signal, {"label": None, "priority": "none"})
        self.assertNotIn("量縮下跌", message)

    def test_normal_volume_is_not_displayed(self):
        message = self.build_alerts()

        self.assertNotIn("正常量能", message)
        self.assertEqual(message, "🚨 Technical Alerts\n\nNo alerts today.")

    def test_missing_avg_volume_does_not_error_or_display(self):
        volume_ratio = calculate_volume_ratio(100, float("nan"))
        signal = get_volume_price_signal(3.0, volume_ratio)
        message = self.build_alerts(
            high_alerts=[] if signal["priority"] == "none" else [signal["label"]]
        )

        self.assertIsNone(volume_ratio)
        self.assertEqual(signal, {"label": None, "priority": "none"})
        self.assertNotIn("Avg Volume", message)

    def price_data(self, latest_close, latest_volume):
        close = [100.0] * 59 + [latest_close]
        volume = [100.0] * 59 + [latest_volume]
        index = pd.date_range("2026-06-01", periods=60, freq="D")
        return pd.DataFrame({"close": close, "volume": volume}, index=index)

    def analyze_with_price_data(self, latest_close, latest_volume):
        with patch(
            "main.get_price_data",
            return_value=self.price_data(latest_close, latest_volume),
        ):
            return analyze_ticker("TEST")

    def test_analyze_appends_volume_expansion_up_to_high_alerts(self):
        result = self.analyze_with_price_data(103.0, 210.0)

        self.assertIn("📈 放量上漲（2.0x Avg Volume）", result["high_alerts"])

    def test_analyze_appends_volume_expansion_down_to_high_alerts(self):
        result = self.analyze_with_price_data(97.0, 210.0)

        self.assertIn("⚠️ 放量下跌（2.0x Avg Volume）", result["high_alerts"])

    def test_analyze_appends_strong_volume_shrink_up_to_medium_alerts(self):
        result = self.analyze_with_price_data(106.0, 10.0)

        self.assertIn("⚠️ 強勢量縮上漲（0.1x Avg Volume）", result["medium_alerts"])

    def test_analyze_does_not_append_regular_volume_shrink_up(self):
        result = self.analyze_with_price_data(103.0, 10.0)

        self.assertNotIn("⚠️ 量縮上漲（0.1x Avg Volume）", result["medium_alerts"])

    def test_analyze_appends_strong_volume_shrink_down_to_medium_alerts(self):
        result = self.analyze_with_price_data(94.0, 10.0)

        self.assertIn("📉 強勢量縮下跌（0.1x Avg Volume）", result["medium_alerts"])

    def test_analyze_does_not_append_regular_volume_shrink_down(self):
        result = self.analyze_with_price_data(97.0, 10.0)

        self.assertNotIn("📉 量縮下跌（0.1x Avg Volume）", result["medium_alerts"])

    def test_analyze_does_not_append_normal_volume_signal(self):
        result = self.analyze_with_price_data(103.0, 120.0)
        all_alerts = result["high_alerts"] + result["medium_alerts"]

        self.assertFalse(any("正常量能" in alert for alert in all_alerts))

    def test_analyze_ignores_missing_avg_volume(self):
        df = self.price_data(103.0, float("nan"))
        df["volume"] = float("nan")
        with patch("main.get_price_data", return_value=df):
            result = analyze_ticker("TEST")
        all_alerts = result["high_alerts"] + result["medium_alerts"]

        self.assertIsNone(result["error"])
        self.assertFalse(any("Avg Volume" in alert for alert in all_alerts))

    def test_analyze_rejects_stale_price_date_when_expected_date_is_set(self):
        df = self.price_data(103.0, 120.0)
        with patch("main.get_price_data", return_value=df):
            result = analyze_ticker(
                "TEST",
                expected_price_date=date(2026, 9, 3),
            )

        self.assertIn("價格資料日期不符", result["error"])
        self.assertEqual(result["price_date"], date(2026, 7, 30))

    def test_get_price_data_uses_quote_when_history_is_stale(self):
        df = self.price_data(103.0, 120.0).rename(
            columns={"close": "Close", "volume": "Volume"}
        )
        ticker = Mock()
        ticker.info = {
            "regularMarketTime": int(
                datetime(2026, 9, 2, 16, 0, tzinfo=main.NEW_YORK_TIMEZONE)
                .timestamp()
            ),
            "regularMarketPrice": 106.0,
            "regularMarketVolume": 210,
        }

        with (
            patch("main.yf.download", return_value=df),
            patch("main.yf.Ticker", return_value=ticker),
        ):
            price_data = get_price_data("TEST", date(2026, 9, 2))

        self.assertEqual(price_data.index[-1].date(), date(2026, 9, 2))
        self.assertEqual(price_data["close"].iloc[-1], 106.0)
        self.assertEqual(price_data["volume"].iloc[-1], 210)

    def test_us_expected_price_date_uses_previous_new_york_close(self):
        now = datetime(2026, 9, 3, 4, 30, tzinfo=TAIPEI_TIMEZONE)

        self.assertEqual(get_expected_price_date("us", now), date(2026, 9, 2))

    def test_medium_volume_signal_survives_when_high_alerts_are_full(self):
        message = self.build_alerts(
            high_alerts=[
                "突破近20日高點",
                "創52週新高",
                "剛站上50MA",
                "其他高優先訊號",
            ],
            medium_alerts=["⚠️ 強勢量縮上漲（0.7x Avg Volume）"],
        )

        self.assertIn("- ⚠️ 強勢量縮上漲（0.7x Avg Volume）", message)


class EventRadarTest(unittest.TestCase):
    def test_earnings_events_use_finnhub_hour_when_available(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "earningsCalendar": [
                {
                    "symbol": "AAPL",
                    "date": "2026-07-24",
                    "hour": "amc",
                }
            ]
        }

        with (
            patch.dict("main.os.environ", {"FINNHUB_API_KEY": "test-key"}),
            patch("main.get_today_taipei", return_value=date(2026, 7, 22)),
            patch("main.requests.get", return_value=response) as mocked_get,
        ):
            events = get_earnings_events(
                [{"ticker": "AAPL", "display_name": "AAPL"}]
            )

        self.assertEqual(
            events,
            [
                {
                    "id": "AAPL_earnings_2026-07-24",
                    "type": "earnings",
                    "name": "AAPL 財報",
                    "ticker": "AAPL",
                    "date": "2026-07-24",
                    "session": "after_market",
                }
            ],
        )
        self.assertEqual(
            mocked_get.call_args.kwargs["params"]["symbol"],
            "AAPL",
        )
        self.assertEqual(
            mocked_get.call_args.kwargs["params"]["token"],
            "test-key",
        )

    def test_tw_earnings_events_request_finnhub_international_calendar(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"earningsCalendar": []}

        with (
            patch.dict("main.os.environ", {"FINNHUB_API_KEY": "test-key"}),
            patch("main.get_today_taipei", return_value=date(2026, 7, 22)),
            patch("main.requests.get", return_value=response) as mocked_get,
            patch("main.yf.Ticker"),
        ):
            get_earnings_events(
                [{"ticker": "2330.TW", "display_name": "台積電"}],
                "tw",
            )

        self.assertEqual(
            mocked_get.call_args.kwargs["params"]["international"],
            "true",
        )

    def test_earnings_events_fall_back_to_yfinance_without_finnhub_key(self):
        ticker = Mock()
        ticker.calendar = pd.DataFrame(
            [datetime(2026, 7, 24, 8, 0)],
            index=["Earnings Date"],
        )

        with (
            patch.dict("main.os.environ", {}, clear=True),
            patch("main.get_today_taipei", return_value=date(2026, 7, 22)),
            patch("main.yf.Ticker", return_value=ticker),
        ):
            events = get_earnings_events(
                [{"ticker": "AAPL", "display_name": "AAPL"}]
            )

        self.assertEqual(events[0]["session"], "before_market")

    def test_parse_bls_release_events_reads_official_schedule_rows(self):
        html = """
        <table>
            <tr>
                <td>June 2026</td>
                <td>Jul. 14, 2026</td>
                <td>08:30 AM</td>
            </tr>
            <tr>
                <td>July 2026</td>
                <td>Aug. 12, 2026</td>
                <td>08:30 AM</td>
            </tr>
        </table>
        """
        schedule = {
            "id_prefix": "CPI",
            "name": "CPI",
            "url": "https://www.bls.gov/schedule/news_release/cpi.htm",
        }

        events = parse_bls_release_events(html, schedule)

        self.assertEqual(
            events,
            [
                {
                    "id": "CPI_2026-07-14",
                    "type": "macro",
                    "name": "CPI",
                    "reference_month": "June 2026",
                    "date": "2026-07-14",
                    "session": "before_market",
                },
                {
                    "id": "CPI_2026-08-12",
                    "type": "macro",
                    "name": "CPI",
                    "reference_month": "July 2026",
                    "date": "2026-08-12",
                    "session": "before_market",
                },
            ],
        )

    def test_macro_events_include_bls_fallback_when_fetch_fails(self):
        with patch("main.requests.get", side_effect=main.requests.RequestException):
            events = get_macro_events()

        event_ids = {event["id"] for event in events}
        self.assertIn("CPI_2026-07-14", event_ids)
        self.assertIn("PPI_2026-07-15", event_ids)
        self.assertIn("NFP_2026-08-07", event_ids)

    def test_upcoming_events_excludes_past_events(self):
        events = [
            {"id": "past", "name": "CPI", "date": "2026-06-12"},
            {"id": "future", "name": "MU 財報", "date": "2026-06-24"},
        ]

        upcoming = get_upcoming_events(events, date(2026, 6, 23))

        self.assertEqual([event["id"] for event in upcoming], ["future"])

    def test_event_radar_keeps_today_and_future_events(self):
        events = [
            {
                "id": "today",
                "name": "今日事件",
                "date": "2026-06-23",
                "session": "unknown",
            },
            {
                "id": "future",
                "name": "未來事件",
                "date": "2026-06-24",
                "session": "unknown",
            },
            {
                "id": "past",
                "name": "過期事件",
                "date": "2026-06-22",
                "session": "unknown",
            },
        ]

        with (
            patch("main.get_today_taipei", return_value=date(2026, 6, 23)),
            patch("main.get_earnings_events", return_value=[]),
            patch("main.get_macro_events", return_value=events),
            patch("main.load_event_state", return_value={"notified_event_ids": []}),
            patch("main.save_event_state"),
        ):
            message = build_event_radar([])

        self.assertIn("今日事件：6/23", message)
        self.assertIn("未來事件：6/24", message)
        self.assertNotIn("過期事件", message)

    def test_event_radar_fills_missing_year_with_current_taipei_year(self):
        events = [
            {
                "id": "future_yearless",
                "name": "未來無年份事件",
                "date": "6/24",
                "session": "unknown",
            },
            {
                "id": "past_yearless",
                "name": "過期無年份事件",
                "date": "6/12",
                "session": "unknown",
            },
        ]

        with (
            patch("main.get_today_taipei", return_value=date(2026, 6, 23)),
            patch("main.get_earnings_events", return_value=[]),
            patch("main.get_macro_events", return_value=events),
            patch("main.load_event_state", return_value={"notified_event_ids": []}),
            patch("main.save_event_state"),
        ):
            message = build_event_radar([])

        self.assertIn("未來無年份事件：6/24", message)
        self.assertNotIn("過期無年份事件", message)

    def test_tw_event_radar_excludes_us_macro_events(self):
        us_macro_events = [
            {
                "id": "CPI_2026-07-10",
                "name": "CPI",
                "date": "2026-07-10",
                "session": "before_market",
            }
        ]
        tw_events = [
            {
                "id": "TW_monthly_revenue_2026-07",
                "name": "台股月營收公告截止",
                "date": "2026-07-10",
                "session": "unknown",
            }
        ]

        with (
            patch("main.get_today_taipei", return_value=date(2026, 6, 24)),
            patch("main.get_earnings_events", return_value=[]),
            patch("main.get_macro_events", return_value=us_macro_events),
            patch("main.get_tw_market_events", return_value=tw_events),
            patch("main.load_event_state", return_value={"notified_event_ids": []}),
            patch("main.save_event_state"),
        ):
            message = build_event_radar([], "tw")

        self.assertIn("台股月營收公告截止：7/10", message)
        self.assertNotIn("CPI", message)

    def test_today_uses_asia_taipei_timezone(self):
        fixed_now = datetime(2026, 6, 23, 1, 0, tzinfo=TAIPEI_TIMEZONE)
        with patch("main.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = fixed_now
            today = main.get_today_taipei()

        mocked_datetime.now.assert_called_once_with(TAIPEI_TIMEZONE)
        self.assertEqual(today, date(2026, 6, 23))


class WatchlistMarketTest(unittest.TestCase):
    def test_us_watchlist_reads_us_sheet_and_uses_ticker_display(self):
        df = pd.DataFrame(
            {
                "ticker": ["ARM", "TSLA"],
                "name": ["Arm", "Tesla"],
                "active": [True, False],
            }
        )

        with patch("main.pd.read_csv", return_value=df) as mocked_read_csv:
            watchlist = get_watchlist_from_sheets("us")

        self.assertIn("sheet=US%20Watchlist", mocked_read_csv.call_args.args[0])
        self.assertEqual(
            watchlist,
            [{"ticker": "ARM", "name": "Arm", "display_name": "ARM"}],
        )

    def test_tw_watchlist_reads_tw_sheet_and_uses_name_display(self):
        df = pd.DataFrame(
            {
                "ticker": ["2330.TW", "2317.TW"],
                "name": ["台積電", ""],
                "active": [True, True],
            }
        )

        with patch("main.pd.read_csv", return_value=df) as mocked_read_csv:
            watchlist = get_watchlist_from_sheets("tw")

        self.assertIn("sheet=TW%20Watchlist", mocked_read_csv.call_args.args[0])
        self.assertEqual(
            watchlist,
            [
                {"ticker": "2330.TW", "name": "台積電", "display_name": "台積電"},
                {"ticker": "2317.TW", "name": "", "display_name": "2317.TW"},
            ],
        )

    def test_daily_watchlist_uses_display_name(self):
        message = build_daily_watchlist(
            [
                {
                    "ticker": "2330.TW",
                    "display_name": "台積電",
                    "close": 1000.0,
                    "change_pct": 1.23,
                    "price_date": date(2026, 9, 3),
                    "error": None,
                }
            ],
            "📈 台股 Watchlist",
        )

        self.assertIn("📈 台股 Watchlist（9/3）", message)
        self.assertIn("台積電: 1000.00 (+1.23%)", message)
        self.assertNotIn("2330.TW", message)

    def test_build_tw_message_uses_name_and_skips_ticker_display(self):
        watchlist = [
            {"ticker": "2330.TW", "name": "台積電", "display_name": "台積電"}
        ]
        result = {
            "ticker": "2330.TW",
            "display_name": "台積電",
            "close": 1000.0,
            "change_pct": 1.23,
            "high_alerts": ["📈 放量上漲（2.1x Avg Volume）"],
            "medium_alerts": [],
            "error": None,
        }

        with (
            patch("main.get_watchlist_from_sheets", return_value=watchlist),
            patch("main.analyze_ticker", return_value=result),
            patch(
                "main.build_event_radar",
                return_value="📅 Event Radar\n今日無新的或當週重要事件。",
            ) as mocked_event_radar,
            patch("main.build_tw_institutional_sections", return_value=[]),
        ):
            message = build_message("tw")

        self.assertIn("台積電: 1000.00 (+1.23%)", message)
        self.assertIn("台積電\n- 📈 放量上漲（2.1x Avg Volume）", message)
        self.assertNotIn("2330.TW", message)
        mocked_event_radar.assert_called_once_with(watchlist, "tw")


class WeeklyMarketRecapTest(unittest.TestCase):
    def weekly_summary(self):
        return {
            "week_range": "2026-06-15 to 2026-06-19",
            "indices": {
                "Dow Jones": "+0.2%",
                "S&P500": "+0.4%",
                "NASDAQ": "-1.1%",
                "PHLX": "+3.2%",
            },
            "sector_etfs": {
                "XLE": "+4.2%",
                "SMH": "+3.8%",
                "IGV": "-2.1%",
            },
            "watchlist_top_movers": {
                "gainers": ["BE +18.0%", "MU +12.0%"],
                "losers": ["PLTR -8.0%", "ARM -7.0%"],
                "big_moves": ["BE +18.0%", "MU +12.0%", "PLTR -8.0%"],
            },
        }

    def test_weekly_recap_runs_on_monday(self):
        self.assertTrue(should_run_weekly_recap(date(2026, 6, 22)))
        self.assertFalse(should_run_weekly_recap(date(2026, 6, 23)))

    def test_weekly_recap_range_uses_friday_close_to_friday_close(self):
        self.assertEqual(
            get_previous_us_week_range(date(2026, 6, 29)),
            (date(2026, 6, 19), date(2026, 6, 26)),
        )

    def test_period_return_is_calculated(self):
        price_data = pd.DataFrame(
            {"close": [100.0, 110.0]},
            index=pd.to_datetime(["2026-06-15", "2026-06-19"]),
        )

        result = calculate_period_return(
            price_data,
            date(2026, 6, 15),
            date(2026, 6, 19),
        )

        self.assertEqual(result, 10.0)

    def test_ai_prompt_contains_market_data(self):
        _, user_prompt = build_weekly_recap_prompt(self.weekly_summary())

        self.assertIn('"indices"', user_prompt)
        self.assertIn('"sector_etfs"', user_prompt)
        self.assertIn('"watchlist_top_movers"', user_prompt)
        self.assertNotIn('"news_headlines"', user_prompt)
        self.assertIn("Dow Jones", user_prompt)
        self.assertIn("S&P500", user_prompt)
        self.assertIn("PHLX", user_prompt)
        self.assertIn("SMH", user_prompt)
        self.assertIn("BE +18.0%", user_prompt)

    def test_ai_failure_returns_fallback_message(self):
        with (
            patch("main.collect_weekly_market_data", return_value=self.weekly_summary()),
            patch("main.call_openai_weekly_recap", side_effect=RuntimeError("AI down")),
        ):
            message = build_weekly_market_recap_message(date(2026, 6, 22))

        self.assertEqual(
            message,
            "📊 Weekly Market Recap\n\nWeekly Market Recap 暫時無法產生。",
        )

    def test_weekly_recap_output_is_line_readable(self):
        ai_output = """📊 Weekly Market Recap

本週主線：

1. 聯準會維持觀望
2. 半導體股反彈
3. 能源股走強

市場反應：

✓ 能源股強勢
✓ 半導體股強勢
✗ 軟體股偏弱

類股輪動：

↑ Energy
↑ Semiconductor
↑ Defense

↓ Software
↓ Mag7

指數表現：

Dow Jones +0.2%
S&P500 +0.4%
NASDAQ -1.1%
PHLX +3.2%"""

        with (
            patch("main.collect_weekly_market_data", return_value=self.weekly_summary()),
            patch("main.call_openai_weekly_recap", return_value=ai_output),
        ):
            message = build_weekly_market_recap_message(date(2026, 6, 22))

        self.assertTrue(message.startswith("📊 Weekly Market Recap"))
        self.assertIn("本週主線：", message)
        self.assertIn("市場反應：", message)
        self.assertIn("類股輪動：", message)
        self.assertIn("指數表現：", message)
        self.assertLess(len(message), 1000)


if __name__ == "__main__":
    unittest.main()
