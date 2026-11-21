from pathlib import Path
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from retronews.cache import Cache
from retronews.config import load
from retronews.content import html_text, parse_feed, web_url
from retronews.network import Budget, HTTP, connect_public
from test_reader import Environment


SOURCE = dict(url='https://example.org/feed', name='Fixture', category='SCIENCE')


class FeedSafetyTests(unittest.TestCase):
    def test_bounds_and_encoding(self):
        for payload in (
            b'<rss>' + b' ' * 2_000_000 + b'</rss>',
            b'<rss>' + b'<x>' * 65 + b'</x>' * 65 + b'</rss>',
            b'<rss><channel>\xff</channel></rss>',
        ):
            with self.subTest(length=len(payload)), self.assertRaises((ValueError, SyntaxError)):
                parse_feed(payload, SOURCE)
        latin = '<?xml version="1.0" encoding="ISO-8859-1"?><rss><item><link>/1</link><title>Café</title></item></rss>'
        self.assertEqual(parse_feed(latin.encode('latin-1'), SOURCE)[0]['title'], 'Café')

    def test_duplicate_bad_date_and_length(self):
        item = '<item><link>/1</link><title>' + 'T' * 1000 + '</title><pubDate>never</pubDate></item>'
        items = parse_feed(('<rss>' + item * 2 + '</rss>').encode(), SOURCE)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0]['title']), 500)
        self.assertEqual(items[0]['published'], 0)
        self.assertEqual(parse_feed(b'<rss><item><title>No link</title></item></rss>', SOURCE), [])

    def test_plain_text_and_malformed_attributes(self):
        self.assertEqual(html_text('<p class>Safe</p><script>hidden</script>')[0], 'Safe')
        for url in ('http://example.org:invalid/', 'http://@example.org/',
                    'file:///tmp/text', 'http://example.org/' + 'x' * 2048):
            self.assertEqual(web_url(url), '')


class StorageSafetyTests(Environment):
    def test_malformed_cache_and_file_boundaries(self):
        item = self.article()
        self.assertTrue(self.cache.put(item))
        cached = self.cache.file(item['id'])
        for data in (b'[]', b'{"mode":"bad"}', b'\xff'):
            cached.write_bytes(data)
            self.assertIsNone(self.cache.read(item['id']))
        target = Path(self.temp.name) / 'external.json'
        target.write_text('{"keep":true}')
        cached.unlink()
        cached.symlink_to(target)
        self.assertIsNone(self.cache.read(item['id']))
        unrelated = self.paths.cache / 'note.json'
        unrelated.write_text('keep')
        Cache(self.paths, self.options).purge()
        self.assertEqual(target.read_text(), '{"keep":true}')
        self.assertEqual(unrelated.read_text(), 'keep')

    def test_config_symlink_and_permission_failure(self):
        feeds = self.paths.config / 'feeds.json'
        feeds.unlink()
        feeds.symlink_to(self.paths.config / 'settings.json')
        with self.assertRaises(ValueError):
            load(self.paths)
        from unittest.mock import patch
        original = self.article()
        self.assertTrue(self.cache.put(original))
        with patch('retronews.cache.os.replace', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.cache.put(original | {'text': 'replacement'})
        self.assertEqual(self.cache.read(original['id']), original)


class NetworkSafetyTests(unittest.TestCase):
    def test_local_permission_is_bound_to_subscribed_origin(self):
        hits = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                if self.path == '/feed-redirect':
                    self.send_response(302)
                    self.send_header('Location', '/feed')
                    self.end_headers()
                    return
                if self.path == '/slow':
                    self.server.release.wait(2)
                    return
                body = b'User-agent: *\n' if self.path == '/robots.txt' else b'<rss/>'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            server.release = threading.Event()
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            try:
                base = f'http://127.0.0.1:{server.server_port}'
                http = HTTP(timeout=.15)
                self.assertEqual(http.get(base + '/feed', 100, Budget(1000))[2], b'<rss/>')
                prior = hits[:]
                with self.assertRaises(ValueError):
                    http.get(base + '/image', 100, Budget(1000),
                             subscription='https://example.org/feed')
                self.assertEqual(hits, prior)
                self.assertEqual(http.get(base + '/feed-redirect', 100, Budget(1000))[2], b'<rss/>')
                with self.assertRaises(TimeoutError):
                    HTTP(timeout=.15).get(base + '/slow', 100, Budget(1000), robots=False)
            finally:
                server.release.set()
                server.shutdown()
                worker.join()

    def test_public_connector_refuses_non_public_addresses(self):
        for host in ('127.0.0.1', '::1', '192.168.1.1', '224.0.0.1'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                connect_public((host, 80), 1)
