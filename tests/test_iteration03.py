"""Isolated media regressions; no real sockets or user data."""
import unittest
from unittest.mock import patch
from test_reader import Environment, PNG
from retronews.content import parse_feed, extract_article, image_url
from retronews.engine import Engine
from retronews.images import validate

class MediaTests(Environment):
    def test_image_never_evicts_text(self):
        a,b=self.article(1),self.article(2)
        self.cache.put(a);self.cache.put(b)
        self.cache.limit=self.cache.size()+10
        self.assertFalse(self.cache.put_image(a['id'],PNG))
        self.assertEqual(len(self.cache.rows()),2)

    def test_offline_indicator_validates_bytes_and_invalidation(self):
        try:validate(PNG)
        except Exception as error:self.skipTest('Decoder unavailable: '+str(error))
        a=self.article();self.cache.put(a)
        self.assertFalse(self.cache.image_available(a['id']))
        self.cache.put_image(a['id'],b'broken')
        self.assertFalse(self.cache.image_available(a['id']))
        self.cache.put_image(a['id'],PNG)
        self.assertTrue(self.cache.image_available(a['id']))
        self.cache.file(a['id'],'.img').unlink()
        self.assertFalse(self.cache.image_available(a['id']))

    def test_availability_state_with_stub_decoder(self):
        a=self.article();self.cache.put(a)
        with patch('retronews.images.validate',return_value=True) as decoder:
            self.assertFalse(self.cache.image_available(a['id']))
            self.cache.put_image(a['id'],PNG)
            self.assertTrue(self.cache.image_available(a['id']))
            self.assertTrue(self.cache.image_available(a['id']))
            self.assertEqual(decoder.call_count,1)
            self.cache.file(a['id'],'.img').unlink()
            self.assertFalse(self.cache.image_available(a['id']))

    def test_sources_and_filters(self):
        source=dict(url='https://example.org/feed',name='Fixture',category='TECH')
        for media in ('<media:content url="/photo.jpg"/>', '<media:content medium="image" url="/photo.jpg"/>', '<media:thumbnail url="/photo.jpg"/>', '<link rel="enclosure" type="image/jpeg" href="/photo.jpg"/>'):
            xml=f'<rss xmlns:media="http://search.yahoo.com/mrss/"><channel><item><link>https://example.org/story</link>{media}</item></channel></rss>'
            self.assertEqual(parse_feed(xml.encode(),source)[0]['image_url'],'https://example.org/photo.jpg')
        body='<article>'+('article text '*100)+'</article>'
        for key in ('property="og:image"','name="twitter:image"'):
            text,image=extract_article((f'<meta {key} content="/photo.jpg">'+body).encode(),source['url'])
            self.assertTrue(text);self.assertEqual(image,'https://example.org/photo.jpg')
        for url in ('/logo.png','/tracking.gif','/avatar.jpg','/image.svg','javascript:alert(1)'):
            self.assertFalse(image_url(url,source['url']))
        self.assertFalse(image_url('/photo.jpg',source['url'],{'width':'1'}))

    @patch('retronews.images.validate',return_value=True)
    @patch('retronews.engine.validate',return_value=True)
    def test_sync_refresh_offline_restart_with_stub_decoder(self, engine_decoder, cache_decoder):
        source=dict(url='https://example.org/feed',name='Fixture',category='TECH',prefetch=True)
        class Transport:
            broken=False
            def get(self,url,*args,**kwargs):
                if self.broken:raise OSError('offline')
                if url==source['url']:return 200,{},b'<rss><channel><item><link>https://example.org/story</link><description>summary</description></item></channel></rss>'
                if url.endswith('.png'):return 200,{},PNG
                return 200,{},('<meta name="twitter:image" content="/photo.png"><article>'+('complete text '*100)+'</article>').encode()
        transport=Transport();e=Engine(self.paths,self.options,[source],transport);e.sync()
        row=e.cache.rows()[0];a=e.cache.read(row['id'])
        self.assertEqual(a['image_url'],'https://example.org/photo.png')
        self.assertTrue(e.cache.image_available(row['id']))
        transport.broken=True
        with e.cache.db() as db:db.execute('UPDATE feeds SET last_success=0')
        e.sync();self.assertEqual(e.cache.read(row['id']),a);e.close()
        from retronews.cache import Cache
        restarted=Cache(self.paths,self.options)
        self.assertTrue(restarted.image_available(row['id']))
        self.assertEqual(restarted.read(row['id']),a)

if __name__=='__main__':unittest.main()
