# HERMES_CPR

External CPR/watchdog utility for an **existing Hermes gateway deployment**.

HERMES_CPR is **not** a standalone chatbot or a generic process supervisor. It is a small companion tool that runs **outside** your `hermes-agent` repo, checks the gateway runtime state, and tries to recover the gateway only when recovery is actually warranted.

---

## What this project does

HERMES_CPR periodically inspects Hermes runtime files and attempts recovery in these cases:

- the Hermes gateway process is missing → `hermes gateway start`
- the gateway is in `startup_failed` → `hermes gateway restart`
- the gateway is stuck in `draining` longer than allowed → `hermes gateway restart`
- the gateway is `running`, but runtime state is stale **and** all platform states are degraded for multiple consecutive checks → `hermes gateway restart`

It is intentionally conservative:

- **process liveness is the top-level signal**
- a healthy `running` gateway is **not** restarted just because `updated_at` stopped moving
- a `running` gateway with at least one connected platform is protected
- a `running` gateway with active agents is protected
- stale restart decisions require repeated confirmation

---

## Who this is for

Use this repo if all of the following are true:

1. You already run a Hermes gateway from the [`hermes-agent`](https://github.com/NousResearch/hermes-agent) codebase.
2. Your Hermes environment writes runtime state such as `gateway_state.json` and `gateway.pid`.
3. You want an **external** recovery loop instead of making the Hermes process self-resurrect.

If you want a standalone monitoring framework for arbitrary processes, this repo is not that.

---

## Prerequisites

Before installing HERMES_CPR, you should already have:

- a working clone of `hermes-agent`
- a valid Hermes profile / `HERMES_HOME`
- a Hermes gateway deployment that produces:
  - `gateway_state.json`
  - `gateway.pid`
- a Python environment where the Hermes CLI binary exists in **one of**:
  - `repo_root/venv/bin/hermes`
  - `repo_root/.venv/bin/hermes`

HERMES_CPR currently assumes the Hermes executable is available from that repo-local virtualenv. If your deployment uses a different packaging/layout, adjust `resolve_hermes_bin()` in `hermes_cpr.py`.

---

## Quick Start

### 1. Clone this repo

```bash
git clone https://github.com/wilgon456/HERMES_CPR.git
cd HERMES_CPR
```

### 2. Create your local config

```bash
cp config.example.json config.json
```

Edit `config.json` for your environment.

Example:

```json
{
  "repo_root": "/opt/hermes-agent",
  "hermes_home": "/home/you/.hermes/profiles/main",
  "profile": "main",
  "stale_seconds": 600,
  "draining_stuck_seconds": 600,
  "stale_confirmations": 3,
  "log_file": "/home/you/.hermes/profiles/main/logs/hermes-cpr.log",
  "tracker_file": "/home/you/.hermes/profiles/main/logs/hermes-cpr-state.json"
}
```

### 3. Run once manually

```bash
python3 hermes_cpr.py --config config.json
```

### 4. Install a scheduler

Choose one:

- macOS → launchd
- Linux → systemd timer or cron
- Windows → Task Scheduler

---

## Configuration

`config.example.json` documents the expected fields:

```json
{
  "repo_root": "/Users/you/hermes-agent",
  "hermes_home": "/Users/you/.hermes/profiles/main",
  "profile": "main",
  "stale_seconds": 600,
  "draining_stuck_seconds": 600,
  "stale_confirmations": 3,
  "log_file": "/Users/you/.hermes/profiles/main/logs/hermes-cpr.log",
  "tracker_file": "/Users/you/.hermes/profiles/main/logs/hermes-cpr-state.json"
}
```

Field meanings:

- `repo_root`: path to your `hermes-agent` checkout
- `hermes_home`: Hermes home/profile directory containing runtime files
- `profile`: Hermes profile name passed to the CLI
- `stale_seconds`: how old `updated_at` must be before stale logic begins
- `draining_stuck_seconds`: grace period before a `draining` gateway is considered stuck
- `stale_confirmations`: number of consecutive degraded stale checks required before restart
- `log_file`: CPR log destination
- `tracker_file`: state file for repeated stale confirmation tracking

### Recommended operating values

If your priority is **revive dead Hermes, do not flap healthy Hermes**, these defaults are intentionally conservative:

- `stale_seconds`: **600 or higher recommended**
- `stale_confirmations`: **3 or higher recommended**
- scheduler interval: **1 minute is fine**

Why:

- if the process is actually dead, HERMES_CPR reacts via `alive=False` and starts it immediately
- a larger stale threshold does **not** materially slow dead-process recovery
- a too-small stale threshold increases the chance of disturbing a healthy-but-quiet gateway

---

## Platform install

### macOS (launchd)

Install a launch agent that runs every minute:

```bash
zsh install_macos_launchd.sh 1
```

This creates:

- `~/Library/LaunchAgents/ai.hermes.cpr.plist`

### Linux (systemd)

A sample unit file is included at:

- `systemd/hermes-cpr.service`
- `systemd/hermes-cpr.timer`

Before enabling the timer, **edit the service file** so that:

- `WorkingDirectory=` points to your actual clone path
- `ExecStart=` points to the right Python executable and `config.json`

Example install:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/hermes-cpr.service ~/.config/systemd/user/
cp systemd/hermes-cpr.timer ~/.config/systemd/user/
$EDITOR ~/.config/systemd/user/hermes-cpr.service
systemctl --user daemon-reload
systemctl --user enable --now hermes-cpr.timer
systemctl --user status hermes-cpr.timer
```

Important note about `systemctl --user`:

- a user timer usually requires the user systemd manager to be running
- if you want CPR to stay active while logged out, enable lingering for your user or create a system-level unit instead
- example lingering command: `sudo loginctl enable-linger $USER`

If you prefer cron, a simple 1-minute entry also works:

```cron
* * * * * cd /path/to/HERMES_CPR && /usr/bin/python3 hermes_cpr.py --config /path/to/HERMES_CPR/config.json
```

### Windows (Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows_task.ps1 -IntervalMinutes 1
```

This creates a scheduled task named `HermesCPR`.

---

## Testing

Run the test suite:

```bash
python3 -m unittest discover -s tests -q
```

Optional syntax check:

```bash
python3 -m py_compile hermes_cpr.py tests/test_hermes_cpr.py
```

---

## Safety model

The main design goal is:

> revive a dead Hermes gateway without becoming the thing that kills a healthy gateway.

Current protections include:

- no stale restart while a platform is still `connected`
- no stale restart while `active_agents > 0`
- no stale restart when no platform telemetry is available
- no premature `draining` restart before the configured grace period
- no immediate stale restart; repeated degraded checks are required

---

## Korean summary / 한국어 요약

이 프로젝트는 **이미 운영 중인 Hermes gateway**를 위한 외부 CPR/watchdog 도구입니다.

핵심 원칙:

- 프로세스가 진짜 죽었을 때 살린다
- 건강한 `running` gateway를 stale만으로 흔들지 않는다
- `connected` 플랫폼이 있거나, active agent가 남아 있으면 stale restart를 하지 않는다
- degraded stale 상태는 연속 확인 후에만 재시작한다

즉, **"죽었을 때 살리는 장치"** 에 가깝게 설계되어 있습니다.

---

## Repository contents

- `hermes_cpr.py` — main CPR logic
- `config.example.json` — example config template
- `install_macos_launchd.sh` — macOS launchd installer
- `install_windows_task.ps1` — Windows Task Scheduler installer
- `systemd/hermes-cpr.service` — sample Linux systemd service unit
- `systemd/hermes-cpr.timer` — sample Linux systemd timer unit
- `tests/test_hermes_cpr.py` — unit tests

---

## Notes

- `config.json` is intentionally ignored by git.
- This repo stores no runtime secrets by default.
- If your Hermes deployment layout differs, adjust `config.json` and/or `resolve_hermes_bin()`.
- Future improvements could include notifications, bounded recovery counts, and multi-profile orchestration.
