"""Client -> UE4SS mod command channel.

The client appends JSON lines to commands.jsonl in the mod folder; the MGSDeltaAP mod executes new lines on the
game thread (polled from its loop and from BeginPlay hooks; the read position is shared through commands.offset).
Every command carries "t" (unix seconds); the mod drops stale exec_proc/warp commands. Commands:
  {"cmd": "exec_proc", "proc": 126}              run a proc of the loaded stage script (e.g. an exit: door bounce)
  {"cmd": "warp", "stage": "s005a"}              Mgs3LoadCmd(stage, "r_sna01"), default spawn
  {"cmd": "popup", "title": "...", "text": "...", "style"?: "warning" | "bad"}  on-screen message (item; "bad" = red
                                     label: traps; "warning" = locked area: subtitle strip + red label)
  {"cmd": "notify_clear"}            remove the on-screen message a few seconds after it was shown (ModLink.clear_due)
  {"cmd": "codec_menu", "text": "..."}           text shown when calling the Archipelago codec frequency
  {"cmd": "codec_set", "freq": 14001, "text": "msg\x1emsg"}  stacked messages for a frequency ("" = unset)
  {"cmd": "camo_refresh", "camo": n, "facepaint": n}  rebuild Snake's camouflage model after GM+0x7AE/0x7AF were
                                     written (the worn types; without them the manager redraws its cached camo)
  Trap commands (the mod calls UE functions; failures are only logged):
  {"cmd": "disarm"}                  AGsrPlayer:ChangeWeaponSlot(0)
  {"cmd": "weather", "type": 2}      BP_Weather_System_C:SwitchWeather(n, true), n = E_WeatherType 0..7 (1 light
                                     rain, 2 heavy rain, 3 fine, 4 cloud, 5 fog, 6 snow, 7 wind); the mod remembers the
                                     weather it replaced
  {"cmd": "weather_restore", "old": 3}  switch back to the remembered weather (else to "old" if >= 0)
  {"cmd": "injury", "wound": 2}      GsrPairingDebugMenuState:CallbackAddPlayerWound with that EGsrPlayerWoundType
"""
import json
import time

try:
    from .paths import mod_file
except ImportError:      # loaded as a plain module
    from paths import mod_file

NOTIFY_SECONDS = 4
POPUP_GAP = 0.8            # s between one item label being cleared and the next one
CODEC_SEPARATOR = "\x1e"   # record separator between stacked messages
CODEC_PAGE_BREAK = "\f"    # a message that is just this ends the codec page (the mod pages on it)
DEFAULT_COMMAND_PATH = mod_file("commands.jsonl")


class ModLink:
    """Writer of the command file the mod polls, plus the popup queue (held until the player can see them)."""

    def __init__(self, path=DEFAULT_COMMAND_PATH):
        self.path = path
        self.seq = int(time.time() * 1000)
        self.popup_at = None
        self.cleared_at = None   # when the last label was cleared (the next one waits POPUP_GAP)
        self.clock = time.monotonic
        self.held = []           # popups waiting for the player to be back in play (see flush)

    def send(self, cmd, **args):
        """Append one command line (with sequence number and time); a write error drops the command."""
        self.seq += 1
        line = json.dumps(dict(args, cmd=cmd, seq=self.seq, t=int(time.time())))
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def exec_proc(self, proc):
        """Run a proc of the loaded stage script."""
        self.send("exec_proc", proc=int(proc))

    def warp(self, stage, ttl=None):
        """ttl: seconds the command stays valid (default: the mod's 15 s for bounces)."""
        if ttl is None:
            self.send("warp", stage=stage)
        else:
            self.send("warp", stage=stage, ttl=int(ttl))

    def popup(self, title, text="", style=""):
        """style "warning": the caption strip plus a red label (locks); "bad": a red label (traps); default: the small
        floating item label (white).
        The mod has no reliable timer, so `clear_due()` asks for an explicit "notify_clear" a few seconds later.
        Popups are held until `flush(True)`: a label shown during a stage load is lost, so the session flushes only
        once the player is back in play."""
        self.held.append((title, text, style))

    def flush(self, ready):
        """Send held popups once the player can see them: warnings (caption strip) at once, item labels one at a time.
        The mod has a single label, so a burst of labels overwrote each other (only the last one was seen); each label
        now waits until the previous one was cleared plus POPUP_GAP. A styled one (re)starts the clear timer too: it
        stayed on screen until the next popup replaced it."""
        if not ready or not self.held:
            return
        for entry in [e for e in self.held if e[2]]:
            self.held.remove(entry)
            self.popup_at = self.clock()
            self.send("popup", title=entry[0], text=entry[1], style=entry[2])
        if not self.held or self.popup_at is not None:
            return
        if self.cleared_at is not None and self.clock() - self.cleared_at < POPUP_GAP:
            return
        title, text, _style = self.held.pop(0)
        self.popup_at = self.clock()
        self.send("popup", title=title, text=text)

    def clear_due(self, seconds=NOTIFY_SECONDS):
        """Send "notify_clear" once the last on-screen message has been shown for `seconds`."""
        if self.popup_at is not None and self.clock() - self.popup_at >= seconds:
            self.popup_at = None
            self.cleared_at = self.clock()
            self.send("notify_clear")

    def codec_menu(self, text):
        """Set the text of the Archipelago codec frequency."""
        self.send("codec_menu", text=text)

    def codec_set(self, freq, messages):
        """Messages shown (as one multi-line message) when `freq` (x100, e.g. 14001) is called; [] = "No response."."""
        self.send("codec_set", freq=int(freq), text=CODEC_SEPARATOR.join(messages))

    # --- trap commands ---
    def disarm(self):
        self.send("disarm")

    def weather(self, weather_type):
        self.send("weather", type=int(weather_type))

    def weather_restore(self, old=None):
        self.send("weather_restore", old=-1 if old is None else int(old))

    def injury(self, wound=2):
        self.send("injury", wound=int(wound))

    def camo_refresh(self, camo=None, facepaint=None):
        """Ask the mod to re-apply the worn camo / face paint (camo / face paint types, GM+0x7AE / 0x7AF). The camouflage
        manager redraws its own cached camo, so the mod sets the types first (UpdateCamouflageByNoPairing)."""
        if camo is None or facepaint is None:
            self.send("camo_refresh")
        else:
            self.send("camo_refresh", camo=int(camo), facepaint=int(facepaint))
