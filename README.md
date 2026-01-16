# Steam Inventory Calculator

A headless Python tool that fetches a fixed Steam profile's inventory, queries market prices, and calculates the total inventory value (can save the result to Firestore). Designed to run unattended via Windows Scheduled Task.

**Quick Overview**
- Script: `main.py`
- Config: `config.json`
- Cache: `price_cache.json` (prices cached for 1 hour)
- Log file: `inventory_value.log`
- Launcher: `run_task.bat`

**Prerequisites**
- Windows with Python 3.10+ installed
- Project virtualenv: `.venv`
- Firebase service account JSON (if using Firestore writes)

**Install**
1. Create venv and install deps (run in project folder):

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

**Configure**
- Edit `config.json` and set:
  - `steam_id`: target SteamID64 (starts with `7656...`)
  - `app_id`: game/app id (e.g. `730` for CS2)
  - `context_id`: usually `2` for game inventories
  - `currency`: Steam currency id (`1` = USD, `3` = EUR, etc.)
  - `sleep_interval`: seconds between price queries (default 3)
  - Optional: `price_cache_file` (defaults to `price_cache.json`)
- If you want Firestore writes, place your Firebase admin JSON next to the script and adjust the path used by `saveToFirestore()` (current filename referenced in code).

**Run manually**
- Quick manual run (activates venv automatically in the batch file):

```powershell
run_task.bat
```

- Or run directly in venv:

```powershell
.venv\Scripts\python.exe main.py
```

**Scheduling (Windows)**
- PowerShell (run as Administrator) — create a daily task at 03:00:

```powershell
$action = New-ScheduledTaskAction -Execute "D:\dev\steam-invetory-calculator\run_task.bat"
$trigger = New-ScheduledTaskTrigger -Daily -At 03:00
Register-ScheduledTask -TaskName "SteamInventoryCalculator" -Action $action -Trigger $trigger -RunLevel Highest -User "SYSTEM" -Description "Daily run of steam inventory value"
```

- schtasks alternative:

```powershell
schtasks /Create /SC DAILY /TN "SteamInventoryCalculator" /TR "D:\dev\steam-invetory-calculator\run_task.bat" /ST 03:00 /RL HIGHEST /RU "SYSTEM" /F
```

**Notes & Tips**
- The tool applies a 1-hour cache for market prices to reduce requests and avoid rate limits.
- If Steam returns `400` errors, reduce `count` in `config.json` or leave default paging behavior (the script already paginates).
- Keep `sleep_interval` conservative (3 seconds recommended) to avoid rate-limiting.
- Confirm the Firebase service account file exists before enabling Firestore writes.