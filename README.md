# RiiTag-RPC

![](https://img.shields.io/github/downloads/t0g3pii/RiiTag-RPC/total)
![](https://img.shields.io/github/actions/workflow/status/t0g3pii/RiiTag-RPC/build.yml)
![](https://img.shields.io/github/commit-activity/m/t0g3pii/RiiTag-RPC)
![](https://img.shields.io/github/last-commit/t0g3pii/RiiTag-RPC)

RiiTag-RPC allows you to show your Discord friends what you're playing on your Wii or Wii U by connecting to your [RiiTag](https://riitag.t0g3pii.de/) account.

## Instructions
1. Grab the latest release from [here](https://github.com/t0g3pii/RiiTag-RPC/releases/latest).
2. Follow the instructions available [here](/GUIDE.md#riitag-rpc).

## Running on Raspberry Pi
It is possible to run RiiTag-RPC on your Raspberry Pi. Only the 4B model has been tested, but you may have luck on other models as well.
Please note that support for this platform is provided on a best-effort basis.

Since Discord does not have an official arm build for their client, you can use [ArmCord](https://github.com/ArmCord/ArmCord) (version **3.2.1** or higher).
We recommend installing it from [Pi-Apps](https://github.com/Botspot/pi-apps), as it makes it easy to keep the client up-to-date.

After installing ArmCord and logging in, RiiTag-RPC should work without issues.

RiiConnect24 is not affiliated with any of these projects. Please report any issues directly to their developers.

## Reporting Issues
Please report any bugs by [creating an issue](https://github.com/t0g3pii/RiiTag-RPC/issues/new).
Packaging
---------
- This project can be packaged as a standalone executable for Linux (and Windows with some caveats).
- Linux (single-file):
  - Prereqs: Python 3.8+, PyInstaller
  - Run: ./tools/build_executable.sh
  - Output: dist/start (Linux)
- Windows: build should be performed on Windows (or a Windows CI) using PyInstaller:
  - Ensure data files banner.txt and config.json are included via --add-data, similar to Linux:
    pyinstaller --onefile --add-data "banner.txt;." --add-data "config.json;." start.py
  - Output: dist/start.exe
- Data files: The app loads banner.txt and config.json at runtime; packaging scripts include these as data assets.
- If you want cross-platform builds, we can add a cross-compile workflow or a Nuitka-based approach.
