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
import html
import tempfile
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
MONTHLY_REPORT_DIR = LOG_DIR / "monthly_trade_reports"
WEEKLY_REPORT_DIR = LOG_DIR / "weekly_trade_reports"
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
        "description": "NIFTY Intraday FNO Strategy with 15:15 IST square-off",
        "enabled": True,
        "run_days": [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, DayOfWeek.THURSDAY, DayOfWeek.FRIDAY],
        "run_time_start": "09:13",  # IST
        "run_time_end": "15:16",  # Give 15:15 square-off a minute to complete
        "api_delay_seconds": 00,  # Delay before starting this script (to space out API calls)
        "send_signal_emails": False,  # Per-signal emails disabled; daily reports remain enabled
    },
    
    "SENSEXINTRA": {
        "script_name": "SENSEXINTRA.py",
        "description": "SENSEX Intraday FNO Strategy with 15:15 IST square-off",
        "enabled": True,
        "run_days": [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, DayOfWeek.THURSDAY, DayOfWeek.FRIDAY],
        "run_days_of_month": None,  # Every day of month
        "run_time_start": "09:13",
        "run_time_end": "15:16",  # Give 15:15 square-off a minute to complete
        "api_delay_seconds": 00,  # Start 30 sec after NIFTY to avoid rate limit
        "send_signal_emails": False,  # Per-signal emails disabled; daily reports remain enabled
    },

    "320trade": {
        "script_name": "320trade.py",
        "description": "Expiry-day 3:20 ITM CE+PE Strategy",
        "enabled": False,
        "run_days": [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, DayOfWeek.THURSDAY, DayOfWeek.FRIDAY],
        "run_time_start": "15:15",
        "run_time_end": "15:30",
        "api_delay_seconds": 0,
        "send_signal_emails": True,  # Email every generated buy/sell signal
    },
    
    "OILMINI": {
        "script_name": "OILMINI.py",
        "description": "Crude Oil Mini Options Strategy",
        "enabled": True,
        "run_days": [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, 
                     DayOfWeek.THURSDAY, DayOfWeek.FRIDAY],
        "run_days_of_month": [],
        "run_time_start": "16:00",
        "run_time_end": "23:00",  # MCX trades longer
        "api_delay_seconds": 00,  # Start 60 sec after NIFTY
        "send_signal_emails": True,  # Email alerts on buy/sell signals
    },
    
    "NATGASMINI": {
        "script_name": "NATGASMINI.py",
        "description": "Natural Gas Mini Options Strategy",
        "enabled": True,
        "run_days": [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, 
                     DayOfWeek.THURSDAY, DayOfWeek.FRIDAY],
        "run_days_of_month": [],
        "run_time_start": "16:00",
        "run_time_end": "23:35",  # Allow the strategy to complete its 23:30 square-off
        "api_delay_seconds": 00,  # Start 90 sec after NIFTY
        "send_signal_emails": True,  # Email alerts on buy/sell signals
    },

    "SupperWeekScan": {
        "script_name": "SupperWeekScan.py",
        "description": "Weekly stock scanner using supertrend logic",
        "enabled": True,
        "run_days": [DayOfWeek.FRIDAY],
        "run_time_start": "23:00",
        "run_time_end": "23:30",
        "api_delay_seconds": 0,
        "send_signal_emails": True,
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
        "send_weekly_summary_on_monday": True,
        "weekly_summary_recipients": [],
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
    """Delete files in scheduler log directories older than the retention window."""
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
                    logger.error(f"[LOG] Failed to remove old file {entry.name}: {exc}")
        except FileNotFoundError:
            continue

    if deleted_count:
        logger.info(
            f"[LOG] Deleted {deleted_count} file(s) from scheduler log directories "
            f"older than {retention_days} day(s)"
        )


def send_email_notification(subject, body, attachment_path=None, html_body=None, recipients=None):
    """Send an email notification if SMTP is configured."""
    email_cfg = CONFIG.get("email_notifications", {})
    if not email_cfg.get("enabled", False):
        return False

    recipients = recipients if recipients is not None else email_cfg.get("to_emails")
    recipients = [recipients] if isinstance(recipients, str) else list(recipients or [])
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
        message.add_alternative(html_body, subtype="html")

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
    if str(run_context.get("config_key") or "").upper() == "320TRADE" and exit_code == 0:
        return "strategy_complete", "320trade completed normally", False

    recent_output = [line.strip() for line in run_context.get("recent_output", []) if str(line).strip()]
    normalized_output = "\n".join(recent_output[-10:]).lower()
    if (
        str(run_context.get("config_key") or "").upper() in {"NIFTYINTRA", "SENSEXINTRA"}
        and exit_code == 0
        and "[strategy complete] all open positions closed at 15:15 ist" in normalized_output
    ):
        return "strategy_complete", "Intraday positions closed at 15:15 IST", False

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
        failure_signature, failure_summary, is_fatal = classify_process_exit(run_context, returncode)
        run_context["failure_signature"] = failure_signature
        run_context["failure_summary"] = failure_summary
        completed_normally = failure_signature == "strategy_complete"
        if completed_normally:
            logger.info(f"[COMPLETED] {config_key} finished normally")
        else:
            logger.error(f"[CRASH] {config_key} crashed (exit code: {returncode})")
        stop_script(
            run_context,
            SCRIPTS_SCHEDULE[config_key],
            reason="Strategy completed normally" if completed_normally else "Process exited unexpectedly",
            terminate_process=False,
        )
        del running_processes[config_key]
        script_launch_logged.discard(config_key)

        if completed_normally:
            blocked_failures[config_key] = {
                "signature": failure_signature,
                "summary": failure_summary,
                "blocked_at": get_ist_time(),
            }
            continue

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


def seconds_until_next_script_start(now=None):
    now = now or get_ist_time()
    delays = []
    for script_config in SCRIPTS_SCHEDULE.values():
        if not script_config.get("enabled", True):
            continue
        allowed_days = [
            day for day in (_normalize_run_day(item) for item in script_config["run_days"])
            if day is not None
        ] if script_config.get("run_days") else []
        if allowed_days and now.weekday() not in allowed_days:
            continue
        run_days_of_month = script_config.get("run_days_of_month")
        if run_days_of_month is not None and now.day not in run_days_of_month:
            continue
        start_time = parse_time(script_config.get("run_time_start", "00:00"))
        start_at = datetime.combine(now.date(), start_time, tzinfo=now.tzinfo)
        delay = (start_at - now).total_seconds()
        if delay > 0:
            delays.append(delay)
    return min(delays) if delays else None


TRADE_SIGNAL_FIELDS = (
    "Script", "Side", "Time", "Symbol", "Strike", "Price", "Reason",
    "Lots", "LotSize", "Qty", "BuyAt", "SellAt", "PnL", "OrderPlaced",
)
TRADE_SIGNAL_FIELD_PATTERN = re.compile(
    r"(?:^|\s)(" + "|".join(TRADE_SIGNAL_FIELDS) + r")="
)
PRICE_MILESTONE_FIELDS = (
    "Script", "Time", "Symbol", "Side", "Entry", "Price", "Move", "Direction",
)
PRICE_MILESTONE_FIELD_PATTERN = re.compile(
    r"(?:^|\s)(" + "|".join(PRICE_MILESTONE_FIELDS) + r")="
)
TRADE_REPORT_COLUMNS = (
    "Script", "Side", "Time", "Symbol", "Strike", "Price", "Reason", "Lots",
    "Lot Size", "Qty", "Order Placed",
)
MONTHLY_REPORT_SHEETS = {
    "NIFTYINTRA": "NIFTY",
    "SENSEXINTRA": "SENSEX",
    "NATGASMINI": "GAS",
    "OILMINI": "OIL",
}
TRADE_REPORT_LOCK = threading.Lock()


def _initialize_trade_report_sheet(workbook, sheet=None, title="Trades"):
    sheet = sheet or workbook.active
    sheet.title = title
    sheet.append((*TRADE_REPORT_COLUMNS, "Event Key"))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    for index, width in enumerate((18, 12, 24, 36, 12, 14, 48, 12, 12, 12, 16), start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    sheet.column_dimensions[sheet.cell(row=1, column=len(TRADE_REPORT_COLUMNS) + 1).column_letter].hidden = True
    return sheet


def _trade_report_values(trade):
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
    return row


def _save_trade_report(workbook, report_path):
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{report_path.stem}.",
            suffix=".xlsx",
            dir=report_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        workbook.save(temporary_path)
        os.replace(temporary_path, report_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


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
        "Buy Price": fields.get("BuyAt", ""),
        "Sell Price": fields.get("SellAt", ""),
        "PnL": fields.get("PnL", ""),
        "Order Placed": fields["OrderPlaced"].upper(),
    }


def parse_price_milestone(line):
    """Parse one structured price-milestone event from script output."""
    if "[PRICE MILESTONE]" not in line:
        return None

    matches = list(PRICE_MILESTONE_FIELD_PATTERN.finditer(line))
    fields = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        fields[match.group(1)] = line[start:end].strip()

    if any(not fields.get(field) for field in PRICE_MILESTONE_FIELDS):
        return None
    return fields


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
        workbook = load_workbook(report_path) if report_path.exists() else Workbook()
        try:
            if "Trades" in workbook.sheetnames:
                sheet = workbook["Trades"]
                headers = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
                if not any(headers):
                    _initialize_trade_report_sheet(workbook, sheet)
                elif headers[:len(TRADE_REPORT_COLUMNS)] != TRADE_REPORT_COLUMNS:
                    raise ValueError(f"Unexpected trade report columns in {report_path}")
                elif len(headers) < len(TRADE_REPORT_COLUMNS) + 1:
                    sheet.cell(row=1, column=len(TRADE_REPORT_COLUMNS) + 1, value="Event Key")
                    sheet.column_dimensions[
                        sheet.cell(row=1, column=len(TRADE_REPORT_COLUMNS) + 1).column_letter
                    ].hidden = True
            else:
                sheet = _initialize_trade_report_sheet(workbook, workbook.create_sheet("Trades"))

            key_column = len(TRADE_REPORT_COLUMNS) + 1
            if any(
                row[0].value == event_key
                for row in sheet.iter_rows(min_row=2, min_col=key_column, max_col=key_column)
            ):
                return report_path, False

            sheet.append(tuple(_trade_report_values(trade)) + (event_key,))
            sheet.auto_filter.ref = sheet.dimensions
            _save_trade_report(workbook, report_path)
        finally:
            workbook.close()
    return report_path, True


def _monthly_trade_report_path(trade_time):
    try:
        trade_date = datetime.fromisoformat(trade_time.replace("Z", "+00:00"))
    except ValueError:
        trade_date = get_ist_time()
    month_label = trade_date.strftime("%b").upper()
    return MONTHLY_REPORT_DIR / f"NIFTY_{month_label}.xlsx"


def _create_monthly_trade_report():
    workbook = Workbook()
    for index, sheet_name in enumerate(MONTHLY_REPORT_SHEETS.values()):
        sheet = workbook.active if index == 0 else workbook.create_sheet()
        _initialize_trade_report_sheet(workbook, sheet, title=sheet_name)
    return workbook


def append_trade_to_monthly_report(config_key, trade):
    """Append a unique market signal to the persistent workbook for its month."""
    sheet_name = MONTHLY_REPORT_SHEETS.get(str(config_key).upper())
    if sheet_name is None:
        return None, False

    event_data = json.dumps(trade, sort_keys=True, separators=(",", ":"))
    event_key = hashlib.sha256(event_data.encode("utf-8")).hexdigest()
    report_path = _monthly_trade_report_path(trade["Time"])
    with TRADE_REPORT_LOCK:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        workbook = load_workbook(report_path) if report_path.exists() else _create_monthly_trade_report()
        try:
            if sheet_name not in workbook.sheetnames:
                sheet = _initialize_trade_report_sheet(
                    workbook, workbook.create_sheet(), title=sheet_name
                )
            else:
                sheet = workbook[sheet_name]
                headers = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
                if not any(headers):
                    _initialize_trade_report_sheet(workbook, sheet, title=sheet_name)
                elif headers[:len(TRADE_REPORT_COLUMNS)] != TRADE_REPORT_COLUMNS:
                    raise ValueError(f"Unexpected trade report columns in {report_path}")
                elif len(headers) < len(TRADE_REPORT_COLUMNS) + 1:
                    key_cell = sheet.cell(row=1, column=len(TRADE_REPORT_COLUMNS) + 1, value="Event Key")
                    key_cell.font = Font(bold=True)
                    sheet.column_dimensions[key_cell.column_letter].hidden = True

            key_column = len(TRADE_REPORT_COLUMNS) + 1
            if any(
                row[0].value == event_key
                for row in sheet.iter_rows(min_row=2, min_col=key_column, max_col=key_column)
            ):
                return report_path, False

            sheet.append(tuple(_trade_report_values(trade)) + (event_key,))
            sheet.auto_filter.ref = sheet.dimensions
            _save_trade_report(workbook, report_path)
        finally:
            workbook.close()
    return report_path, True


def _trade_report_for_run(run_context):
    trade_date = run_context["started_at"].strftime("%Y%m%d")
    return RUN_LOG_DIR / f"trades_{run_context['config_key']}_{trade_date}.xlsx"


def _ensure_trade_report_exists(report_path):
    with TRADE_REPORT_LOCK:
        if report_path.exists():
            return
        workbook = Workbook()
        try:
            _initialize_trade_report_sheet(workbook)
            _save_trade_report(workbook, report_path)
        finally:
            workbook.close()


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


def _trade_report_rows(report_path, sheet_name="Trades"):
    workbook = load_workbook(report_path, read_only=True, data_only=True)
    try:
        if sheet_name not in workbook.sheetnames:
            return []
        sheet = workbook[sheet_name]
        headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
        indices = {name: headers.index(name) for name in TRADE_REPORT_COLUMNS}
        return [
            {name: row[indices[name]] for name in TRADE_REPORT_COLUMNS}
            for row in sheet.iter_rows(min_row=2, values_only=True)
            if any(value is not None for value in row[:len(TRADE_REPORT_COLUMNS)])
        ]
    finally:
        workbook.close()


def _trade_rows_html(rows):
    header_cells = "".join(f"<th>{html.escape(column)}</th>" for column in TRADE_REPORT_COLUMNS)
    if not rows:
        return "<p>No trade signals were recorded.</p>"
    table_rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(row.get(column) if row.get(column) is not None else ''))}</td>"
            for column in TRADE_REPORT_COLUMNS
        )
        + "</tr>"
        for row in rows
    )
    return (
        "<table border='1' cellpadding='6' cellspacing='0'><thead><tr>"
        + header_cells
        + "</tr></thead><tbody>"
        + table_rows
        + "</tbody></table>"
    )


def _trade_rows_text(rows):
    if not rows:
        return "No trade signals were recorded."
    return "\n".join(
        [
            " | ".join(TRADE_REPORT_COLUMNS),
            *(
                " | ".join(
                    str(row.get(column) if row.get(column) is not None else "")
                    for column in TRADE_REPORT_COLUMNS
                )
                for row in rows
            ),
        ]
    )


def _completed_trade_outcomes(report_path):
    workbook = load_workbook(report_path, read_only=True, data_only=True)
    try:
        sheet = workbook["Trades"]
        headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
        indices = {name: headers.index(name) for name in TRADE_REPORT_COLUMNS}
        open_buys = {}
        outcomes = []
        for values in sheet.iter_rows(min_row=2, values_only=True):
            side = str(values[indices["Side"]] or "").upper()
            symbol = str(values[indices["Symbol"]] or "")
            if side not in {"BUY", "SELL"} or not symbol:
                continue
            try:
                price = float(values[indices["Price"]])
                quantity = float(values[indices["Qty"]] or 0)
            except (TypeError, ValueError):
                continue
            if side == "BUY":
                open_buys.setdefault(symbol, []).append((price, quantity))
                continue
            buys = open_buys.get(symbol, [])
            remaining_sell_quantity = quantity
            while buys and remaining_sell_quantity > 0:
                buy_price, buy_quantity = buys.pop(0)
                matched_quantity = min(buy_quantity, remaining_sell_quantity)
                outcomes.append((price - buy_price) * matched_quantity)
                remaining_sell_quantity -= matched_quantity
                if buy_quantity > matched_quantity:
                    buys.insert(0, (buy_price, buy_quantity - matched_quantity))
        return outcomes
    finally:
        workbook.close()


def _320trade_outcome_rows(report_path):
    workbook = load_workbook(report_path, read_only=True, data_only=True)
    try:
        sheet = workbook["Trades"]
        headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
        indices = {name: headers.index(name) for name in TRADE_REPORT_COLUMNS}
        legs = {}
        for values in sheet.iter_rows(min_row=2, values_only=True):
            row = {name: values[index] for name, index in indices.items()}
            symbol = str(row["Symbol"] or "")
            side = str(row["Side"] or "").upper()
            if not symbol or side not in {"BUY", "SELL"}:
                continue
            legs.setdefault(symbol, {})[side] = row

        outcomes = []
        for symbol, events in sorted(legs.items()):
            buy = events.get("BUY")
            sell = events.get("SELL")
            if not buy or not sell:
                continue
            try:
                buy_price = float(buy["Price"])
                sell_price = float(sell["Price"])
                quantity = float(buy["Qty"] or sell["Qty"] or 0)
            except (TypeError, ValueError):
                continue
            outcomes.append({
                "symbol": symbol,
                "strike": buy["Strike"] or sell["Strike"] or "",
                "buy_price": buy_price,
                "sell_time": sell["Time"] or "",
                "sell_price": sell_price,
                "quantity": quantity,
                "pnl": (sell_price - buy_price) * quantity,
                "order_placed": (
                    str(buy["Order Placed"] or "").upper() == "YES"
                    and str(sell["Order Placed"] or "").upper() == "YES"
                ),
            })
        return outcomes
    finally:
        workbook.close()


def _320trade_summary_email_content(run_context, reason, report_path, counts):
    outcomes = _320trade_outcome_rows(report_path)
    trading_date = run_context["started_at"].strftime("%Y-%m-%d")
    total, buys, sells, orders_placed = counts
    text = [
        "320trade completed",
        f"Trading date: {trading_date}",
        f"Stop reason: {reason}",
        f"Trade signals: {total} (buys: {buys}, sells: {sells}, orders placed: {orders_placed})",
    ]
    if not outcomes:
        text.append("No completed CE/PE trade outcomes were recorded.")
        return "\n".join(text), None

    columns = (
        "Strike", "Buy Price", "Sell Price", "Sell Time", "Quantity", "PnL",
        "Order Placed",
    )
    text.append("")
    text.append(" | ".join(columns))
    text.append(" | ".join("-" * len(column) for column in columns))
    for row in outcomes:
        text.append(
            f"{row['strike']} | {row['buy_price']:.2f} | {row['sell_price']:.2f} | "
            f"{row['sell_time']} | {row['quantity']:g} | {row['pnl']:.2f} | "
            f"{'YES' if row['order_placed'] else 'NO'}"
        )
    text.append(f"Total gross P&L (before charges): {sum(row['pnl'] for row in outcomes):.2f}")

    table_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['strike']))}</td>"
        f"<td>{row['buy_price']:.2f}</td>"
        f"<td>{row['sell_price']:.2f}</td>"
        f"<td>{html.escape(str(row['sell_time']))}</td>"
        f"<td>{row['quantity']:g}</td>"
        f"<td>{row['pnl']:.2f}</td>"
        f"<td>{'YES' if row['order_placed'] else 'NO'}</td>"
        "</tr>"
        for row in outcomes
    )
    html_body = (
        "<html><body><h2>320trade completed</h2>"
        f"<p>Trading date: {html.escape(trading_date)}<br>"
        f"Stop reason: {html.escape(str(reason))}</p>"
        f"<table border='1' cellpadding='6' cellspacing='0'><thead><tr>"
        + "".join(f"<th>{html.escape(column)}</th>" for column in columns)
        + "</tr></thead><tbody>"
        + table_rows
        + "</tbody></table>"
        f"<p><strong>Total gross P&amp;L (before charges): {sum(row['pnl'] for row in outcomes):.2f}</strong></p>"
        f"<p>Signals: {total} (buys: {buys}, sells: {sells}, orders placed: {orders_placed})</p>"
        "</body></html>"
    )
    return "\n".join(text), html_body


def email_daily_trade_summary(run_context, reason):
    """Send one daily summary when a trading script stops."""
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
    script_config = SCRIPTS_SCHEDULE.get(run_context.get("config_key"), {})
    recipients = script_config.get("email_recipients")
    if str(run_context.get("config_key") or "").upper() == "320TRADE":
        body, html_body = _320trade_summary_email_content(
            run_context, reason, report_path, (total, buys, sells, orders_placed)
        )
        send_email_notification(
            subject,
            body,
            html_body=html_body,
            recipients=recipients,
        )
        return

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
    html_sections = [
        f"<html><body><h2>{html.escape(subject)}</h2>"
        f"<p>{html.escape(body).replace(chr(10), '<br>')}</p>"
        "<h3>Trade details</h3>"
        + _trade_rows_html(_trade_report_rows(report_path))
    ]
    if str(run_context.get("config_key") or "").upper() in {
        "NIFTYINTRA",
        "SENSEXINTRA",
        "NATGASMINI",
    }:
        try:
            outcomes = _completed_trade_outcomes(report_path)
            wins = sum(pnl > 0 for pnl in outcomes)
            losses = sum(pnl < 0 for pnl in outcomes)
            body += (
                f"\nCompleted paper/live trades: {len(outcomes)}"
                f"\nWinning trades: {wins}"
                f"\nLosing trades: {losses}"
                f"\nGross realized P&L (before charges): {sum(outcomes):.2f}"
            )
            html_sections.append(
                f"<p>Completed paper/live trades: {len(outcomes)}<br>"
                f"Winning trades: {wins}<br>Losing trades: {losses}<br>"
                f"Gross realized P&amp;L (before charges): {sum(outcomes):.2f}</p>"
            )
        except Exception as exc:
            logger.error(f"[REPORT] Failed to calculate completed outcomes for {run_context['config_key']}: {exc}")

    html_sections.append("</body></html>")
    attachment_path = None if str(run_context.get("config_key") or "").upper() in {"NIFTYINTRA", "SENSEXINTRA"} else report_path
    send_email_notification(
        subject,
        body + "\n\nTrade details:\n" + _trade_rows_text(_trade_report_rows(report_path)),
        attachment_path=attachment_path,
        html_body="".join(html_sections),
        recipients=recipients,
    )


def _week_trade_date(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _weekly_trade_report_path(week_start, week_end):
    return WEEKLY_REPORT_DIR / (
        f"Weekly_{week_start:%Y%m%d}_{week_end:%Y%m%d}.xlsx"
    )


def _create_weekly_trade_report(week_start, week_end):
    workbook = Workbook()
    sheet_names = list(MONTHLY_REPORT_SHEETS.values())
    for index, sheet_name in enumerate(sheet_names):
        sheet = workbook.active if index == 0 else workbook.create_sheet()
        _initialize_trade_report_sheet(workbook, sheet, title=sheet_name)

    month_paths = {
        _monthly_trade_report_path(datetime.combine(day, dt_time.min).isoformat())
        for day_offset in range((week_end - week_start).days + 1)
        for day in [week_start + timedelta(days=day_offset)]
    }
    weekly_rows = {sheet_name: [] for sheet_name in sheet_names}
    for source_path in sorted(month_paths):
        if not source_path.exists():
            continue
        for sheet_name in sheet_names:
            for row in _trade_report_rows(source_path, sheet_name):
                trade_date = _week_trade_date(row.get("Time"))
                if trade_date is not None and week_start <= trade_date <= week_end:
                    weekly_rows[sheet_name].append(row)

    for sheet_name, rows in weekly_rows.items():
        rows.sort(key=lambda row: str(row.get("Time") or ""))
        sheet = workbook[sheet_name]
        for row in rows:
            sheet.append(tuple(row.get(column) for column in TRADE_REPORT_COLUMNS) + ("",))
        sheet.auto_filter.ref = sheet.dimensions

    return workbook, weekly_rows


def _weekly_summary_due(now, running_processes):
    if now.weekday() != DayOfWeek.MONDAY.value:
        return False

    return not any(
        config_key.upper() in MONTHLY_REPORT_SHEETS
        and run_context["process"].poll() is None
        for config_key, run_context in running_processes.items()
    )


def email_weekly_trade_summary(now=None, running_processes=None):
    """Email the previous Monday-through-Sunday trade report on Monday."""
    email_cfg = CONFIG.get("email_notifications", {})
    if (
        not email_cfg.get("enabled", False)
        or not email_cfg.get("send_weekly_summary_on_monday", True)
    ):
        return

    now = now or get_ist_time()
    running_processes = running_processes or {}
    if not _weekly_summary_due(now, running_processes):
        return

    week_start = (now - timedelta(days=now.weekday() + 7)).date()
    week_end = week_start + timedelta(days=6)
    week_key = week_start.strftime("%Y%m%d")
    WEEKLY_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    marker_path = WEEKLY_REPORT_DIR / f"summary_sent_{week_key}.txt"
    if marker_path.exists():
        return

    report_path = _weekly_trade_report_path(week_start, week_end)
    try:
        workbook, market_rows = _create_weekly_trade_report(week_start, week_end)
        try:
            _save_trade_report(workbook, report_path)
        finally:
            workbook.close()
    except Exception as exc:
        logger.error(f"[REPORT] Failed to prepare weekly summary for {week_key}: {exc}")
        return

    all_rows = [row for rows in market_rows.values() for row in rows]
    buys = sum(str(row.get("Side") or "").upper() == "BUY" for row in all_rows)
    sells = sum(str(row.get("Side") or "").upper() == "SELL" for row in all_rows)
    orders_placed = sum(
        str(row.get("Order Placed") or "").upper() == "YES" for row in all_rows
    )
    subject = f"[WEEKLY SUMMARY] {week_start:%Y-%m-%d} to {week_end:%Y-%m-%d}"
    body = (
        f"Weekly trade summary for {week_start:%Y-%m-%d} to {week_end:%Y-%m-%d}\n"
        f"Total signals: {len(all_rows)} | Buy signals: {buys} | "
        f"Sell signals: {sells} | Orders placed: {orders_placed}\n"
        "The attached Excel workbook contains the complete week's buy and sell signals, "
        "including whether each order was placed."
    )

    if send_email_notification(
        subject,
        body,
        attachment_path=report_path,
        recipients=email_cfg.get("weekly_summary_recipients"),
    ):
        try:
            marker_path.write_text(now.isoformat(), encoding="utf-8")
        except OSError as exc:
            logger.error(f"[REPORT] Failed to mark weekly summary {week_key} as sent: {exc}")


def email_trade_signal(config_key, trade, recipients=None):
    """Email one trade event with its available execution details."""
    side = trade.get("Side") or "UNKNOWN"
    symbol = trade.get("Symbol") or "UNKNOWN"
    trade_time = trade.get("Time") or "UNKNOWN"
    subject = f"[TRADE] {config_key} {side} {symbol} at {trade_time}"
    details = (
        ("Script", config_key),
        ("Action", side),
        ("Time", trade_time),
        ("Symbol", symbol),
        ("Strike", trade.get("Strike") or "N/A"),
        ("Price", trade.get("Price") or "N/A"),
        ("Buy price", trade.get("Buy Price") or "N/A"),
        ("Sell price", trade.get("Sell Price") or "N/A"),
        ("PnL", trade.get("PnL") or "N/A"),
        ("Quantity", trade.get("Qty") or "N/A"),
        ("Reason", trade.get("Reason") or "N/A"),
        ("Order placed", trade.get("Order Placed") or "UNKNOWN"),
    )
    body = "Trade order event\n\n" + "\n".join(
        f"{label}: {value}" for label, value in details
    )
    rows = "".join(
        "<tr>"
        f"<th align='left'>{html.escape(label)}</th>"
        f"<td>{html.escape(str(value))}</td>"
        "</tr>"
        for label, value in details
    )
    html_body = f"<html><body><h2>Trade order event</h2><table>{rows}</table></body></html>"
    send_email_notification(subject, body, html_body=html_body, recipients=recipients)


def process_trade_signal(config_key, output_line, script_config=None):
    """Record each unique trade signal and optionally email it immediately."""
    trade = parse_trade_signal(output_line)
    if trade is None:
        return
    is_new = True
    try:
        _, is_new = append_trade_to_report(config_key, trade)
    except Exception as exc:
        logger.error(f"[REPORT] Failed to record trade signal for {config_key}: {exc}")
    try:
        append_trade_to_monthly_report(config_key, trade)
    except Exception as exc:
        logger.error(f"[REPORT] Failed to record monthly trade signal for {config_key}: {exc}")
    send_on_signals = CONFIG.get("email_notifications", {}).get("send_on_signals", False)
    script_emails_enabled = not script_config or script_config.get("send_signal_emails", True)
    if is_new and send_on_signals and script_emails_enabled:
        recipients = script_config.get("email_recipients") if script_config else None
        email_trade_signal(config_key, trade, recipients=recipients)


def process_price_milestone(config_key, output_line, script_config=None):
    """Email a price milestone emitted by a configured strategy."""
    milestone = parse_price_milestone(output_line)
    if milestone is None:
        return

    send_on_signals = CONFIG.get("email_notifications", {}).get("send_on_signals", False)
    script_emails_enabled = not script_config or script_config.get("send_signal_emails", True)
    if not send_on_signals or not script_emails_enabled:
        return

    subject = (
        f"[PRICE MOVE] {config_key} {milestone['Symbol']} "
        f"{milestone['Move']} points ({milestone['Direction']})"
    )
    body = (
        "Option price milestone\n\n"
        f"Script: {config_key}\n"
        f"Time: {milestone['Time']}\n"
        f"Symbol: {milestone['Symbol']}\n"
        f"Side: {milestone['Side']}\n"
        f"Entry price: {milestone['Entry']}\n"
        f"Current price: {milestone['Price']}\n"
        f"Move from entry: {milestone['Move']} points ({milestone['Direction']})\n"
    )
    recipients = script_config.get("email_recipients") if script_config else None
    send_email_notification(subject, body, recipients=recipients)


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
    except Exception as e:
        logger.error(f"[{config_key}] Error reading output: {e}")


def launch_script(script_name, config_key):
    """Launch a script in a separate process"""
    script_path = Path(__file__).parent / script_name
    script_config = SCRIPTS_SCHEDULE.get(config_key, {})
    
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
            email_weekly_trade_summary(now=get_ist_time(), running_processes=running_processes)
            
            # Sleep before next check
            sleep_seconds = float(CONFIG["check_interval_seconds"])
            next_start_delay = seconds_until_next_script_start()
            if next_start_delay is not None:
                sleep_seconds = min(sleep_seconds, next_start_delay)
            monitored_sleep(sleep_seconds, running_processes, script_launch_logged, blocked_failures)
    
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
