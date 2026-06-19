"""
Manual presence test - simulates playing a Wii or Wii U game.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
from time import sleep, time

from pypresence import DiscordNotFound, InvalidID, InvalidPipe, ServerError
from pypresence.presence import Presence

from riitag.presence import format_presence
from riitag.user import RiitagInfo, RiitagTitleResolver

GAMES = [
    # {"game_id": "AMKE01", "console": "wiiu"},  # Mario Kart 8
    {"game_id": "RMGE01", "console": "wii"},  # Super Mario Galaxy
]
WAIT = 15


def make_riitag_info(game_id, console):
    return RiitagInfo(
        **{
            "user": {"name": "TestUser", "id": "000000000000000000"},
            "game_data": {
                "last_played": {
                    "game_id": game_id,
                    "console": console,
                    "region": "US",
                    "time": int(time()),
                },
                "games": [f"{console}-{game_id}"],
            },
        }
    )


def main():
    config_path = Path(__file__).resolve().parents[1] / "config.json"
    with open(config_path) as f:
        config = json.load(f)

    client_id = config["rpc"]["client_id"]
    print(f"Connecting to Discord (client_id={client_id})...")

    rpc = Presence(client_id)
    try:
        rpc.connect()
    except (DiscordNotFound, InvalidPipe, ConnectionRefusedError) as e:
        print(f"Could not connect to Discord: {e}")
        print("Make sure Discord is running.")
        return
    print("Connected.")

    resolver = RiitagTitleResolver()
    print("Fetching game titles from GameTDB...")
    resolver.update()

    print(
        f"\nCycling through {len(GAMES)} game(s) every {WAIT}s — press Ctrl+C to stop.\n"
    )

    try:
        while True:
            for game in GAMES:
                riitag_info = make_riitag_info(game["game_id"], game["console"])
                options = format_presence(riitag_info, resolver)

                print(
                    f"Setting presence: {options.get('name', game['game_id'])} ({game['console']})"
                )
                print(f"  state:       {options.get('state')}")
                print(f"  large_image: {options.get('large_image')}")

                try:
                    rpc.update(**options)
                    print("Presence updated.")
                except (InvalidPipe, InvalidID):
                    print("Lost Discord connection, reconnecting...")
                    rpc.close()
                    rpc = Presence(client_id)
                    rpc.connect()
                except ServerError as e:
                    print(f"Discord rejected update: {e}")

                sleep(WAIT)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        rpc.clear()
        rpc.close()


if __name__ == "__main__":
    main()
