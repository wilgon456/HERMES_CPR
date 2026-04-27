from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import hermes_cpr


class HermesCPRTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.hermes_home = self.root / "hermes-home"
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        self.config = hermes_cpr.Config(
            repo_root=self.root,
            hermes_home=self.hermes_home,
            profile="main",
            stale_seconds=300,
            draining_stuck_seconds=600,
            stale_confirmations=3,
            log_file=self.hermes_home / "logs" / "hermes-cpr.log",
            tracker_file=self.hermes_home / "logs" / "hermes-cpr-state.json",
            launchd_label="ai.hermes.gateway-main",
            launchd_plist=self.root / "ai.hermes.gateway-main.plist",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_state(self, payload: dict) -> None:
        (self.hermes_home / "gateway_state.json").write_text(json.dumps(payload), encoding="utf-8")

    def write_pid(self, pid: int) -> None:
        (self.hermes_home / "gateway.pid").write_text(json.dumps({"pid": pid}), encoding="utf-8")

    def test_legacy_running_connected_platform_does_not_restart(self) -> None:
        self.write_state(
            {
                "pid": 123,
                "gateway_state": "running",
                "updated_at": "2026-04-23T00:00:00+00:00",
                "platforms": {"discord": {"state": "connected"}},
            }
        )
        self.write_pid(123)

        with (
            patch("hermes_cpr.seconds_since", return_value=360),
            patch("hermes_cpr.process_alive", return_value=True),
            patch("hermes_cpr.recover_restart", return_value=0) as restart,
            patch("hermes_cpr.recover_start") as start,
        ):
            rc1 = hermes_cpr.decide_and_recover(self.config)
            rc2 = hermes_cpr.decide_and_recover(self.config)
            rc3 = hermes_cpr.decide_and_recover(self.config)

        self.assertEqual((rc1, rc2, rc3), (0, 0, 0))
        start.assert_not_called()
        restart.assert_not_called()
        self.assertFalse(self.config.tracker_file.exists())

    def test_explicit_stale_heartbeat_requires_consecutive_checks(self) -> None:
        self.write_state(
            {
                "pid": 123,
                "gateway_state": "running",
                "heartbeat_at": "2026-04-23T00:00:00+00:00",
                "updated_at": "2026-04-23T00:00:00+00:00",
                "platforms": {"discord": {"state": "connected"}},
            }
        )
        self.write_pid(123)

        with (
            patch("hermes_cpr.seconds_since", return_value=360),
            patch("hermes_cpr.process_alive", return_value=True),
            patch("hermes_cpr.recover_restart", return_value=0) as restart,
            patch("hermes_cpr.recover_start") as start,
        ):
            rc1 = hermes_cpr.decide_and_recover(self.config)
            rc2 = hermes_cpr.decide_and_recover(self.config)
            rc3 = hermes_cpr.decide_and_recover(self.config)

        self.assertEqual((rc1, rc2, rc3), (0, 0, 0))
        start.assert_not_called()
        self.assertEqual(restart.call_count, 1)
        self.assertIn("confirmed stale runtime status after 3 checks", restart.call_args.args[1])
        self.assertFalse(self.config.tracker_file.exists())

    def test_running_without_platform_telemetry_tracks_stale_restart(self) -> None:
        self.write_state(
            {
                "pid": 123,
                "gateway_state": "running",
                "updated_at": "2026-04-23T00:00:00+00:00",
                "platforms": {},
            }
        )
        self.write_pid(123)

        with (
            patch("hermes_cpr.seconds_since", return_value=420),
            patch("hermes_cpr.process_alive", return_value=True),
            patch("hermes_cpr.recover_restart") as restart,
        ):
            rc = hermes_cpr.decide_and_recover(self.config)

        self.assertEqual(rc, 0)
        restart.assert_not_called()
        self.assertTrue(self.config.tracker_file.exists())

    def test_legacy_running_with_active_agents_does_not_restart(self) -> None:
        self.write_state(
            {
                "pid": 123,
                "gateway_state": "running",
                "updated_at": "2026-04-23T00:00:00+00:00",
                "active_agents": 2,
                "platforms": {"discord": {"state": "disconnected"}},
            }
        )
        self.write_pid(123)

        with (
            patch("hermes_cpr.seconds_since", return_value=420),
            patch("hermes_cpr.process_alive", return_value=True),
            patch("hermes_cpr.recover_restart") as restart,
        ):
            rc = hermes_cpr.decide_and_recover(self.config)

        self.assertEqual(rc, 0)
        restart.assert_not_called()
        self.assertFalse(self.config.tracker_file.exists())

    def test_running_degraded_platforms_require_consecutive_stale_checks(self) -> None:
        state = {
            "pid": 123,
            "gateway_state": "running",
            "updated_at": "2026-04-23T00:00:00+00:00",
            "platforms": {"discord": {"state": "disconnected"}},
        }
        self.write_state(state)
        self.write_pid(123)

        with (
            patch("hermes_cpr.seconds_since", return_value=420),
            patch("hermes_cpr.process_alive", return_value=True),
            patch("hermes_cpr.recover_restart", return_value=0) as restart,
        ):
            rc1 = hermes_cpr.decide_and_recover(self.config)
            rc2 = hermes_cpr.decide_and_recover(self.config)
            rc3 = hermes_cpr.decide_and_recover(self.config)

        self.assertEqual((rc1, rc2, rc3), (0, 0, 0))
        self.assertEqual(restart.call_count, 1)
        self.assertIn("confirmed stale runtime status after 3 checks", restart.call_args.args[1])
        self.assertFalse(self.config.tracker_file.exists())

    def test_draining_state_respects_grace_period(self) -> None:
        self.write_state(
            {
                "pid": 123,
                "gateway_state": "draining",
                "updated_at": "2026-04-23T00:00:00+00:00",
                "platforms": {"discord": {"state": "disconnected"}},
            }
        )
        self.write_pid(123)

        with (
            patch("hermes_cpr.seconds_since", return_value=420),
            patch("hermes_cpr.process_alive", return_value=True),
            patch("hermes_cpr.recover_restart") as restart,
        ):
            rc = hermes_cpr.decide_and_recover(self.config)

        self.assertEqual(rc, 0)
        restart.assert_not_called()
        self.assertFalse(self.config.tracker_file.exists())

    def test_draining_state_past_grace_requires_consecutive_stale_checks(self) -> None:
        state = {
            "pid": 123,
            "gateway_state": "draining",
            "updated_at": "2026-04-23T00:00:00+00:00",
            "platforms": {"discord": {"state": "disconnected"}},
        }
        self.write_state(state)
        self.write_pid(123)

        with (
            patch("hermes_cpr.seconds_since", return_value=720),
            patch("hermes_cpr.process_alive", return_value=True),
            patch("hermes_cpr.recover_restart", return_value=0) as restart,
        ):
            rc1 = hermes_cpr.decide_and_recover(self.config)
            rc2 = hermes_cpr.decide_and_recover(self.config)
            rc3 = hermes_cpr.decide_and_recover(self.config)

        self.assertEqual((rc1, rc2, rc3), (0, 0, 0))
        self.assertEqual(restart.call_count, 1)
        self.assertIn("confirmed stale runtime status after 3 checks", restart.call_args.args[1])

    def test_unknown_stale_state_does_not_restart(self) -> None:
        self.write_state(
            {
                "pid": 123,
                "gateway_state": "paused",
                "updated_at": "2026-04-23T00:00:00+00:00",
                "platforms": {"discord": {"state": "disconnected"}},
            }
        )
        self.write_pid(123)

        with (
            patch("hermes_cpr.seconds_since", return_value=720),
            patch("hermes_cpr.process_alive", return_value=True),
            patch("hermes_cpr.recover_restart") as restart,
        ):
            rc = hermes_cpr.decide_and_recover(self.config)

        self.assertEqual(rc, 0)
        restart.assert_not_called()
        self.assertFalse(self.config.tracker_file.exists())

    def test_missing_process_triggers_start_immediately(self) -> None:
        self.write_state(
            {
                "pid": 123,
                "gateway_state": "running",
                "updated_at": "2026-04-23T00:00:00+00:00",
            }
        )
        self.write_pid(123)

        with (
            patch("hermes_cpr.process_alive", return_value=False),
            patch("hermes_cpr.recover_start", return_value=0) as start,
        ):
            rc = hermes_cpr.decide_and_recover(self.config)

        self.assertEqual(rc, 0)
        start.assert_called_once_with(self.config, "gateway process missing")

    def test_permission_error_counts_as_alive(self) -> None:
        with patch("hermes_cpr.os.kill", side_effect=PermissionError):
            self.assertTrue(hermes_cpr.process_alive(123))

    def test_pid_file_can_protect_against_stale_state_pid(self) -> None:
        self.write_state(
            {
                "pid": 111,
                "gateway_state": "running",
                "updated_at": "2026-04-23T00:00:00+00:00",
                "platforms": {"discord": {"state": "connected"}},
            }
        )
        self.write_pid(222)

        def fake_process_alive(pid: int | None) -> bool:
            return pid == 222

        with (
            patch("hermes_cpr.seconds_since", return_value=120),
            patch("hermes_cpr.process_alive", side_effect=fake_process_alive),
            patch("hermes_cpr.recover_start") as start,
            patch("hermes_cpr.recover_restart") as restart,
        ):
            rc = hermes_cpr.decide_and_recover(self.config)

        self.assertEqual(rc, 0)
        start.assert_not_called()
        restart.assert_not_called()

    def test_startup_failed_state_is_case_insensitive(self) -> None:
        self.write_state(
            {
                "pid": 123,
                "gateway_state": "Startup_Failed",
                "updated_at": "2026-04-23T00:00:00+00:00",
            }
        )
        self.write_pid(123)

        with (
            patch("hermes_cpr.process_alive", return_value=True),
            patch("hermes_cpr.recover_restart", return_value=0) as restart,
        ):
            rc = hermes_cpr.decide_and_recover(self.config)

        self.assertEqual(rc, 0)
        restart.assert_called_once_with(self.config, "gateway in startup_failed state")

    def test_recover_restart_uses_launchctl_kickstart(self) -> None:
        with patch("hermes_cpr.run_launchctl") as run_launchctl:
            run_launchctl.return_value.returncode = 0
            run_launchctl.return_value.stdout = ""
            run_launchctl.return_value.stderr = ""

            rc = hermes_cpr.recover_restart(self.config, "stale heartbeat")

        self.assertEqual(rc, 0)
        run_launchctl.assert_called_once_with(
            "kickstart",
            "-k",
            f"gui/{os.getuid()}/ai.hermes.gateway-main",
        )

    def test_recover_start_bootstraps_when_service_is_unloaded(self) -> None:
        def fake_launchctl(*args: str):
            proc = Mock()
            proc.stdout = ""
            proc.stderr = ""
            proc.returncode = 113 if args[0] == "print" else 0
            return proc

        with patch("hermes_cpr.run_launchctl", side_effect=fake_launchctl) as run_launchctl:
            rc = hermes_cpr.recover_start(self.config, "gateway process missing")

        self.assertEqual(rc, 0)
        self.assertEqual(
            run_launchctl.call_args_list[-1].args,
            ("bootstrap", f"gui/{os.getuid()}", str(self.config.launchd_plist)),
        )

    def test_stale_lock_is_replaced(self) -> None:
        lock_dir = self.hermes_home / "logs" / hermes_cpr.LOCK_DIR_NAME
        lock_dir.mkdir(parents=True)
        hermes_cpr.write_json(
            lock_dir / hermes_cpr.LOCK_OWNER_FILE,
            {
                "pid": 999999,
                "created_at": "2026-04-23T00:00:00+00:00",
            },
        )

        with (
            patch("hermes_cpr.process_alive", return_value=False),
            patch("hermes_cpr.utc_now") as now,
        ):
            now.return_value = hermes_cpr.parse_iso("2026-04-23T01:00:00+00:00")
            acquired = hermes_cpr.acquire_lock(self.config)

        self.assertEqual(acquired, lock_dir)
        self.assertTrue((lock_dir / hermes_cpr.LOCK_OWNER_FILE).exists())
        hermes_cpr.release_lock(acquired)
        self.assertFalse(lock_dir.exists())

    def test_fresh_live_lock_skips(self) -> None:
        lock_dir = self.hermes_home / "logs" / hermes_cpr.LOCK_DIR_NAME
        lock_dir.mkdir(parents=True)
        hermes_cpr.write_json(
            lock_dir / hermes_cpr.LOCK_OWNER_FILE,
            {
                "pid": os.getpid(),
                "created_at": hermes_cpr.utc_now().isoformat(),
            },
        )

        with patch("hermes_cpr.process_alive", return_value=True):
            self.assertIsNone(hermes_cpr.acquire_lock(self.config))

        hermes_cpr.release_lock(lock_dir)

    def test_load_config_clamps_invalid_intervals(self) -> None:
        config_path = self.root / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "repo_root": str(self.root),
                    "hermes_home": str(self.hermes_home),
                    "stale_seconds": -1,
                    "draining_stuck_seconds": "bad",
                    "stale_confirmations": 0,
                }
            ),
            encoding="utf-8",
        )

        config = hermes_cpr.load_config(config_path)

        self.assertEqual(config.stale_seconds, 0)
        self.assertEqual(config.draining_stuck_seconds, hermes_cpr.DEFAULT_DRAINING_STUCK_SECONDS)
        self.assertEqual(config.stale_confirmations, 1)


if __name__ == "__main__":
    unittest.main()
