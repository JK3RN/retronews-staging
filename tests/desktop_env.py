"""Provide private app paths while retaining access to the current Wayland display."""
from pathlib import Path
import os


def desktop_env(root):
    env = {f'XDG_{kind}_HOME': str(root / kind.lower())
           for kind in ('CONFIG', 'CACHE', 'STATE')}
    env['XDG_RUNTIME_DIR'] = str(root / 'runtime')
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    display = os.environ.get('WAYLAND_DISPLAY', 'wayland-0')
    if runtime and not Path(display).is_absolute():
        env['WAYLAND_DISPLAY'] = str(Path(runtime) / display)
    return env
