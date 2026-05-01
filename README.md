# Eleòra Kleon

A system maintenance utility for Fedora Linux, optimized for KDE Plasma.

## Features

- **DNF** — upgrade packages, autoremove unused ones, clean cache
- **Flatpak** — update apps and remove unused runtimes
- **Kernel cleanup** — remove obsolete kernel versions
- **systemd journal** — reduce journal size to a configured limit
- **Log files** — delete rotated and compressed logs in /var/log
- **Core dumps** — remove crash dump files
- **PackageKit cache** — clear the PackageKit package cache
- **Temporary files** — remove stale files from /tmp and /var/tmp
- **Bash history** — clear root and user history
- **User cache** — clean ~/.cache, preserve KDE essentials
- **Recent documents** — remove KDE and GTK recent files
- **Browser cleanup** — cache, history, sessions and passwords for Brave, Chrome and Firefox
- **SMART summary** — disk health status at the end of each run
- **Bilingual** — Italian and English, auto-detected from system locale

## Requirements

- Fedora Linux
- KDE Plasma
- Python 3.10+
- PySide6
- pkexec (for root operations)

## Installation

```bash
git clone https://github.com/eleora/kleon.git
cd kleon
pip install PySide6 --break-system-packages
python kleon
```

## Privacy

This application does not collect, store, transmit or share any personal data.
All operations are performed locally on your machine.

Full privacy policy: [eleora.github.io/kleon/privacy.html](https://eleora.github.io/kleon/privacy.html)

## License

MIT License — see [LICENSE](LICENSE) for details.

## Author

Gerardo Perilli · [Eleòra](https://github.com/eleora)
