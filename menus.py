from __future__ import annotations

import abc
import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from enum import Enum
from typing import TYPE_CHECKING

import requests
from prompt_toolkit.application import get_app
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.bindings.focus import focus_next, focus_previous
from prompt_toolkit.layout.containers import HSplit, VSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Box, Button, Frame, Label
from sentry_sdk import configure_scope

from riitag import oauth2, presence, user, watcher
from riitag.util import get_cache, get_cache_dir


# Get resource when frozen with PyInstaller
def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


if TYPE_CHECKING:
    from start import RiiTagApplication

with open(resource_path("banner.txt"), "r+") as banner:
    BANNER = banner.read()


class SettingsModifyMode(Enum):
    INCREASE = 1
    DECREASE = 0


class PreferenceButton(Button):
    def __init__(self, value, increments, limits: tuple):
        self.value = value
        self.increments = increments
        self.limits = limits

        super().__init__(str(self.value))

    def update(self):
        self.text = str(self.value)

    @property
    def is_focused(self):
        return get_app().layout.current_window == self.window

    def increase(self):
        new_value = self.value + self.increments
        if new_value > self.limits[1]:
            return

        self.value = new_value
        self.update()

    def decrease(self):
        new_value = self.value - self.increments
        if new_value < self.limits[0]:
            return

        self.value = new_value
        self.update()


class Menu(metaclass=abc.ABCMeta):
    name = "Generic Menu"
    is_framed = True

    def __init__(self, application: RiiTagApplication = None):
        self.app = application

        self._run = True
        self._tasks = []
        self._task_thread = threading.Thread(target=self._task_manager, daemon=True)

    def _task_manager(self):
        while self._run:
            to_delete = []

            curr_time = int(time.time())
            for task in self._tasks:
                if curr_time >= task[0]:
                    task[1]()
                    to_delete.append(task)

            if to_delete:
                self.update()

            for task in to_delete:
                self._tasks.remove(task)

            time.sleep(0.5)

    def update(self):
        self.app.invalidate()

    def exec_after(self, seconds, callback):
        exec_at = int(time.time()) + seconds
        self._tasks.append((exec_at, callback))

    def on_start(self):
        self._task_thread.start()
    def get_all_kb(self):
        return self.get_kb()

    def on_exit(self):
        self._run = False

        # self._task_thread will just die off eventually... no reason to join()
        self._task_thread = None

    def quit_app(self):
        self.on_exit()

        if self.app.riitag_watcher:
            self.app.riitag_watcher.stop()
            self.app.riitag_watcher.join(timeout=5)

        self.app.exit()


def _copy_to_clipboard(text: str) -> bool:
    """Copy given text to the system clipboard if possible.

    Tries in order: pyperclip, xclip, xsel. Returns True on success, False otherwise.
    """
    # Try pyperclip if available
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return True
    except Exception:
        pass

    # Fallback to xclip/xsel on Linux
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


# noinspection PyMethodMayBeStatic
class SplashScreen(Menu):
    name = "Splash Screen"
    is_framed = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._connect_attempt = 0
        self._is_connecting = False

        self.status_str = "Loading..."

    def get_layout(self):
        return HSplit(
            [
                Window(FormattedTextControl(BANNER), align=WindowAlign.CENTER),
                Window(
                    FormattedTextControl(
                        f"{self.app.version_string}\nCreated by Mike Almeloo\nForked and edited with ♥ by t0g3pii\n\n{self.status_str}"
                    ),
                    align=WindowAlign.CENTER,
                ),
            ]
        )

    def on_start(self):
        super().on_start()

        self.exec_after(5, self._new_connect)

    def get_kb(self):
        kb = KeyBindings()

        # time traveller!?!?
        @kb.add("enter")
        def skip_loading(_):
            self._new_connect()

        return kb

    @property
    def is_token_cached(self):
        return os.path.isfile(get_cache("token.json"))

    def _refresh_token(self, token):
        try:
            token.refresh()
            token.save(get_cache("token.json"))

            self.app.token = token
            self.app.user = token.get_user()
        except requests.HTTPError:  # token revoked, modified?
            self.app.set_menu(SetupMenu)

            return

        self.app.set_menu(MainMenu)

    def _new_connect(self):
        if self._is_connecting:
            return

        self._is_connecting = True
        self._connect_presence()

    def _connect_presence(self):
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)

        self._connect_attempt += 1

        self.app.rpc_handler.connect()

        if not self.app.rpc_handler.is_connected:
            self.status_str = (
                f"Trying to connect... ({self._connect_attempt})\n"
                f"Please make sure your Discord client is running."
            )
            self.update()

            time.sleep(4)
            self._connect_presence()
        else:
            self._login()

    def _login(self):
        if self.is_token_cached:
            with open(get_cache("token.json"), "r") as file:
                token_data = json.load(file)
            try:
                token = oauth2.OAuth2Token(self.app.oauth_client, **token_data)
                if token.needs_refresh:
                    self.status_str = "Refreshing Discord connection..."
                    self.update()

                    self.exec_after(0.5, lambda: self._refresh_token(token))

                else:
                    self.app.token = token
                    try:
                        self.app.user = token.get_user()
                    except requests.HTTPError:  # generic error
                        self.app.set_menu(SetupMenu)

                        return

                    self.app.set_menu(MainMenu)
            except KeyError:  # invalid token in cache?
                self.app.set_menu(SetupMenu)
        else:
            self.app.set_menu(SetupMenu)


# noinspection PyMethodMayBeStatic
class SetupMenu(Menu):
    name = "Setup"
    is_framed = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.state = "setup_start"

        if not os.path.isfile(get_cache("token.json")):  # new user
            self.setup_start_layout = Window(
                FormattedTextControl(
                    HTML(
                        "\n\n\n<b>Hello!</b> It looks like this is your first time using this program.\n"
                        "No worries! Let's get your Discord account linked up first.\n\n\n"
                        'You can exit this program at any time by pressing "q" or "Ctrl-c".\n\n\n'
                        "<b>Press enter to show the login prompt.</b>",
                    )
                ),
                align=WindowAlign.CENTER,
                wrap_lines=True,
            )
        else:  # existing user
            self.setup_start_layout = Window(
                FormattedTextControl(
                    HTML(
                        "\n\n\n<b>We couldn't log you in.</b>\n\n"
                        "This might have happened because the login token changed,\n"
                        "or you revoked access for this application through Discord.\n"
                        "Fear not! Let's try to get that fixed.\n\n\n"
                        "<b>Press enter to log in again.</b>",
                    )
                ),
                align=WindowAlign.CENTER,
                wrap_lines=True,
            )

        self.waiting_layout = HSplit(
            [
                Window(
                    FormattedTextControl(
                        HTML(
                            "\n\n\nWe'll try to automagically open up your browser. Fingers crossed..."
                        )
                    ),
                    align=WindowAlign.CENTER,
                    wrap_lines=True,
                )
            ]
        )

    def get_layout(self):
        if self.state == "setup_start":
            return self.setup_start_layout
        elif self.state == "waiting":
            return self.waiting_layout
        else:
            return Window()

    def get_kb(self):
        kb = KeyBindings()

        @kb.add("enter")
        def switch_state(_):
            if self.state == "setup_start":
                self.state = "waiting"
                self.update()

                self.exec_after(2, self._get_token)

        return kb

    def _get_token(self):
        auth_url = self.app.oauth_client.auth_url
        opened = False
        try:
            if shutil.which("xdg-open"):
                subprocess.run(
                    ["xdg-open", auth_url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                opened = True
            # Fallback to Python's webbrowser if xdg-open fails
            if not opened:
                opened = webbrowser.open(auth_url)
        except Exception:
            opened = False

        # Build a new waiting layout with status message and Copy URL button
        if opened:
            msg_window = Window(
                FormattedTextControl(
                    HTML(
                        "Looks like that worked. Sweet!\n"
                        "Please follow the instructions in your browser."
                    )
                ),
                align=WindowAlign.CENTER,
                wrap_lines=True,
            )
        else:
            msg_window = Window(
                FormattedTextControl(
                    HTML(
                        "Yikes, that didn't work. Please manually paste this URL into your browser:\n"
                        + auth_url
                    )
                ),
                align=WindowAlign.CENTER,
                wrap_lines=False,
            )

        copy_btn = Button("Copy URL", handler=lambda: self._copy_auth_url(auth_url))
        self.waiting_layout = HSplit([msg_window, copy_btn])

        self.update()
        code = self.app.oauth_client.wait_for_code()

        self.waiting_layout.children = [
            Window(
                FormattedTextControl(HTML("\n\n\n\n\nFinishing the last bits...")),
                align=WindowAlign.CENTER,
                wrap_lines=False,
            )
        ]
        self.update()

        token = self.app.oauth_client.get_token(code)
        token.save(get_cache("token.json"))
        self.app.token = token

        self.app.user = token.get_user()

        time.sleep(2)
        self.waiting_layout.children = [
            Window(
                FormattedTextControl(
                    HTML(
                        "\n\n\n\n\n<b>Done!</b>\n\nSigned in as <b>{}#{}</b>.\n\n"
                    ).format(self.app.user.username, self.app.user.discriminator)
                ),
                align=WindowAlign.CENTER,
                wrap_lines=False,
            )
        ]
        self.update()

        time.sleep(2)
        self.app.set_menu(MainMenu)

    def _copy_auth_url(self, url: str) -> None:
        """Copy the provided auth URL to clipboard and show status."""
        ok = _copy_to_clipboard(url)
        if ok:
            msg = "Copied login URL to clipboard."
        else:
            msg = "Clipboard not available. Please manually copy the URL:\n" + url
        self.waiting_layout.children.append(
            Window(
                FormattedTextControl(HTML(msg)),
                align=WindowAlign.CENTER,
                wrap_lines=False,
            )
        )
        self.update()


# noinspection PyMethodMayBeStatic
class MainMenu(Menu):
    name = "Main Menu"
    is_framed = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.riitag_info = user.RiitagInfo()  # placeholder

        if discord_user := self.app.user:
            with configure_scope() as scope:
                scope.set_tag(
                    "discord.user",
                    f"{discord_user.username}#{discord_user.discriminator}",
                )
                scope.set_tag("discord.id", discord_user.id)

        self.menu_settings_button = Button(
            "Settings", handler=lambda: self._set_state("Settings")
        )
        self.menu_view_button = Button("View Tag", handler=self.view_riitag)
        self.menu_exit_button = Button("Exit", handler=self.quit_app)
        self.menu_logout_button = Button("Logout", handler=self._logout)

        self.settings_back_button = Button(
            "Back...", width=12, handler=lambda: self._set_state("Menu")
        )
        self.settings_reset_button = Button(
            "Reset...", width=12, handler=self._reset_preferences
        )
        self.settings_pres_timeout_button = PreferenceButton(
            value=self.app.preferences.presence_timeout,
            increments=10,
            limits=(10, 12 * 60),
        )
        self.settings_check_interval_button = PreferenceButton(
            value=self.app.preferences.check_interval, increments=10, limits=(30, 60)
        )

        self.right_panel_state = "Menu"
        self.menu_layout = Frame(
            Box(
                HSplit(
                    [
                        self.menu_settings_button,
                        Label(""),
                        self.menu_view_button,
                        self.menu_exit_button,
                        self.menu_logout_button,
                    ]
                ),
                padding_left=3,
                padding_top=2,
            ),
            title="Menu",
        )
        self.settings_layout = Frame(
            Box(
                HSplit(
                    [
                        Window(
                            FormattedTextControl(
                                HTML(
                                    "This is where you can modify settings\nregarding the underlying presence\nwatcher."
                                )
                            ),
                            wrap_lines=True,
                            width=25,
                        ),
                        Label(""),
                        VSplit(
                            [
                                Label("Presence Timeout (min.):"),
                                self.settings_pres_timeout_button,
                            ],
                            width=15,
                        ),
                        VSplit(
                            [
                                Label("Refresh Interval (sec.):"),
                                self.settings_check_interval_button,
                            ],
                            padding=3,
                        ),
                        Label(""),
                        VSplit(
                            [self.settings_back_button, self.settings_reset_button],
                            align=WindowAlign.CENTER,
                        ),
                    ]
                ),
                padding_left=3,
                padding_top=2,
            ),
            title="Settings",
        )

    def on_start(self):
        super().on_start()

        self.app.layout.focus(self.menu_settings_button)
        self._start_thread()

    # In der get_layout Methode der MainMenu Klasse:
    def get_layout(self):
        game_labels = []
        for game in self.riitag_info.games:
            if not game:
                continue

            console_and_game_id = game.split("-")
            if len(console_and_game_id) == 2:
                console: str = console_and_game_id[0]
                game_id: str = console_and_game_id[1]

                label_text = HTML("<b>-</b> {} ({})").format(game_id, console.title())
            else:
                label_text = HTML("<b>-</b> {}").format(console_and_game_id[0])
            game_labels.append(Label(label_text))

        # RPC Status ermitteln
        rpc_status = (
            "Connected" if self.app.rpc_handler.is_connected else "Disconnected"
        )

        right_panel_layout = HSplit([])
        if self.right_panel_state == "Menu":
            right_panel_layout = self.menu_layout
        elif self.right_panel_state == "Settings":
            right_panel_layout = self.settings_layout

        return HSplit(
            [
                Box(
                    Label(text="Use the arrow keys and enter to navigate."),
                    height=3,
                    padding_left=2,
                ),
                VSplit(
                    [
                        Frame(
                            Box(
                                HSplit(
                                    [
                                        # Geändert von "Name" zu "RiiTag Username"
                                        Label(
                                            HTML("<b>RiiTag Username:</b> {}").format(
                                                self.riitag_info.name
                                            )
                                        ),
                                        # Discord Benutzername hinzugefügt
                                        Label(
                                            HTML("<b>Discord:</b> {}").format(
                                                self.app.user.username
                                                if self.app.user
                                                else "Unknown"
                                            )
                                        ),
                                        # RPC Status hinzugefügt
                                        Label(
                                            HTML("<b>Status:</b> {}").format(rpc_status)
                                        ),
                                        Label(
                                            HTML("<b>Games:</b> {}").format(
                                                len(game_labels)
                                            )
                                        ),
                                        *game_labels,
                                    ]
                                ),
                                padding_left=3,
                                padding_top=2,
                            ),
                            title="RiiTag",
                        ),
                        right_panel_layout,
                    ]
                ),
            ]
        )

    def get_kb(self):
        kb = KeyBindings()

        @kb.add("tab")
        @kb.add("down")
        def next_option(event):
            focus_next(event)

        @kb.add("s-tab")
        @kb.add("up")
        def prev_option(event):
            focus_previous(event)

        @kb.add("right")
        def increase_preference(event):
            modified = self._modify_setting(SettingsModifyMode.INCREASE)
            if not modified:  # treat as regular event
                focus_next(event)

        @kb.add("left")
        def decrease_preference(event):
            modified = self._modify_setting(SettingsModifyMode.DECREASE)
            if not modified:  # treat as regular event
                focus_previous(event)

        return kb

    ################
    # Helper Funcs #
    ################

    def _logout_callback(self, confirm):
        if confirm:
            os.remove(get_cache("token.json"))
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

    def _modify_setting(self, mode):
        is_modified = False

        if self.settings_check_interval_button.is_focused:
            if mode == SettingsModifyMode.INCREASE:
                self.settings_check_interval_button.increase()
            elif mode == SettingsModifyMode.DECREASE:
                self.settings_check_interval_button.decrease()

            is_modified = True
            self.app.preferences.check_interval = (
                self.settings_check_interval_button.value
            )

        elif self.settings_pres_timeout_button.is_focused:
            if mode == SettingsModifyMode.INCREASE:
                self.settings_pres_timeout_button.increase()
            elif mode == SettingsModifyMode.DECREASE:
                self.settings_pres_timeout_button.decrease()

            is_modified = True
            self.app.preferences.presence_timeout = (
                self.settings_pres_timeout_button.value
            )

        self.app.preferences.save(get_cache("prefs.json"))

        return is_modified

    def _reset_preferences(self):
        self.app.preferences.reset()
        self.app.preferences.save(get_cache("prefs.json"))

        self.settings_pres_timeout_button.value = self.app.preferences.presence_timeout
        self.settings_pres_timeout_button.update()
        self.settings_check_interval_button.value = self.app.preferences.check_interval
        self.settings_check_interval_button.update()

    def _set_state(self, state):
        self.right_panel_state = state

        self.update()

        if state == "Menu":
            self.app.layout.focus(self.menu_settings_button)
        elif state == "Settings":
            self.app.layout.focus(self.settings_back_button)

    def _update_riitag(self, riitag: user.RiitagInfo):
        if not riitag:
            return

        self.riitag_info = riitag

        if not riitag.outdated:
            options = presence.format_presence(self.riitag_info)
            self.app.rpc_handler.set_presence(**options)
        else:
            self.app.rpc_handler.clear()

        self.update()

    def view_riitag(self):
        client_id = self.app.user.id
        tag_url = f"https://riitag.t0g3pii.de/{client_id}"
        try:
            webbrowser.open(tag_url)
        except webbrowser.Error:
            self.app.show_message(
                "Title",
                "Yikes, that didn't work. Please manually paste this URL into your browser:\n"
                + tag_url,
            )

    def _start_thread(self):
        self.app.riitag_watcher = watcher.RiitagWatcher(
            preferences=self.app.preferences,
            user=self.app.user,
            update_callback=self._update_riitag,
            message_callback=None,
        )
        self.app.riitag_watcher.start()


class DebugMenu(Menu):
    name = "Debug Menu"
    is_framed = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.back_button = Button("Back to Main Menu", width=20, handler=self._go_back)
        self.refresh_button = Button(
            "Refresh Data", width=20, handler=self._refresh_data
        )

        # Debug-Statistiken
        self.rpc_connection_attempts = 0
        self.last_error = "None"
        self.last_update_time = "Never"
        self.cache_info = {}

        # Debug-Daten holen
        self._refresh_data()

    def _go_back(self):
        self.app.set_menu(MainMenu)

    def _refresh_data(self):
        # RPC-Verbindungsstatistik
        if hasattr(self.app, "_connect_attempt"):
            self.rpc_connection_attempts = self.app._connect_attempt

        # Cache-Informationen sammeln
        cache_dir = get_cache_dir()
        self.cache_info = {
            "directory": cache_dir,
            "token_exists": os.path.exists(get_cache("token.json")),
            "prefs_exists": os.path.exists(get_cache("prefs.json")),
            "uid_exists": os.path.exists(get_cache("_uid")),
        }

        # Letzte Aktualisierungszeit holen, falls vorhanden
        if hasattr(self.app, "riitag_watcher") and self.app.riitag_watcher:
            self.last_update_time = self.app.riitag_watcher._last_check.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        self.update()

    def on_start(self):
        super().on_start()
        # Focus auf den ersten Button beim Start
        self.app.layout.focus(self.back_button)

    def get_layout(self):
        # Ermittle RPC-Status mit Details
        rpc_status = (
            "Connected" if self.app.rpc_handler.is_connected else "Disconnected"
        )

        # Discord-Token-Info
        token_info = "Valid"
        if not self.app.token:
            token_info = "Not available"
        elif self.app.token.needs_refresh:
            token_info = "Needs refresh"

        # RiiTag-Info
        riitag_info = "Valid"
        if (
            not self.app.user
            or not hasattr(self.app.user, "riitag")
            or not self.app.user.riitag
        ):
            riitag_info = "Not available"

        # Aktuell angezeigtes Spiel ermitteln
        current_game_info = "Not displaying any game"
        last_played_info = "No game data available"

        # Prüfe, ob riitag_watcher existiert und Daten enthält
        if (
            hasattr(self.app, "riitag_watcher")
            and self.app.riitag_watcher
            and hasattr(self.app.riitag_watcher, "_last_riitag")
            and self.app.riitag_watcher._last_riitag
        ):
            last_riitag = self.app.riitag_watcher._last_riitag

            # Prüfe, ob ein Spiel zuletzt gespielt wurde
            if last_riitag.last_played and last_riitag.last_played.game_id:
                game_id = last_riitag.last_played.game_id
                console = last_riitag.last_played.console
                last_played_info = f"{game_id} ({console})"

                # Prüfe, ob das Spiel aktuell angezeigt wird oder veraltet ist
                if not last_riitag.outdated:
                    current_game_info = f"Displaying: {game_id} ({console})"
                else:
                    current_game_info = f"Game outdated (timeout): {game_id}"

        # Hier erstellen wir ein Split Layout mit Links- und Rechtsbereich
        # ähnlich wie im Hauptmenü
        main_content = HSplit(
            [
                # Prominente Sicherheitswarnung
                Window(
                    FormattedTextControl(
                        HTML(
                            "<ansired><b>!!! SECURITY WARNING !!!</b></ansired>\n"
                            "<ansired>DO NOT SHARE ANY INFORMATION FROM THIS DEBUG SCREEN</ansired>\n"
                            "<ansired>with anyone except t0g3pii (the developer).</ansired>\n"
                            "<ansired>Contains sensitive data that could lead to account access!</ansired>"
                        )
                    ),
                    align=WindowAlign.CENTER,
                    height=4,
                ),
                Label(""),
                Label(HTML("<b>== RiiTag-RPC Debug Information ==</b>")),
                Label(""),
                Label(HTML("<b>Version:</b> {}").format(self.app.version_string)),
                Label(HTML("<b>Discord RPC Status:</b> {}").format(rpc_status)),
                Label(HTML("<b>RPC Display:</b> {}").format(current_game_info)),
                Label(HTML("<b>Last Played Game:</b> {}").format(last_played_info)),
                Label(
                    HTML("<b>RPC Connection Attempts:</b> {}").format(
                        self.rpc_connection_attempts
                    )
                ),
                Label(HTML("<b>Discord Token:</b> {}").format(token_info)),
                Label(HTML("<b>RiiTag Status:</b> {}").format(riitag_info)),
                Label(HTML("<b>Last Update:</b> {}").format(self.last_update_time)),
                Label(""),
                Label(HTML("<b>== Cache Information ==</b>")),
                Label(
                    HTML("<b>Cache Directory:</b> {}").format(
                        self.cache_info.get("directory", "Unknown")
                    )
                ),
                Label(
                    HTML("<b>Token File:</b> {}").format(
                        "Present" if self.cache_info.get("token_exists") else "Missing"
                    )
                ),
                Label(
                    HTML("<b>Preferences File:</b> {}").format(
                        "Present" if self.cache_info.get("prefs_exists") else "Missing"
                    )
                ),
                Label(
                    HTML("<b>User ID File:</b> {}").format(
                        "Present" if self.cache_info.get("uid_exists") else "Missing"
                    )
                ),
                Label(""),
                Label(HTML("<b>== User Information ==</b>")),
                Label(
                    HTML("<b>Discord User:</b> {}#{}").format(
                        self.app.user.username if self.app.user else "Unknown",
                        self.app.user.discriminator if self.app.user else "0000",
                    )
                ),
                Label(
                    HTML("<b>Discord ID:</b> {}").format(
                        self.app.user.id if self.app.user else "Unknown"
                    )
                ),
                Label(
                    HTML("<b>RiiTag Username:</b> {}").format(
                        self.app.user.riitag.name
                        if self.app.user
                        and hasattr(self.app.user, "riitag")
                        and self.app.user.riitag
                        else "Unknown"
                    )
                ),
            ]
        )

        # Rechter Bereich für die Buttons - ähnlich wie im Hauptmenü,
        # jetzt mit fester Breite für die Buttons
        button_layout = Frame(
            Box(
                HSplit(
                    [
                        self.back_button,
                        Label(""),
                        self.refresh_button,
                    ]
                ),
                padding_left=3,
                padding_top=2,
                width=30,  # Feste Breite für den rechten Bereich
            ),
            title="Menu",
        )

        return VSplit(
            [
                main_content,
                button_layout,  # Buttons rechts angeordnet wie im Hauptmenü
            ]
        )

    def get_kb(self):
        kb = KeyBindings()

        @kb.add("tab")
        @kb.add("down")
        def next_option(event):
            focus_next(event)

        @kb.add("s-tab")
        @kb.add("up")
        def prev_option(event):
            focus_previous(event)

        return kb
