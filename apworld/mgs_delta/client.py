"""MGS Delta Archipelago client: connects the game (via the UE4SS mod + memory) to the AP server.

Networking lives here; the run logic is in game/session.py (Session), game primitives in game/bridge.py.
"""
import asyncio
import logging
import time

import Utils
from CommonClient import (ClientCommandProcessor, CommonContext, get_base_parser, gui_enabled, handle_url_arg,
                          logger, server_loop)
from NetUtils import ClientStatus

from .data_loader import load
from .game import effects
from .game.bridge import Bridge
from .game.modlink import ModLink
from .game.progress import ProgressPublisher
from .game.session import Session
from .game.state import RunState
from .items import GAME, ITEM_BY_KEY, ITEM_TABLE
from .locations import BOX_IDS, BOX_TABLE, LOCATION_NAME_TO_ID, SECTION_BY_ID

POLL_SECONDS = 0.05
ITEM_KEY_BY_NAME = {e["name"]: (e["kind"], e["id"]) for e in ITEM_TABLE}


def build_sections(slot_data):
    """Ordered sections from slot data (unlock item and count, areas) plus their display names."""
    out = []
    for sid, sec in (slot_data.get("sections") or {}).items():
        name = SECTION_BY_ID.get(sid, {}).get("name", sid)
        item = sec.get("unlock_item", sec.get("unlock"))      # older slot data: "unlock" only
        count = sec.get("unlock_count", 1 if item else 0)
        out.append({"id": sid, "name": name, "unlock": sec.get("unlock"), "unlock_item": item if count else None,
                    "unlock_count": count, "areas": sec.get("areas", []),
                    # where a gate physically is belongs to the client (fixed gates reach older seeds too)
                    "gates": SECTION_BY_ID.get(sid, {}).get("gates", sec.get("gates", []))})
    return out


class MGSDeltaCommandProcessor(ClientCommandProcessor):
    """Client console commands (/goal, /startrun, /game)."""

    def _cmd_goal(self):
        """Mark the end of the game as reached (the goal is sent then)."""
        session = self.ctx.session
        if session is None:
            self.output("Not connected to a slot yet.")
            return
        session.force_goal()
        self.output("Goal reached; it is sent as soon as the required pieces are received.")

    def _cmd_startrun(self):
        """Start this seed's run on the loaded save even though it is past the Virtuous Mission (wipes the inventory)."""
        session = self.ctx.session
        if session is None:
            self.output("Not connected to a slot yet.")
            return
        if session.state.initialized:
            self.output("The run has already started.")
            return
        session.allow_later_save = True
        self.output("The run starts on this save once Snake is in play.")

    def _cmd_game(self):
        """Show the game connection status."""
        bridge = self.ctx.bridge
        self.output(f"Game attached: {bridge.game is not None}, stage: {bridge.stage}, masking: {bridge.masking}")
        session = self.ctx.session
        if session:
            run_state = session.state
            self.output(f"Run started: {run_state.initialized}; found: " +
                        ", ".join(f"{acq_type} {len(ids)}" for acq_type, ids in run_state.acquired.items()) +
                        f"; sections completed: {', '.join(run_state.completed) or 'none'}")


class MGSDeltaContext(CommonContext):
    """Archipelago connection of the client; owns the game bridge, the mod link and (once connected) the Session."""
    game = GAME
    items_handling = 0b111  # receive all items, including our own and the start inventory
    command_processor = MGSDeltaCommandProcessor

    def __init__(self, server_address, password):
        super().__init__(server_address, password)
        self.bridge = Bridge(BOX_TABLE, BOX_IDS, ITEM_BY_KEY, log=logger.info)
        self.modlink = ModLink()
        self.session = None
        self.progress_publisher = ProgressPublisher(time.monotonic)
        self._received_cache = []

    async def server_auth(self, password_requested=False):
        if password_requested and not self.password:
            await super().server_auth(password_requested)
        await self.get_username()
        await self.send_connect()

    def on_package(self, cmd, args):
        """On "Connected": load this seed/slot's run state, build the Session and ask what every location holds."""
        if cmd == "Connected":
            slot_data = args.get("slot_data") or {}
            state_path = Utils.user_path(f"mgs_delta_{self.server_seed_name or self.seed_name}_{self.slot}.json")
            state = RunState(state_path)
            self.session = Session(self.bridge, self.modlink, state, slot_data, LOCATION_NAME_TO_ID,
                                   ITEM_KEY_BY_NAME, build_sections(slot_data), load("exits.json"),
                                   log=logger.info, gamestate=load("gamestate.json"), entries=load("entries.json"),
                                   bosses=load("bosses.json"), checkpoints=load("checkpoints.json"),
                                   animals=load("animals.json"),
                                   detail=logging.getLogger("MGSDeltaAP").info)   # not shown in the window
            self._received_cache = []
            self.progress_publisher.reset()
            death_link = bool(slot_data.get("options", {}).get("death_link", 0))
            Utils.async_start(self.update_death_link(death_link))
            # learn what every location holds, for the "Found X for Player" label when a check is sent
            Utils.async_start(self.send_msgs([{"cmd": "LocationScouts", "locations": sorted(self.server_locations),
                                               "create_as_hint": 0}]))

    def announce_found(self, location_ids):
        """Label for checks holding another player's item ("Found X for Alice"); own items get the "Received" label."""
        for location_id in location_ids:
            info = self.locations_info.get(location_id)
            if info is None or info.player == self.slot:
                continue
            item = self.item_names.lookup_in_slot(info.item, info.player)
            player = self.player_names.get(info.player, "someone")
            self.modlink.popup("Found", f"Found {item} for {player}")

    def on_deathlink(self, data):
        super().on_deathlink(data)
        if self.session and self.bridge.game is not None:
            self.session.kill()

    def received(self):
        """[(item name, sender name)] for every received item, in order (cached per length)."""
        cache = self._received_cache
        for item in self.items_received[len(cache):]:
            name = self.item_names.lookup_in_game(item.item)
            sender = "You" if item.player == self.slot else self.player_names.get(item.player, "Archipelago")
            cache.append((name, sender))
        return cache

    def make_gui(self):
        ui = super().make_gui()
        ui.base_title = "MGS Delta Client"
        return ui


async def game_watcher(ctx: MGSDeltaContext):
    """Poll loop: keep the game attached, tick the Session and send what it returns (checks, goal, DeathLink)."""
    while not ctx.exit_event.is_set():
        await asyncio.sleep(POLL_SECONDS)
        bridge = ctx.bridge
        try:
            if not bridge.connected():
                continue
        except Exception as e:     # a half-started or exiting game process: retry, never end the watcher
            logger.debug(f"Attach failed: {e}")
            bridge.drop()
            await asyncio.sleep(1.0)
            continue
        try:
            effects.tick(bridge.game)
            session = ctx.session
            if session is None or ctx.slot is None:
                continue
            checked = ctx.checked_locations | ctx.locations_checked
            result = session.tick(ctx.received(), checked, ctx.server_locations)
            new_locations = [location_id for location_id in dict.fromkeys(result.locations)
                             if location_id in ctx.server_locations and location_id not in checked]
            if new_locations:
                ctx.locations_checked.update(new_locations)
                await ctx.send_msgs([{"cmd": "LocationChecks", "locations": new_locations}])
                ctx.announce_found(new_locations)
            progress_message = ctx.progress_publisher.message(ctx.team, ctx.slot, session.progress())
            if progress_message:
                await ctx.send_msgs([progress_message])
            if result.goal and not ctx.finished_game:
                ctx.finished_game = True
                await ctx.send_msgs([{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}])
                if session.opt("release_on_goal"):
                    logger.info("Release on Goal: sending !release (subject to the server's release permission)")
                    await ctx.send_msgs([{"cmd": "Say", "text": "!release"}])
            if result.death and "DeathLink" in ctx.tags:
                await ctx.send_death(result.death)
        except OSError:
            logger.info("Lost connection to the game; waiting for it to restart.")
            bridge.drop()
        except Exception as e:     # keep the watcher alive; log the bug
            logger.exception(f"Game watcher error: {e}")
            await asyncio.sleep(1.0)


def launch(*args):
    """Entry point of the MGS Delta client (the launcher component)."""
    async def main(parsed):
        ctx = MGSDeltaContext(parsed.connect, parsed.password)
        ctx.auth = parsed.name
        ctx.server_task = asyncio.create_task(server_loop(ctx), name="server loop")
        watcher = asyncio.create_task(game_watcher(ctx), name="game watcher")
        if gui_enabled:
            ctx.run_gui()
        ctx.run_cli()
        await ctx.exit_event.wait()
        watcher.cancel()
        if ctx.session is not None:
            ctx.session.restore_kerotan_push()
            ctx.session.restore_raised()      # item maxima raised for received items' boxes
        await ctx.shutdown()

    Utils.init_logging("MGSDeltaClient", exception_logger="Client")
    parser = get_base_parser(description="MGS Delta Archipelago client")
    parser.add_argument("--name", default=None, help="Slot name to connect as.")
    parser.add_argument("url", nargs="?", help="Archipelago connection url")
    parsed = handle_url_arg(parser.parse_args(args), parser=parser)
    asyncio.run(main(parsed))
