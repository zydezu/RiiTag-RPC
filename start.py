import json
import sys
import threading
import traceback
import uuid

import sentry_sdk

import menus
from riitag import oauth2, preferences, presence, user, watcher
from riitag.tui import C, clear, read_key
from riitag.util import get_config, is_bundled, migrate_config, resource_path

_APP_INSTANCE: "RiiTagApp | None" = None


def on_error(exc_type, exc_value, exc_traceback):
    if _APP_INSTANCE is not None and _APP_INSTANCE.is_running:
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

        _APP_INSTANCE.show_message(
            "Whoops!",
            "An unexpected error has occured.\n"
            "The exception will be reported so the developers can look into it.\n\n"
            "Need help? Contact us with this ID so we can help you out:\n"
            + (get_user_id() or "<not found>")
            + "\n\n"
            + "Reported exception:\n"
            + f"{exc_type.__name__} - {exc_value or '<no except value>'}",
            ok_only=True,
        )
        return

    print()
    print(
        "+-------------------------------------------------------+\n"
        "RiiTag-RPC failed to start :/ \n\n"
        "Please contact us with this ID so we can help you out:\n"
        + (get_user_id() or "<not found>")
        + "\n"
        + "+-------------------------------------------------------+"
    )
    print()

    print("** Original exception was: **")
    traceback.print_exception(exc_value)
    print()
    print("** Press Enter to exit **")
    input()
    sys.exit(1)


def on_thread_error(args):
    on_error(args.exc_type, args.exc_value, args.exc_traceback)


sys.excepthook = on_error
threading.excepthook = on_thread_error

try:
    get_config("")
    migrate_config()
except OSError:
    print("ERROR: Could not create config directory.")
    print("Please check file permissions and try again.")
    print()
    print("Press enter to exit.")
    input()
    sys.exit(1)


def get_user_id():
    try:
        with open(get_config("_uid"), "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        uid = str(uuid.uuid4())
        try:
            with open(get_config("_uid"), "w+") as f:
                f.write(uid)
        except Exception:
            return None
        return uid


try:
    with open(resource_path("config.json"), "r") as file:
        CONFIG: dict = json.load(file)
except FileNotFoundError:
    print("[!] The config file seems to be missing.")
    print("[!] Please re-download this program or create it manually.")

    input()
    sys.exit(1)

VERSION = CONFIG.get("version", "<unknown_version>")
sentry_sdk.init(
    "https://0206915cd7604929997a753583292296@o107347.ingest.sentry.io/5450405",
    traces_sample_rate=1.0,
    release=f"riitag-rpc@{VERSION}",
    include_local_variables=False,
)
with sentry_sdk.configure_scope() as scope:
    # noinspection PyDunderSlots,PyUnresolvedReferences
    scope.user = {"id": get_user_id()}
    scope.set_tag("bundled", is_bundled())


class RiiTagApp:
    def __init__(self):
        global _APP_INSTANCE
        _APP_INSTANCE = self

        self._current_menu: menus.Menu | None = None
        self._pending_message: dict | None = None
        self._lock = threading.Lock()
        self._dirty = True
        self._running = False

        self.preferences = preferences.Preferences.load(get_config("prefs.json"))
        self.oauth_client = oauth2.OAuth2Client(CONFIG.get("oauth2"))
        self.rpc_handler = presence.RPCHandler(CONFIG.get("rpc", {}).get("client_id"))
        self.title_resolver = user.RiitagTitleResolver()

        self.token: oauth2.OAuth2Token | None = None
        self.user: user.User | None = None

        self.riitag_watcher: watcher.RiitagWatcher | None = None

        self.set_menu(menus.SplashScreen)

        self.oauth_client.start_server(CONFIG.get("port", 4000))

    ######################
    # State / Lifecycle  #
    ######################

    @property
    def is_running(self):
        return self._running

    @property
    def version_string(self):
        return f"RiiTag-RPC v{VERSION}"

    @property
    def header_string(self):
        return f"RiiTag-RPC - {self._current_menu.name}"

    def set_menu(self, menu):
        if not issubclass(menu, menus.Menu):
            raise ValueError("menu must be a subclass of menus.Menu")

        if self._current_menu:
            self._current_menu.on_exit()

        self._current_menu = menu(self)
        self.invalidate()
        self._current_menu.on_start()

    def exit(self):
        self._running = False

    ####################
    # Rendering / Input #
    ####################

    def invalidate(self):
        self._dirty = True

    def redraw(self):
        clear()
        if self._current_menu.is_framed:
            print(f"  {C.BOLD}{C.CYAN}RiiTag-RPC{C.RESET}  {C.GRAY}— {self._current_menu.name}{C.RESET}")
            print(f"  {C.GRAY}{'─' * 56}{C.RESET}")
        self._current_menu.render()
        if self._pending_message:
            self._render_message()

    def _render_message(self):
        m = self._pending_message
        print(f"\n  {C.BOLD}{C.MAGENTA}{m['title']}{C.RESET}")
        for line in m["message"].split("\n"):
            print(f"  {line}")
        print()
        if m["ok_only"]:
            print(f"  [{C.YELLOW}enter{C.RESET}] OK")
        else:
            print(f"  [{C.YELLOW}y{C.RESET}]es   [{C.YELLOW}n{C.RESET}]o")

    def show_message(self, title, message, callback=None, ok_only=False):
        with self._lock:
            self._pending_message = {
                "title": title,
                "message": message,
                "callback": callback,
                "ok_only": ok_only,
            }
        self.invalidate()

    def _handle_message_key(self, key):
        m = self._pending_message
        if m["ok_only"]:
            if key in ("enter", " "):
                self._resolve_message(True)
        else:
            if key == "y":
                self._resolve_message(True)
            elif key in ("n", "esc"):
                self._resolve_message(False)

    def _resolve_message(self, is_ok):
        with self._lock:
            m = self._pending_message
            self._pending_message = None

        if m and m["callback"]:
            m["callback"](is_ok)

        self.invalidate()

    def run(self):
        self._running = True
        try:
            while self._running:
                if self._dirty:
                    self._dirty = False
                    self.redraw()

                try:
                    key = read_key(timeout=0.2)
                except KeyboardInterrupt:
                    self._current_menu.quit_app()
                    break

                if key is None:
                    continue

                if self._pending_message:
                    self._handle_message_key(key)
                else:
                    self._current_menu.handle_key(key)
        finally:
            clear()


def main():
    application = RiiTagApp()
    application.run()


if __name__ == "__main__":
    main()
