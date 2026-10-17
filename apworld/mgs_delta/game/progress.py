"""Progress toward checks, published to Archipelago data storage for trackers (the PopTracker pack reads it).

The key is mgsdelta_progress_<team>_<slot>; the value is Session.progress() (replaced as a whole when it changes).
"""
import json

PROGRESS_INTERVAL = 1.0      # seconds: at most one Set message per interval


def progress_key(team, slot):
    return f"mgsdelta_progress_{team}_{slot}"


class ProgressPublisher:
    """Decides when the progress dict is sent: when it changed and PROGRESS_INTERVAL has passed since the last send
    (the last change is always sent eventually). reset() on (re)connect so the first tick sends it again."""

    def __init__(self, clock):
        self.clock = clock
        self.reset()

    def reset(self):
        self.last_text = None
        self.last_sent = float("-inf")

    def message(self, team, slot, progress):
        """The Set message to send now, or None."""
        text = json.dumps(progress, sort_keys=True)
        now = self.clock()
        if text == self.last_text or now - self.last_sent < PROGRESS_INTERVAL:
            return None
        self.last_text, self.last_sent = text, now
        return {"cmd": "Set", "key": progress_key(team, slot), "default": {}, "want_reply": False,
                "operations": [{"operation": "replace", "value": progress}]}
