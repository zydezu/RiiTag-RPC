"""Headless entry point for RiiTag-RPC.

Runs the same background presence watcher as the interactive TUI,
but never touches a terminal. Meant for a plain `Type=simple` systemd service.

Requires a valid login token first.
"""

import json
import signal
import threading

import requests

from . import oauth2, presence, watcher
from .preferences import Preferences
from .user import RiitagTitleResolver
from .util import get_config, migrate_config


def _log(msg):
    print(msg, flush=True)


class HeadlessApp:
    def __init__(self, config: dict):
        migrate_config()

        self.preferences = Preferences.load(get_config("prefs.json"))
        self.oauth_client = oauth2.OAuth2Client(config["oauth2"])
        self.rpc_handler = presence.RPCHandler(config["rpc"]["client_id"])
        self.title_resolver = RiitagTitleResolver()

        self.token: oauth2.OAuth2Token | None = None
        self.user = None
        self.riitag_watcher: watcher.RiitagWatcher | None = None

        self._stop_event = threading.Event()

    # ---- login ----------------------------------------------------------

    def _load_cached_token(self):
        try:
            with open(get_config("token.json"), "r") as file:
                token_data = json.load(file)
        except FileNotFoundError:
            return None

        try:
            return oauth2.OAuth2Token(self.oauth_client, **token_data)
        except (KeyError, ValueError, TypeError) as e:
            _log(f"[!] Cached token is corrupt ({e}); can't log in headlessly.")
            return None

    def _login(self):
        token = self._load_cached_token()
        if token is None:
            _log(
                "[!] No cached Discord login found. Run RiiTag-RPC "
                "interactively once to log in - headless mode can take "
                "over from there."
            )
            return False

        if token.needs_refresh:
            _log("Refreshing Discord login...")
            try:
                token.refresh()
                token.save(get_config("token.json"))
            except (requests.RequestException, KeyError) as e:
                _log(f"[!] Login refresh failed ({e}); log in interactively again.")
                return False

        try:
            self.user = token.get_user()
        except requests.HTTPError as e:
            _log(f"[!] Could not load Discord user ({e}); log in interactively again.")
            return False

        self.token = token
        _log(f"✓  Logged in as {self.user.username}")
        return True

    # ---- Discord RPC ------------------------------------------------------

    def _connect_rpc(self):
        delay, max_delay, attempt = 0.5, 30, 0
        while not self._stop_event.is_set():
            attempt += 1
            if self.rpc_handler.connect():
                _log("✓  Connected to Discord client")
                return True
            _log(
                f"Trying to connect to Discord... ({attempt}) "
                "Please make sure your Discord client is running."
            )
            if self._stop_event.wait(delay):
                break
            delay = min(delay * 2, max_delay)
        return False

    # ---- watcher callbacks --------------------------------------------------

    def _on_update(self, riitag):
        if not riitag:
            return

        if riitag.outdated:
            self.rpc_handler.clear()
            _log("Presence cleared (idle / timed out)")
            return

        options = presence.format_presence(
            riitag,
            self.title_resolver,
            short_console_name=self.preferences.short_console_name,
        )
        if not options:
            return
        self.rpc_handler.set_presence(**options)
        _log(f"Game: {options['name']} [{options['large_text']}]")

    def _on_message(self, title, message):
        _log(f"[{title}] {message.splitlines()[0]}")

    # ---- lifecycle -----------------------------------------------------------

    def run(self):
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        if not self._connect_rpc():
            return 0  # stopped before connecting - not an error
        if not self._login():
            return 1

        self.riitag_watcher = watcher.RiitagWatcher(
            preferences=self.preferences,
            user=self.user,
            update_callback=self._on_update,
            message_callback=self._on_message,
        )
        self.riitag_watcher.start()
        _log("Watching for presence updates...")

        self._stop_event.wait()

        _log("Shutting down...")
        self.riitag_watcher.stop()
        self.riitag_watcher.join(timeout=5)
        self.rpc_handler.clear()
        return 0

    def _handle_signal(self, signum, frame):
        self._stop_event.set()
