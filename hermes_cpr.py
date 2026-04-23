#!/usr/bin/env python3
"""Standalone Hermes gateway CPR watcher."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_STALE_SECONDS = 300
DEFAULT_DRAINING_STUCK_SECONDS = 600
DEFAULT_PROFILE = "main"
LOCK_DIR_NAME = "hermes-cpr.lock.d"


@dataclass
class Config:
    repo_root: Path
    hermes_home: Path
    profile: str
    stale_seconds: int
    draining_stuck_seconds: int
    log_file: Path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8"))
    hermes_home = Path(raw["hermes_home"]).expanduser().resolve()
    default_log = hermes_home / "logs" / "hermes-cpr.log"
    return Config(
        repo_root=Path(raw["repo_root"]).expanduser().resolve(),
        hermes_home=hermes_home,
        profile=str(raw.get("profile", DEFAULT_PROFILE)).strip() or DEFAULT_PROFILE,
        stale_seconds=int(raw.get("stale_seconds", DEFAULT_STALE_SECONDS)),
        draining_stuck_seconds=int(
            raw.get("draining_stuck_seconds", DEFAULT_DRAINING_STUCK_SECONDS)
        ),
        log_file=Path(raw.get("log_file", default_log)).expanduser().resolve(),
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


def process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


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


def resolve_hermes_bin(repo_root: Path) -> str:
    windows = os.name == "nt"
    candidates = [
        repo_root / "venv" / ("Scripts" if windows else "bin") / ("hermes.exe" if windows else "hermes"),
        repo_root / ".venv" / ("Scripts" if windows else "bin") / ("hermes.exe" if windows else "hermes"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("Could not find Hermes executable in venv/ or .venv/")


def run_hermes(config: Config, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["HERMES_HOME"] = str(config.hermes_home)
    return subprocess.run(
        [resolve_hermes_bin(config.repo_root), "--profile", config.profile, *args],
        cwd=config.repo_root,
        capture_output=True,
        text=True,
        env=env,
    )


def acquire_lock(config: Config) -> Path | None:
    lock_dir = config.hermes_home / "logs" / LOCK_DIR_NAME
    try:
        lock_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        log_line(config.log_file, "another CPR instance is already running, skipping")
        return None
    return lock_dir


def release_lock(lock_dir: Path | None) -> None:
    if lock_dir is None:
        return
    try:
        lock_dir.rmdir()
    except OSError:
        pass


def recover_start(config: Config, reason: str) -> int:
    log_line(config.log_file, f"start requested: {reason}")
    proc = run_hermes(config, "gateway", "start")
    output = "\n".join(filter(None, [proc.stdout, proc.stderr])).strip()
    if output:
        log_line(config.log_file, output)
    return proc.returncode


def recover_restart(config: Config, reason: str) -> int:
    log_line(config.log_file, f"restart requested: {reason}")
    proc = run_hermes(config, "gateway", "restart")
    output = "\n".join(filter(None, [proc.stdout, proc.stderr])).strip()
    if output:
        log_line(config.log_file, output)
    return proc.returncode


def decide_and_recover(config: Config) -> int:
    state_path = config.hermes_home / "gateway_state.json"
    pid_path = config.hermes_home / "gateway.pid"

    state = read_json(state_path) or {}
    pid_record = read_json(pid_path) or {}

    pid = None
    for candidate in (state.get("pid"), pid_record.get("pid")):
        try:
            pid = int(candidate)
            break
        except (TypeError, ValueError):
            continue

    alive = process_alive(pid)
    gateway_state = str(state.get("gateway_state", "")).strip()
    age = seconds_since(state.get("updated_at"))

    log_line(
        config.log_file,
        f"check pid={pid or 'none'} alive={alive} state={gateway_state or 'unknown'} "
        f"updated_age={age if age is not None else 'unknown'}s",
    )

    if not alive:
        return recover_start(config, "gateway process missing")

    if gateway_state == "startup_failed":
        return recover_restart(config, "gateway in startup_failed state")

    if gateway_state == "draining" and age is not None and age > config.draining_stuck_seconds:
        return recover_restart(config, f"gateway stuck in draining for {age}s")

    if age is not None and age > config.stale_seconds:
        return recover_restart(config, f"gateway runtime status stale for {age}s")

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
