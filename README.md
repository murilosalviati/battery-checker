# Battery Checker

Windows tray icons showing the battery level of your wireless mouse, keyboard and headset. Each device gets its own icon with the percentage (and a letter in the corner: **M**, **K** or **H**), and you get a Windows notification when each one drops below **50%**, **25%** and **10%**.

Colors: green above 50% · yellow 30–50% · orange 15–29% · red below 15% · grey when the device doesn't answer. While a device is charging, a yellow lightning bolt appears next to the number (and the icon tooltip says "charging").

## Supported devices

| Device | How it's read |
|---|---|
| VXE mice (R1, R1 Pro, R1 SE+, Pro Max…) through the 2.4G dongle | Kysona/Compx protocol |
| AULA keyboards through the Compx receiver 3554:FA09 (tested with the F75; F87 Pro/F99 Pro also reported) | Compx protocol |
| Corsair Virtuoso (RGB Wireless, SE, XT, MAX) and Void headsets through the dongle | Corsair/Bragi protocol (from HeadsetControl) |

## Installation

1. Install Python 3.10+ from https://www.python.org/downloads/ and tick **"Add Python to PATH"** (or run `winget install -e --id Python.Python.3.12 --scope user`).
2. Open PowerShell in this folder and run:
   ```powershell
   python -m pip install -r requirements.txt
   ```
3. **Test it first** (turn all devices on and move the mouse so it wakes up):
   ```powershell
   python battery_checker.py --scan
   ```
   Every recognized device prints a line such as `>> Mouse: 72%`.
4. If that works, run it in the tray:
   ```powershell
   Start-Process pythonw -ArgumentList "battery_checker.py"
   ```
5. Right-click any icon and tick **Start with Windows**.

> Tip: Windows 11 hides new tray icons behind the `^` arrow. Go to **Settings → Personalization → Taskbar → Other system tray icons** and turn on the Python icons.

## Icon menu

- **Refresh now** (or double-click): reads the batteries right away.
- **Start with Windows**: toggles autostart.
- **Open settings**: opens `config.json`.
- **Quit**.

## Settings

Stored in `%APPDATA%\BatteryChecker\config.json`:

```json
{
  "thresholds": [50, 25, 10],
  "interval_seconds": 300,
  "hysteresis": 5,
  "names": { "3554:f58a": "VXE R1 mouse" }
}
```

- `thresholds`: percentages that trigger a notification. Each one fires **once**; it re-arms when you plug the device in or when the battery climbs 5% above it.
- `interval_seconds`: how often to read the batteries (default 5 min). Use `60` if you want the charging bolt to show up faster.
- `names`: overrides the displayed name. The `VID:PID` key is shown by `--scan`.

Restart the app after editing.

## Troubleshooting

- **VXE mouse not answering**: it sleeps quickly. Move it and click *Refresh now*. Close ATK HUB if it's open (they fight over the dongle).
- **Corsair headset not answering**: a running iCUE sometimes swallows the replies. Try with iCUE closed.
- **AULA keyboard not recognized**: your receiver may not be the 3554:FA09. Run `python battery_checker.py --scan` and check which devices show up.

## Building an .exe (optional)

```powershell
python -m pip install pyinstaller
pyinstaller --onefile --noconsole --name BatteryChecker battery_checker.py
```

The executable ends up in `dist\BatteryChecker.exe`. For diagnostics keep using the `.py` with `--scan` (the .exe has no console).

---
Protocols adapted from [Battery Hub](https://github.com/battery-hub/battery-hub) (MIT license), which builds on the Linux hid-kysona/hid-pulsar drivers, HeadsetControl and AulaF87ProBattery.
