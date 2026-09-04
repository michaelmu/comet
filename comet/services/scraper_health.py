import re
import time
from dataclasses import dataclass

from comet.core.logger import logger
from comet.core.models import database, settings
from comet.observability import metrics

HEALTHY = "healthy"
QUARANTINED = "quarantined"
HALF_OPEN = "half_open"
_KEY_COMPONENT = re.compile(r"[^a-z0-9]+")


def scraper_health_key(display_name: str) -> str:
    """Return a stable, non-secret database key for a scraper instance."""

    normalized = _KEY_COMPONENT.sub("-", display_name.strip().casefold()).strip("-")
    return normalized or "unknown"


@dataclass(frozen=True, slots=True)
class HealthDecision:
    allowed: bool
    state: str
    next_retry_at: float | None = None


class ScraperHealthManager:
    @staticmethod
    def _enabled() -> bool:
        return bool(settings.SCRAPER_HEALTH_ENABLED and database.is_connected)

    async def should_allow(
        self, display_name: str, *, now: float | None = None
    ) -> HealthDecision:
        if not self._enabled():
            return HealthDecision(True, HEALTHY)

        current_time = time.time() if now is None else now
        key = scraper_health_key(display_name)
        try:
            row = await database.fetch_one(
                """
                SELECT state, next_retry_at
                FROM scraper_health
                WHERE scraper_key = :scraper_key
                """,
                {"scraper_key": key},
                force_primary=True,
            )
            if row is None or row["state"] == HEALTHY:
                return HealthDecision(True, HEALTHY)

            next_retry_at = row["next_retry_at"]
            if next_retry_at is not None and current_time < next_retry_at:
                return HealthDecision(False, row["state"], next_retry_at)

            # Claim a bounded half-open probe. If its worker disappears, another
            # request may retry after SCRAPER_HEALTH_PROBE_INTERVAL.
            probe_lease = current_time + settings.SCRAPER_HEALTH_PROBE_INTERVAL
            await database.execute(
                """
                UPDATE scraper_health
                SET state = :state,
                    next_retry_at = :next_retry_at,
                    updated_at = :updated_at
                WHERE scraper_key = :scraper_key
                """,
                {
                    "state": HALF_OPEN,
                    "next_retry_at": probe_lease,
                    "updated_at": current_time,
                    "scraper_key": key,
                },
            )
            metrics.set_scraper_health(display_name, HALF_OPEN)
            return HealthDecision(True, HALF_OPEN, probe_lease)
        except Exception as error:
            logger.debug(
                f"Scraper health lookup failed open for {display_name}: "
                f"{type(error).__name__}"
            )
            return HealthDecision(True, HEALTHY)

    async def record_outcome(
        self,
        display_name: str,
        outcome: str,
        *,
        error_type: str | None = None,
        now: float | None = None,
    ) -> None:
        if not self._enabled():
            return

        current_time = time.time() if now is None else now
        key = scraper_health_key(display_name)
        try:
            row = await database.fetch_one(
                """
                SELECT *
                FROM scraper_health
                WHERE scraper_key = :scraper_key
                """,
                {"scraper_key": key},
                force_primary=True,
            )
            values = self._next_values(
                dict(row) if row is not None else None,
                display_name=display_name,
                outcome=outcome,
                error_type=error_type,
                now=current_time,
            )
            await database.execute(
                """
                INSERT INTO scraper_health (
                    scraper_key,
                    display_name,
                    state,
                    consecutive_failures,
                    recovery_successes,
                    backoff_level,
                    total_attempts,
                    total_successes,
                    total_failures,
                    last_outcome,
                    last_error_type,
                    last_attempt_at,
                    last_success_at,
                    next_retry_at,
                    updated_at
                ) VALUES (
                    :scraper_key,
                    :display_name,
                    :state,
                    :consecutive_failures,
                    :recovery_successes,
                    :backoff_level,
                    :total_attempts,
                    :total_successes,
                    :total_failures,
                    :last_outcome,
                    :last_error_type,
                    :last_attempt_at,
                    :last_success_at,
                    :next_retry_at,
                    :updated_at
                )
                ON CONFLICT (scraper_key) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    state = EXCLUDED.state,
                    consecutive_failures = EXCLUDED.consecutive_failures,
                    recovery_successes = EXCLUDED.recovery_successes,
                    backoff_level = EXCLUDED.backoff_level,
                    total_attempts = EXCLUDED.total_attempts,
                    total_successes = EXCLUDED.total_successes,
                    total_failures = EXCLUDED.total_failures,
                    last_outcome = EXCLUDED.last_outcome,
                    last_error_type = EXCLUDED.last_error_type,
                    last_attempt_at = EXCLUDED.last_attempt_at,
                    last_success_at = EXCLUDED.last_success_at,
                    next_retry_at = EXCLUDED.next_retry_at,
                    updated_at = EXCLUDED.updated_at
                """,
                values,
            )
            previous_state = row["state"] if row is not None else HEALTHY
            new_state = values["state"]
            metrics.set_scraper_health(display_name, new_state)
            if previous_state != new_state:
                if new_state == QUARANTINED:
                    logger.warning(
                        f"Scraper {display_name} quarantined until "
                        f"{values['next_retry_at']:.0f} after repeated {outcome} outcomes"
                    )
                    metrics.observe_scraper_quarantine(display_name, outcome)
                elif new_state == HEALTHY:
                    logger.log("SCRAPER", f"Scraper {display_name} recovered")
        except Exception as error:
            logger.debug(
                f"Failed to record scraper health for {display_name}: "
                f"{type(error).__name__}"
            )

    @staticmethod
    def _next_values(
        row: dict | None,
        *,
        display_name: str,
        outcome: str,
        error_type: str | None,
        now: float,
    ) -> dict:
        current = row or {}
        previous_state = current.get("state", HEALTHY)
        success = outcome == "success"
        total_attempts = int(current.get("total_attempts") or 0) + 1
        total_successes = int(current.get("total_successes") or 0) + int(success)
        total_failures = int(current.get("total_failures") or 0) + int(not success)
        last_success_at = current.get("last_success_at")
        backoff_level = int(current.get("backoff_level") or 0)
        recovery_successes = int(current.get("recovery_successes") or 0)

        if success:
            last_success_at = now
            consecutive_failures = 0
            if previous_state == HALF_OPEN:
                recovery_successes += 1
                if recovery_successes >= settings.SCRAPER_HEALTH_RECOVERY_SUCCESSES:
                    state = HEALTHY
                    recovery_successes = 0
                    backoff_level = 0
                    next_retry_at = None
                else:
                    state = QUARANTINED
                    next_retry_at = now + settings.SCRAPER_HEALTH_PROBE_INTERVAL
            else:
                state = HEALTHY
                recovery_successes = 0
                backoff_level = 0
                next_retry_at = None
        else:
            recovery_successes = 0
            consecutive_failures = int(current.get("consecutive_failures") or 0) + 1
            should_quarantine = (
                previous_state in {HALF_OPEN, QUARANTINED}
                or consecutive_failures >= settings.SCRAPER_HEALTH_FAILURE_THRESHOLD
            )
            if should_quarantine:
                cooldown = min(
                    settings.SCRAPER_HEALTH_INITIAL_COOLDOWN * (2**backoff_level),
                    settings.SCRAPER_HEALTH_MAX_COOLDOWN,
                )
                state = QUARANTINED
                next_retry_at = now + cooldown
                backoff_level = min(backoff_level + 1, 30)
            else:
                state = HEALTHY
                next_retry_at = None

        return {
            "scraper_key": scraper_health_key(display_name),
            "display_name": display_name,
            "state": state,
            "consecutive_failures": consecutive_failures,
            "recovery_successes": recovery_successes,
            "backoff_level": backoff_level,
            "total_attempts": total_attempts,
            "total_successes": total_successes,
            "total_failures": total_failures,
            "last_outcome": outcome,
            # Store only the exception class. Third-party messages and URLs may
            # contain API keys or embedded credentials.
            "last_error_type": error_type,
            "last_attempt_at": now,
            "last_success_at": last_success_at,
            "next_retry_at": next_retry_at,
            "updated_at": now,
        }


scraper_health = ScraperHealthManager()
