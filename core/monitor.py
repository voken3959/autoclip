import os
import time
from collections import deque
from datetime import datetime, timezone


def env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Monitor:
    def __init__(self):
        self.calibration_seconds = env_int("CALIBRATION_SECONDS", 120)
        self.window_seconds = env_int("BASELINE_WINDOW_SECONDS", 300)
        self.update_seconds = env_int("BASELINE_UPDATE_SECONDS", 10)
        self.multiplier_threshold = env_float("SPIKE_MULTIPLIER", 2.5)
        self.min_extra = env_float("SPIKE_MIN_EXTRA_MSGS_PER_SEC", 5)
        self.confirm_seconds = env_int("SPIKE_CONFIRM_SECONDS", 3)
        self.cooldown_seconds = env_int("CLIP_COOLDOWN_SECONDS", 90)

        self.started_at = time.monotonic()
        self.second_counts = deque(maxlen=max(self.window_seconds, self.calibration_seconds) + 5)
        self.current_second = int(time.monotonic())
        self.current_count = 0
        self.total_messages = 0
        self.total_chatters = set()
        self.baseline = None
        self.last_baseline_update = 0.0
        self.above_threshold_streak = 0
        self.last_detection_at = 0.0
        self.last_event = None
        self.events = deque(maxlen=30)
        self.clips = deque(maxlen=30)
        self.last_message_at = None
        self.connection_status = "connecting"
        self.last_error = None
        self.last_rate = 0.0

    def set_connection(self, status: str, error: str | None = None):
        self.connection_status = status
        self.last_error = error

    def _roll_second(self):
        now_second = int(time.monotonic())
        while self.current_second < now_second:
            self.second_counts.append(self.current_count)
            self.last_rate = float(self.current_count)
            self.current_count = 0
            self.current_second += 1

    def _calibration_average(self):
        values = list(self.second_counts)[-self.calibration_seconds:]
        return sum(values) / len(values) if values else 0.0

    def _update_baseline(self, now: float):
        if self.baseline is None or now - self.last_baseline_update >= self.update_seconds:
            values = list(self.second_counts)[-self.window_seconds:]
            if self.baseline is None:
                clean = values
            else:
                # Exclude large outliers so an exciting moment does not inflate
                # the definition of normal chat activity.
                cap = max(self.baseline * 1.8, self.baseline + 2.0)
                clean = [value for value in values if value <= cap]
            if clean:
                self.baseline = sum(clean) / len(clean)
            self.last_baseline_update = now

    def add_message(self, message: dict):
        self.current_count += 1
        self.total_messages += 1
        username = message.get("username", "viewer")
        self.total_chatters.add(username.lower())
        self.last_message_at = datetime.now(timezone.utc).isoformat()

    def tick(self):
        """Run exactly once per dashboard second; detection streaks are seconds, not messages."""
        now = time.monotonic()
        self._roll_second()
        elapsed = now - self.started_at
        if elapsed < self.calibration_seconds:
            return None

        if self.baseline is None:
            self.baseline = self._calibration_average()
            self.last_baseline_update = now

        # Evaluate a five-second moving rate, once per second.
        recent = list(self.second_counts)[-5:]
        recent_rate = sum(recent) / max(1, len(recent))
        baseline = max(self.baseline or 0.0, 0.5)
        threshold = max(baseline * self.multiplier_threshold, baseline + self.min_extra)

        if recent_rate >= threshold:
            self.above_threshold_streak += 1
        else:
            self.above_threshold_streak = 0

        event = None
        if (
            self.above_threshold_streak >= self.confirm_seconds
            and now - self.last_detection_at >= self.cooldown_seconds
        ):
            multiplier = recent_rate / baseline
            event = {
                "time": datetime.now(timezone.utc).isoformat(),
                "rate": round(recent_rate, 2),
                "baseline": round(baseline, 2),
                "multiplier": round(multiplier, 2),
                "score": min(100, round((multiplier / self.multiplier_threshold) * 60)),
            }
            self.events.appendleft(event)
            self.last_event = event
            self.last_detection_at = now
            self.above_threshold_streak = 0

        # Update only after evaluating this second. The outlier filter protects
        # the rolling average from the spike that may have just been detected.
        self._update_baseline(now)
        return event

    def add_clip(self, clip: dict):
        self.clips.appendleft(clip)

    def snapshot(self):
        self._roll_second()
        elapsed = time.monotonic() - self.started_at
        calibrating = elapsed < self.calibration_seconds
        if calibrating:
            progress = min(100, round(elapsed / self.calibration_seconds * 100))
            phase = "calibrating"
        else:
            progress = 100
            phase = "monitoring"
        baseline = self.baseline
        current_rate = self.last_rate if int(time.monotonic()) != self.current_second else self.current_count
        if baseline and baseline > 0:
            multiplier = current_rate / baseline
            score = min(100, round((multiplier / max(self.multiplier_threshold, 0.1)) * 60))
        else:
            multiplier = 0
            score = 0
        return {
            "channel": os.getenv("TWITCH_CHANNEL", "jynxzi"),
            "connection": self.connection_status,
            "error": self.last_error,
            "phase": phase,
            "calibration_progress": progress,
            "calibration_elapsed": int(elapsed),
            "calibration_total": self.calibration_seconds,
            "messages_per_second": current_rate,
            "baseline": round(baseline, 2) if baseline is not None else None,
            "multiplier": round(multiplier, 2),
            "hype_score": score,
            "total_messages": self.total_messages,
            "unique_chatters": len(self.total_chatters),
            "last_message_at": self.last_message_at,
            "last_event": self.last_event,
            "events": list(self.events),
            "clips": list(self.clips),
            "auto_clip_enabled": os.getenv("AUTO_CLIP_ENABLED", "false").lower() == "true",
        }
