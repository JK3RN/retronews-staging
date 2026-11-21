# Install and run RetroNews

## Linux dependencies

- Python 3.11 or newer with SQLite, SSL and current security updates.
- GTK 4.8 or newer, PyGObject and GTK4/GdkPixbuf typelibs. Install the distribution's PNG and JPEG image loaders.
- A graphical Linux session for the reader. The full test suite also uses Pycairo and PangoCairo.

Arch Linux and Omarchy (verified):

```bash
sudo pacman -S --needed python python-gobject gtk4 gdk-pixbuf2
```

For the font test, also install `python-cairo`. Example package names for current Debian or Ubuntu with GTK ≥4.8 (installation not exercised):

```bash
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-gdkpixbuf-2.0
```

For the font test, also install `python3-gi-cairo`. [PyGObject installation guide](https://pygobject.gnome.org/getting_started.html).

## Start from source or install for one user

```bash
/usr/bin/python3 -m retronews
/usr/bin/python3 install.py --prefix "$HOME/.local"
"$HOME/.local/bin/retronews"
```

The installer copies the Python package to the prefix's `share/retronews` directory and writes a terminal launcher to `bin/retronews`. Run either launch command. The user installation requires no root access and seeds settings only at application startup.

## Subscribe and read

1. Launch once and quit with **Q**. Open `${XDG_CONFIG_HOME:-$HOME/.config}/retronews/feeds.json`.
2. Replace the empty array with your subscriptions. The example address is illustrative. Substitute a feed you want the application to fetch.

   ```json
   [
     {
       "name": "My feed",
       "category": "SCIENCE",
       "language": "en",
       "url": "https://example.org/feed.xml",
       "enabled": true,
       "prefetch": false
     }
   ]
   ```

3. Restart and press **R** if required. Press **H** to read each source's status. Articles stay readable offline while they remain in the cache.

Use `language` values `de`, `en` or `unknown`. Set `auto_sync` or `images` to `false` in `settings.json` to disable automatic refresh or image downloads. The defaults are a 30-minute sync interval, 350 MB of article and image files, and persistent storage. Successful feeds have a 60-second minimum interval for manual refresh.

`python -m retronews status` shows local statistics and subscription URLs. `stop` closes the reader and preserves the cache. `clean` while stopped and `stop --clean` explicitly remove managed offline data while preserving subscriptions and settings.

## Reproduce the screenshots and verify offline reading

From the package root, start the demo in one terminal:

```bash
profile=$(mktemp -d)
printf '%s\n' "$profile"
/usr/bin/python3 docs/showcase/serve.py --profile "$profile"
```

Set `profile` in a second terminal to the directory just printed. Keep the session's existing `XDG_RUNTIME_DIR` for Wayland. Exit any running RetroNews instance before this test to avoid sharing its command socket.

```bash
export XDG_CONFIG_HOME="$profile/config"
export XDG_CACHE_HOME="$profile/cache"
export XDG_STATE_HOME="$profile/state"
/usr/bin/python3 -m retronews
```

1. Wait for 12 headlines. Press **Enter** and scroll the observation log, then use **Esc**, **L** and **Tab** to verify navigation and filters.
2. Press **Q**. Stop the demo server in the first terminal with **Ctrl+C**. Wait until 60 seconds have elapsed since the successful refresh.
3. Restart from the second terminal. Check that the status reports a connection error and that the 12 cached articles are still readable.

The screenshots use a profile-local theme with background `#101619`, foreground `#DAE7E0`, accent `#00FF66` and warning `#FFCC00`. To reproduce that palette, set those values in `$XDG_STATE_HOME/omarchy/current/theme/colors.toml` inside the demo profile. RetroNews reads the theme automatically.
