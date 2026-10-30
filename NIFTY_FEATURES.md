# NIFTYIntra strategy guide

This guide describes the NIFTY intraday options strategy and its current
configuration. SENSEXINTRA uses the same strategy features and Super Run
sequence rules, but its contract universe, lot size, and risk values are
configured separately in `SENSEXINTRA.py`.

## Safety and execution mode

- `enable_actual_trade` is `False` by default. Signals that pass entry checks
  are tracked as paper positions; no broker buy/sell order is sent for them.
- `super_run_enabled` is `True`. Super Run follows the same paper/live setting
  as other entries. With the current default execution mode, it is paper-only.
- To switch to live execution, change `enable_actual_trade` only after reviewing
  the order, sizing, margin, and risk settings. This guide is not a
  recommendation to trade.
- API credentials are read from `GROWW_API_KEY` and `GROWW_API_SECRET`
  environment variables.

## Market and contracts

- Underlying: NIFTY, NSE F&O options.
- Candle interval: `1M` (one-minute candles).
- Supertrend: ATR period `10`, factor `3.0`.
- The script chooses the nearest configured expiry and selects the nearest
  available ATM strike in the configured range, using a 50-point strike step.
- The scheduler starts NIFTYIntra at 09:13 IST. Strategy entries are allowed
  from 09:13 until before 15:10 IST; open positions are managed after the
  entry window and squared off at 15:15 IST.

## Standard entry rules

The script evaluates CE and PE option candles and acts on closed-candle signals.
An ordinary entry requires a confirmed Supertrend flip from down to up within
the configured two-candle confirmation window. Additional default checks are:

- Momentum filter enabled; the signal close must be above the previous close.
- Signal candle body no larger than 30 points.
- Entry candle open no more than 10 points away from the previous close.
- Only one position may be open at a time.

A pullback re-entry is a separate path. It is enabled and requires an eligible
prior exit, an uptrend, a touch near Supertrend followed by a reclaim, continued
price strength, and the same candle/momentum/gap checks. Re-entry cooldown and
daily limits apply.

## Super Run

When enabled, Super Run permits one additional fresh-flip trade after the first
two completed positions of the day are on opposite sides:

- CE then PE makes one CE runner eligible.
- PE then CE makes one PE runner eligible.
- The eligible runner side must produce a fresh Supertrend buy signal between
  10:00 and 11:00 IST (11:00 is excluded).
- The first two positions must have closed before the runner can be considered.
- The runner is one-time per day, must pass ordinary entry checks, and counts
  against the side's normal daily trade limit. Pullback re-entry is not a
  runner signal.
- If another entry is accepted before the runner, pending runner eligibility
  is cleared.
- If both the early-profit pause and the consecutive-loss pause were triggered
  before 10:00, Super Run is disabled for that day.
- The runner uses the same stop, target, trailing-stop, and Supertrend-flip
  exit rules as other positions. Logs identify it as `ST_Runner`.

The relevant switch is `CONFIG["super_run_enabled"]` in `NIFTYIntra.py`.
SENSEXINTRA has its own independent switch and daily runner state.

## Exits and risk controls

- Entry-candle stop: with `special_exit_enabled`, the loss stop is based on the
  entry candle low minus `loss_stop_points` (currently 0).
- Trailing stop: enabled. It activates after 8 points of favorable movement.
  The trailing gap is 8 points, plus 3 points when entry premium is above 100.
- Maximum-profit booking: enabled at 100 option-price points, checked by the
  profit monitor at the configured 60-second interval.
- Supertrend exit: an open position exits on a fresh Supertrend flip down.
- Session square-off: open positions are closed at 15:15 IST.
- Daily realized P&L limits: entries are halted for live trading at the
  configured profit/loss thresholds (15,000 profit / 50,000 loss). Rejected
  live entries may be tracked as paper-only positions according to the existing
  daily-limit behavior.
- Exit-order cooldown: 90 seconds between repeated exit attempts for a symbol.

## Daily entry pauses and limits

- Per-side fresh-entry limits: five CE and five PE entries. Re-entries are
  controlled separately, with at most two per side by default.
- Consecutive loss-stop pause: three losing exits caused by `LSL` in a row pause
  entries until 12:00 IST. Any non-LSL exit or non-losing LSL resets the streak.
  The counter is shared across CE and PE and resets each day.
- Early live-profit pause: if the first three live trades' cumulative P&L reaches
  40% of the 35,000 allocation (14,000) before noon, further live entries are
  tracked paper-only until noon. The loss-streak pause blocks buys outright.
- Super Run never bypasses either pause. When both pause triggers occur before
  10:00, the runner is additionally disqualified for that day.

## Position sizing

- Lot size: 65; maximum one lot per trade.
- Allocation budget: 35,000 per trade.
- Margin availability is checked before a live order by default.
- `enable_actual_trade=False` keeps strategy-generated positions paper-only;
  sizing and risk calculations still use the configured lot and allocation
  values.

## Signals, logs, and reports

- Trade events are logged in a structured format for the scheduler's daily
  report.
- The scheduler sends a daily summary when the script stops and includes trade
  details and completed-trade gross realized P&L before charges.
- Paper and live positions are included in strategy reports where the script
  emits accepted trade events. P&L is gross and does not include fees, taxes,
  slippage, or other charges.
- Debug signal tracking is enabled; debug-only signals are distinct from
  accepted paper/live positions.

## Main NIFTY configuration

Values below are the defaults in `NIFTYIntra.py`; edit that file's `CONFIG`
dictionary to change them.

| Setting | Default | Purpose |
|---|---:|---|
| `candle_interval` | `1M` | Option and index signal timeframe |
| `atr_period` / `factor` | `10` / `3.0` | Supertrend parameters |
| `flip_confirmation_window_candles` | `2` | Recent flip confirmation window |
| `signal_candle_body_limit_points` | `30` | Maximum signal candle body |
| `entry_open_gap_limit_points` | `10` | Maximum open/previous-close gap |
| `entry_window_start` / `entry_window_end` | `09:13` / `15:10` | Entry window (IST) |
| `force_exit_time` | `15:15` | End-of-session square-off (IST) |
| `enable_actual_trade` | `False` | Paper/live execution switch |
| `super_run_enabled` | `True` | Enable the one-time alternating-side runner |
| `max_trades_per_day_ce/pe` | `5` / `5` | Fresh entries allowed per side |
| `max_reentries_per_day` | `2` | Pullback re-entries allowed per side |
| `consecutive_loss_stop_limit` | `3` | Losing LSL exits that trigger pause |
| `loss_streak_entry_pause_until` | `12:00` | End of loss-streak pause |
| `max_profit_booking_points` | `100` | Premium points for profit booking |
| `loss_stop_points` | `0` | Offset below entry-candle low |
| `trailing_stop_trigger_points` | `8` | Favorable move to activate trailing stop |
| `trailing_stop_loss_points` | `8` | Trailing gap (plus high-premium adjustment) |
| `lot_size` / `max_lots_per_trade` | `65` / `1` | Position size limits |

SENSEXINTRA uses the same feature logic, but its own values may differ. Check
that script's `CONFIG` before applying any NIFTY value to SENSEX.
