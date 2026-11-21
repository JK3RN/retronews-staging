#!/usr/bin/env python3
"""Offline user installation; no package manager, root or desktop configuration."""
import argparse
from pathlib import Path
import shutil
import sys

MARKER='RetroNews user installation v0.1'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--prefix',type=Path,default=Path.home()/'.local')
    args=parser.parse_args();prefix=args.prefix.expanduser().absolute()
    import gi
    gi.require_version('Gtk','4.0');gi.require_version('GdkPixbuf','2.0')
    from gi.repository import Gtk,GdkPixbuf
    library=prefix/'share/retronews';launcher=prefix/'bin/retronews';marker=library/'.installation'
    if library.is_symlink() or launcher.is_symlink():parser.error('Refusing symlink installation targets')
    if library.exists() and (not marker.exists() or marker.read_text()!=MARKER):parser.error('Unrelated installation directory')
    if launcher.exists() and MARKER not in launcher.read_text():parser.error('Unrelated launcher exists')
    library.mkdir(parents=True,exist_ok=True)
    shutil.copytree(Path(__file__).resolve().parent/'retronews',library/'retronews',dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    marker.write_text(MARKER)
    launcher.parent.mkdir(parents=True,exist_ok=True)
    launcher.write_text(f'#!{sys.executable}\n# {MARKER}\nimport sys\nsys.path.insert(0,{str(library)!r})\nfrom retronews.__main__ import main\nraise SystemExit(main())\n')
    launcher.chmod(0o755)
    print(f'Installed {launcher}\nNo settings or cache were changed.')


if __name__=='__main__':main()
