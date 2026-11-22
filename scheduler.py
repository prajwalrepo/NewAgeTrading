# =============================================================================
# CENTRAL SCHEDULER - Coordinates all 4 stock scanner scripts
# 
# Features:
# 1. Runs scripts based on specific days & times to avoid rate limiting
# 2. Configurable schedule for each script (e.g., NIFTY only on Fri 9:15-3:30)
# 3. Dry-run mode (logs without sending broker requests)
# 4. Staggered API calls with delay between scripts
# 5. Central logging for all scripts
# 6. Easy to add more scripts
# =============================================================================

import os
import sys
import json
import logging
import threading
import subprocess
import time
import smtplib
import mimetypes
import re
import hashlib
from collections import deque
from datetime import datetime, timedelta, time as dt_time, timezone
from email.message import EmailMessage
from pathlib import Path
from enum import Enum

from openpyxl import Workbook
from openpyxl import load_workbook
from openpyxl.styles import Font

# Configure central logging
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
RUN_LOG_DIR = LOG_DIR / "script_runs"
RUN_LOG_DIR.mkdir(exist_ok=True)
CONFIG_FILE = Path(__file__).parent / "scheduler_config.json"

log_file = LOG_DIR / f"scheduler_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(name)s] - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("SCHEDULER")


class DayOfWeek(Enum):
    """Day enumeration"""
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


# ============================================================================
# SCHEDULE CONFIGURATION - Define when each script should run
# ============================================================================
SCRIPTS_SCHEDULE = {
    "NIFTYIntra": {
        "script_name": "NIFTYIntra.py",
        "description": "NIFTY Intraday FNO Strategy",
        "enabled": True,
        "run_days": [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.FRIDAY],
        "run_time_start": "09:13",  # IST
        "run_time_end": "15:30",
        "api_delay_seconds": 10,  # Delay before starting this script (to space out API calls)
        "send_signal_emails": True,  # Email alerts on buy/sell signals
    },
    
    "SENSEXINTRA": {
        "script_name": "SENSEXINTRA.py",
        "description": "SENSEX Intraday FNO Strategy",
        "enabled": True,
        "run_days": [DayOfWeek.WEDNESDAY, DayOfWeek.THURSDAY],
        "run_days_of_month": None,  # Every day of month
        "run_time_start": "09:12",
        "run_time_end": "15:30",
        "api_delay_seconds": 10,  # Start 30 sec after NIFTY to avoid rate limit
        "send_signal_emails": True,  # Email alerts on buy/sell signals
    },

    "320trade": {
        "script_name": "320trade.py",
        "description": "Expiry-day 3:20 ATM CE+PE Straddle Strategy",
        "enabled": True,
        "run_days": [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, DayOfWeek.THURSDAY, DayOfWeek.FRIDAY],
        "run_time_start": "15:18",
        "run_time_end": "15:30",
        "api_delay_seconds": 0,
        "send_signal_emails": False,  # One final summary after close, not every signal
    },
    
    "OILMINI": {
        "script_name": "OILMINI.py",
        "description": "Crude Oil Mini Options Strategy",
        "enabled": False,
        "exclusive_group": "MCX_COMMODITY",
        "run_days": [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, 
                     DayOfWeek.THURSDAY, DayOfWeek.FRIDAY],
        "run_days_of_month": list(range(9, 21)),  # 9th to 20th of every month
        "run_time_start": "16:00",
        "run_time_end": "23:30",  # MCX trades longer
        "api_delay_seconds": 10,  # Start 60 sec after NIFTY
        "send_signal_emails": True,  # Email alerts on buy/sell signals
    },
    
    "NATGASMINI": {
        "script_name": "NATGASMINI.py",
        "description": "Natural Gas Mini Options Strategy",
        "enabled": True,
        "exclusive_group": "MCX_COMMODITY",
        "run_days": [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, 
                     DayOfWeek.THURSDAY, DayOfWeek.FRIDAY],
        "run_days_of_month": None,  # Every day of the month
        "run_time_start": "16:00",
        "run_time_end": "23:30",  # MCX trades longer
        "api_delay_seconds": 10,  # Start 90 sec after NIFTY
        "send_signal_emails": True,  # Email alerts on buy/sell signals
    },

    "SupperWeekScan": {
        "script_name": "SupperWeekScan.py",
        "description": "Weekly stock scanner using supertrend logic",
        "enabled": False,
        "run_days": [DayOfWeek.FRIDAY],
        "run_time_start": "23:00",
        "run_time_end": "23:05",
        "api_delay_seconds": 0,
        "send_signal_emails": True,
    },

    "SupperDayScan": {
        "script_name": "SupperDayScan.py",
        "description": "Daily stock scanner using Groww daily candles",
        "enabled": False,
        "run_days": [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY,
                     DayOfWeek.THURSDAY, DayOfWeek.FRIDAY, DayOfWeek.SATURDAY,
                     DayOfWeek.SUNDAY],
        "run_time_start": "23:30",
        "run_time_end": "23:59",
        "run_once_per_window": True,
        "api_delay_seconds": 0,
        "send_signal_emails": True,
        "send_daily_summary_on_stop": False,
    },

    "yfday": {
        "script_name": "yfday.py",
        "description": "Daily Yahoo Finance Supertrend scanner",
        "enabled": True,
        "run_days": [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, DayOfWeek.THURSDAY, DayOfWeek.FRIDAY],
        "run_time_start": "23:59",
        "run_time_end": "23:59",
        "run_once_per_window": True,
        "api_delay_seconds": 0,
        "send_signal_emails": True,
        "send_daily_summary_on_stop": False,
    },

    "yfweek": {
        "script_name": "yfweek.py",
        "description": "Weekly Yahoo Finance Supertrend scanner",
        "enabled": True,
        "run_days": [DayOfWeek.FRIDAY],
        "run_time_start": "23:59",
        "run_time_end": "23:59",
        "run_once_per_window": True,
        "api_delay_seconds": 0,
        "send_signal_emails": True,
        "send_daily_summary_on_stop": False,
    },
}


# ============================================================================
# GLOBAL CONFIGURATION
# ============================================================================
CONFIG = {
    "timezone_offset_minutes": 330,  # IST (UTC+5:30)
    "check_interval_seconds": 60,    # Check every 60 seconds if script should run
    "dry_run": False,                # Set True to test without sending broker requests
    "enable_all_day_logging": True,  # Keep logs running all day
    "restart_crashed_scripts": False,  # Auto-restart if script crashes
    "api_rate_limit_spacing": True,  # Space out API calls
    "log_retention_days": 2,
    "email_notifications": {
        "enabled": False,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "use_ssl": True,
        "smtp_username": "",
        "smtp_password": "",
        "from_email": "",
        "to_emails": [],
        "send_on_signals": False,
        "send_daily_summary_on_stop": True,
        "scan_report_recipients": [],  # Recipients for scan reports
    },
}


# ============================================================================
# Helper Functions
# ============================================================================

def get_ist_time():
    """Get current time in IST"""
    offset = timedelta(minutes=CONFIG["timezone_offset_minutes"])
    return datetime.now(timezone(offset))


def _merge_dict(base, updates):
    """Recursively merge updates into base."""
    for key, value in (updates or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_dict(base[key], value)
        else:
            base[key] = value
    return base


def _normalize_run_day(day_value):
    """Convert configured day values to weekday integers."""
    if isinstance(day_value, DayOfWeek):
        return day_value.value
    if isinstance(day_value, int):
        return day_value
    if isinstance(day_value, str):
        enum_value = DayOfWeek.__members__.get(day_value.strip().upper())
        if enum_value is not None:
            return enum_value.value
    return None


def load_scheduler_config():
    """Load optional scheduler overrides from scheduler_config.json."""
    if not CONFIG_FILE.exists():
        logger.info(f"[CONFIG] Using in-file schedule defaults (missing {CONFIG_FILE.name})")
        return

    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as config_handle:
            data = json.load(config_handle)
    except Exception as exc:
        logger.error(f"[CONFIG] Failed to read {CONFIG_FILE.name}: {exc}")
        return

    global_settings = data.get("global_settings")
    if isinstance(global_settings, dict):
        _merge_dict(CONFIG, {k: v for k, v in global_settings.items() if k != "email_notifications"})
        if isinstance(global_settings.get("email_notifications"), dict):
            _merge_dict(CONFIG["email_notifications"], global_settings["email_notifications"])

    email_settings = data.get("email_notifications")
    if isinstance(email_settings, dict):
        _merge_dict(CONFIG["email_notifications"], email_settings)

    scripts_data = data.get("scripts")
    if isinstance(scripts_data, dict):
        for script_key, overrides in scripts_data.items():
            if not isinstance(overrides, dict):
                continue
            if script_key not in SCRIPTS_SCHEDULE:
                SCRIPTS_SCHEDULE[script_key] = {}
            previous_enabled = SCRIPTS_SCHEDULE[script_key].get("enabled")
            _merge_dict(SCRIPTS_SCHEDULE[script_key], overrides)
            current_enabled = SCRIPTS_SCHEDULE[script_key].get("enabled")
            if "enabled" in overrides and previous_enabled != current_enabled:
                logger.info(
                    f"[CONFIG] {script_key}.enabled overridden from {previous_enabled} to {current_enabled} by {CONFIG_FILE.name}"
                )

    logger.info(f"[CONFIG] Loaded scheduler settings from {CONFIG_FILE.name}")


def parse_time(time_str):
    """Parse HH:MM format to time object"""
    return datetime.strptime(time_str, "%H:%M").time()


def _format_date_component(ts):
    return ts.strftime("%d%b")


def _format_time_component(ts):
    return ts.strftime("%H%M").lstrip("0") or "0"


def _use_single_daily_run_log(config_key):
    """Return True when a script should append to one daily run log file."""
    return str(config_key).upper() == "320TRADE"


def build_daily_run_log_path(config_key, ts):
    """Build a single per-day run log filename for scripts that avoid per-run files."""
    return RUN_LOG_DIR / f"{config_key}{_format_date_component(ts)}.log"


def build_temp_run_log_path(config_key, started_at):
    """Create a temporary log path until the script ends and we know the stop time."""
    if _use_single_daily_run_log(config_key):
        return build_daily_run_log_path(config_key, started_at)
    return RUN_LOG_DIR / f"{config_key}{_format_date_component(started_at)}{_format_time_component(started_at)}_RUNNING.log"


def build_final_run_log_path(config_key, started_at, ended_at):
    """Build the final per-run log filename using the end time for the completed run."""
    return RUN_LOG_DIR / f"{config_key}{_format_date_component(started_at)}{_format_time_component(ended_at)}.log"


def _dedupe_path(path_obj):
    """Avoid overwriting an existing path by appending a counter."""
    if not path_obj.exists():
        return path_obj

    counter = 1
    while True:
        candidate = path_obj.with_name(f"{path_obj.stem}_{counter}{path_obj.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def append_run_log(log_path, message):
    """Append a line to the per-run log file."""
    try:
        with Path(log_path).open("a", encoding="utf-8") as log_handle:
            log_handle.write(f"{message}\n")
    except Exception as exc:
        logger.error(f"[LOG] Failed to write run log {log_path}: {exc}")


def cleanup_old_logs():
    """Delete scheduler logs older than the configured retention window."""
    retention_days = int(CONFIG.get("log_retention_days", 2) or 2)
    cutoff = datetime.now() - timedelta(days=retention_days)
    deleted_count = 0

    for directory in (LOG_DIR, RUN_LOG_DIR):
        try:
            for entry in directory.iterdir():
                if not entry.is_file():
                    continue
                try:
                    modified_at = datetime.fromtimestamp(entry.stat().st_mtime)
                    if modified_at < cutoff:
                        entry.unlink()
                        deleted_count += 1
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    logger.error(f"[LOG] Failed to remove old log {entry.name}: {exc}")
        except FileNotFoundError:
            continue

    if deleted_count:
        logger.info(f"[LOG] Deleted {deleted_count} log file(s) older than {retention_days} day(s)")


def send_email_notification(subject, body, attachment_path=None, html_body=None, recipients=None):
    """Send an email notification if SMTP is configured."""
    email_cfg = CONFIG.get("email_notifications", {})
    if not email_cfg.get("enabled", False):
        return False

    recipients = recipients or email_cfg.get("to_emails") or []
    smtp_host = str(email_cfg.get("smtp_host", "") or "").strip()
    smtp_username = str(email_cfg.get("smtp_username", "") or "").strip()
    smtp_password = str(email_cfg.get("smtp_password", "") or "").strip()
    from_email = str(email_cfg.get("from_email", smtp_username) or smtp_username).strip()
    smtp_port = int(email_cfg.get("smtp_port", 465) or 465)

    if not smtp_host or not from_email or not recipients:
        logger.warning("[EMAIL] Skipping email; SMTP host, sender, or recipients are missing")
        return False

    if not smtp_username or not smtp_password:
        logger.warning("[EMAIL] Skipping email; SMTP credentials are missing")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_email
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    if html_body:
        message.add_alternative(str(html_body), subtype="html")

    if attachment_path is not None:
        attachment = Path(attachment_path)
        if attachment.exists():
            content_type, _ = mimetypes.guess_type(attachment.name)
            maintype, subtype = (content_type or "application/octet-stream").split("/", 1)
            with attachment.open("rb") as file_handle:
                message.add_attachment(
                    file_handle.read(),
                    maintype=maintype,
                    subtype=subtype,
                    filename=attachment.name,
                )

    try:
        if email_cfg.get("use_ssl", True):
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as smtp:
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as smtp:
                smtp.starttls()
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(message)
        logger.info(f"[EMAIL] Sent: {subject}")
        return True
    except KeyboardInterrupt:
        logger.warning(f"[EMAIL] Interrupted while sending '{subject}'")
        return False
    except Exception as exc:
        logger.error(f"[EMAIL] Failed to send '{subject}': {exc}")
        return False


def finalize_run_log(run_context, ended_at, reason, exit_code=None):
    """Finalize a per-script run log by appending stop status and renaming it."""
    run_log_path = Path(run_context["run_log_path"])
    append_run_log(
        run_log_path,
        f"[{ended_at.strftime('%Y-%m-%d %H:%M:%S %Z')}] [STOPPED] Reason={reason} ExitCode={exit_code}",
    )

    if _use_single_daily_run_log(run_context["config_key"]):
        # For daily consolidated logs we keep appending to the same file.
        run_context["run_log_path"] = run_log_path
        return Path(run_context["run_log_path"])

    final_path = _dedupe_path(build_final_run_log_path(run_context["config_key"], run_context["started_at"], ended_at))
    try:
        run_log_path.rename(final_path)
        run_context["run_log_path"] = final_path
    except Exception as exc:
        logger.error(f"[LOG] Failed to finalize run log for {run_context['config_key']}: {exc}")
    return Path(run_context["run_log_path"])


def stop_script(run_context, script_config, reason, terminate_process=True):
    """Stop a running script and finalize its log."""
    process = run_context["process"]

    if terminate_process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    output_thread = run_context.get("output_thread")
    if output_thread is not None:
        output_thread.join()

    ended_at = get_ist_time()
    exit_code = process.poll()
    final_log_path = finalize_run_log(run_context, ended_at, reason, exit_code=exit_code)
    logger.info(f"[STOPPED] {run_context['config_key']} stopped | Reason={reason} | ExitCode={exit_code} | Log={final_log_path.name}")
    if reason != "Scheduler stopped by user":
        email_daily_trade_summary(run_context, reason)
    return exit_code


def classify_process_exit(run_context, exit_code):
    """Classify script exits so fatal repeated errors can be suppressed for the current window."""
    recent_output = [line.strip() for line in run_context.get("recent_output", []) if str(line).strip()]
    normalized_output = "\n".join(recent_output[-10:]).lower()

    if (
        "authentication failed" in normalized_output
        or "authorisation failed" in normalized_output
        or "required permissions" in normalized_output
    ):
        return "auth_failure", "Authentication failed / API permission issue", True

    if recent_output:
        for line in reversed(recent_output):
            if "[ERROR]" in line or "traceback" in line.lower():
                return f"exit_{exit_code}:{line[:120]}", line[:200], False

    if exit_code == 0:
        return "unexpected_exit_0", "Process exited early without staying alive", False

    return f"exit_{exit_code}", f"Process exited with code {exit_code}", False


def clear_failure_block_if_needed(config_key, script_config, blocked_failures):
    """Allow retries again after the active run window has ended."""
    if config_key in blocked_failures and not should_script_run_now(script_config):
        blocked_failures.pop(config_key, None)


def get_conflicting_running_script(config_key, script_config, running_processes):
    """Return another running script in the same exclusive group, if any."""
    exclusive_group = str(script_config.get("exclusive_group") or "").strip()
    if not exclusive_group:
        return None

    for other_key, run_context in running_processes.items():
        if other_key == config_key:
            continue
        if run_context["process"].poll() is not None:
            continue
        other_group = str(SCRIPTS_SCHEDULE.get(other_key, {}).get("exclusive_group") or "").strip()
        if other_group == exclusive_group:
            return other_key
    return None


def handle_process_state_changes(running_processes, script_launch_logged, blocked_failures):
    """Detect exited child scripts promptly and trigger stop email/restart flow."""
    for config_key in list(running_processes.keys()):
        run_context = running_processes.get(config_key)
        if run_context is None:
            continue

        process = run_context["process"]
        if process.poll() is None:
            continue

        returncode = process.returncode
        if returncode == 0 and _is_scan_script(config_key, run_context.get("script_name", "")):
            logger.info(f"[COMPLETE] {config_key} completed normally")
            stop_script(
                run_context,
                SCRIPTS_SCHEDULE[config_key],
                reason="Scanner completed normally",
                terminate_process=False,
            )
            del running_processes[config_key]
            script_launch_logged.discard(config_key)
            continue

        failure_signature, failure_summary, is_fatal = classify_process_exit(run_context, returncode)
        run_context["failure_signature"] = failure_signature
        run_context["failure_summary"] = failure_summary
        logger.error(f"[CRASH] {config_key} crashed (exit code: {returncode})")
        stop_script(
            run_context,
            SCRIPTS_SCHEDULE[config_key],
            reason="Process exited unexpectedly",
            terminate_process=False,
        )
        del running_processes[config_key]
        script_launch_logged.discard(config_key)

        if is_fatal:
            blocked_failures[config_key] = {
                "signature": failure_signature,
                "summary": failure_summary,
                "blocked_at": get_ist_time(),
            }
            logger.error(f"[BLOCKED] {config_key} blocked for current run window: {failure_summary}")
            continue

        if CONFIG.get("restart_crashed_scripts") and should_script_run_now(SCRIPTS_SCHEDULE[config_key]):
            logger.info(f"[RESTART] Restarting {config_key}...")
            time.sleep(5)
            relaunched = launch_script(
                SCRIPTS_SCHEDULE[config_key]["script_name"],
                config_key,
            )
            if relaunched:
                running_processes[config_key] = relaunched
                script_launch_logged.add(config_key)


def monitored_sleep(seconds, running_processes, script_launch_logged, blocked_failures):
    """Sleep in short intervals so crash handling is not delayed."""
    end_time = time.time() + max(float(seconds), 0)
    while time.time() < end_time:
        handle_process_state_changes(running_processes, script_launch_logged, blocked_failures)
        remaining = end_time - time.time()
        if remaining <= 0:
            break
        time.sleep(min(1.0, remaining))


def should_script_run_now(script_config):
    """Determine if a script should run at this moment"""
    if not script_config.get("enabled", True):
        return False
    
    now = get_ist_time()
    current_day = now.weekday()
    current_day_of_month = now.day
    current_time = now.time()
    
    allowed_days = script_config.get("run_days", [])
    run_start = parse_time(script_config.get("run_time_start", "00:00"))
    run_end = parse_time(script_config.get("run_time_end", "23:59"))
    
    # Check if today (weekday) is an allowed day
    normalized_days = [day for day in (_normalize_run_day(item) for item in allowed_days) if day is not None]
    if normalized_days and current_day not in normalized_days:
        return False
    
    # Check if today (day of month) is allowed
    run_days_of_month = script_config.get("run_days_of_month")
    if run_days_of_month is not None:
        # If specific days are configured, check if today is one of them
        if current_day_of_month not in run_days_of_month:
            return False
    # If run_days_of_month is None, allow any day of month
    
    # Check if current time is within trading window
    if not (run_start <= current_time <= run_end):
        return False
    
    return True


TRADE_SIGNAL_FIELDS = (
    "Script", "Side", "Time", "Symbol", "Strike", "Price", "Reason",
    "Lots", "LotSize", "Qty", "OrderPlaced", "BuyAt", "SellAt", "PnL", "HighestPrice",
)
TRADE_SIGNAL_FIELD_PATTERN = re.compile(
    r"(?:^|\s)(" + "|".join(TRADE_SIGNAL_FIELDS) + r")="
)
PRICE_MILESTONE_FIELDS = (
    "Script", "Time", "Symbol", "Side", "Strike", "Qty", "OrderPlaced",
    "Entry", "Price", "Move", "Direction", "Peak",
)
PRICE_MILESTONE_FIELD_PATTERN = re.compile(
    r"(?:^|\s)(" + "|".join(PRICE_MILESTONE_FIELDS) + r")="
)
TRADE_REPORT_COLUMNS = (
    "Script", "Side", "Time", "Symbol", "Strike", "Price", "Reason", "Lots",
    "Lot Size", "Qty", "Order Placed",
)
TRADE_REPORT_LOCK = threading.Lock()
PRICE_MILESTONE_EMAIL_GUARD = {}


def _initialize_trade_report_sheet(workbook):
    sheet = workbook.active
    sheet.title = "Trades"
    sheet.append((*TRADE_REPORT_COLUMNS, "Event Key"))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    for index, width in enumerate((18, 12, 24, 36, 12, 14, 48, 12, 12, 12, 16), start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    sheet.column_dimensions[sheet.cell(row=1, column=len(TRADE_REPORT_COLUMNS) + 1).column_letter].hidden = True
    return sheet


def parse_trade_signal(line):
    """Parse one structured trade event from script output."""
    if "[TRADE SIGNAL]" not in line:
        return None

    matches = list(TRADE_SIGNAL_FIELD_PATTERN.finditer(line))
    fields = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        fields[match.group(1)] = line[start:end].strip()

    required_fields = ("Script", "Side", "Time", "Symbol", "Strike", "Price", "Reason", "OrderPlaced")
    if any(not fields.get(field) for field in required_fields):
        return None
    if fields["Side"].upper() not in {"BUY", "SELL"} or fields["OrderPlaced"].upper() not in {"YES", "NO"}:
        return None

    return {
        "Script": fields["Script"],
        "Side": fields["Side"].upper(),
        "Time": fields["Time"],
        "Symbol": fields["Symbol"],
        "Strike": fields["Strike"],
        "Price": fields["Price"],
        "Reason": fields["Reason"],
        "Lots": fields.get("Lots", ""),
        "Lot Size": fields.get("LotSize", ""),
        "Qty": fields.get("Qty", ""),
        "Order Placed": fields["OrderPlaced"].upper(),
        "Buy At": fields.get("BuyAt", ""),
        "Sell At": fields.get("SellAt", ""),
        "PnL": fields.get("PnL", ""),
        "Highest Price": fields.get("HighestPrice", ""),
    }


def parse_price_milestone(line):
    """Parse one price milestone event from script output."""
    if "[PRICE MILESTONE]" not in line:
        return None

    matches = list(PRICE_MILESTONE_FIELD_PATTERN.finditer(line))
    fields = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        fields[match.group(1)] = line[start:end].strip()

    required_fields = (
        "Script", "Time", "Symbol", "Side", "Strike", "OrderPlaced", "Entry", "Price", "Move", "Direction"
    )
    if any(not fields.get(field) for field in required_fields):
        return None

    if fields["OrderPlaced"].upper() not in {"YES", "NO"}:
        return None

    return {
        "Script": fields["Script"],
        "Time": fields["Time"],
        "Symbol": fields["Symbol"],
        "Side": fields["Side"],
        "Strike": fields["Strike"],
        "Qty": fields.get("Qty", ""),
        "Order Placed": fields["OrderPlaced"].upper(),
        "Entry": fields["Entry"],
        "Price": fields["Price"],
        "Move": fields["Move"],
        "Direction": fields["Direction"].upper(),
        "Peak": fields.get("Peak", ""),
    }


def _extract_option_side(symbol):
    symbol_upper = str(symbol or "").upper()
    if symbol_upper.endswith("-CE"):
        return "CE"
    if symbol_upper.endswith("-PE"):
        return "PE"
    return "NA"


def _format_signal_time_short(iso_or_text):
    try:
        dt = datetime.fromisoformat(str(iso_or_text).replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except ValueError:
        text = str(iso_or_text)
        if "T" in text and len(text.split("T", 1)[1]) >= 5:
            return text.split("T", 1)[1][:5]
        return text


def _format_script_display_name(script_name):
    name = str(script_name or "").strip()
    if not name:
        return "UNKNOWN"
    if len(name) > 1 and name[1:].upper() == name[1:]:
        return name.upper()
    return name


def _to_float_text(value):
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value or "")


def _to_int_text(value):
    try:
        number = float(value)
        return str(int(number)) if number.is_integer() else str(number)
    except (TypeError, ValueError):
        return str(value or "")


def _build_trade_email_subject(trade):
    live_prefix = "LIVE TRADE" if trade.get("Order Placed", "NO") == "YES" else "PAPER TRADE"
    script_name = _format_script_display_name(trade.get("Script"))
    side = _extract_option_side(trade.get("Symbol"))
    strike = trade.get("Strike", "NA")
    action = trade.get("Side", "NA").upper()
    short_time = _format_signal_time_short(trade.get("Time"))
    return f"[{live_prefix}] {script_name} {action} {side} {strike} at {short_time}"


def _build_trade_email_content(trade):
    action = trade.get("Side", "NA").upper()
    action_label = "BUY" if action == "BUY" else "SELL"
    side = _extract_option_side(trade.get("Symbol"))
    script_name = _format_script_display_name(trade.get("Script"))
    real_order_text = "Yes" if trade.get("Order Placed", "NO") == "YES" else "No"

    lines = [
        "Option trade signal",
        "",
        f"Script name: {script_name}",
        f"Time: {_format_signal_time_short(trade.get('Time'))}",
        f"Strike: {trade.get('Strike', '')}",
        f"Signal: {action_label}",
    ]

    if action_label == "BUY":
        lines.append(f"Buy price: {_to_float_text(trade.get('Price'))}")
    else:
        entry_value = trade.get("Buy At") or trade.get("Entry") or ""
        if entry_value:
            lines.append(f"Entry: {_to_float_text(entry_value)}")
        sell_value = trade.get("Sell At") or trade.get("Price")
        lines.append(f"Sell price: {_to_float_text(sell_value)}")

    lines.extend([
        f"Side: {side}",
        f"Quantity: {_to_int_text(trade.get('Qty'))}",
        f"Real order: {real_order_text}",
    ])

    highest = trade.get("Highest Price")
    pnl = trade.get("PnL")
    if action_label == "SELL":
        lines.append(f"MA price recorded: {_to_float_text(highest)}" if highest else "MA price recorded:")
        lines.append(f"PNL: {_to_int_text(pnl)}" if pnl else "PNL:")

    html_lines = [
        "<p><strong>Option trade signal</strong></p>",
        "<table>",
        f"<tr><td>Script name</td><td>{script_name}</td></tr>",
        f"<tr><td>Time</td><td>{_format_signal_time_short(trade.get('Time'))}</td></tr>",
        f"<tr><td>Strike</td><td>{trade.get('Strike', '')}</td></tr>",
        f"<tr><td>Signal</td><td><strong>{action_label}</strong></td></tr>",
    ]

    if action_label == "BUY":
        html_lines.append(f"<tr><td>Buy price</td><td>{_to_float_text(trade.get('Price'))}</td></tr>")
    else:
        entry_value = trade.get("Buy At") or trade.get("Entry") or ""
        if entry_value:
            html_lines.append(f"<tr><td>Entry</td><td>{_to_float_text(entry_value)}</td></tr>")
        sell_value = trade.get("Sell At") or trade.get("Price")
        html_lines.append(f"<tr><td>Sell price</td><td>{_to_float_text(sell_value)}</td></tr>")

    html_lines.extend([
        f"<tr><td>Side</td><td>{side}</td></tr>",
        f"<tr><td>Quantity</td><td>{_to_int_text(trade.get('Qty'))}</td></tr>",
        f"<tr><td>Real order</td><td>{real_order_text}</td></tr>",
    ])

    if action_label == "SELL":
        html_lines.append(
            f"<tr><td>MA price recorded</td><td>{_to_float_text(highest)}</td></tr>" if highest else "<tr><td>MA price recorded</td><td></td></tr>"
        )
        html_lines.append(
            f"<tr><td>PNL</td><td>{_to_int_text(pnl)}</td></tr>" if pnl else "<tr><td>PNL</td><td></td></tr>"
        )

    html_lines.append("</table>")
    return "\n".join(lines), "\n".join(html_lines)


def _build_price_milestone_subject(milestone):
    move_text = _to_float_text(milestone.get("Move"))
    direction = milestone.get("Direction", "NA")
    return (
        f"[PRICE MOVE] {milestone.get('Script')} Option "
        f"{move_text} points ({direction}) - {milestone.get('Side')} "
        f"{milestone.get('Symbol')} at {milestone.get('Time')}"
    )


def _build_price_milestone_content(milestone):
    script_name = _format_script_display_name(milestone.get("Script"))
    real_order_text = "Yes" if milestone.get("Order Placed", "NO") == "YES" else "No"
    price_move_text = f"{_to_float_text(milestone.get('Move'))} points ({milestone.get('Direction', 'NA')})"

    lines = [
        "Option price milestone",
        "",
        f"Script name: {script_name}",
        f"Time: {milestone.get('Time')}",
        f"Strike: {milestone.get('Strike')}",
        "Signal: PRICE MOVE",
        f"Entry: {_to_float_text(milestone.get('Entry'))}",
        f"Current premium: {_to_float_text(milestone.get('Price'))}",
        f"Quantity: {_to_int_text(milestone.get('Qty'))}",
        f"Real order: {real_order_text}",
        f"Price move: {price_move_text}",
    ]

    peak = milestone.get("Peak")
    retracement_text = ""
    if peak:
        try:
            retracement_text = _to_float_text(float(peak) - float(milestone.get("Price")))
        except (TypeError, ValueError):
            retracement_text = ""
    if retracement_text:
        lines.append(f"Retracement from peak: {retracement_text} (peak {peak})")

    html_lines = [
        "<p><strong>Option price milestone</strong></p>",
        "<table>",
        f"<tr><td>Script name</td><td>{script_name}</td></tr>",
        f"<tr><td>Time</td><td>{milestone.get('Time')}</td></tr>",
        f"<tr><td>Strike</td><td>{milestone.get('Strike')}</td></tr>",
        "<tr><td>Signal</td><td>PRICE MOVE</td></tr>",
        f"<tr><td>Entry</td><td>{_to_float_text(milestone.get('Entry'))}</td></tr>",
        f"<tr><td>Current premium</td><td>{_to_float_text(milestone.get('Price'))}</td></tr>",
        f"<tr><td>Quantity</td><td>{_to_int_text(milestone.get('Qty'))}</td></tr>",
        f"<tr><td>Real order</td><td>{real_order_text}</td></tr>",
        f"<tr><td>Price move</td><td><strong>{price_move_text}</strong></td></tr>",
    ]

    if retracement_text:
        html_lines.append(
            f"<tr><td>Retracement from peak</td><td>{retracement_text} (peak {peak})</td></tr>"
        )

    html_lines.append("</table>")
    return "\n".join(lines), "\n".join(html_lines)


def _trade_report_path(config_key, trade_time):
    try:
        trade_date = datetime.fromisoformat(trade_time.replace("Z", "+00:00")).strftime("%Y%m%d")
    except ValueError:
        trade_date = get_ist_time().strftime("%Y%m%d")
    return RUN_LOG_DIR / f"trades_{config_key}_{trade_date}.xlsx"


def append_trade_to_report(config_key, trade):
    """Append a unique trade event to that script's daily Excel report."""
    event_data = json.dumps(trade, sort_keys=True, separators=(",", ":"))
    event_key = hashlib.sha256(event_data.encode("utf-8")).hexdigest()
    report_path = _trade_report_path(config_key, trade["Time"])
    with TRADE_REPORT_LOCK:
        if report_path.exists():
            workbook = load_workbook(report_path)
            sheet = workbook["Trades"]
            key_column = len(TRADE_REPORT_COLUMNS) + 1
            if any(
                row[0].value == event_key
                for row in sheet.iter_rows(min_row=2, min_col=key_column, max_col=key_column)
            ):
                return report_path, False
        else:
            workbook = Workbook()
            sheet = _initialize_trade_report_sheet(workbook)

        row = []
        for column in TRADE_REPORT_COLUMNS:
            value = trade[column]
            if column in {"Strike", "Price", "Lots", "Lot Size", "Qty"} and value:
                try:
                    number = float(value)
                    value = int(number) if number.is_integer() else number
                except ValueError:
                    pass
            row.append(value)
        sheet.append(tuple(row) + (event_key,))
        sheet.auto_filter.ref = sheet.dimensions
        workbook.save(report_path)
    return report_path, True


def _trade_report_for_run(run_context):
    trade_date = run_context["started_at"].strftime("%Y%m%d")
    return RUN_LOG_DIR / f"trades_{run_context['config_key']}_{trade_date}.xlsx"


def _ensure_trade_report_exists(report_path):
    with TRADE_REPORT_LOCK:
        if report_path.exists():
            return
        workbook = Workbook()
        _initialize_trade_report_sheet(workbook)
        workbook.save(report_path)


def _trade_report_summary(report_path):
    workbook = load_workbook(report_path, read_only=True, data_only=True)
    try:
        sheet = workbook["Trades"]
        headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
        side_index = headers.index("Side")
        order_index = headers.index("Order Placed")
        total = buys = sells = orders_placed = 0
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if not any(value is not None for value in row[:len(TRADE_REPORT_COLUMNS)]):
                continue
            total += 1
            side = str(row[side_index] or "").upper()
            buys += side == "BUY"
            sells += side == "SELL"
            orders_placed += str(row[order_index] or "").upper() == "YES"
        return total, buys, sells, orders_placed
    finally:
        workbook.close()


def _extract_320trade_summary(run_context):
    """Extract the final CE/PE executed outcome from the script run log."""
    config_key = str(run_context.get("config_key") or "").upper()
    if config_key != "320TRADE":
        return None

    log_path = run_context.get("run_log_path")
    if not log_path:
        return None

    path = Path(log_path)
    if not path.exists():
        return None

    pattern = re.compile(
        r"\[TRADE RESULT\]\s+Script=\S+\s+Side=(?P<side>\S+)\s+"
        r"Symbol=(?P<symbol>\S+)\s+Strike=(?P<strike>\S+)\s+"
        r"BuyAt=(?P<buy>[-+]?\d*\.?\d+)\s+"
        r"SellAt=(?P<sell>[-+]?\d*\.?\d+)\s+"
        r"PnL=(?P<pnl>[-+]?\d*\.?\d+)"
    )

    latest_by_symbol = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = pattern.search(line.strip())
                if not match:
                    continue
                symbol = match.group("symbol")
                row = {
                    "side": match.group("side"),
                    "buy": float(match.group("buy")),
                    "sell": float(match.group("sell")),
                    "pnl": float(match.group("pnl")),
                }
                latest_by_symbol[symbol] = row
    except Exception as exc:
        logger.warning(f"[REPORT] Failed to parse 320trade result summary: {exc}")
        return None

    if not latest_by_symbol:
        return None

    summary_lines = []
    total_pnl = 0.0
    for symbol in sorted(latest_by_symbol):
        row = latest_by_symbol[symbol]
        total_pnl += row["pnl"]
        summary_lines.append(
            f"{symbol}: BUY={row['buy']:.2f}, SELL={row['sell']:.2f}, PnL={row['pnl']:.2f}"
        )
    summary_lines.append(f"Net PnL: {total_pnl:.2f}")
    return "\n".join(summary_lines)


def email_daily_trade_summary(run_context, reason):
    """Send one daily workbook summary when a trading script stops."""
    email_cfg = CONFIG.get("email_notifications", {})
    if not email_cfg.get("enabled", False) or not email_cfg.get("send_daily_summary_on_stop", True):
        return
    if run_context.get("daily_summary_email_sent"):
        return
    run_context["daily_summary_email_sent"] = True

    report_path = _trade_report_for_run(run_context)
    try:
        _ensure_trade_report_exists(report_path)
        total, buys, sells, orders_placed = _trade_report_summary(report_path)
    except Exception as exc:
        logger.error(f"[REPORT] Failed to prepare daily summary for {run_context['config_key']}: {exc}")
        return

    trading_date = run_context["started_at"].strftime("%Y-%m-%d")
    subject = f"[DAILY SUMMARY] {run_context['config_key']} {trading_date}"
    body = (
        f"Daily trading summary\n"
        f"Script: {run_context['config_key']}\n"
        f"Trading date: {trading_date}\n"
        f"Stop reason: {reason}\n"
        f"Trade signals: {total}\n"
        f"Buy signals: {buys}\n"
        f"Sell signals: {sells}\n"
        f"Orders placed: {orders_placed}\n"
        f"Excel report: {report_path.name}"
    )

    if str(run_context.get("config_key") or "").upper() == "320TRADE":
        extra_summary = _extract_320trade_summary(run_context)
        if extra_summary:
            body = f"{body}\n\nExecuted 3:20 CE/PE outcome:\n{extra_summary}"

    send_email_notification(subject, body, attachment_path=report_path)


def email_trade_signal(config_key, trade, report_path=None):
    """Email one trade signal in a standard format used by every script."""
    _ = config_key
    subject = _build_trade_email_subject(trade)
    body, html_body = _build_trade_email_content(trade)
    send_email_notification(subject, body, attachment_path=report_path, html_body=html_body)


def process_trade_signal(config_key, output_line, script_config=None):
    """Record each unique trade signal and optionally email it immediately."""
    trade = parse_trade_signal(output_line)
    if trade is None:
        return
    try:
        report_path, is_new = append_trade_to_report(config_key, trade)
    except Exception as exc:
        logger.error(f"[REPORT] Failed to record trade signal for {config_key}: {exc}")
        return
    send_on_signals = CONFIG.get("email_notifications", {}).get("send_on_signals", False)
    script_emails_enabled = not script_config or script_config.get("send_signal_emails", True)
    if is_new and send_on_signals and script_emails_enabled:
        email_trade_signal(config_key, trade, report_path)


def process_price_milestone(config_key, output_line, script_config=None):
    """Email one price milestone while suppressing duplicates for the same symbol+timestamp."""
    milestone = parse_price_milestone(output_line)
    if milestone is None:
        return

    send_on_signals = CONFIG.get("email_notifications", {}).get("send_on_signals", False)
    script_emails_enabled = not script_config or script_config.get("send_signal_emails", True)
    if not send_on_signals or not script_emails_enabled:
        return

    dedupe_key = (
        str(milestone.get("Script") or config_key).upper(),
        str(milestone.get("Symbol") or ""),
        str(milestone.get("Time") or ""),
    )
    if PRICE_MILESTONE_EMAIL_GUARD.get(dedupe_key):
        return
    PRICE_MILESTONE_EMAIL_GUARD[dedupe_key] = True

    # Prevent unbounded growth for very long scheduler uptime.
    if len(PRICE_MILESTONE_EMAIL_GUARD) > 5000:
        for old_key in list(PRICE_MILESTONE_EMAIL_GUARD.keys())[:1000]:
            PRICE_MILESTONE_EMAIL_GUARD.pop(old_key, None)

    subject = _build_price_milestone_subject(milestone)
    body, html_body = _build_price_milestone_content(milestone)
    send_email_notification(subject, body, html_body=html_body)


def capture_script_output(config_key, process, run_log_path, run_context, script_config=None):
    """Read and log script output in real-time"""
    try:
        with Path(run_log_path).open("a", encoding="utf-8") as script_log_handle:
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                output = str(line).rstrip("\r\n")
                if output:
                    run_context["recent_output"].append(output)
                    logger.info(f"[{config_key}] {output}")
                    script_log_handle.write(f"{output}\n")
                    script_log_handle.flush()
                    process_trade_signal(config_key, output, script_config)
                    process_price_milestone(config_key, output, script_config)
                    process_scan_results(config_key, output, script_config)
    except Exception as e:
        logger.error(f"[{config_key}] Error reading output: {e}")


def _is_scan_script(config_key, script_name=""):
    """Identify scan scripts that finish normally with exit code 0."""
    text = f"{config_key} {script_name}".upper()
    return "SCAN" in text or "YFDAY" in text or "YFWEEK" in text


def process_scan_results(script_name, output, script_config=None):
    """Parse scan output, create an Excel BUY/SELL report, and email it."""
    if "[SCAN RESULTS]" not in output:
        return

    script_match = re.search(r"Script=([^\s]+)", output)
    period_match = re.search(r"Period=([^\s]+)", output)
    date_match = re.search(r"ScanDate=([^\s]+)", output)
    json_match = re.search(r"Results=(\[.*\])\s*$", output)
    if not all((script_match, period_match, date_match, json_match)):
        logger.warning(f"[{script_name}] Could not parse scanner result line")
        return

    try:
        results = json.loads(json_match.group(1))
        if not isinstance(results, list):
            raise ValueError("Results must be a JSON list")
    except Exception as exc:
        logger.error(f"[{script_name}] Invalid scan result JSON: {exc}")
        return

    scan_script = script_match.group(1)
    period = period_match.group(1).lower()
    scan_date = date_match.group(1)
    buys = [row for row in results if isinstance(row, dict) and str(row.get("side", "")).upper() == "BUY"]
    sells = [row for row in results if isinstance(row, dict) and str(row.get("side", "")).upper() == "SELL"]

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "BUY SELL Signals"
    sheet.append(("Symbol", "Side", "Date", "Price", "Supertrend", "RSI"))
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for row in results:
        if isinstance(row, dict):
            sheet.append((
                row.get("symbol", ""),
                str(row.get("side", "")).upper(),
                row.get("date", ""),
                row.get("price", ""),
                row.get("supertrend", ""),
                row.get("rsi", ""),  # Add RSI value
            ))

    for index, width in enumerate((20, 10, 14, 14, 14, 10), start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    attachment_path = RUN_LOG_DIR / f"{period}_scan_{scan_script}_{scan_date}.xlsx"
    workbook.save(attachment_path)

    subject = f"[{period.upper()} SCAN REPORT] {scan_script} {scan_date}"
    body_lines = [
        subject,
        "",
        f"BUY signals: {len(buys)}",
        f"SELL signals: {len(sells)}",
        f"Total signals: {len(results)}",
    ]
    for row in buys:
        body_lines.append(
            f"BUY | {row.get('symbol', '')} | Date={row.get('date', '')} | "
            f"Price={row.get('price', '')} | Supertrend={row.get('supertrend', '')} | "
            f"RSI={row.get('rsi', '')}"
        )
    for row in sells:
        body_lines.append(
            f"SELL | {row.get('symbol', '')} | Date={row.get('date', '')} | "
            f"Price={row.get('price', '')} | Supertrend={row.get('supertrend', '')} | "
            f"RSI={row.get('rsi', '')}"
        )

    html_rows = "".join(
        "<tr>"
        f"<td>{row.get('symbol', '')}</td>"
        f"<td>{row.get('side', '')}</td>"
        f"<td>{row.get('date', '')}</td>"
        f"<td>{row.get('price', '')}</td>"
        f"<td>{row.get('supertrend', '')}</td>"
        f"<td>{row.get('rsi', '')}</td>"  # Add RSI value
        "</tr>"
        for row in results if isinstance(row, dict)
    )
    html_body = (
        f"<h2>{subject}</h2>"
        f"<p>BUY signals: {len(buys)} | SELL signals: {len(sells)} | Total: {len(results)}</p>"
        "<table border='1' cellpadding='5' cellspacing='0'>"
        "<tr><th>Symbol</th><th>Side</th><th>Date</th><th>Price</th><th>Supertrend</th><th>RSI</th></tr>"
        f"{html_rows}</table>"
    )

    recipients = CONFIG.get("email_notifications", {}).get("scan_report_recipients")
    send_email_notification(
        subject,
        "\n".join(body_lines),
        attachment_path=attachment_path,
        html_body=html_body,
        recipients=recipients,
    )


def launch_script(script_name, config_key):
    """Launch a script in a separate process"""
    script_path = Path(__file__).parent / script_name
    
    if not script_path.exists():
        logger.error(f"[ERROR] Script not found: {script_path}")
        return None
    
    try:
        logger.info(f"[SELECTED] {config_key} selected for running")
        started_at = get_ist_time()
        run_log_path = build_temp_run_log_path(config_key, started_at)
        append_run_log(
            run_log_path,
            f"[{started_at.strftime('%Y-%m-%d %H:%M:%S %Z')}] [STARTED] {config_key} -> {script_name}",
        )
        
        # Build command with dry-run flag if enabled
        cmd = [sys.executable, str(script_path)]
        
        if CONFIG.get("dry_run"):
            cmd.append("--dry-run")
        
        # Start process with real-time output capture
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout
            cwd=Path(__file__).parent,
            bufsize=1,  # Line buffered
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        
        logger.info(f"[STARTED] {config_key} started (PID {process.pid})")

        run_context = {
            "process": process,
            "config_key": config_key,
            "script_name": script_name,
            "started_at": started_at,
            "run_log_path": run_log_path,
            "recent_output": deque(maxlen=25),
            "failure_signature": None,
            "failure_summary": None,
        }
        
        # Start output capture in a separate thread
        script_config = SCRIPTS_SCHEDULE.get(config_key, {})
        output_thread = threading.Thread(
            target=capture_script_output,
            args=(config_key, process, run_log_path, run_context, script_config),
            daemon=True
        )
        output_thread.start()

        run_context["output_thread"] = output_thread
        return run_context
    
    except Exception as e:
        logger.error(f"[ERROR] Failed to launch {script_name}: {e}")
        return None


def log_startup_status():
    """Log startup status once"""
    now = get_ist_time()
    cleanup_old_logs()
    logger.info("=" * 80)
    logger.info(f"[SCHEDULER] Started at {now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    logger.info(f"[MODE] {'DRY RUN' if CONFIG['dry_run'] else 'LIVE'}")
    logger.info(f"[SCRIPTS] {len(SCRIPTS_SCHEDULE)} configured")
    logger.info(f"[LOG_RETENTION] {CONFIG.get('log_retention_days', 2)} day(s)")
    email_enabled = CONFIG.get("email_notifications", {}).get("enabled", False)
    logger.info(f"[EMAIL] {'Enabled' if email_enabled else 'Disabled'}")
    logger.info("=" * 80)


def scheduler_loop():
    """Main scheduler loop - runs continuously"""
    log_startup_status()
    
    running_processes = {}  # {config_key: run_context}
    script_launch_logged = set()  # Track if we already logged for a script
    blocked_failures = {}  # {config_key: {signature, summary, blocked_at}}
    
    try:
        while True:
            now = get_ist_time()
            cleanup_old_logs()
            handle_process_state_changes(running_processes, script_launch_logged, blocked_failures)
            
            # Check each script
            for config_key, script_config in SCRIPTS_SCHEDULE.items():
                clear_failure_block_if_needed(config_key, script_config, blocked_failures)
                should_run = should_script_run_now(script_config)
                is_running = config_key in running_processes and running_processes[config_key]["process"].poll() is None
                
                if should_run and not is_running:
                    if config_key in blocked_failures:
                        continue

                    conflicting_script = get_conflicting_running_script(config_key, script_config, running_processes)
                    if conflicting_script is not None:
                        logger.info(f"[SKIP] {config_key} not started because {conflicting_script} is already running")
                        continue

                    # Script should run but isn't running - start it
                    
                    # Calculate delay for this script (to avoid rate limiting)
                    delay_sec = script_config.get("api_delay_seconds", 0)
                    if delay_sec > 0:
                        logger.info(f"[DELAY] Waiting {delay_sec}s before {config_key}...")
                        monitored_sleep(delay_sec, running_processes, script_launch_logged, blocked_failures)

                    handle_process_state_changes(running_processes, script_launch_logged, blocked_failures)
                    if config_key in blocked_failures:
                        continue
                    if config_key in running_processes and running_processes[config_key]["process"].poll() is None:
                        continue
                    conflicting_script = get_conflicting_running_script(config_key, script_config, running_processes)
                    if conflicting_script is not None:
                        logger.info(f"[SKIP] {config_key} not started because {conflicting_script} is already running")
                        continue
                    
                    # Launch the script
                    process = launch_script(
                        script_config["script_name"],
                        config_key
                    )
                    if process:
                        running_processes[config_key] = process
                        script_launch_logged.add(config_key)
                
                elif not should_run and is_running:
                    # Script shouldn't run but is still running - stop it
                    logger.info(f"[STOP] Stopping {config_key} (trading hours ended)")
                    stop_script(running_processes[config_key], script_config, reason="Trading window ended")
                    del running_processes[config_key]
                    script_launch_logged.discard(config_key)
                    blocked_failures.pop(config_key, None)
                
                elif not should_run and config_key in script_launch_logged:
                    # Just mark that we've logged the launch
                    script_launch_logged.discard(config_key)
                    blocked_failures.pop(config_key, None)
            
            handle_process_state_changes(running_processes, script_launch_logged, blocked_failures)
            
            # Sleep before next check
            monitored_sleep(CONFIG["check_interval_seconds"], running_processes, script_launch_logged, blocked_failures)
    
    except KeyboardInterrupt:
        logger.info("[STOP] Scheduler stopped by user")
        # Gracefully shutdown all running scripts
        for config_key, run_context in list(running_processes.items()):
            logger.info(f"[STOP] Terminating {config_key}...")
            stop_script(run_context, SCRIPTS_SCHEDULE[config_key], reason="Scheduler stopped by user")
        sys.exit(0)


# ============================================================================
# CLI and Configuration Management
# ============================================================================

def print_schedule():
    """Print the configured schedule in a readable format"""
    print("\n" + "=" * 80)
    print("[SCHEDULE] CONFIGURED SCHEDULE")
    print("=" * 80)
    
    for config_key, script_config in SCRIPTS_SCHEDULE.items():
        status = "[OK] ENABLED" if script_config.get("enabled") else "[ERROR] DISABLED"
        day_names = []
        for day in script_config.get("run_days", []):
            if isinstance(day, DayOfWeek):
                day_names.append(day.name)
            else:
                day_names.append(str(day))
        days = ", ".join(day_names)
        start = script_config.get("run_time_start", "N/A")
        end = script_config.get("run_time_end", "N/A")
        delay = script_config.get("api_delay_seconds", 0)
        days_of_month = script_config.get("run_days_of_month")
        
        print(f"\n{config_key:20} {status}")
        print(f"  Script:  {script_config.get('script_name')}")
        print(f"  Desc:    {script_config.get('description')}")
        print(f"  Days:    {days}")
        
        # Show day-of-month info if configured
        if days_of_month is not None:
            if isinstance(days_of_month, list) and len(days_of_month) > 0:
                # Show specific days of month
                print(f"  Of Month: Days {min(days_of_month)}-{max(days_of_month)}")
            else:
                print(f"  Of Month: Every day")
        else:
            print(f"  Of Month: Every day")
        
        print(f"  Time:    {start} - {end} IST")
        if delay > 0:
            print(f"  Delay:   {delay} seconds (before API call)")
    
    print("\n" + "=" * 80)
    print(f"Mode: {'DRY RUN' if CONFIG['dry_run'] else 'LIVE'}")
    email_cfg = CONFIG.get("email_notifications", {})
    print(f"Email: {'ENABLED' if email_cfg.get('enabled') else 'DISABLED'}")
    print("=" * 80 + "\n")


def modify_schedule(script_key, days=None, start_time=None, end_time=None, enabled=None):
    """Modify schedule for a specific script"""
    if script_key not in SCRIPTS_SCHEDULE:
        logger.error(f"Script '{script_key}' not found in schedule")
        return False
    
    config = SCRIPTS_SCHEDULE[script_key]
    
    if days is not None:
        config["run_days"] = days
        logger.info(f"Updated {script_key} run days: {[d.name for d in days]}")
    
    if start_time is not None:
        config["run_time_start"] = start_time
        logger.info(f"Updated {script_key} start time: {start_time}")
    
    if end_time is not None:
        config["run_time_end"] = end_time
        logger.info(f"Updated {script_key} end time: {end_time}")
    
    if enabled is not None:
        config["enabled"] = enabled
        logger.info(f"Updated {script_key} enabled: {enabled}")
    
    return True


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Central Scheduler for Stock Scanner Scripts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scheduler.py                    # Run in live mode
  python scheduler.py --dry-run         # Test without broker calls
  python scheduler.py --show-schedule    # Display configured schedule
  python scheduler.py --logs             # Show log directory
        """
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in dry-run mode (no broker requests sent)"
    )
    
    parser.add_argument(
        "--show-schedule",
        action="store_true",
        help="Display the current schedule and exit"
    )
    
    parser.add_argument(
        "--logs",
        action="store_true",
        help="Show log file location and exit"
    )
    
    parser.add_argument(
        "--disable-script",
        type=str,
        help="Disable a specific script (e.g., NIFTY)"
    )
    
    parser.add_argument(
        "--enable-script",
        type=str,
        help="Enable a specific script"
    )
    
    args = parser.parse_args()
    load_scheduler_config()
    
    # Handle CLI arguments
    if args.dry_run:
        CONFIG["dry_run"] = True
        logger.info("[DRY_RUN] DRY RUN MODE ENABLED - No broker requests will be sent")
    
    if args.show_schedule:
        print_schedule()
        sys.exit(0)
    
    if args.logs:
        print(f"\n[LOG_DIR] Log directory: {LOG_DIR}")
        print(f"[LOG_FILE] Current log: {log_file}\n")
        sys.exit(0)
    
    if args.disable_script:
        if args.disable_script in SCRIPTS_SCHEDULE:
            SCRIPTS_SCHEDULE[args.disable_script]["enabled"] = False
            logger.info(f"Disabled: {args.disable_script}")
        else:
            logger.error(f"Script not found: {args.disable_script}")
    
    if args.enable_script:
        if args.enable_script in SCRIPTS_SCHEDULE:
            SCRIPTS_SCHEDULE[args.enable_script]["enabled"] = True
            logger.info(f"Enabled: {args.enable_script}")
        else:
            logger.error(f"Script not found: {args.enable_script}")
    
    # Start the main scheduler
    scheduler_loop()
