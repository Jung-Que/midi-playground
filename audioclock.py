from time import time
from typing import Callable, Optional


class AudioClock:
    """Tracks song time from the mixer when possible, with a monotonic wall-clock fallback."""

    def __init__(
            self,
            position_ms: Callable[[], int],
            time_fn: Callable[[], float] = time,
    ):
        self._position_ms = position_ms
        self._time = time_fn
        self.started_at = self._time()
        self.last_position = 0.0
        self.drift_ms = 0.0
        self.using_mixer = False

    def start_now(self, now: Optional[float] = None) -> float:
        self.started_at = self._time() if now is None else now
        self.last_position = 0.0
        self.drift_ms = 0.0
        self.using_mixer = False
        return self.started_at

    def start_from_transition(
            self,
            event_time: float,
            max_probe_ms: int,
            now: Optional[float] = None,
    ) -> float:
        now = self._time() if now is None else now
        mixer_ms = self._safe_mixer_position()
        if 0 <= mixer_ms <= max(int(max_probe_ms), 0):
            self.started_at = now - mixer_ms / 1000
            self.last_position = mixer_ms / 1000
            self.using_mixer = True
        else:
            self.started_at = event_time or now
            self.last_position = max(0.0, now - self.started_at)
            self.using_mixer = False
        self.drift_ms = 0.0
        return self.started_at

    def _safe_mixer_position(self) -> int:
        try:
            return int(self._position_ms())
        except Exception:
            return -1

    def position(self, now: Optional[float] = None) -> float:
        now = self._time() if now is None else now
        wall_position = max(0.0, now - self.started_at)
        mixer_ms = self._safe_mixer_position()
        mixer_position = mixer_ms / 1000

        mixer_is_plausible = (
            mixer_ms >= 0 and
            abs(mixer_position - wall_position) <= 2.0 and
            mixer_position + 0.05 >= self.last_position
        )
        if mixer_is_plausible:
            self.drift_ms = (mixer_position - wall_position) * 1000
            position = mixer_position
            self.using_mixer = True
        else:
            self.drift_ms = 0.0
            position = wall_position
            self.using_mixer = False

        self.last_position = max(self.last_position, position)
        return self.last_position
