from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional


class DeadlineError(TimeoutError):
    pass


class DeadlineConfigurationError(DeadlineError):
    pass


class DeadlineExpiredError(DeadlineError):
    pass


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class Deadline:
    expires_at: float

    @classmethod
    def after(cls, seconds: float) -> "Deadline":
        if not math.isfinite(seconds) or seconds <= 0:
            raise DeadlineConfigurationError("Deadline duration must be finite and positive.")
        return cls(time.monotonic() + seconds)

    def remaining(self, maximum: Optional[float] = None, reserve: float = 0.0) -> float:
        if not math.isfinite(reserve) or reserve < 0:
            raise DeadlineConfigurationError("Deadline reserve must be finite and nonnegative.")
        seconds = self.expires_at - time.monotonic() - reserve
        if seconds <= 0:
            raise DeadlineExpiredError("Operation deadline expired.")
        if maximum is None:
            return seconds
        if not math.isfinite(maximum) or maximum <= 0:
            raise DeadlineConfigurationError("Deadline maximum must be finite and positive.")
        return min(seconds, maximum)
