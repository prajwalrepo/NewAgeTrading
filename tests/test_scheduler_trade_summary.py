from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from openpyxl import Workbook

import OILMINI
import scheduler


class SchedulerTradeSummaryTests(unittest.TestCase):
    def test_closed_position_does_not_block_a_new_entry(self):
        symbol = "CRUDEOILM-17Nov26-8000-CE"
        original_positions = OILMINI.positions
        try:
            OILMINI.positions = {
                symbol: {"status": "CLOSED"},
                "CRUDEOILM-17Nov26-8000-PE": {"status": "OPEN"},
            }

            self.assertIsNone(OILMINI.get_open_position(symbol))
            self.assertEqual(
                OILMINI.get_open_position("CRUDEOILM-17Nov26-8000-PE")["status"],
                "OPEN",
            )
        finally:
            OILMINI.positions = original_positions

    def test_oil_monitor_emits_ten_point_milestones_and_exits_at_thirty(self):
        symbol = "CRUDEOILM-17Nov26-8000-PE"
        position = {
            "status": "OPEN",
            "entry_price": 60.0,
            "highest_price": 60.0,
            "qty": 10,
            "order_symbol": "CRUDEOILM17OCT8000PE",
            "side": "PE",
        }
        with (
            patch.object(OILMINI, "positions", {symbol: position}),
            patch.object(OILMINI, "debug_signal_positions", {}),
            patch.dict(
                OILMINI.CONFIG,
                {
                    "price_milestone_emails_enabled": True,
                    "price_milestone_points": 10.0,
                    "max_profit_booking_enabled": True,
                    "max_profit_booking_points": 30.0,
                },
            ),
            patch.object(OILMINI, "fetch_latest_price_1m", return_value=90.0),
            patch.object(OILMINI, "place_sell_order") as sell,
        ):
            output = StringIO()
            with redirect_stdout(output):
                OILMINI.check_price_milestones()

        self.assertIn("Move=+10.00", output.getvalue())
        self.assertIn("Move=+20.00", output.getvalue())
        self.assertIn("Move=+30.00", output.getvalue())
        sell.assert_called_once_with(
            symbol,
            position["order_symbol"],
            90.0,
            reason="MAX_PROFIT: +30 pts",
            qty=10,
        )

    def test_paper_trade_buy_and_sell_update_position_without_broker(self):
        symbol = "CRUDEOILM-17Nov26-8000-CE"
        order_symbol = "CRUDEOILM17OCT8000CE"
        with (
            patch.object(OILMINI, "positions", {}),
            patch.dict(OILMINI.candle_to_side, {symbol: "CE"}),
            patch.dict(OILMINI.side_cooldown_tracker, {"CE": 0, "PE": 0}),
            patch.dict(OILMINI.exit_attempt_tracker, {}),
            patch.dict(OILMINI.pullback_reentry_tracker, {}),
            patch.object(OILMINI, "trades_today_ce", 0),
            patch.object(OILMINI, "trades_today_pe", 0),
            patch.dict(
                OILMINI.CONFIG,
                {
                    "enable_actual_trade": False,
                    "lot_size": 10,
                    "max_lots_per_trade": 1,
                    "allocation_per_trade": 1000,
                    "pullback_reentry_enabled": False,
                },
            ),
            patch.object(OILMINI, "place_order_with_retry") as broker_order,
        ):
            OILMINI.place_buy_order(symbol, order_symbol, 50.0, 50.0, strike=8000)
            self.assertEqual(OILMINI.positions[symbol]["status"], "OPEN")
            OILMINI.place_sell_order(symbol, order_symbol, 80.0, reason="MAX_PROFIT: +30 pts")

            self.assertEqual(OILMINI.positions[symbol]["status"], "CLOSED")
            self.assertEqual(OILMINI.positions[symbol]["qty"], 0)
            broker_order.assert_not_called()

    def test_summary_shows_option_side_action_and_sell_peak(self):
        rows = [
            {
                "Script": "OILMINI",
                "Side": "BUY",
                "Symbol": "CRUDEOILM-17Nov26-8000-CE",
                "Time": "2026-11-17T15:57:02",
                "Strike": 8000,
                "Price": 60.35,
                "Reason": "Supertrend flip entry",
                "Order Placed": "NO",
                "Highest Price Observed": "",
            },
            {
                "Script": "OILMINI",
                "Side": "SELL",
                "Symbol": "CRUDEOILM-17Nov26-8000-CE",
                "Time": "2026-11-17T16:09:04",
                "Strike": 8000,
                "Price": 90.35,
                "Reason": "MAX_PROFIT: +30 pts",
                "Order Placed": "NO",
                "Highest Price Observed": 92.4,
            },
        ]

        text_rows = scheduler._trade_rows_text(rows).splitlines()

        self.assertIn("Side (CE/PE)", text_rows[0])
        self.assertIn("Action", text_rows[0])
        self.assertIn("CE | BUY", text_rows[1])
        self.assertIn("CE | SELL", text_rows[2])
        self.assertTrue(text_rows[1].endswith("NO | "))
        self.assertTrue(text_rows[2].endswith("NO | 92.4"))

    def test_sell_highest_price_is_parsed(self):
        signal = (
            "[TRADE SIGNAL] Script=OILMINI Side=SELL "
            "Time=2026-11-17T16:09:04 Symbol=CRUDEOILM-17Nov26-8000-CE "
            "Strike=8000 Price=90.35 Reason=MAX_PROFIT: +30 pts "
            "OrderPlaced=NO HighestPrice=92.40"
        )

        trade = scheduler.parse_trade_signal(signal)

        self.assertEqual(trade["Highest Price Observed"], "92.40")
        self.assertEqual(scheduler._trade_report_values(trade)[-1], 92.4)

    def test_ten_point_milestone_requests_an_email(self):
        line = (
            "[PRICE MILESTONE] Script=OILMINI Time=2026-11-17T16:00:00 "
            "Symbol=CRUDEOILM-17Nov26-8000-CE Side=CE Entry=60.00 Price=70.00 "
            "Move=+10.00 Direction=UP"
        )
        with (
            patch.dict(scheduler.CONFIG["email_notifications"], {"send_on_signals": True}),
            patch.object(scheduler, "send_email_notification") as send_email,
        ):
            scheduler.process_price_milestone(
                "OILMINI", line, script_config={"send_signal_emails": True}
            )

        send_email.assert_called_once()

    def test_existing_trade_report_gains_peak_column_before_event_key(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append((*scheduler.LEGACY_TRADE_REPORT_COLUMNS, "Event Key"))

        scheduler._prepare_trade_report_sheet(workbook, sheet, "legacy.xlsx")

        headers = tuple(next(sheet.iter_rows(min_row=1, max_row=1, values_only=True)))
        self.assertEqual(headers[:len(scheduler.TRADE_REPORT_COLUMNS)], scheduler.TRADE_REPORT_COLUMNS)
        self.assertEqual(headers[len(scheduler.TRADE_REPORT_COLUMNS)], "Event Key")
        self.assertTrue(
            sheet.column_dimensions[
                sheet.cell(row=1, column=len(scheduler.TRADE_REPORT_COLUMNS) + 1).column_letter
            ].hidden
        )
        workbook.close()


if __name__ == "__main__":
    unittest.main()
