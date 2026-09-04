import unittest
from unittest.mock import patch

from comet.core.models import settings
from comet.services.scraper_health import (
    HALF_OPEN,
    HEALTHY,
    QUARANTINED,
    ScraperHealthManager,
    scraper_health_key,
)


class ScraperHealthStateTests(unittest.TestCase):
    def setUp(self):
        self.manager = ScraperHealthManager()
        self.settings = (
            patch.object(settings, "SCRAPER_HEALTH_FAILURE_THRESHOLD", 5),
            patch.object(settings, "SCRAPER_HEALTH_INITIAL_COOLDOWN", 900),
            patch.object(settings, "SCRAPER_HEALTH_MAX_COOLDOWN", 3600),
            patch.object(settings, "SCRAPER_HEALTH_RECOVERY_SUCCESSES", 2),
            patch.object(settings, "SCRAPER_HEALTH_PROBE_INTERVAL", 60),
        )
        for setting in self.settings:
            setting.start()

    def tearDown(self):
        for setting in reversed(self.settings):
            setting.stop()

    def test_instance_names_have_stable_non_secret_keys(self):
        self.assertEqual(scraper_health_key("Torrentio #1"), "torrentio-1")
        self.assertEqual(scraper_health_key("  MediaFusion #2 "), "mediafusion-2")

    def test_repeated_failures_quarantine_at_threshold(self):
        row = None
        for attempt in range(5):
            row = self.manager._next_values(
                row,
                display_name="Torrentio #1",
                outcome="timeout",
                error_type="TimeoutError",
                now=1000 + attempt,
            )

        self.assertEqual(row["state"], QUARANTINED)
        self.assertEqual(row["consecutive_failures"], 5)
        self.assertEqual(row["next_retry_at"], 1904)
        self.assertEqual(row["last_error_type"], "TimeoutError")

    def test_half_open_failure_increases_cooldown(self):
        row = {
            "state": HALF_OPEN,
            "consecutive_failures": 5,
            "recovery_successes": 0,
            "backoff_level": 1,
            "total_attempts": 5,
            "total_successes": 0,
            "total_failures": 5,
            "last_success_at": None,
        }

        result = self.manager._next_values(
            row,
            display_name="Torrentio #1",
            outcome="error",
            error_type="RuntimeError",
            now=2000,
        )

        self.assertEqual(result["state"], QUARANTINED)
        self.assertEqual(result["next_retry_at"], 3800)
        self.assertEqual(result["backoff_level"], 2)

    def test_two_successful_probes_restore_health(self):
        row = {
            "state": HALF_OPEN,
            "consecutive_failures": 5,
            "recovery_successes": 0,
            "backoff_level": 2,
            "total_attempts": 5,
            "total_successes": 0,
            "total_failures": 5,
            "last_success_at": None,
        }
        first = self.manager._next_values(
            row,
            display_name="Torrentio #1",
            outcome="success",
            error_type=None,
            now=3000,
        )
        self.assertEqual(first["state"], QUARANTINED)
        self.assertEqual(first["recovery_successes"], 1)

        first["state"] = HALF_OPEN
        second = self.manager._next_values(
            first,
            display_name="Torrentio #1",
            outcome="success",
            error_type=None,
            now=3060,
        )
        self.assertEqual(second["state"], HEALTHY)
        self.assertEqual(second["backoff_level"], 0)
        self.assertIsNone(second["next_retry_at"])


if __name__ == "__main__":
    unittest.main()
