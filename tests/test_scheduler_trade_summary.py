from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
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

        text_rows = scheduler._trade_rows_text(
            rows, columns=scheduler.DAILY_SUMMARY_COLUMNS
        ).splitlines()
        legacy_rows = scheduler._trade_rows_text(rows).splitlines()

        self.assertIn("Side (CE/PE)", text_rows[0])
        self.assertIn("Action", text_rows[0])
        self.assertIn("CE | BUY", text_rows[1])
        self.assertIn("CE | SELL", text_rows[2])
        self.assertTrue(text_rows[1].endswith("NO | "))
        self.assertTrue(text_rows[2].endswith("NO | 92.4"))
        self.assertIn("Script", legacy_rows[0])
        self.assertIn("2026-11-17T15:57:02", legacy_rows[1])

    def test_daily_summary_uses_compact_table_and_shows_today_pnl(self):
        signals = [
            (
                "[TRADE SIGNAL] Script=SENSEXINTRA Side=BUY "
                "Time=2026-11-18T09:59:03 Symbol=SENSEX-19Nov26-82300-CE "
                "Strike=82300 Price=361.00 Qty=80 Reason=Supertrend flip entry OrderPlaced=NO"
            ),
            (
                "[TRADE SIGNAL] Script=SENSEXINTRA Side=SELL "
                "Time=2026-11-18T10:30:09 Symbol=SENSEX-19Nov26-82300-CE "
                "Strike=82300 Price=500.00 Qty=80 Reason=TSL OrderPlaced=NO HighestPrice=520"
            ),
        ]

        with TemporaryDirectory() as temp_dir:
            with (
                patch.object(scheduler, "RUN_LOG_DIR", Path(temp_dir)),
                patch.dict(
                    scheduler.CONFIG["email_notifications"],
                    {
                        "enabled": True,
                        "send_daily_summary_on_stop": True,
                        "daily_summary_recipients": ["test@example.com"],
                    },
                ),
                patch.dict(
                    scheduler.SCRIPTS_SCHEDULE,
                    {"SENSEXINTRA": {"send_daily_summary_on_stop": True}},
                ),
                patch.object(scheduler, "send_email_notification") as send_email,
            ):
                for signal in signals:
                    scheduler.append_trade_to_report(
                        "SENSEXINTRA", scheduler.parse_trade_signal(signal)
                    )

                scheduler.email_daily_trade_summary(
                    {
                        "config_key": "SENSEXINTRA",
                        "started_at": datetime(2026, 11, 18, 9, 30),
                    },
                    "Strategy completed normally",
                )

        body = send_email.call_args.args[1]
        self.assertIn("Today PNL: 11120.00", body)
        self.assertIn(
            "Side (CE/PE) | Action | Time | Strike | Price | Reason | Qty | "
            "Order Placed | Highest Price Observed",
            body,
        )
        self.assertIn("CE | BUY | 09:59:03 | 82300", body)
        self.assertIn("CE | SELL | 10:30:09 | 82300 | 500", body)
        self.assertNotIn("Price milestones", body)
        self.assertIn("Today PNL: 11120.00", send_email.call_args.kwargs["html_body"])

    def test_320trade_keeps_its_previous_daily_summary_format(self):
        script_names = ("320TRADE", "NIFTYSUPPERBUY", "SENSEXSUPPERBUY")

        with TemporaryDirectory() as temp_dir:
            with (
                patch.object(scheduler, "RUN_LOG_DIR", Path(temp_dir)),
                patch.dict(
                    scheduler.CONFIG["email_notifications"],
                    {
                        "enabled": True,
                        "send_daily_summary_on_stop": True,
                        "daily_summary_recipients": ["test@example.com"],
                    },
                ),
                patch.dict(
                    scheduler.SCRIPTS_SCHEDULE,
                    {
                        name: {"send_daily_summary_on_stop": True}
                        for name in script_names
                    },
                ),
                patch.object(scheduler, "send_email_notification") as send_email,
            ):
                for name in script_names:
                    scheduler.email_daily_trade_summary(
                        {
                            "config_key": name,
                            "started_at": datetime(2026, 11, 18, 9, 30),
                        },
                        "Strategy completed normally",
                    )

        self.assertEqual(send_email.call_count, len(script_names))
        for call, name in zip(send_email.call_args_list, script_names):
            body = call.args[1]
            self.assertIn(f"{name} completed", body)
            self.assertIn("No completed live or paper CE/PE trade outcomes were recorded.", body)
            self.assertNotIn("Today PNL:", body)

    def test_daily_and_weekly_scan_emails_attach_formatted_excel_reports(self):
        scan_date = "2026-11-19"
        results = [
            {
                "symbol": "ABC",
                "side": "BUY",
                "date": "2026-11-18",
                "price": 100.5,
                "supertrend": 98.0,
            },
            {
                "symbol": "XYZ",
                "side": "SELL",
                "date": "2026-11-18",
                "price": 90.0,
                "supertrend": 95.0,
            },
        ]

        with TemporaryDirectory() as temp_dir:
            with (
                patch.object(scheduler, "RUN_LOG_DIR", Path(temp_dir)),
                patch.dict(
                    scheduler.CONFIG["email_notifications"],
                    {"enabled": True, "send_on_signals": True},
                ),
                patch.object(scheduler, "send_email_notification") as send_email,
            ):
                for period, script_name in (
                    ("daily", "SupperDayScan"),
                    ("weekly", "SupperWeekScan"),
                ):
                    output = (
                        f"[SCAN RESULTS] Script={script_name} Period={period} "
                        f"ScanDate={scan_date} Results="
                        f"{json.dumps(results, separators=(',', ':'))}"
                    )
                    scheduler.process_scan_results(
                        script_name, output, script_config={"send_signal_emails": True}
                    )

            self.assertEqual(send_email.call_count, 2)
            for call, period in zip(send_email.call_args_list, ("daily", "weekly")):
                body = call.args[1]
                self.assertIn("BUY signals (1)", body)
                self.assertIn("ABC", body)
                self.assertNotIn("XYZ", body)
                self.assertIn("ABC", call.kwargs["html_body"])
                self.assertNotIn("XYZ", call.kwargs["html_body"])
                attachment_path = call.kwargs["attachment_path"]
                self.assertTrue(attachment_path.exists())
                self.assertEqual(attachment_path.name, f"{period}_scan_20261119.xlsx")
                workbook = scheduler.load_workbook(
                    attachment_path, read_only=True, data_only=True
                )
                try:
                    sheet = workbook["Scan Results"]
                    self.assertEqual(
                        tuple(cell.value for cell in sheet[1]),
                        ("Stock Name", "Price", "ST Value", "Signal", "Generated Date"),
                    )
                    self.assertEqual(
                        tuple(cell.value for cell in sheet[2][:4]),
                        ("ABC", 100.5, 98.0, "BUY"),
                    )
                    self.assertEqual(
                        tuple(cell.value for cell in sheet[3][:4]),
                        ("XYZ", 90.0, 95.0, "SELL"),
                    )
                    self.assertEqual(
                        sheet.cell(row=2, column=5).value.strftime("%Y-%m-%d"),
                        scan_date,
                    )
                    self.assertEqual(
                        sheet.cell(row=2, column=5).number_format,
                        "yyyy-mm-dd",
                    )
                finally:
                    workbook.close()

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
            "Symbol=CRUDEOILM-17Nov26-8000-CE Side=CE Strike=8000 "
            "Entry=60.00 Price=70.00 Qty=10 OrderPlaced=NO "
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
        subject, body = send_email.call_args.args[:2]
        self.assertIn("[PRICE MOVE] OILMINI", subject)
        self.assertIn("Side: CE", body)
        self.assertIn("Strike: 8000", body)
        self.assertIn("Quantity: 10", body)
        self.assertIn("Real order: No", body)
        html_body = send_email.call_args.kwargs["html_body"]
        self.assertIn("<strong>+10.00 points</strong>", html_body)
        self.assertIn("<td>70.00</td>", html_body)
        self.assertNotIn("<strong>70.00</strong>", html_body)

    def test_buy_email_has_standard_fields_and_bolds_only_signal(self):
        trade = scheduler.parse_trade_signal(
            "[TRADE SIGNAL] Script=NIFTYIntra Side=BUY "
            "Time=2026-11-19T09:40:00 Symbol=NIFTY-19Nov26-22500-CE "
            "Strike=22500 Price=7.00 Qty=50 OrderPlaced=NO Reason=entry"
        )
        with patch.object(scheduler, "send_email_notification") as send_email:
            scheduler.email_trade_signal("NIFTYINTRA", trade)

        subject, body = send_email.call_args.args[:2]
        self.assertEqual(subject, "[PAPER TRADE] NIFTYIntra BUY CE 22500 at 09:40")
        self.assertIn("Buy price: 7.00", body)
        self.assertIn("Quantity: 50", body)
        self.assertIn("Real order: No", body)
        html_body = send_email.call_args.kwargs["html_body"]
        self.assertIn("<strong>BUY</strong>", html_body)
        self.assertNotIn("<strong>7.00</strong>", html_body)

    def test_sell_email_uses_sell_price_and_includes_sell_details(self):
        trade = scheduler.parse_trade_signal(
            "[TRADE SIGNAL] Script=NIFTYIntra Side=SELL "
            "Time=2026-11-19T10:05:00 Symbol=NIFTY-19Nov26-22500-CE "
            "Strike=22500 Price=10.00 BuyAt=7.00 SellAt=10.00 "
            "Qty=50 PnL=150 HighestPrice=12.00 OrderPlaced=YES Reason=exit"
        )
        with patch.object(scheduler, "send_email_notification") as send_email:
            scheduler.email_trade_signal("NIFTYINTRA", trade)

        subject, body = send_email.call_args.args[:2]
        self.assertEqual(subject, "[LIVE TRADE] NIFTYIntra SELL CE 22500 at 10:05")
        self.assertIn("Entry: 7.00", body)
        self.assertIn("Sell price: 10.00", body)
        self.assertNotIn("Buy price:", body)
        self.assertIn("MA price recorded: 12.00", body)
        self.assertIn("PNL: 150", body)
        self.assertIn("Quantity: 50", body)

    def test_paper_order_confirmation_email_includes_quantity_and_price(self):
        line = (
            "[2026-11-19 09:40:00] [PAPER BUY TRACKED] "
            "CRUDEOILM-19Nov26-8000-CE Qty=10 Price=7.00"
        )
        with (
            patch.dict(scheduler.CONFIG["email_notifications"], {"send_on_signals": True}),
            patch.object(scheduler, "send_email_notification") as send_email,
        ):
            scheduler.process_order_confirmation(
                "OILMINI", line, script_config={"send_signal_emails": True}
            )

        subject, body = send_email.call_args.args[:2]
        self.assertEqual(subject, "[PAPER TRADE] OILMINI BUY CE 8000 at 09:40")
        self.assertIn("Buy price: 7.00", body)
        self.assertIn("Quantity: 10", body)
        self.assertIn("Real order: No", body)

    def test_confirmed_oil_sell_email_uses_actual_result_fields(self):
        line = (
            "[TRADE RESULT] Script=OILMINI Side=SELL Time=2026-11-19T10:05:00 "
            "Symbol=CRUDEOILM-19Nov26-8000-CE Strike=8000 BuyAt=7.00 "
            "SellAt=10.00 Qty=10 PnL=30 OrderPlaced=NO HighestPrice=12.00"
        )
        with (
            patch.dict(scheduler.CONFIG["email_notifications"], {"send_on_signals": True}),
            patch.object(scheduler, "send_email_notification") as send_email,
        ):
            scheduler.process_trade_result(
                "OILMINI", line, script_config={"send_signal_emails": True}
            )

        subject, body = send_email.call_args.args[:2]
        self.assertEqual(subject, "[PAPER TRADE] OILMINI SELL CE 8000 at 10:05")
        self.assertIn("Sell price: 10.00", body)
        self.assertIn("Quantity: 10", body)
        self.assertIn("MA price recorded: 12.00", body)
        self.assertIn("PNL: 30", body)

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

    def test_daily_and_monthly_reports_append_multiple_signal_events(self):
        signals = [
            (
                "[TRADE SIGNAL] Script=SENSEXINTRA Side=BUY "
                "Time=2026-11-18T09:59:03 Symbol=SENSEX-19Nov26-82300-CE "
                "Strike=82300 Price=361.00 Qty=80 Reason=Supertrend flip entry OrderPlaced=NO"
            ),
            (
                "[TRADE SIGNAL] Script=SENSEXINTRA Side=SELL "
                "Time=2026-11-18T10:00:03 Symbol=SENSEX-19Nov26-82300-CE "
                "Strike=82300 Price=325.00 Qty=80 BuyAt=361.00 SellAt=325.00 "
                "PnL=-2880.00 Reason=LSL OrderPlaced=NO"
            ),
        ]

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with (
                patch.object(scheduler, "RUN_LOG_DIR", temp_path),
                patch.object(scheduler, "MONTHLY_REPORT_DIR", temp_path / "monthly"),
            ):
                for signal in signals:
                    trade = scheduler.parse_trade_signal(signal)
                    scheduler.append_trade_to_report("SENSEXINTRA", trade)
                    scheduler.append_trade_to_monthly_report("SENSEXINTRA", trade)

                daily_path = temp_path / "trades_SENSEXINTRA_20261118.xlsx"
                monthly_path = temp_path / "monthly" / "NIFTY_NOV.xlsx"
                self.assertEqual(scheduler._trade_report_summary(daily_path), (2, 1, 1, 0))
                self.assertEqual(len(scheduler._trade_report_rows(daily_path)), 2)
                self.assertEqual(
                    len(scheduler._trade_report_rows(monthly_path, sheet_name="SENSEX")),
                    2,
                )


if __name__ == "__main__":
    unittest.main()
