# NewAgeTrading AWS Installation Guide

This guide explains how to run the trading scripts from the GitHub repository on an AWS EC2 Ubuntu server.

Repository:

```text
https://github.com/prajwalrepo/NewAgeTrading.git
```

Strategies currently available:

- `NATGASMINI.py`
- `OILMINI.py`
- `NIFTYIntra.py`
- `SENSEXINTRA.py`

## 1. Before going live

The current Python files contain Groww API credentials in `CONFIG`. Those credentials have been exposed in source/chat and should be revoked and regenerated before live trading.

Do not commit API keys to GitHub. The recommended approach is:

1. Create new Groww API credentials.
2. Change each script to read credentials from environment variables:

```python
"api_key": os.getenv("GROWW_API_KEY", ""),
"api_secret": os.getenv("GROWW_API_SECRET", ""),
```

3. Use the same change in all four strategy files.
4. Store the values only in the private environment file described below.

The daily realized profit/loss entry lock discussed separately is not currently present in all four scripts. Do not assume that AWS installation adds that risk control; verify that feature before live trading.

## 2. Create the EC2 server

In AWS Console:

1. Open **EC2** and choose **Launch instance**.
2. Name it `newage-trading`.
3. Select **Ubuntu Server 24.04 LTS**.
4. Use at least a `t3.small` instance for these scripts.
5. Create or select an SSH key pair and download the `.pem` file.
6. In the security group, allow:
   - SSH TCP port `22` only from your own IP address.
   - No inbound trading/API port is required.
7. Launch the instance.
8. Copy its public IPv4 address.

Keep the EC2 instance private. Never expose the trading process through a web port.

## 3. Connect to Ubuntu

### Option A: Browser connection

In the EC2 console:

1. Select the instance.
2. Choose **Connect**.
3. Choose **EC2 Instance Connect**.
4. Choose **Connect**.

A black terminal is expected. When a prompt like this appears, the server is ready:

```text
ubuntu@ip-172-31-xx-xx:~$
```

### Option B: Local SSH

On your own computer:

```bash
chmod 400 newage-trading.pem
ssh -i newage-trading.pem ubuntu@YOUR_EC2_PUBLIC_IP
```

Replace `YOUR_EC2_PUBLIC_IP` with the address shown in AWS.

## 4. Install the project

Run these commands inside the EC2 terminal:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3 python3-venv python3-pip

git clone https://github.com/prajwalrepo/NewAgeTrading.git
cd NewAgeTrading

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If the GitHub repository is private, authenticate Git first or use a deploy key. Do not place a GitHub password or token in a shell command that may be saved in history.

## 5. Configure Groww credentials securely

Create a protected directory and environment file:

```bash
sudo mkdir -p /etc/newage-trading
sudo nano /etc/newage-trading/groww.env
```

Add the new credentials:

```text
GROWW_API_KEY=replace_with_new_api_key
GROWW_API_SECRET=replace_with_new_api_secret
```

Save the file, then protect it:

```bash
sudo chown root:ubuntu /etc/newage-trading/groww.env
sudo chmod 640 /etc/newage-trading/groww.env
```

The four Python files must use `os.getenv("GROWW_API_KEY", "")` and `os.getenv("GROWW_API_SECRET", "")` in `CONFIG` for this file to work.

## 6. Check configuration before live execution

Review the configuration in each script:

```bash
nano NATGASMINI.py
nano OILMINI.py
nano NIFTYIntra.py
nano SENSEXINTRA.py
```

Check these values carefully:

- `trading_weekdays`
- `entry_window_start`
- `entry_window_end`
- `force_exit_time`
- `expiry_day_force_exit_time`
- expiry arrays and order expiry arrays
- strike range and strike step
- lot size
- allocation per trade
- loss stop and trailing stop
- daily profit/loss limits, once implemented

The scripts use their configured offset of `330` minutes for IST. AWS itself may use UTC; that does not change the script's internal IST calculation.

## 7. Run one script manually first

Activate the environment:

```bash
cd ~/NewAgeTrading
source .venv/bin/activate
```

Run one strategy:

```bash
python NATGASMINI.py
```

Expected startup output includes authentication, selected expiry, margin, and market data messages.

Stop it with:

```text
Ctrl+C
```

Test the other scripts one at a time:

```bash
python OILMINI.py
python NIFTYIntra.py
python SENSEXINTRA.py
```

Do not run duplicate copies of the same strategy. Duplicate processes can submit duplicate orders.

## 8. Use one scheduler service for all four scripts

Yes. One service is enough now.

`scheduler.py` is the only long-running process you need under `systemd`. It decides which of the four strategy files should run based on `scheduler_config.json`, launches them at the configured times, stops them when the window ends, and writes logs and emails centrally.

Do not keep the old per-script `trading@...` services enabled together with the scheduler. That would create duplicate runs.

## 9. Stop and remove the old per-script services

Run this once on the AWS server:

```bash
sudo systemctl stop trading@NATGASMINI
sudo systemctl stop trading@OILMINI
sudo systemctl stop trading@NIFTYIntra
sudo systemctl stop trading@SENSEXINTRA

sudo systemctl disable trading@NATGASMINI
sudo systemctl disable trading@OILMINI
sudo systemctl disable trading@NIFTYIntra
sudo systemctl disable trading@SENSEXINTRA

sudo rm -f /etc/systemd/system/trading@.service
sudo systemctl daemon-reload
sudo systemctl reset-failed
```

If your existing unit names use `NIFTYINTRA` instead of `NIFTYIntra`, check first with:

```bash
systemctl list-units 'trading@*' --all
systemctl list-unit-files 'trading@*'
```

Then stop and disable the exact unit names shown on the server.

## 10. Commit and deploy code updates

Recommended flow:

1. On your local machine, commit and push the latest code.
2. On the AWS server, pull the latest code.
3. Reinstall requirements if needed.
4. Compile-check the files.
5. Restart the scheduler service.

Local machine:

```bash
git status
git add scheduler.py scheduler_config.json server.md NATGASMINI.py OILMINI.py NIFTYIntra.py SENSEXINTRA.py
git commit -m "Switch deployment to scheduler service"
git push
```

AWS server:

```bash
cd ~/NewAgeTrading
git pull --ff-only

source .venv/bin/activate
python -m pip install -r requirements.txt

python -m py_compile scheduler.py
python -m py_compile NATGASMINI.py
python -m py_compile OILMINI.py
python -m py_compile NIFTYIntra.py
python -m py_compile SENSEXINTRA.py
```

If you only want to deploy server-side changes that already exist in GitHub, the AWS part is enough.

## 11. Create the scheduler service

Create a dedicated service file:

```bash
sudo nano /etc/systemd/system/newage-scheduler.service
```

Paste:

```ini
[Unit]
Description=NewAgeTrading central scheduler
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/NewAgeTrading
EnvironmentFile=/etc/newage-trading/groww.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/NewAgeTrading/.venv/bin/python /home/ubuntu/NewAgeTrading/scheduler.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now newage-scheduler
```

Check status:

```bash
sudo systemctl status newage-scheduler
```

## 12. View logs

Follow the scheduler live:

```bash
sudo journalctl -u newage-scheduler -f
```

View logs from the current boot:

```bash
sudo journalctl -u newage-scheduler -b --no-pager
```

View the last 100 lines:

```bash
sudo journalctl -u newage-scheduler -n 100 --no-pager
```

View full-day logs:

```bash
sudo journalctl -u newage-scheduler --since today --no-pager
```

Scheduler-created files are also written under:

```text
~/NewAgeTrading/logs/
~/NewAgeTrading/logs/script_runs/
```

## 13. Start, stop, and restart

Start the scheduler:

```bash
sudo systemctl start newage-scheduler
```

Stop the scheduler:

```bash
sudo systemctl stop newage-scheduler
```

Restart after changing code or config:

```bash
cd ~/NewAgeTrading
git pull --ff-only
sudo systemctl restart newage-scheduler
```

Disable auto-start:

```bash
sudo systemctl disable newage-scheduler
```

Enable auto-start again:

```bash
sudo systemctl enable newage-scheduler
```

## 14. How the scheduler decides which script runs

The scheduler service runs all day, but it does not run all four strategies all day.

It checks `scheduler_config.json` and starts only the scripts whose:

- `enabled` is `true`
- current IST weekday matches `run_days`
- current time is between `run_time_start` and `run_time_end`
- current day of month matches `run_days_of_month`, if that is configured

Example with the current setup:

- `NIFTYIntra` runs on Monday, Tuesday, and Friday from `09:13` to `15:30`
- `SENSEXINTRA` runs on Wednesday and Thursday from `09:12` to `15:30`
- `OILMINI` runs from `16:00` to `23:30`, only on days `9` to `20` of the month
- `NATGASMINI` runs from `16:30` to `23:30`

So yes, one scheduler bot is enough to handle all four scripts, because it launches each one only in its allowed window.

## 15. How expiry updates work now

The expiry arrays still live inside the individual strategy files. After an expiry passes:

1. Update the relevant Python file.
2. Commit and push the change.
3. Pull on the AWS server.
4. Restart the scheduler service.
5. Confirm the logs show the correct selected expiry.

Example:

```bash
cd ~/NewAgeTrading
nano NATGASMINI.py
python -m py_compile NATGASMINI.py
sudo systemctl restart newage-scheduler
sudo journalctl -u newage-scheduler -n 100 --no-pager
```

If you edit directly on the server, skip the `git pull` in that moment and restart after the change. Long term, keep one source of truth and avoid mixing local-only and server-only edits.

## 16. Daily profit and loss control

A daily realized P&L guard should work as follows:

- Count only P&L from confirmed closed trades.
- Reset the total when the IST calendar date changes.
- Stop all new buys after the daily profit limit is reached.
- Stop all new buys after the daily loss limit is reached.
- Continue allowing exits for already-open positions.
- Keep per-trade max-profit booking separate from the daily entry lock.

The current repository must still be checked to confirm this feature exists in all four files before live deployment. A normal `max_profit_booking_points` setting is only a per-position exit rule; it is not a daily profit limit.

## 17. Basic safety checklist

Before live orders:

- Confirm the API credentials are new and not committed to GitHub.
- Confirm the correct Groww account and segment.
- Confirm the scheduler mail settings are correct.
- Confirm the selected expiry in the logs.
- Confirm lot size and allocation.
- Confirm margin output is available.
- Confirm weekday and IST time settings in `scheduler_config.json`.
- Test with one enabled strategy first.
- Use small size first.
- Watch the scheduler journal during the first full session.
- Verify that force square-off occurs as expected.
- Confirm daily profit/loss entry blocking before enabling all strategies.

## 18. Useful diagnostic commands

```bash
# Check scheduler service
systemctl status newage-scheduler
systemctl list-units 'newage-scheduler*' --all

# Check old trading units are gone
systemctl list-units 'trading@*' --all
systemctl list-unit-files 'trading@*'

# Check server time
date

# Check Python and installed package
~/NewAgeTrading/.venv/bin/python --version
~/NewAgeTrading/.venv/bin/pip show growwapi

# Check scheduler effective config
cd ~/NewAgeTrading
source .venv/bin/activate
python scheduler.py --show-schedule

# Check disk and memory
df -h
free -h
```

If the scheduler repeatedly restarts, inspect its logs first:

```bash
sudo journalctl -u newage-scheduler -n 200 --no-pager
```


