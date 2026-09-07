import os
import select
import sys
import time

if os.name == "nt":
    import msvcrt
else:
    import termios
    import tty


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"


_WIN_ARROWS = {"H": "up", "P": "down", "K": "left", "M": "right"}
_POSIX_ARROWS = {"A": "up", "B": "down", "C": "right", "D": "left"}


def _read_key_nt(timeout):
    deadline = None if timeout is None else time.monotonic() + timeout
    while deadline is None or time.monotonic() < deadline:
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):  # arrow / function key prefix
                scan = msvcrt.getwch()
                return _WIN_ARROWS.get(scan, "esc")
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch == "\r":
                return "enter"
            if ch == "\x1b":
                return "esc"
            if ch == "\x08":
                return "backspace"
            return ch.lower()
        time.sleep(0.02)
    return None


def _read_key_posix(timeout):
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None

        ch = sys.stdin.read(1)
        if ch == "\x1b":
            deadline = time.monotonic() + 0.05
            remaining = deadline - time.monotonic()
            if not select.select([fd], [], [], max(remaining, 0))[0]:
                return "esc"
            sys.stdin.read(1)  # [
            seq = sys.stdin.read(1)
            return _POSIX_ARROWS.get(seq, "esc")
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x7f":
            return "backspace"
        return ch.lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_key(timeout=None):
    """Read one keypress, waiting up to `timeout` seconds (None = forever).

    Returns lowercase char, 'up'/'down'/'left'/'right', 'enter', 'esc',
    'backspace', or None if `timeout` elapsed with no input.
    """
    if os.name == "nt":
        return _read_key_nt(timeout)
    return _read_key_posix(timeout)


def clear() -> None:
    print("\033[H\033[J", end="", flush=True)


def key_opt(key: str, label: str, note: str = "") -> str:
    """Format a coloured key hint: [E]dit  or  [E]dit (3)"""
    note_str = f" {C.GRAY}({note}){C.RESET}" if note else ""
    return f"[{C.YELLOW}{key}{C.RESET}]{label}{note_str}"
