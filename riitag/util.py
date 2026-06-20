import logging
import os
import platform
import shutil
import sys

LOG = logging.getLogger(__name__)
CONFIG_DIR_NAME = "riitag-rpc"


def _get_old_cache_dir():
    plat = platform.system()
    if plat == "Windows":
        path = os.getenv("LOCALAPPDATA")
    elif plat == "Linux":
        fallback = os.path.join(os.getenv("HOME"), ".cache")
        path = os.getenv("XDG_CACHE_HOME", fallback)
    elif plat == "Darwin":
        fallback = os.path.join(os.getenv("HOME"), "Library/Caches")
        path = os.getenv("XDG_CACHE_HOME", fallback)
    else:
        return None
    return os.path.join(path, CONFIG_DIR_NAME)


def migrate_config():
    old_dir = _get_old_cache_dir()
    if not old_dir or not os.path.isdir(old_dir):
        return

    new_dir = get_config_dir()
    if old_dir == new_dir:
        return

    migrated = []
    for filename in os.listdir(old_dir):
        src = os.path.join(old_dir, filename)
        dst = os.path.join(new_dir, filename)
        if not os.path.exists(dst):
            shutil.move(src, dst)
            migrated.append(filename)

    if migrated:
        LOG.info("Migrated config files to %s: %s", new_dir, ", ".join(migrated))

    try:
        os.rmdir(old_dir)
    except OSError:
        pass


def get_config_dir():
    plat = platform.system()
    if plat == "Windows":
        path = os.getenv("LOCALAPPDATA")
    elif plat == "Linux":
        fallback = os.path.join(os.getenv("HOME"), ".config")
        path = os.getenv("XDG_CONFIG_HOME", fallback)
    elif plat == "Darwin":
        fallback = os.path.join(os.getenv("HOME"), "Library/Application Support")
        path = os.getenv("XDG_CONFIG_HOME", fallback)
    else:
        raise OSError(f"Platform unsupported: {plat}")

    path = os.path.join(path, CONFIG_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def get_config(filename):
    return os.path.join(get_config_dir(), filename)


def is_bundled():
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_path(relative_path):
    if is_bundled():
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)
