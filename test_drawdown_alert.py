import unittest
from unittest.mock import patch

import pandas as pd

from main import analyze_ticker, build_daily_watchlist


class DrawdownAlertTest(unittest.TestCase):
    def analyze(self, prices):
        data = pd.DataFrame(
            {"close": prices, "volume": [1000] * len(prices)},
            index=pd.date_range("2026-01-01", periods=len(prices)),
        )
        with patch("main.get_price_data", return_value=data):
            result = analyze_ticker("TEST", "測試股票")
        self.assertIsNone(result["error"])
        return result

    def test_alert_threshold_is_strictly_above_fifteen_percent(self):
        for latest, expected in [(86, False), (85, False), (84.99, True), (80, True)]:
            with self.subTest(latest=latest):
                result = self.analyze([100, 90, latest])
                message = build_daily_watchlist([result])
                self.assertEqual("已回落" in message, expected)

    def test_alert_contains_peak_and_drawdown_even_with_many_other_alerts(self):
        result = self.analyze([100, 90, 80])
        result["high_alerts"] = ["其他提醒"] * 10
        message = build_daily_watchlist([result])
        self.assertIn("測試股票", message)
        self.assertIn("最高收盤價 100.00 已回落 20.00%", message)

    def test_repeats_while_below_threshold_and_stops_after_recovery(self):
        for prices, expected in [([100, 80], True), ([100, 80, 82], True),
                                 ([100, 80, 86], False), ([100, 110], False)]:
            with self.subTest(prices=prices):
                self.assertEqual(
                    "已回落" in build_daily_watchlist([self.analyze(prices)]),
                    expected,
                )

    def test_error_does_not_show_drawdown(self):
        result = self.analyze([100, 80])
        result["error"] = "價格資料日期不符"
        self.assertNotIn("已回落", build_daily_watchlist([result]))


if __name__ == "__main__":
    unittest.main()
