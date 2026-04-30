#!/usr/bin/env python3
"""Standalone Hermes gateway CPR watcher."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    fcntl = None
else:
    import fcntl


DEFAULT_STALE_SECONDS = 300
DEFAULT_DRAINING_STUCK_SECONDS = 600
DEFAULT_STALE_CONFIRMATIONS = 3
DEFAULT_PROFILE = "main"
DEFAULT_LAUNCHD_LABEL = "ai.hermes.gateway-main"
DEFAULT_LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LAUNCHD_LABEL}.plist"
LOCK_DIR_NAME = "hermes-cpr.lock.d"
STALE_TRACKER_FILE = "hermes-cpr-state.json"
LOCK_OWNER_FILE = "owner.json"
LOCK_STALE_SECONDS = 900
GATEWAY_LOCK_FILE = "gateway.lock"
WATCHDOG_LEASE_FILE = "gateway_watchdog.json"


@dataclass
class Config:
    repo_root: Path
    hermes_home: Path
    profile: str
    stale_seconds: int
    draining_stuck_seconds: int
    stale_confirmations: int
    log_file: Path
    tracker_file: Path
    launchd_label: str
    launchd_plist: Path
    watchdog_file: Path | None = None
    watchdog_stale_seconds: int = DEFAULT_STALE_SECONDS


@dataclass
class StaleDecision:
    should_track: bool
    restart_allowed: bool
    reason: str


@dataclass
class GatewayPresence:
    pid: int | None
    alive: bool
    source: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def config_int(raw: dict[str, Any], key: str, default: int, minimum: int) -> int:
    try:
        value = int(raw.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8"))
    hermes_home = Path(raw["hermes_home"]).expanduser().resolve()
    default_log = hermes_home / "logs" / "hermes-cpr.log"
    default_tracker = hermes_home / "logs" / STALE_TRACKER_FILE
    default_watchdog = hermes_home / WATCHDOG_LEASE_FILE
    return Config(
        repo_root=Path(raw["repo_root"]).expanduser().resolve(),
        hermes_home=hermes_home,
        profile=str(raw.get("profile", DEFAULT_PROFILE)).strip() or DEFAULT_PROFILE,
        stale_seconds=config_int(raw, "stale_seconds", DEFAULT_STALE_SECONDS, 0),
        draining_stuck_seconds=config_int(
            raw, "draining_stuck_seconds", DEFAULT_DRAINING_STUCK_SECONDS, 0
        ),
        stale_confirmations=config_int(
            raw, "stale_confirmations", DEFAULT_STALE_CONFIRMATIONS, 1
        ),
        log_file=Path(raw.get("log_file", default_log)).expanduser().resolve(),
        tracker_file=Path(raw.get("tracker_file", default_tracker)).expanduser().resolve(),
        launchd_label=str(raw.get("launchd_label", DEFAULT_LAUNCHD_LABEL)).strip()
        or DEFAULT_LAUNCHD_LABEL,
        launchd_plist=Path(raw.get("launchd_plist", DEFAULT_LAUNCHD_PLIST))
        .expanduser()
        .resolve(),
        watchdog_file=Path(raw.get("watchdog_file", default_watchdog)).expanduser().resolve(),
        watchdog_stale_seconds=config_int(
            raw,
            "watchdog_stale_seconds",
            config_int(raw, "stale_seconds", DEFAULT_STALE_SECONDS, 0),
            0,
        ),
    )


def log_line(log_file: Path, message: str) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = utc_now().strftime("%Y-%m-%d %H:%M:%S")
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] [hermes-cpr] {message}\n")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def gateway_lock_active(lock_path: Path) -> bool | None:
    """Return whether Hermes' runtime lock is held by a live gateway process."""
    if fcntl is None or not lock_path.exists():
        return None

    try:
        handle = lock_path.open("a+", encoding="utf-8")
    except OSError:
        return None

    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return None

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        try:
            handle.close()
        except OSError:
            pass


def parse_iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def seconds_since(raw: Any) -> int | None:
    value = parse_iso(raw)
    if value is None:
        return None
    return max(0, int((utc_now() - value).total_seconds()))


def runtime_heartbeat_age(state: dict[str, Any]) -> tuple[int | None, bool]:
    """Return gateway heartbeat age and whether an explicit heartbeat exists."""
    heartbeat_at = state.get("heartbeat_at")
    if isinstance(heartbeat_at, str) and heartbeat_at.strip():
        age = seconds_since(heartbeat_at)
        if age is not None:
            return age, True
    return seconds_since(state.get("updated_at")), False


def run_launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
    )


def launchd_service(config: Config) -> str:
    return f"gui/{os.getuid()}/{config.launchd_label}"


def launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def log_completed_process(config: Config, proc: subprocess.CompletedProcess[str]) -> None:
    output = "\n".join(filter(None, [proc.stdout, proc.stderr])).strip()
    if output:
        log_line(config.log_file, output)


def launchd_service_loaded(config: Config) -> bool:
    proc = run_launchctl("print", launchd_service(config))
    return proc.returncode == 0


def bootstrap_launchd_service(config: Config) -> subprocess.CompletedProcess[str]:
    return run_launchctl("bootstrap", launchd_domain(), str(config.launchd_plist))


def kickstart_launchd_service(config: Config) -> subprocess.CompletedProcess[str]:
    return run_launchctl("kickstart", "-k", launchd_service(config))


def run_hermes(config: Config, *args: str) -> subprocess.CompletedProcess[str]:
    windows = os.name == "nt"
    candidates = [
        config.repo_root / "venv" / ("Scripts" if windows else "bin") / ("hermes.exe" if windows else "hermes"),
        config.repo_root / ".venv" / ("Scripts" if windows else "bin") / ("hermes.exe" if windows else "hermes"),
    ]
    hermes_bin = next((str(candidate) for candidate in candidates if candidate.exists()), None)
    if hermes_bin is None:
        raise FileNotFoundError("Could not find Hermes executable in venv/ or .venv/")
    env = dict(os.environ)
    env["HERMES_HOME"] = str(config.hermes_home)
    return subprocess.run(
        [hermes_bin, "--profile", config.profile, *args],
        cwd=config.repo_root,
        capture_output=True,
        text=True,
        env=env,
    )


def write_lock_owner(lock_dir: Path) -> None:
    write_json(
        lock_dir / LOCK_OWNER_FILE,
        {
            "pid": os.getpid(),
            "created_at": utc_now().isoformat(),
        },
    )


def lock_is_stale(lock_dir: Path) -> bool:
    owner = read_json(lock_dir / LOCK_OWNER_FILE) or {}
    created_at = parse_iso(owner.get("created_at"))
    try:
        owner_pid = int(owner.get("pid", 0))
    except (TypeError, ValueError):
        owner_pid = 0

    if owner_pid > 0 and process_alive(owner_pid):
        return False

    if created_at is None:
        return True

    return (utc_now() - created_at).total_seconds() > LOCK_STALE_SECONDS


def acquire_lock(config: Config) -> Path | None:
    lock_dir = config.hermes_home / "logs" / LOCK_DIR_NAME
    try:
        lock_dir.mkdir(parents=True, exist_ok=False)
        write_lock_owner(lock_dir)
    except FileExistsError:
        if not lock_is_stale(lock_dir):
            log_line(config.log_file, "another CPR instance is already running, skipping")
            return None

        log_line(config.log_file, "stale CPR lock found, removing it")
        try:
            shutil.rmtree(lock_dir)
            lock_dir.mkdir(parents=True, exist_ok=False)
            write_lock_owner(lock_dir)
        except OSError:
            log_line(config.log_file, "failed to replace stale CPR lock, skipping")
            return None
    return lock_dir


def release_lock(lock_dir: Path | None) -> None:
    if lock_dir is None:
        return
    try:
        (lock_dir / LOCK_OWNER_FILE).unlink(missing_ok=True)
        lock_dir.rmdir()
    except OSError:
        pass


def recover_start(config: Config, reason: str) -> int:
    log_line(config.log_file, f"start requested: {reason}")
    if launchd_service_loaded(config):
        proc = kickstart_launchd_service(config)
    else:
        proc = bootstrap_launchd_service(config)
    log_completed_process(config, proc)
    return proc.returncode


def recover_restart(config: Config, reason: str) -> int:
    log_line(config.log_file, f"restart requested: {reason}")
    proc = kickstart_launchd_service(config)
    if proc.returncode != 0 and not launchd_service_loaded(config):
        log_completed_process(config, proc)
        proc = bootstrap_launchd_service(config)
    log_completed_process(config, proc)
    return proc.returncode


def extract_platform_states(state: dict[str, Any]) -> dict[str, str]:
    raw_platforms = state.get("platforms")
    if not isinstance(raw_platforms, dict):
        return {}

    result: dict[str, str] = {}
    for name, payload in raw_platforms.items():
        if not isinstance(payload, dict):
            continue
        raw_state = payload.get("state")
        if not isinstance(raw_state, str):
            continue
        normalized = raw_state.strip().lower()
        if normalized:
            result[str(name)] = normalized
    return result


def evaluate_stale_decision(
    state: dict[str, Any],
    age: int | None,
    config: Config,
    *,
    explicit_heartbeat: bool,
) -> StaleDecision:
    if age is None or age <= config.stale_seconds:
        return StaleDecision(False, False, "runtime status fresh")

    gateway_state = str(state.get("gateway_state", "")).strip().lower() or "unknown"
    platform_states = extract_platform_states(state)
    try:
        active_agents = max(0, int(state.get("active_agents", 0)))
    except (TypeError, ValueError):
        active_agents = 0

    if gateway_state == "draining":
        if age <= config.draining_stuck_seconds:
            return StaleDecision(
                False,
                False,
                "gateway is draining within the allowed grace period",
            )
        return StaleDecision(
            True,
            True,
            "gateway draining state remains stale past the allowed grace period",
        )

    if gateway_state == "running":
        if active_agents > 0:
            return StaleDecision(
                False,
                False,
                f"runtime status is stale but active_agents={active_agents}; refusing to restart a live gateway",
            )

        live_platform_states = {
            name: value
            for name, value in platform_states.items()
            if value in {"connected", "connecting", "retrying"}
        }
        if live_platform_states:
            details = ", ".join(
                f"{name}={value}" for name, value in sorted(live_platform_states.items())
            )
            heartbeat_label = "explicit gateway heartbeat" if explicit_heartbeat else "legacy runtime status"
            return StaleDecision(
                False,
                False,
                f"{heartbeat_label} is stale but live platform telemetry exists ({details}); refusing to restart a live gateway",
            )

        if not explicit_heartbeat:
            return StaleDecision(
                True,
                True,
                "legacy runtime status is stale and no live platform telemetry is available",
            )

        if platform_states:
            details = ", ".join(f"{name}={value}" for name, value in sorted(platform_states.items()))
            return StaleDecision(
                True,
                True,
                f"explicit gateway heartbeat is stale; platform states were {details}",
            )

        return StaleDecision(
            True,
            True,
            "explicit gateway heartbeat is stale and no platform telemetry is available",
        )

    return StaleDecision(
        False,
        False,
        f"runtime status stale but gateway state '{gateway_state}' is not a restart signal",
    )


def clear_stale_tracker(config: Config) -> None:
    try:
        config.tracker_file.unlink(missing_ok=True)
    except OSError:
        pass


def bump_stale_tracker(config: Config, state: dict[str, Any], *, age: int, reason: str) -> int:
    current_pid = state.get("pid")
    current_updated_at = state.get("updated_at")
    payload = read_json(config.tracker_file) or {}

    same_sample = (
        payload.get("pid") == current_pid
        and payload.get("updated_at") == current_updated_at
        and payload.get("reason") == reason
    )
    try:
        previous_count = int(payload.get("count", 0))
    except (TypeError, ValueError):
        previous_count = 0
    count = previous_count + 1 if same_sample else 1

    write_json(
        config.tracker_file,
        {
            "pid": current_pid,
            "updated_at": current_updated_at,
            "reason": reason,
            "age": age,
            "count": count,
            "recorded_at": utc_now().isoformat(),
        },
    )
    return count


def watchdog_file(config: Config) -> Path:
    return config.watchdog_file or config.hermes_home / WATCHDOG_LEASE_FILE


def watchdog_lease_age(config: Config) -> tuple[dict[str, Any] | None, int | None]:
    lease = read_json(watchdog_file(config))
    if lease is None:
        return None, None
    return lease, seconds_since(lease.get("heartbeat_at"))


def format_age(age: int | None) -> str:
    return f"{age}s" if age is not None else "missing"


def track_and_maybe_restart(config: Config, sample: dict[str, Any], age: int, reason: str) -> int:
    count = bump_stale_tracker(config, sample, age=age, reason=reason)
    log_line(
        config.log_file,
        f"stale tracker {count}/{config.stale_confirmations}: {reason}",
    )
    if count >= config.stale_confirmations:
        clear_stale_tracker(config)
        return recover_restart(
            config,
            f"confirmed stale runtime status after {count} checks: {reason}",
        )
    return 0


def parse_pid(raw: Any) -> int | None:
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def resolve_gateway_presence(
    config: Config,
    state: dict[str, Any],
    pid_record: dict[str, Any],
) -> GatewayPresence:
    lock_path = config.hermes_home / GATEWAY_LOCK_FILE
    lock_record = read_json(lock_path) or {}
    lock_state = gateway_lock_active(lock_path)

    candidates = [
        parse_pid(lock_record.get("pid")),
        parse_pid(state.get("pid")),
        parse_pid(pid_record.get("pid")),
    ]
    candidates = [pid for pid in candidates if pid is not None]

    if lock_state is not None:
        return GatewayPresence(candidates[0] if candidates else None, lock_state, "gateway.lock")

    for pid in candidates:
        if process_alive(pid):
            return GatewayPresence(pid, True, "pid")

    return GatewayPresence(candidates[0] if candidates else None, False, "pid")


def decide_and_recover(config: Config) -> int:
    state_path = config.hermes_home / "gateway_state.json"
    pid_path = config.hermes_home / "gateway.pid"

    state = read_json(state_path) or {}
    pid_record = read_json(pid_path) or {}

    presence = resolve_gateway_presence(config, state, pid_record)
    lease, lease_age = watchdog_lease_age(config)
    gateway_state = str(state.get("gateway_state", "")).strip().lower()
    age, explicit_heartbeat = runtime_heartbeat_age(state)

    log_line(
        config.log_file,
        f"check pid={presence.pid or 'none'} alive={presence.alive} source={presence.source} "
        f"state={gateway_state or 'unknown'} "
        f"watchdog_age={format_age(lease_age)} "
        f"heartbeat_age={format_age(age)} "
        f"heartbeat={'explicit' if explicit_heartbeat else 'legacy'}",
    )

    if not presence.alive:
        clear_stale_tracker(config)
        return recover_start(config, "gateway process missing")

    if gateway_state == "startup_failed":
        clear_stale_tracker(config)
        return recover_restart(config, "gateway in startup_failed state")

    if lease is not None:
        if lease_age is None:
            return track_and_maybe_restart(
                config,
                lease,
                -1,
                "gateway watchdog lease has no valid heartbeat_at",
            )
        if lease_age > config.watchdog_stale_seconds:
            return track_and_maybe_restart(
                config,
                lease,
                lease_age,
                f"gateway watchdog lease is stale ({lease_age}s > {config.watchdog_stale_seconds}s)",
            )

        clear_stale_tracker(config)
        log_line(config.log_file, "gateway watchdog lease fresh")
        return 0

    stale_decision = evaluate_stale_decision(
        state,
        age,
        config,
        explicit_heartbeat=explicit_heartbeat,
    )
    if stale_decision.should_track:
        return track_and_maybe_restart(config, state, age or -1, stale_decision.reason)

    clear_stale_tracker(config)
    if age is not None and age > config.stale_seconds:
        log_line(config.log_file, stale_decision.reason)
    else:
        log_line(config.log_file, "gateway looks healthy")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config.expanduser().resolve())
    lock_dir = acquire_lock(config)
    if lock_dir is None:
        return 0
    try:
        return decide_and_recover(config)
    finally:
        release_lock(lock_dir)


if __name__ == "__main__":
    raise SystemExit(main())
