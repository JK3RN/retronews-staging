"""Configuration is separate from disposable article/media files."""
import json
import os
import stat
import tempfile
from pathlib import Path

DEFAULTS = dict(auto_sync=True, persist=True, images=True, cache_limit_mb=350,
                retention_hours=24, sync_minutes=30, timeout_seconds=5,
                prefetch_articles=18, items_per_feed=20, max_articles=1000,
                sync_budget_mb=16, image_limit_mb=2)


class Paths:
    def __init__(self):
        self.config = Path(os.getenv('XDG_CONFIG_HOME', Path.home() / '.config')) / 'retronews'
        self.cache = Path(os.getenv('XDG_CACHE_HOME', Path.home() / '.cache')) / 'retronews'
        self.state = Path(os.getenv('XDG_STATE_HOME', Path.home() / '.local/state')) / 'retronews'
        # A short per-user socket path also works without a desktop runtime directory.
        self.runtime = Path(os.getenv('XDG_RUNTIME_DIR', self.state)) / 'retronews'
        self.db = self.state / 'index.sqlite3'
        self.socket = self.runtime / 'control.sock'
        self.lock = self.runtime / 'instance.lock'

    def prepare(self):
        for path in (self.config, self.cache, self.state, self.runtime):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.is_symlink() or not path.is_dir():
                raise ValueError('Refusing non-directory or symlink application path')


def seed(path, content):
    if path.is_symlink() or (path.exists() and not stat.S_ISREG(path.stat().st_mode)):
        raise ValueError('Refusing non-regular configuration file')
    if path.exists():
        return
    with tempfile.NamedTemporaryFile(dir=path.parent) as pending:
        pending.write(content)
        pending.flush()
        os.fsync(pending.fileno())
        try:
            os.link(pending.name, path)
        except FileExistsError:
            pass


def read_json(path, maximum=200_000):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Expected a regular file')
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('Local JSON file exceeds size limit')
    return json.loads(data)



def load(paths):
    paths.prepare()
    settings = paths.config / 'settings.json'
    feeds = paths.config / 'feeds.json'
    seed(settings, (json.dumps(DEFAULTS, indent=2) + '\n').encode())
    seed(feeds, (Path(__file__).parent / 'feeds.json').read_bytes())
    configured = read_json(settings)
    if not isinstance(configured, dict):
        raise ValueError('settings.json: object required')
    options = DEFAULTS | configured
    for key in ('auto_sync', 'persist', 'images'):
        if type(options[key]) is not bool:
            raise ValueError(f'{key}: boolean required')
    limits = dict(cache_limit_mb=(0.01, 350), retention_hours=(0.01, 720),
                  sync_minutes=(30, 1440), timeout_seconds=(1, 10),
                  prefetch_articles=(0, 40), items_per_feed=(1, 50),
                  max_articles=(1, 1000), sync_budget_mb=(1, 32), image_limit_mb=(0.01, 2))
    for key, (low, high) in limits.items():
        value = options[key]
        if type(value) not in (int, float) or not low <= value <= high:
            raise ValueError(f'{key}: expected {low}..{high}')
    sources = read_json(feeds)
    if not isinstance(sources, list) or len(sources) > 60:
        raise ValueError('feeds.json: array with at most 60 sources required')
    known = {f['url']: f for f in json.loads((Path(__file__).parent / 'feeds.json').read_text())}
    seen = set()
    for feed in sources:
        if not isinstance(feed, dict) or any(not isinstance(feed.get(k), str) or not feed[k].strip()
                                            for k in ('name', 'category', 'url')):
            raise ValueError('Feed requires name, category and url')
        if feed['url'] in seen:
            raise ValueError('Duplicate feed URL')
        if len(feed['url'])>2048 or len(feed['name'])>100 or len(feed['category'])>30:
            raise ValueError('Feed fields exceed size limits')
        from .content import web_url
        if not web_url(feed['url']):raise ValueError('Feed URL requires unauthenticated HTTP(S)')
        # Enrich in memory only: preserve all user configuration and custom URLs.
        feed.setdefault('language', known.get(feed['url'], {}).get('language', 'unknown'))
        if feed['language'] not in ('de', 'en', 'unknown'):
            raise ValueError('language: de, en or unknown required')
        seen.add(feed['url'])
        if type(feed.get('enabled', True)) is not bool or type(feed.get('prefetch', False)) is not bool:
            raise ValueError('enabled: boolean required')
    return options, sources
