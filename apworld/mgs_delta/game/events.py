"""Reads the MGSDeltaAP UE4SS mod's events.jsonl and turns raw item_end events into pickups.

An item actor ending play is either a pickup or part of a stage unload. A stage unload ends every remaining
item and is always followed by a stage event, so pending item_end events are classified as:
  - a stage event arrives within BATCH_WINDOW  -> they were an unload
  - BATCH_WINDOW passes without a stage event  -> each one is a pickup
Several pickups can end at the same moment (e.g. after unpausing during the pickup message), so the
number of items in a batch is not used. While the client waits for a warp it sent (`loading`), pending item_end
events never time out into pickups: the warp's unload ends them, and its stage event can come late (a Life Medicine
ending under a codec warp counted as a find).
"""
import json
import os
import time

try:
    from .paths import mod_file
except ImportError:      # loaded as a plain module
    from paths import mod_file

DEFAULT_EVENT_PATH = mod_file("events.jsonl")

# How long to wait for a stage event after an item_end before treating it as a pickup (a door fade takes ~3 s).
BATCH_WINDOW = 4.0
# item_end events read within this many seconds of each other arrived together.
SIMULTANEOUS = 0.2


class EventReader:
    def __init__(self, path=DEFAULT_EVENT_PATH, from_start=False):
        self.path = path
        self.offset = 0
        if not from_start and os.path.exists(path):
            self.offset = os.path.getsize(path)

    def read_new(self):
        """Returns the events appended since the last call."""
        if not os.path.exists(self.path):
            return []
        size = os.path.getsize(self.path)
        if size < self.offset:  # file was truncated/recreated
            self.offset = 0
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read()
        # Only consume complete lines; a partial last line is read next time.
        end = data.rfind(b"\n") + 1
        self.offset += end
        events = []
        for line in data[:end].splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return events


class PickupDetector:
    """Feeds raw events through a short batching window and yields classified events."""

    def __init__(self, reader):
        self.reader = reader
        self.pending = []
        self.pending_since = None
        self.loading = False     # set by the client while a warp it sent is loading: item_end events are an unload

    def poll(self):
        """Returns a list of ("pickup" | "unload" | "stage" | "other", event_or_events)."""
        out = []
        for ev in self.reader.read_new():
            ev["_seen_at"] = time.monotonic()
            if ev.get("type") == "item_end":
                if not self.pending:
                    self.pending_since = time.monotonic()
                self.pending.append(ev)
            elif ev.get("type") == "stage":
                out += self._flush(unload=True)
                out.append(("stage", ev))
            else:
                out.append(("other", ev))
        if self.pending and self.loading:
            self.pending_since = time.monotonic()        # the window starts again once the warp is over
        elif self.pending and time.monotonic() - self.pending_since >= BATCH_WINDOW:
            out += self._flush(unload=False)
        return out

    def discard_pending(self):
        """Drop the item_end events still waiting (a death's reload ended them). Returns how many."""
        dropped, self.pending = len(self.pending), []
        return dropped

    def _flush(self, unload):
        batch, self.pending = self.pending, []
        if not batch:
            return []
        # Quitting to the title screen ends all items without a following stage event. Three or more items
        # ending together is never a pickup.
        together = len(batch) >= 3 and batch[-1]["_seen_at"] - batch[0]["_seen_at"] < SIMULTANEOUS
        if unload or together:
            return [("unload", batch)]
        return [("pickup", ev) for ev in batch]
