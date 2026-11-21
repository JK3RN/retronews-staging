"""Flat managed cache with atomic writes, protected readers and a hard byte cap."""
import contextlib
import json
import os
import re
import sqlite3
import stat
import threading
import time

from .config import read_json

MANAGED = re.compile(r'[0-9a-f]{64}\.(?:json|img)(?:\.tmp)?')
TEXT_FIELDS = ('feed', 'title', 'url', 'source', 'category', 'summary', 'text', 'image_url')


class Cache:
    def __init__(self, paths, options):
        self.paths, self.options = paths, options
        paths.prepare()
        self.lock = threading.RLock()
        self.active = None
        self.limit = int(options['cache_limit_mb'] * 1_000_000)
        with self.db() as db:
            db.executescript('''
CREATE TABLE IF NOT EXISTS articles(id TEXT PRIMARY KEY, feed TEXT, title TEXT, url TEXT,
 source TEXT, category TEXT, published REAL, saved REAL, mode TEXT, seen INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS feeds(url TEXT PRIMARY KEY, name TEXT, category TEXT, status TEXT,
 last_attempt REAL, last_success REAL, etag TEXT, modified TEXT, failures INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
''')
        # Incomplete writes from a crashed instance are never usable articles.
        for path in paths.cache.glob('*.tmp'):
            if MANAGED.fullmatch(path.name) and (path.is_file() or path.is_symlink()):
                path.unlink()
        with self.lock:
            with self.db() as db:
                ids = {r['id'] for r in db.execute('SELECT id FROM articles')}
                for ident in list(ids):
                    if not self.file(ident).exists():
                        db.execute('DELETE FROM articles WHERE id=?', (ident,))
            for path in paths.cache.iterdir():
                if (MANAGED.fullmatch(path.name) and path.stem not in ids
                        and (path.is_file() or path.is_symlink())):
                    path.unlink()
            self.make_room(0)

    @contextlib.contextmanager
    def db(self):
        for suffix in ('', '-journal', '-wal', '-shm'):
            path = self.paths.db.with_name(self.paths.db.name + suffix)
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError('Refusing non-regular database file')
        with contextlib.closing(sqlite3.connect(self.paths.db, timeout=3)) as con:
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA journal_mode=DELETE')
            con.execute('PRAGMA max_page_count=4096')
            with con:
                yield con

    def file(self, ident, suffix='.json'):
        if len(ident) != 64 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError('Invalid cache ID')
        return self.paths.cache / (ident + suffix)

    def size(self):
        return sum(p.lstat().st_size for p in self.paths.cache.iterdir()
                   if MANAGED.fullmatch(p.name) and (p.is_file() or p.is_symlink()))

    def set_active(self, ident):
        with self.lock:
            self.active = ident

    def rows(self):
        with self.lock, self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM articles ORDER BY published DESC, saved DESC LIMIT 1000')]

    def read(self, ident, mark=False):
        with self.lock:
            try:
                data = read_json(self.file(ident), 2_000_000)
            except (OSError, ValueError, RecursionError):
                return None
            if (not isinstance(data, dict) or data.get('id') != ident
                    or data.get('mode') not in ('summary', 'feed_content', 'extracted')
                    or any(not isinstance(data.get(k), str) for k in TEXT_FIELDS)
                    or type(data.get('published')) not in (int, float)):
                return None
            if mark:
                with self.db() as db:
                    db.execute('UPDATE articles SET seen=1 WHERE id=?', (ident,))
            return data

    def remove(self, ident):
        if ident == self.active:
            return False
        for suffix in ('.json', '.img'):
            self.file(ident, suffix).unlink(missing_ok=True)
        with self.db() as db:
            db.execute('DELETE FROM articles WHERE id=?', (ident,))
        return True

    def make_room(self, extra, exclude=None):
        if extra > self.limit:
            return False
        with self.db() as db:
            candidates = [r['id'] for r in db.execute('SELECT id FROM articles ORDER BY saved')]
        for ident in candidates:
            if self.size() + extra <= self.limit:
                break
            if ident not in (self.active, exclude):
                self.remove(ident)
        return self.size() + extra <= self.limit

    def write_atomic(self, ident, data, suffix):
        # Old version + complete temporary version must BOTH fit until os.replace().
        if not self.make_room(len(data), exclude=ident):
            return False
        dest = self.file(ident, suffix)
        temp = dest.with_suffix(suffix + '.tmp')
        stream = temp.open('xb')
        try:
            with stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            assert self.size() <= self.limit
            os.replace(temp, dest)
            return True
        finally:
            temp.unlink(missing_ok=True)

    def put(self, article):
        data = json.dumps(article, ensure_ascii=False).encode()
        with self.lock:
            if not self.write_atomic(article['id'], data, '.json'):
                return False
            with self.db() as db:
                db.execute('''INSERT INTO articles(id,feed,title,url,source,category,published,saved,mode)
VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,
 published=excluded.published, mode=excluded.mode,source=excluded.source,category=excluded.category''',
                           (article['id'], article['feed'], article['title'], article['url'],
                            article['source'], article['category'], article['published'], time.time(), article['mode']))
                excess = db.execute('SELECT count(*) FROM articles').fetchone()[0] - int(self.options['max_articles'])
                stale = [r['id'] for r in db.execute('SELECT id FROM articles WHERE id != ? ORDER BY saved LIMIT ?',
                                                    (self.active or '', max(0, excess)))]
            for ident in stale:
                self.remove(ident)
            return True

    def put_image(self, ident, data):
        with self.lock:
            if not self.file(ident).exists():
                return False
            # Optional media must never evict a text article.
            if self.size() + len(data) > self.limit:
                return False
            return self.write_atomic(ident, data, '.img')

    def image_available(self, ident):
        """Validate once per file generation, including files from older caches."""
        path = self.file(ident, '.img')
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                return False
            key = (ident, info.st_mtime_ns, info.st_size)
            if not hasattr(self, '_image_checks'):
                self._image_checks = {}
            if key not in self._image_checks:
                from .images import validate
                if info.st_size > self.options['image_limit_mb'] * 1_000_000:
                    return False
                validate(path.read_bytes())
                self._image_checks = {k: v for k, v in self._image_checks.items() if k[0] != ident}
                self._image_checks[key] = True
            return True
        except Exception:
            return False

    def retention(self, successful_feeds):
        """Never discard an offline-only feed or its last usable generation due to age."""
        cutoff = time.time() - self.options['retention_hours'] * 3600
        with self.lock, self.db() as db:
            candidates = []
            for feed in successful_feeds:
                if db.execute('SELECT 1 FROM articles WHERE feed=? AND saved>=? LIMIT 1', (feed, cutoff)).fetchone():
                    candidates.extend(r['id'] for r in db.execute('SELECT id FROM articles WHERE feed=? AND saved<?', (feed, cutoff)))
        with self.lock:
            for ident in candidates:
                self.remove(ident)
            self.make_room(0)

    def feed_info(self, url):
        with self.lock, self.db() as db:
            row = db.execute('SELECT * FROM feeds WHERE url=?', (url,)).fetchone()
            return dict(row) if row else {}

    def feed_result(self, feed, status, headers=None):
        headers = headers or {}
        old = self.feed_info(feed['url'])
        success = status in ('OK', 'NOT MODIFIED')
        now = time.time()
        with self.lock, self.db() as db:
            db.execute('''INSERT OR REPLACE INTO feeds VALUES (?,?,?,?,?,?,?,?,?)''',
                       (feed['url'], feed['name'], feed['category'], status[:200], now,
                        now if success else old.get('last_success', 0),
                        headers.get('ETag', old.get('etag', '')), headers.get('Last-Modified', old.get('modified', '')),
                        0 if success else old.get('failures', 0) + 1))

    def stats(self):
        with self.lock, self.db() as db:
            n, full = db.execute("SELECT count(*),coalesce(sum(mode!='summary'),0) FROM articles").fetchone()
            last = db.execute('SELECT max(last_success) FROM feeds').fetchone()[0]
            feeds = [dict(r) for r in db.execute('SELECT url,name,category,status,last_attempt,last_success,failures FROM feeds')]
            modes={r['mode']:r['n'] for r in db.execute('SELECT mode,count(*) AS n FROM articles GROUP BY mode')}
            return dict(articles=n, long_texts=full, extracted_articles=modes.get('extracted',0), feed_content_articles=modes.get('feed_content',0), cached_images=len(list(self.paths.cache.glob('*.img'))), bytes=self.size(), limit_bytes=self.limit,
                        last_success=last, feeds=feeds, metadata_bytes=sum(p.stat().st_size for p in self.paths.state.glob('index.sqlite3*') if p.is_file()))

    def purge(self):
        with self.lock:
            if self.active:
                raise RuntimeError('Reader still owns an active article')
            entries = [p for p in self.paths.cache.iterdir() if MANAGED.fullmatch(p.name)]
            if any(p.is_dir() and not p.is_symlink() for p in entries):
                raise RuntimeError('Unexpected cache directory; refusing recursive deletion')
            with self.db() as db:
                db.execute('DELETE FROM articles')
                db.execute('DELETE FROM feeds')
            for p in entries:
                p.unlink()
            with self.db() as db:
                db.execute('VACUUM')
