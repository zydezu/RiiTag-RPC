import calendar

import pypresence

from .user import RiitagInfo, RiitagTitleResolver


def format_presence(
    riitag_info: RiitagInfo, resolver: RiitagTitleResolver | None = None
):
    last_played = riitag_info.last_played
    if not last_played:
        return {}

    start_timestamp = calendar.timegm(last_played.time.utctimetuple())

    if resolver is None:
        resolver = RiitagTitleResolver()

    title = resolver.resolve(last_played.console, last_played.game_id)

    return {
        "name": title.name,
        "state": f"Playing on {title.console_name}",
        "start": start_timestamp,
        "large_image": title.get_cover_url(),
        "large_text": title.game_id,
        "buttons": [
            {
                "label": "View Profile",
                "url": f"https://riitag.t0g3pii.de/user/{riitag_info.id}",
            }
        ],
    }


class RPCHandler:
    def __init__(self, client_id):
        self._presence = pypresence.Presence(
            client_id=client_id, response_timeout=5, connection_timeout=5, handler=None
        )

        self._is_connected = False

    @property
    def is_connected(self):
        return self._is_connected

    def connect(self):
        try:
            self._presence.connect()
        except (ConnectionRefusedError, pypresence.PyPresenceException):
            self._is_connected = False
            return False
        else:
            self._is_connected = True
            return True

    def clear(self):
        try:
            self._presence.clear()
        except pypresence.ResponseTimeout:
            pass
        except OSError:
            self._is_connected = False

    def set_presence(self, **options):
        try:
            self._presence.update(**options)
        except OSError:
            self._is_connected = False
            raise
