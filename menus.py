from __future__ import annotations

import abc
import json
import os
import shutil
import subprocess
import threading
import time
import webbrowser
from typing import TYPE_CHECKING

import requests
from rich import box
from rich.console import Console
from rich.table import Table
from sentry_sdk import configure_scope

from riitag import oauth2, presence, user, watcher
from riitag.tui import C, key_opt, read_key
from riitag.util import get_config, get_config_dir, resource_path

if TYPE_CHECKING:
    from start import RiiTagApp

with open(resource_path("banner.txt"), "r+") as banner:
    BANNER = banner.read()

console = Console()


def _copy_to_clipboard(text: str) -> bool:
    """Copy given text to the system clipboard if possible.

    Tries in order: pyperclip, xclip, xsel. Returns True on success, False otherwise.
    """
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return True
    except Exception:
        pass

    try:
        if shutil.which("xclip"):
            p = subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode(),
                capture_output=True,
                check=False,
            )
            if p.returncode == 0:
                return True
        if shutil.which("xsel"):
            p = subprocess.run(
                ["xsel", "--clipboard", "--input"],
                input=text.encode(),
                capture_output=True,
                check=False,
            )
            if p.returncode == 0:
                return True
    except Exception:
        pass

    return False


def _prompt(msg: str) -> str:
    print(f"\n  {C.BOLD}{msg}{C.RESET} ", end="", flush=True)
    return input()


def _pause(msg: str, ok: bool = True) -> None:
    symbol = f"{C.GREEN}✓" if ok else f"{C.RED}✗"
    print(f"  {symbol}  {msg}{C.RESET}")
    print(f"\n  {C.GRAY}Press any key to continue…{C.RESET}", end="", flush=True)
    read_key()


class Menu(metaclass=abc.ABCMeta):
    name = "Generic Menu"
    is_framed = True

    def __init__(self, application: "RiiTagApp" = None):
        self.app = application

        self._run = True
        self._tasks = []
        self._lock = threading.Lock()
        self._task_thread = threading.Thread(target=self._task_manager, daemon=True)

    def _task_manager(self):
        while self._run:
            curr_time = int(time.time())
            with self._lock:
                to_run = [task for task in self._tasks if curr_time >= task[0]]
                for task in to_run:
                    self._tasks.remove(task)

            for task in to_run:
                task[1]()

            if to_run:
                self.update()

            time.sleep(0.05)

    def update(self):
        self.app.invalidate()

    def exec_after(self, seconds, callback):
        exec_at = int(time.time()) + seconds
        with self._lock:
            self._tasks.append((exec_at, callback))

    def on_start(self):
        self._task_thread.start()

    def on_exit(self):
        self._run = False

        # self._task_thread will just die off eventually... no reason to join()
        self._task_thread = None

    def render(self):
        raise NotImplementedError

    def handle_key(self, key):
        pass

    def quit_app(self):
        self.on_exit()

        if self.app.riitag_watcher:
            self.app.riitag_watcher.stop()
            self.app.riitag_watcher.join(timeout=5)

        self.app.exit()


# noinspection PyMethodMayBeStatic
class SplashScreen(Menu):
    name = "Splash Screen"
    is_framed = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._connect_attempt = 0
        self._is_connecting = False

        self.status_str = "Loading..."

    def render(self):
        print()
        for line in BANNER.splitlines():
            print(f"  {C.CYAN}{line}{C.RESET}")
        print()
        print(f"  {C.BOLD}{self.app.version_string}{C.RESET}")
        print("  Created by Mike Almeloo")
        print("  Forked and edited with ♥ by t0g3pii")
        print()
        print(f"  {self.status_str}")

    def on_start(self):
        super().on_start()

        # Start connecting as soon as possible to minimize the loading time.
        self.exec_after(0, self._new_connect)

    def handle_key(self, key):
        # time traveller!?!?
        if key == "enter":
            self._new_connect()

    @property
    def is_token_cached(self):
        return os.path.isfile(get_config("token.json"))

    def _refresh_token(self, token):
        try:
            token.refresh()
            token.save(get_config("token.json"))

            self.app.token = token
            self.app.user = token.get_user()
        except (
            requests.RequestException,
            KeyError,
        ):  # token revoked, network error, bad response
            self.app.set_menu(SetupMenu)

            return

        self.app.set_menu(MainMenu)

    def _new_connect(self):
        if self._is_connecting:
            return

        self._is_connecting = True
        self._connect_presence()

    def _connect_presence(self):
        delay = 0.5
        max_delay = 30

        while not self.app.rpc_handler.is_connected:
            self._connect_attempt += 1

            self.status_str = f"Discord RPC: connect attempt {self._connect_attempt}"
            self.update()

            self.app.rpc_handler.connect()

            if self.app.rpc_handler.is_connected:
                break

            self.status_str = (
                f"Trying to connect... ({self._connect_attempt})\n"
                f"  Please make sure your Discord client is running."
            )
            self.update()

            time.sleep(delay)
            delay = min(delay * 2, max_delay)

        self.status_str = "Discord RPC connected. Loading session..."
        self.update()
        self._login()

    def _login(self):
        if self.is_token_cached:
            self.status_str = "Loading cached token..."
            self.update()
            with open(get_config("token.json"), "r") as file:
                token_data = json.load(file)
            try:
                token = oauth2.OAuth2Token(self.app.oauth_client, **token_data)
                if token.needs_refresh:
                    self.status_str = "Refreshing Discord connection..."
                    self.update()

                    self.exec_after(0.5, lambda: self._refresh_token(token))

                else:
                    self.status_str = "Validating token..."
                    self.update()
                    self.app.token = token
                    try:
                        self.app.user = token.get_user()
                    except requests.HTTPError:  # generic error
                        self.app.set_menu(SetupMenu)

                        return
                    self.status_str = "Token valid. User loaded."
                    self.update()
                    self.app.set_menu(MainMenu)
            except KeyError:  # invalid token in cache?
                self.app.set_menu(SetupMenu)
        else:
            self.status_str = "Starting login flow..."
            self.update()
            self.app.set_menu(SetupMenu)


# noinspection PyMethodMayBeStatic
class SetupMenu(Menu):
    name = "Setup"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.state = "setup_start"
        self.waiting_stage = "connecting"
        self.auth_url = None
        self.copy_flash = None

        self.is_new_user = not os.path.isfile(get_config("token.json"))

    def render(self):
        if self.state == "setup_start":
            self._render_start()
        elif self.state == "waiting":
            self._render_waiting()

    def _render_start(self):
        if self.is_new_user:
            print(f"\n  {C.BOLD}Hello!{C.RESET} It looks like this is your first time using this program.")
            print("  No worries! Let's get your Discord account linked up first.")
        else:
            print(f"\n  {C.BOLD}We couldn't log you in.{C.RESET}")
            print()
            print("  This might have happened because the login token changed,")
            print("  or you revoked access for this application through Discord.")
            print("  Fear not! Let's try to get that fixed.")
        print()
        print(f"  You can exit this program at any time by pressing {key_opt('q', '')} or {C.YELLOW}Ctrl-C{C.RESET}.")
        print()
        print(f"  {C.BOLD}Press enter to show the login prompt.{C.RESET}")

    def _render_waiting(self):
        if self.waiting_stage == "connecting":
            print("\n  We'll try to automagically open up your browser. Fingers crossed...")
        elif self.waiting_stage == "opened":
            print(f"\n  {C.GREEN}Browser opened!{C.RESET}")
            print("  Please follow the instructions in your browser.")
            print(f"\n  {key_opt('c', 'opy URL')}")
        elif self.waiting_stage == "manual":
            print(f"\n  {C.YELLOW}Something went wrong...{C.RESET} Please manually paste this URL into your browser:")
            print(f"  {self.auth_url}")
            print(f"\n  {key_opt('c', 'opy URL')}")
        elif self.waiting_stage == "timeout":
            print(f"\n  {C.RED}Timed out{C.RESET} waiting for browser login.")
            print("  Please manually open this URL in your browser:")
            print(f"  {self.auth_url}")
        elif self.waiting_stage == "finishing":
            print("\n  Finishing the last bits...")
        elif self.waiting_stage == "done":
            print(f"\n  {C.GREEN}{C.BOLD}Done!{C.RESET}")
            print()
            print(
                f"  Signed in as {C.BOLD}{self.app.user.username}#{self.app.user.discriminator}{C.RESET}."
            )

        if self.copy_flash:
            print(f"\n  {C.GREEN}{self.copy_flash}{C.RESET}")

    def handle_key(self, key):
        if self.state == "setup_start" and key == "enter":
            self.state = "waiting"
            self.waiting_stage = "connecting"
            self.update()

            self.exec_after(2, self._get_token)
        elif (
            self.state == "waiting"
            and self.waiting_stage in ("opened", "manual")
            and key == "c"
        ):
            self._copy_auth_url()

    def _copy_auth_url(self):
        ok = _copy_to_clipboard(self.auth_url)
        self.copy_flash = (
            "Copied login URL to clipboard."
            if ok
            else "Clipboard not available. Please manually copy the URL above."
        )
        self.update()

    def _get_token(self):
        self.auth_url = self.app.oauth_client.auth_url
        opened = False
        try:
            if shutil.which("xdg-open"):
                result = subprocess.run(
                    ["xdg-open", self.auth_url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                opened = result.returncode == 0
            # Fallback to Python's webbrowser if xdg-open fails
            if not opened:
                opened = webbrowser.open(self.auth_url)
        except Exception:
            opened = False

        self.waiting_stage = "opened" if opened else "manual"
        self.update()

        code = self.app.oauth_client.wait_for_code(timeout=120)

        if code is None:
            self.waiting_stage = "timeout"
            self.update()
            return

        self.waiting_stage = "finishing"
        self.update()

        token = self.app.oauth_client.get_token(code)
        token.save(get_config("token.json"))
        self.app.token = token

        self.app.user = token.get_user()

        self.waiting_stage = "done"
        self.update()

        self.app.set_menu(MainMenu)


# noinspection PyMethodMayBeStatic
class MainMenu(Menu):
    name = "Main Menu"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.riitag_info = user.RiitagInfo()  # placeholder
        self.right_panel_state = "menu"  # "menu" | "settings"

        if discord_user := self.app.user:
            with configure_scope() as scope:
                scope.set_tag(
                    "discord.user",
                    f"{discord_user.username}#{discord_user.discriminator}",
                )
                scope.set_tag("discord.id", discord_user.id)

    def on_start(self):
        super().on_start()
        self._start_thread()

    def render(self):
        rpc_status = "Connected" if self.app.rpc_handler.is_connected else "Disconnected"
        status_color = C.GREEN if self.app.rpc_handler.is_connected else C.RED

        print()
        print(
            f"  {C.BOLD}RiiTag Username:{C.RESET} {self.riitag_info.name or C.GRAY + 'Unknown' + C.RESET}"
        )
        print(
            f"  {C.BOLD}Discord:{C.RESET} {self.app.user.username if self.app.user else 'Unknown'}"
        )
        print(f"  {C.BOLD}Status:{C.RESET} {status_color}{rpc_status}{C.RESET}")
        print(f"  {C.BOLD}Games:{C.RESET} {len(self.riitag_info.games)}")

        games = [game for game in self.riitag_info.games if game]
        if games:
            table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1, 0, 0))
            table.add_column("game")
            for game in games:
                parts = game.split("-")
                if len(parts) == 2:
                    console_name, game_id = parts
                    table.add_row(f"- {game_id} {C.GRAY}({console_name.title()}){C.RESET}")
                else:
                    table.add_row(f"- {parts[0]}")
            print()
            console.print(table)

        if self.right_panel_state == "menu":
            print()
            opts = [
                key_opt("v", "iew tag"),
                key_opt("s", "ettings"),
                key_opt("l", "ogout"),
                key_opt("q", "uit"),
            ]
            print("  " + "   ".join(opts))
        else:
            self._render_settings()

    def _render_settings(self):
        print(f"\n  {C.BOLD}Settings{C.RESET}")
        print(
            f"  {key_opt('1', ' Presence timeout', f'{self.app.preferences.presence_timeout} min')}"
        )
        print(
            f"  {key_opt('2', ' Refresh interval', f'{self.app.preferences.check_interval} sec')}"
        )
        print()
        opts = [key_opt("r", "eset"), key_opt("b", "ack")]
        print("  " + "   ".join(opts))

    def handle_key(self, key):
        if self.right_panel_state == "menu":
            if key == "v":
                self.view_riitag()
            elif key == "s":
                self.right_panel_state = "settings"
                self.update()
            elif key == "l":
                self._logout()
            elif key == "q":
                self.quit_app()
        else:
            if key == "1":
                self._edit_presence_timeout()
            elif key == "2":
                self._edit_check_interval()
            elif key == "r":
                self._reset_preferences()
            elif key == "b":
                self.right_panel_state = "menu"
                self.update()

    ################
    # Helper Funcs #
    ################

    def _logout_callback(self, confirm):
        if confirm:
            os.remove(get_config("token.json"))
            self.app.exit()

    def _logout(self):
        self.app.show_message(
            "Logout Confirmation",
            "Are you sure you want to log out?\n\n"
            "This will close RiiTag-RPC, and you\n"
            "will have to log in again the next time\n"
            "you use it.",
            callback=self._logout_callback,
        )

    def _edit_presence_timeout(self):
        self._edit_numeric_pref(
            label="presence timeout (minutes)",
            setter=lambda v: setattr(self.app.preferences, "presence_timeout", v),
            limits=(10, 12 * 60),
        )

    def _edit_check_interval(self):
        self._edit_numeric_pref(
            label="refresh interval (seconds)",
            setter=lambda v: setattr(self.app.preferences, "check_interval", v),
            limits=(30, 60),
        )

    def _edit_numeric_pref(self, label, setter, limits):
        self.app.redraw()
        raw = _prompt(f"New {label}  [{limits[0]}-{limits[1]}]:")
        if raw.strip():
            try:
                value = int(raw.strip())
            except ValueError:
                _pause(f"Couldn't parse {raw!r} as a number", ok=False)
            else:
                value = max(limits[0], min(limits[1], value))
                setter(value)
                self.app.preferences.save(get_config("prefs.json"))
                _pause(f"{label.capitalize()} set to {value}")
        self.update()

    def _reset_preferences(self):
        self.app.preferences.reset()
        self.app.preferences.save(get_config("prefs.json"))

        self.update()

    def _update_riitag(self, riitag: user.RiitagInfo):
        if not riitag:
            return

        self.riitag_info = riitag

        if not riitag.outdated:
            options = presence.format_presence(
                self.riitag_info,
                self.app.title_resolver,
                short_console_name=self.app.preferences.short_console_name,
            )
            self.app.rpc_handler.set_presence(**options)
        else:
            self.app.rpc_handler.clear()

        self.update()

    def view_riitag(self):
        client_id = self.app.user.id
        tag_url = f"https://riitag.t0g3pii.de/{client_id}"
        opened = False
        try:
            if shutil.which("xdg-open"):
                proc = subprocess.Popen(
                    ["xdg-open", tag_url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.wait(timeout=10)
                opened = proc.returncode == 0
        except Exception:
            opened = False
        if not opened:
            self.app.show_message(
                "Title",
                "Please manually open this URL in your browser:\n" + tag_url,
                ok_only=True,
            )

    def _start_thread(self):
        self.app.riitag_watcher = watcher.RiitagWatcher(
            preferences=self.app.preferences,
            user=self.app.user,
            update_callback=self._update_riitag,
            message_callback=lambda title, msg: self.app.show_message(
                title, msg, ok_only=True
            ),
        )
        self.app.riitag_watcher.start()


class DebugMenu(Menu):
    name = "Debug Menu"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.rpc_connection_attempts = 0
        self.last_error = "None"
        self.last_update_time = "Never"
        self.cache_info = {}

        self._refresh_data()

    def _refresh_data(self):
        if hasattr(self.app, "_connect_attempt"):
            self.rpc_connection_attempts = self.app._connect_attempt

        cache_dir = get_config_dir()
        self.cache_info = {
            "directory": cache_dir,
            "token_exists": os.path.exists(get_config("token.json")),
            "prefs_exists": os.path.exists(get_config("prefs.json")),
            "uid_exists": os.path.exists(get_config("_uid")),
        }

        if hasattr(self.app, "riitag_watcher") and self.app.riitag_watcher:
            self.last_update_time = self.app.riitag_watcher._last_check.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        self.update()

    def handle_key(self, key):
        if key == "b":
            self.app.set_menu(MainMenu)
        elif key == "r":
            self._refresh_data()

    def render(self):
        rpc_status = "Connected" if self.app.rpc_handler.is_connected else "Disconnected"

        token_info = "Valid"
        if not self.app.token:
            token_info = "Not available"
        elif self.app.token.needs_refresh:
            token_info = "Needs refresh"

        riitag_info = "Valid"
        if (
            not self.app.user
            or not hasattr(self.app.user, "riitag")
            or not self.app.user.riitag
        ):
            riitag_info = "Not available"

        current_game_info = "Not displaying any game"
        last_played_info = "No game data available"

        if (
            hasattr(self.app, "riitag_watcher")
            and self.app.riitag_watcher
            and hasattr(self.app.riitag_watcher, "_last_riitag")
            and self.app.riitag_watcher._last_riitag
        ):
            last_riitag = self.app.riitag_watcher._last_riitag

            if last_riitag.last_played and last_riitag.last_played.game_id:
                game_id = last_riitag.last_played.game_id
                console_name = last_riitag.last_played.console
                last_played_info = f"{game_id} ({console_name})"

                if not last_riitag.outdated:
                    current_game_info = f"Displaying: {game_id} ({console_name})"
                else:
                    current_game_info = f"Game outdated (timeout): {game_id}"

        print()
        print(f"  {C.RED}{C.BOLD}!!! SECURITY WARNING !!!{C.RESET}")
        print(f"  {C.RED}DO NOT SHARE ANY INFORMATION FROM THIS DEBUG SCREEN{C.RESET}")
        print(f"  {C.RED}with anyone except t0g3pii (the developer).{C.RESET}")
        print(f"  {C.RED}Contains sensitive data that could lead to account access!{C.RESET}")
        print()
        print(f"  {C.BOLD}== RiiTag-RPC Debug Information =={C.RESET}")
        print()
        print(f"  {C.BOLD}Version:{C.RESET} {self.app.version_string}")
        print(f"  {C.BOLD}Discord RPC Status:{C.RESET} {rpc_status}")
        print(f"  {C.BOLD}RPC Display:{C.RESET} {current_game_info}")
        print(f"  {C.BOLD}Last Played Game:{C.RESET} {last_played_info}")
        print(f"  {C.BOLD}RPC Connection Attempts:{C.RESET} {self.rpc_connection_attempts}")
        print(f"  {C.BOLD}Discord Token:{C.RESET} {token_info}")
        print(f"  {C.BOLD}RiiTag Status:{C.RESET} {riitag_info}")
        print(f"  {C.BOLD}Last Update:{C.RESET} {self.last_update_time}")
        print()
        print(f"  {C.BOLD}== Cache Information =={C.RESET}")
        print(f"  {C.BOLD}Cache Directory:{C.RESET} {self.cache_info.get('directory', 'Unknown')}")
        print(
            f"  {C.BOLD}Token File:{C.RESET} "
            + ("Present" if self.cache_info.get("token_exists") else "Missing")
        )
        print(
            f"  {C.BOLD}Preferences File:{C.RESET} "
            + ("Present" if self.cache_info.get("prefs_exists") else "Missing")
        )
        print(
            f"  {C.BOLD}User ID File:{C.RESET} "
            + ("Present" if self.cache_info.get("uid_exists") else "Missing")
        )
        print()
        print(f"  {C.BOLD}== User Information =={C.RESET}")
        print(
            f"  {C.BOLD}Discord User:{C.RESET} "
            f"{self.app.user.username if self.app.user else 'Unknown'}#"
            f"{self.app.user.discriminator if self.app.user else '0000'}"
        )
        print(f"  {C.BOLD}Discord ID:{C.RESET} {self.app.user.id if self.app.user else 'Unknown'}")
        print(
            f"  {C.BOLD}RiiTag Username:{C.RESET} "
            + (
                self.app.user.riitag.name
                if self.app.user and hasattr(self.app.user, "riitag") and self.app.user.riitag
                else "Unknown"
            )
        )
        print()
        print("  " + "   ".join([key_opt("r", "efresh"), key_opt("b", "ack")]))
