import unittest
from datetime import date, datetime
from unittest.mock import patch

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
    get_watchlist_from_sheets,
    get_upcoming_events,
    get_volume_price_signal,
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
        return pd.DataFrame({"close": close, "volume": volume})

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
                    "error": None,
                }
            ],
            "📈 台股 Watchlist",
        )

        self.assertIn("📈 台股 Watchlist", message)
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
            patch("main.build_event_radar", return_value="📅 Event Radar\n今日無新的或當週重要事件。"),
        ):
            message = build_message("tw")

        self.assertIn("台積電: 1000.00 (+1.23%)", message)
        self.assertIn("台積電\n- 📈 放量上漲（2.1x Avg Volume）", message)
        self.assertNotIn("2330.TW", message)


class WeeklyMarketRecapTest(unittest.TestCase):
    def weekly_summary(self):
        return {
            "week_range": "2026-06-15 to 2026-06-19",
            "indices": {
                "SOX": "+3.2%",
                "NASDAQ": "-1.1%",
                "SP500": "+0.4%",
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
            "news_headlines": [
                {
                    "title": "Fed keeps rates unchanged",
                    "source": "Reuters",
                    "published_at": "2026-06-18",
                    "url": "https://example.com/fed",
                }
            ],
        }

    def test_weekly_recap_runs_on_monday(self):
        self.assertTrue(should_run_weekly_recap(date(2026, 6, 22)))
        self.assertFalse(should_run_weekly_recap(date(2026, 6, 23)))

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
        self.assertIn('"news_headlines"', user_prompt)
        self.assertIn("Fed keeps rates unchanged", user_prompt)
        self.assertIn("SOX", user_prompt)
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

SOX +3.2%
NASDAQ -1.1%
SP500 +0.4%"""

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
