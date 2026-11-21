import json
import unittest
from unittest.mock import patch
from test_reader import Environment
from retronews.config import load
from retronews.language import filter_rows
from retronews.content import extract_article

class LanguageTests(Environment):
    def test_cached_legacy_unknown_and_categories_without_network(self):
        feeds=[dict(url='de',language='de'),dict(url='en',language='en')]
        for i,feed in enumerate(('de','en','custom')):self.cache.put(self.article(i,feed=feed))
        with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('network')):
            self.assertEqual(len(filter_rows(self.cache.rows(),feeds)),3)
            self.assertEqual([r['feed'] for r in filter_rows(self.cache.rows(),feeds,'DE')],['de'])
            self.assertEqual([r['feed'] for r in filter_rows(self.cache.rows(),feeds,'EN')],['en'])
            self.assertEqual(filter_rows(self.cache.rows(),feeds,'DE','SPACE'),[])
    def test_legacy_feed_defaults_to_unknown_without_rewriting_file(self):
        p = self.paths.config / 'feeds.json'
        p.write_text(json.dumps([dict(name='Custom', category='TECH', url='https://example.com/rss')]))
        before = p.read_bytes()
        _, sources = load(self.paths)
        self.assertEqual(sources[0]['language'], 'unknown')
        self.assertEqual(p.read_bytes(), before)

class ExtractionTests(unittest.TestCase):
    def test_wordpress_microdata_and_article_cleanup(self):
        for wrapper in ('div class="entry-content"','section itemprop="articleBody"','article'):
            tag=wrapper.split()[0]
            body='Research findings and practical details. '*40
            html=f'<main><nav>BAD NAV</nav><{wrapper}><p>{body}</p><div class="cookie-banner">BAD COOKIE</div><aside>BAD RELATED</aside><div aria-hidden="true">BAD HIDDEN</div></{tag}><footer>BAD FOOTER</footer></main>'
            if tag!='article':html=html.replace('<main>','<div>').replace('</main>','</div>')
            text,_=extract_article(html.encode(),'https://example.com')
            self.assertIn(body.strip(),text)
            self.assertNotIn('BAD',text)
    def test_fallback_and_paywall(self):
        self.assertEqual(extract_article(b'<div class="entry-content">short</div>','https://example.com'),('',''))
        self.assertEqual(extract_article(('<article>Subscribe to continue '+'text '*200+'</article>').encode(),'https://example.com'),('',''))
