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
from collections import deque
from datetime import datetime, timedelta, time as dt_time, timezone
from email.message import EmailMessage
from pathlib import Path
from enum import Enum

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
    },
    
    "OILMINI": {
        "script_name": "OILMINI.py",
        "description": "Crude Oil Mini Options Strategy",
        "enabled": True,
        "run_days": [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, 
                     DayOfWeek.THURSDAY, DayOfWeek.FRIDAY],
        "run_days_of_month": list(range(9, 21)),  # 9th to 20th of every month
        "run_time_start": "16:00",
        "run_time_end": "23:30",  # MCX trades longer
        "api_delay_seconds": 60,  # Start 60 sec after NIFTY
    },
    
    "NATGASMINI": {
        "script_name": "NATGASMINI.py",
        "description": "Natural Gas Mini Options Strategy",
        "enabled": True,
        "run_days": [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, 
                     DayOfWeek.THURSDAY, DayOfWeek.FRIDAY],
        "run_days_of_month": None,  # Every day of the month
        "run_time_start": "16:30",
        "run_time_end": "23:30",  # MCX trades longer
        "api_delay_seconds": 90,  # Start 90 sec after NIFTY
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
    "restart_crashed_scripts": True,  # Auto-restart if script crashes
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
        "attach_log_on_stop": True,
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
            _merge_dict(SCRIPTS_SCHEDULE[script_key], overrides)

    logger.info(f"[CONFIG] Loaded scheduler settings from {CONFIG_FILE.name}")


def parse_time(time_str):
    """Parse HH:MM format to time object"""
    return datetime.strptime(time_str, "%H:%M").time()


def _format_date_component(ts):
    return ts.strftime("%d%b")


def _format_time_component(ts):
    return ts.strftime("%H%M").lstrip("0") or "0"


def build_temp_run_log_path(config_key, started_at):
    """Create a temporary log path until the script ends and we know the stop time."""
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


def send_email_notification(subject, body, attachment_path=None):
    """Send an email notification if SMTP is configured."""
    email_cfg = CONFIG.get("email_notifications", {})
    if not email_cfg.get("enabled", False):
        return False

    recipients = email_cfg.get("to_emails") or []
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

    if attachment_path is not None:
        attachment = Path(attachment_path)
        if attachment.exists():
            with attachment.open("rb") as file_handle:
                message.add_attachment(
                    file_handle.read(),
                    maintype="text",
                    subtype="plain",
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


def send_script_start_email(config_key, script_config, started_at, log_path):
    """Send the start notification for a script run."""
    subject = f"[{config_key}] started at {_format_time_component(started_at)} IST"
    body = (
        f"Script started\n"
        f"Script: {config_key}\n"
        f"File: {script_config.get('script_name')}\n"
        f"Started: {started_at.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
        f"Configured window: {script_config.get('run_time_start')} - {script_config.get('run_time_end')} IST\n"
        f"Run log: {Path(log_path).name}"
    )
    send_email_notification(subject, body)


def finalize_run_log(run_context, ended_at, reason, exit_code=None):
    """Finalize a per-script run log by appending stop status and renaming it."""
    run_log_path = Path(run_context["run_log_path"])
    append_run_log(
        run_log_path,
        f"[{ended_at.strftime('%Y-%m-%d %H:%M:%S %Z')}] [STOPPED] Reason={reason} ExitCode={exit_code}",
    )
    final_path = _dedupe_path(build_final_run_log_path(run_context["config_key"], run_context["started_at"], ended_at))
    try:
        run_log_path.rename(final_path)
        run_context["run_log_path"] = final_path
    except Exception as exc:
        logger.error(f"[LOG] Failed to finalize run log for {run_context['config_key']}: {exc}")
    return Path(run_context["run_log_path"])


def send_script_stop_email(run_context, script_config, ended_at, reason, exit_code=None):
    """Send the stop notification with the final log attached."""
    attach_log = CONFIG.get("email_notifications", {}).get("attach_log_on_stop", True)
    attachment_path = run_context.get("run_log_path") if attach_log else None
    status_word = "crashed" if reason == "Process exited unexpectedly" else "stopped"
    failure_summary = run_context.get("failure_summary")
    subject = f"[{run_context['config_key']}] {status_word} at {_format_time_component(ended_at)} IST"
    body = (
        f"Script {status_word}\n"
        f"Script: {run_context['config_key']}\n"
        f"File: {script_config.get('script_name')}\n"
        f"Started: {run_context['started_at'].strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
        f"Ended: {ended_at.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
        f"Reason: {reason}\n"
        f"Exit code: {exit_code}\n"
        f"Summary: {failure_summary or 'n/a'}\n"
        f"Log file: {Path(attachment_path).name if attachment_path else 'not attached'}"
    )
    send_email_notification(subject, body, attachment_path=attachment_path)


def stop_script(run_context, script_config, reason, terminate_process=True):
    """Stop a running script, finalize its log, and send the stop email."""
    process = run_context["process"]

    if terminate_process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    ended_at = get_ist_time()
    exit_code = process.poll()
    final_log_path = finalize_run_log(run_context, ended_at, reason, exit_code=exit_code)
    logger.info(f"[STOPPED] {run_context['config_key']} stopped | Reason={reason} | ExitCode={exit_code} | Log={final_log_path.name}")
    send_script_stop_email(run_context, script_config, ended_at, reason, exit_code=exit_code)
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
                send_script_start_email(
                    config_key,
                    SCRIPTS_SCHEDULE[config_key],
                    relaunched["started_at"],
                    relaunched["run_log_path"],
                )


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


def capture_script_output(config_key, process, run_log_path, run_context):
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
    except Exception as e:
        logger.error(f"[{config_key}] Error reading output: {e}")


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
        output_thread = threading.Thread(
            target=capture_script_output,
            args=(config_key, process, run_log_path, run_context),
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
                    
                    # Launch the script
                    process = launch_script(
                        script_config["script_name"],
                        config_key
                    )
                    if process:
                        running_processes[config_key] = process
                        script_launch_logged.add(config_key)
                        send_script_start_email(config_key, script_config, process["started_at"], process["run_log_path"])
                
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
