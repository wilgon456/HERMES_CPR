from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_state(self, payload: dict) -> None:
        (self.hermes_home / "gateway_state.json").write_text(json.dumps(payload), encoding="utf-8")

    def write_pid(self, pid: int) -> None:
        (self.hermes_home / "gateway.pid").write_text(json.dumps({"pid": pid}), encoding="utf-8")

    def test_running_connected_platform_skips_stale_restart(self) -> None:
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
            patch("hermes_cpr.recover_restart") as restart,
            patch("hermes_cpr.recover_start") as start,
        ):
            rc = hermes_cpr.decide_and_recover(self.config)

        self.assertEqual(rc, 0)
        start.assert_not_called()
        restart.assert_not_called()
        self.assertFalse(self.config.tracker_file.exists())

    def test_running_without_platform_telemetry_skips_stale_restart(self) -> None:
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
        self.assertFalse(self.config.tracker_file.exists())

    def test_running_with_active_agents_skips_stale_restart(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
