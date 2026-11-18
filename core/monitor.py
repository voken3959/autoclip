
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
    def __init__(self, channel: str = ""):
        self.channel = channel or os.getenv("TWITCH_CHANNEL", "jynxzi")
        self.calibration_seconds = env_int("CALIBRATION_SECONDS", 120)
        self.window_seconds = env_int("BASELINE_WINDOW_SECONDS", 300)
        self.update_seconds = env_int("BASELINE_UPDATE_SECONDS", 10)
        self.multiplier_threshold = env_float("SPIKE_MULTIPLIER", 2.5)
        self.min_extra = env_float("SPIKE_MIN_EXTRA_MSGS_PER_SEC", 5)
        self.confirm_seconds = env_int("SPIKE_CONFIRM_SECONDS", 3)
        self.cooldown_seconds = env_int("CLIP_COOLDOWN_SECONDS", 90)

        # Everything that belongs to one watched channel lives in _reset_state,
        # so switching channels can start again from a clean slate.
        self.chat_message_id = 0
        # Called with each new chat message the moment it arrives.
        self.on_chat = None
        # Optional: badges tag (str) -> list of badge dicts, set by main.
        self.badges_resolver = None
        self._reset_state()

    def _reset_state(self):
        self.started_at = time.monotonic()
        self.second_counts = deque(
            maxlen=max(self.window_seconds, self.calibration_seconds) + 5
        )

        self.current_second = int(time.monotonic())
        self.current_count = 0
        self.total_messages = 0
        self.total_chatters = set()

        self.baseline = None
        self.last_baseline_update = 0.0
        self.above_threshold_streak = 0
        self.last_detection_at = 0.0

        self.last_event = None
        self.event_id = 0
        self.events = deque(maxlen=30)
        self.clips = deque(maxlen=30)

        # Live chat storage (message IDs keep counting up across channels)
        self.chat_messages = deque(maxlen=100)

        self.last_message_at = None
        self.connection_status = "connecting"
        self.last_error = None
        self.last_rate = 0.0

    def switch_channel(self, channel: str):
        """Forget the old channel's chat, baseline, spikes and clips."""
        self.channel = channel
        status, error = self.connection_status, self.last_error
        self._reset_state()
        self.connection_status, self.last_error = status, error

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
        if (
            self.baseline is None
            or now - self.last_baseline_update >= self.update_seconds
        ):
            values = list(self.second_counts)[-self.window_seconds:]

            if self.baseline is None:
                clean = values
            else:
                cap = max(self.baseline * 1.8, self.baseline + 2.0)
                clean = [value for value in values if value <= cap]

            if clean:
                self.baseline = sum(clean) / len(clean)

            self.last_baseline_update = now

    def add_message(self, message: dict):
        self.current_count += 1
        self.total_messages += 1

        username = message.get("username", "viewer")
        display_name = message.get("display_name", username)
        message_text = message.get("text", "")

        self.total_chatters.add(username.lower())
        self.last_message_at = datetime.now(timezone.utc).isoformat()

        self.chat_message_id += 1

        entry = {
            "id": self.chat_message_id,
            "username": username,
            "display_name": display_name,
            "text": message_text,
            "time": self.last_message_at,
            "badges": [],
        }

        badges_tag = message.get("badges", "")
        if badges_tag and self.badges_resolver:
            try:
                entry["badges"] = self.badges_resolver(badges_tag) or []
            except Exception:
                pass  # badges are cosmetic; never let them break chat

        self.chat_messages.append(entry)

        if self.on_chat:
            try:
                self.on_chat(entry)
            except Exception:
                pass  # a broken listener must never stop chat from being counted

    def tick(self):
        """Run once per dashboard second."""
        now = time.monotonic()

        self._roll_second()

        elapsed = now - self.started_at

        if elapsed < self.calibration_seconds:
            return None

        if self.baseline is None:
            self.baseline = self._calibration_average()
            self.last_baseline_update = now

        recent = list(self.second_counts)[-5:]
        recent_rate = sum(recent) / max(1, len(recent))

        baseline = max(self.baseline or 0.0, 0.5)

        threshold = max(
            baseline * self.multiplier_threshold,
            baseline + self.min_extra
        )

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

            self.event_id += 1

            event = {
                "id": self.event_id,
                "time": datetime.now(timezone.utc).isoformat(),
                "rate": round(recent_rate, 2),
                "baseline": round(baseline, 2),
                "multiplier": round(multiplier, 2),
                "score": min(
                    100,
                    round((multiplier / self.multiplier_threshold) * 60)
                )
            }

            self.events.appendleft(event)
            self.last_event = event
            self.last_detection_at = now
            self.above_threshold_streak = 0

        self._update_baseline(now)

        return event

    def add_clip(self, clip: dict):
        self.clips.appendleft(clip)

    def threshold_for(self, baseline: float) -> float:
        """The rate chat has to stay above to count as a spike (same maths as tick)."""
        baseline = max(baseline, 0.5)
        return max(baseline * self.multiplier_threshold, baseline + self.min_extra)

    def peak_since(self, start_tick: int) -> tuple[int, float]:
        """When chat was busiest since `start_tick` (a second number, see `tick`).

        Returns (second number, messages per second averaged over 3 seconds).
        If several moments are equally busy the earliest one wins.
        """
        counts = list(self.second_counts)
        first_tick = self.current_second - len(counts)
        best_tick, best = start_tick, -1.0
        for i in range(max(0, start_tick - first_tick), len(counts)):
            window = counts[max(0, i - 2): i + 1]
            smooth = sum(window) / len(window)
            if smooth > best + 1e-9:
                best, best_tick = smooth, first_tick + i
        return best_tick, max(best, 0.0)

    def _avg_recent(self, seconds: int) -> float:
        recent = list(self.second_counts)[-seconds:]
        if not recent:
            return float(self.current_count)
        return sum(recent) / len(recent)

    @staticmethod
    def _with_age(item: dict, now: datetime) -> dict:
        """Copy of an event/clip record plus how many seconds ago it happened."""
        data = dict(item)
        try:
            then = datetime.fromisoformat(item["time"])
            data["ago"] = max(0.0, round((now - then).total_seconds(), 1))
        except (KeyError, TypeError, ValueError):
            data["ago"] = None
        return data

    def snapshot(self, include_chat: bool = True):
        self._roll_second()

        elapsed = time.monotonic() - self.started_at
        calibrating = elapsed < self.calibration_seconds

        if calibrating:
            progress = min(
                100,
                round(elapsed / self.calibration_seconds * 100)
            )
            phase = "calibrating"
        else:
            progress = 100
            phase = "monitoring"

        baseline = self.baseline

        # Smoothed over the last few *completed* seconds, so the number does
        # not jump around depending on where in the second we sample it.
        rate = self._avg_recent(3)
        # What spike detection itself compares against the baseline.
        detect_rate = self._avg_recent(5)

        if baseline is not None:
            floor = max(baseline, 0.5)
            multiplier = detect_rate / floor
            threshold = self.threshold_for(baseline)
            score = min(
                100,
                round(
                    (multiplier / max(self.multiplier_threshold, 0.1)) * 60
                )
            )
        else:
            multiplier = 0
            threshold = None
            score = 0

        now = datetime.now(timezone.utc)

        data = {
            "channel": self.channel,
            "connection": self.connection_status,
            "error": self.last_error,
            "phase": phase,
            "calibration_progress": progress,
            "calibration_elapsed": int(elapsed),
            "calibration_total": self.calibration_seconds,
            "messages_per_second": round(rate, 2),
            "recent_rate": round(detect_rate, 2),
            "baseline": round(baseline, 2) if baseline is not None else None,
            "threshold": round(threshold, 2) if threshold is not None else None,
            "spike_multiplier": self.multiplier_threshold,
            "multiplier": round(multiplier, 2),
            "hype_score": score,
            "total_messages": self.total_messages,
            "unique_chatters": len(self.total_chatters),
            "last_message_at": self.last_message_at,
            "last_event": self.last_event,
            "events": [self._with_age(e, now) for e in self.events],
            "clips": [self._with_age(c, now) for c in self.clips],

            # Completed per-second message counts, oldest first, for the chart.
            # `tick` is the current second's number, so the chart can tell
            # exactly how far to slide between two updates.
            "history": list(self.second_counts)[-120:],
            "tick": self.current_second,
            "server_time": now.timestamp(),
        }

        # The per-second graph updates leave chat out: messages are pushed on
        # their own as they arrive (see `on_chat`).
        if include_chat:
            data["chat_messages"] = list(self.chat_messages)

        return data
