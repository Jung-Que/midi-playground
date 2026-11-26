from time import perf_counter
from typing import Callable, Optional


class AudioClock:
    """Song time that advances smoothly with the wall clock and is slowly steered onto the mixer position.

    pygame.mixer.music.get_pos() only moves in audio-buffer steps (6-21 ms per 16 ms frame measured), so using it
    directly makes everything placed from song time shake by several pixels. The wall clock runs smoothly; the
    mixer is used only to correct drift, gently, unless it is far off (a stall or seek), where it snaps.
    """

    smoothing_seconds = 0.5  # time constant of the drift correction
    snap_seconds = 0.08  # larger mismatches are corrected at once

    def __init__(
            self,
            position_ms: Callable[[], int],
            time_fn: Callable[[], float] = perf_counter,
    ):
        self._position_ms = position_ms
        self._time = time_fn
        self.started_at = self._time()
        self.last_position = 0.0
        self.drift_ms = 0.0
        self.using_mixer = False
        self.offset = 0.0  # seconds added to the wall-clock position
        self._last_now: Optional[float] = None

    def _reset(self):
        self.drift_ms = 0.0
        self.offset = 0.0
        self._last_now = None

    def start_now(self, now: Optional[float] = None) -> float:
        self.started_at = self._time() if now is None else now
        self.last_position = 0.0
        self.using_mixer = False
        self._reset()
        return self.started_at

    def start_from_transition(
            self,
            event_time: float,
            max_probe_ms: int,
            now: Optional[float] = None,
    ) -> float:
        now = self._time() if now is None else now
        mixer_ms = self._safe_mixer_position()
        self._reset()
        if 0 <= mixer_ms <= max(int(max_probe_ms), 0):
            self.started_at = now - mixer_ms / 1000
            self.last_position = mixer_ms / 1000
            self.using_mixer = True
        else:
            self.started_at = event_time or now
            self.last_position = max(0.0, now - self.started_at)
            self.using_mixer = False
        return self.started_at

    def _safe_mixer_position(self) -> int:
        try:
            return int(self._position_ms())
        except Exception:
            return -1

    def position(self, now: Optional[float] = None, present_at: Optional[float] = None) -> float:
        """Song position at present_at (default: now) - the moment the frame being drawn reaches the screen.
        The mixer is compared with the wall clock at `now`, so drawing ahead does not bend the steering."""
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
            error = mixer_position - (wall_position + self.offset)
            if abs(error) > self.snap_seconds:
                self.offset += error
            elif self._last_now is not None:
                elapsed = max(now - self._last_now, 0.0)
                self.offset += error * min(1.0, elapsed / self.smoothing_seconds)
            self.drift_ms = (mixer_position - wall_position) * 1000
            self.using_mixer = True
        else:
            self.drift_ms = 0.0
            self.using_mixer = False
        self._last_now = now

        if present_at is not None:
            wall_position = max(0.0, present_at - self.started_at)
        position = max(0.0, wall_position + self.offset)
        self.last_position = max(self.last_position, position)
        return self.last_position
