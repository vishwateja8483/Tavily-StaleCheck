"""Cooperative, caller-owned limits for one gateway/search journey.

This bounds transport timeouts, not platform DNS or an uncooperative transport.
Only normalized accounting metadata belongs in the ledger; never requests or keys.
"""
import math
import time

from .contracts import ValidationError, require


class JourneyBudget:
    def __init__(self, max_attempts=25, max_seconds=180, *, monotonic=time.monotonic):
        require(type(max_attempts) is int and 1 <= max_attempts <= 25, "invalid journey attempt limit")
        require(type(max_seconds) in (int, float) and 0 < max_seconds <= 180, "invalid journey time limit")
        self.max_attempts = max_attempts
        self.max_seconds = max_seconds
        self.records = []
        self._monotonic = monotonic
        self._deadline = monotonic() + max_seconds

    def remaining_seconds(self):
        """Remaining cooperative time, including any human approval wait."""
        return max(0.0, self._deadline - self._monotonic())

    def check(self):
        """Check elapsed time; consuming the final permitted attempt is valid."""
        if self._monotonic() >= self._deadline:
            raise ValidationError("journey time budget exhausted")

    def begin(self, service, timeout):
        require(service in ("gateway", "search"), "invalid journey service")
        require(type(timeout) in (int, float) and 0 < timeout < math.inf, "invalid journey timeout")
        self.check()
        require(len(self.records) < self.max_attempts, "journey attempt budget exhausted")
        remaining = self._deadline - self._monotonic()
        require(remaining > 0, "journey time budget exhausted")
        # Failed until the adapter has validated the response, including its contract.
        record = {"service": service, "status": "failed", "usage": None, "routing": None}
        self.records.append(record)
        return record, min(timeout, remaining)

    def finish(self, attempt, usage=None, status="ok", routing=None):
        """Finalize accounting without checking limits or hiding a caller's error.

        Adapters supply sanitized metadata; unknown attempt handles are ignored.
        """
        if not any(attempt is record for record in self.records):
            return
        attempt.update(status="ok" if status == "ok" else "failed", usage=usage, routing=routing)
