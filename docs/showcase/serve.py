#!/usr/bin/env python3
"""Serve synthetic RSS and Atom feeds and create subscriptions for an isolated profile."""
import argparse
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path


FEEDS = {
    'retro': ('Workbench Dispatch', 'RETRO COMPUTING', 'en', (
        ('Rebuilding the serial console', 'A quiet bench session traces a dropped byte to a worn connector.'),
        ('A disk image for the spare terminal', 'The archive team records a bootable image and checks its checksum.'),
        ('A green screen returns to service', 'A terminal gets a new cable and a fresh line of test text.'),
    )),
    'open': ('Patch Notes', 'OPEN SOURCE', 'en', (
        ('A small parser gets a second pass', 'Three fixture files make it easier to discuss an ambiguous timestamp.'),
        ('The documentation sprint opens', 'The first examples cover configuration, offline reading and exported logs.'),
        ('A closer look at reproducible archives', 'A sample build records its inputs, file modes and compression options.'),
    )),
    'linux': ('Kernel-Bank', 'LINUX DEV', 'de', (
        ('Ein Neustart ohne Netz', 'Die Leseliste bleibt nach dem Ausschalten des Testservers verfügbar.'),
        ('Messwerte aus dem Testlabor', 'Das Team erfasst Startzeit, Speicherverbrauch und die Größe des lokalen Index.'),
        ('Ein kleiner Patch für die Konsole', 'Der Test prüft lange Zeilen und mehrsprachige Ausgaben.'),
    )),
    'science': ('Orbital Field Notes', 'SPACE & SCIENCE', 'en', (
        ('Listening for a quiet sky', 'A synthetic observation log follows a radio survey from calibration to handover.'),
        ('An antenna log for the night shift', 'The operator records a schedule for a fictional three-night survey.'),
        ('A notebook for the lunar horizon', 'A training exercise follows changing shadows across a simulated crater.'),
    )),
}

MEDIA_PATH = '/media/reference-sweep.png'
MEDIA = Path(__file__).with_name('reference-sweep.png')

FEATURE = (
    '03:20 UTC / CALIBRATION',
    'The array runs against a quiet reference field before tracking the survey target. '
    'Operators compare the noise floor across three channels and mark the interval with a stable baseline.',
    '03:55 UTC / FIRST PASS',
    'The first sweep shows a broad rise near the center channel. A shorter reference sweep helps the team '
    'distinguish receiver drift from a change in the synthetic sky model. The second sweep repeats the baseline.',
    '04:40 UTC / HANDOVER',
    'The night shift records antenna orientation, receiver temperature and the reference field used for the pass. '
    'The morning team repeats calibration before comparing the two observations.',
    'APPENDIX / OBSERVATION LOG',
    *(
        f'{hour:02d}:05 UTC / The simulated receiver remains within the expected band. '
        'The operator records the reference sweep and checks the instrument clock before closing the entry.'
        for hour in range(5, 13)
    ),
    'END OF OBSERVATION LOG',
)


def feed_xml(key):
    name, _, language, stories = FEEDS[key]
    anchor = datetime(2026, 1, 19, 10, 35, tzinfo=timezone.utc)
    entries = []
    for index, (title, summary) in enumerate(stories):
        published = anchor - timedelta(minutes=24 * index + (3 - list(FEEDS).index(key)) * 5)
        path = f'/article/{key}/{index + 1}'
        title, summary = escape(title), escape(summary)
        if key in ('science', 'linux'):
            content = ''
            enclosure = ''
            if key == 'science' and index == 0:
                markup = ''.join(f'<p>{escape(paragraph)}</p>' for paragraph in FEATURE)
                content = f'<content type="html">{escape(markup)}</content>'
                enclosure = f'<link rel="enclosure" type="image/png" href="{MEDIA_PATH}"/>'
            entries.append(f'<entry><id>urn:retronews:{key}:{index + 1}</id><title>{title}</title>'
                           f'<link href="{path}"/><updated>{published.isoformat()}</updated>'
                           f'<summary>{summary}</summary>{content}{enclosure}</entry>')
        else:
            entries.append(f'<item><title>{title}</title><link>{path}</link>'
                           f'<pubDate>{format_datetime(published)}</pubDate>'
                           f'<description>{summary}</description></item>')
    body = ''.join(entries)
    if key in ('science', 'linux'):
        return (f'<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="{language}">'
                f'<title>{name}</title>{body}</feed>').encode()
    return f'<rss version="2.0"><channel><title>{name}</title><language>{language}</language>{body}</channel></rss>'.encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        key = self.path.removeprefix('/feeds/')
        if self.path == '/robots.txt':
            body, mime = b'User-agent: *\n', 'text/plain'
        elif self.path == MEDIA_PATH:
            body, mime = MEDIA.read_bytes(), 'image/png'
        elif key in FEEDS and self.path == f'/feeds/{key}':
            body, mime = feed_xml(key), 'application/xml'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765, help='Loopback HTTP port')
    parser.add_argument('--profile', type=Path, required=True, help='New, isolated XDG profile root')
    args = parser.parse_args()
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        url = f'http://127.0.0.1:{server.server_port}'
        config = args.profile / 'config/retronews'
        config.mkdir(parents=True, exist_ok=True)
        sources = [
            dict(name=name, category=category, language=language, url=f'{url}/feeds/{key}')
            for key, (name, category, language, _) in FEEDS.items()
        ]
        with (config / 'feeds.json').open('x') as stream:
            json.dump(sources, stream, indent=2)
            stream.write('\n')
        print(f'Synthetic feeds ready on {url}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
