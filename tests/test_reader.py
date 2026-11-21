import base64
import dataclasses
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from retronews.config import Paths, DEFAULTS, load
from retronews.content import parse_feed, extract_article, html_text
from retronews.cache import Cache
from retronews.engine import Engine
from retronews.network import HTTP, Budget
from retronews.images import validate

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures'
PNG=(FIX/'image.png').read_bytes()


class Environment(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='retronews-test-')
        root=Path(self.temp.name)
        self.env={k:str(root/n) for k,n in [('XDG_CONFIG_HOME','config'),('XDG_CACHE_HOME','cache'),('XDG_STATE_HOME','state'),('XDG_RUNTIME_DIR','run')]}
        self.patch=patch.dict(os.environ,self.env);self.patch.start()
        self.paths=Paths();self.options,self.feeds=load(self.paths)
        self.cache=Cache(self.paths,self.options)
    def tearDown(self):
        self.patch.stop();self.temp.cleanup()
    def article(self,n=0,text='cached body',feed='https://example.org/rss'):
        import hashlib
        return dict(id=hashlib.sha256(str(n).encode()).hexdigest(),feed=feed,title='Title '+str(n),url='https://example.org/'+str(n),source='Fixture',category='TECH',published=time.time()+n,summary='summary',text=text,mode='summary',image_url='')


class ParserTests(unittest.TestCase):
    source=dict(url='https://example.org/feed',name='Fixture',category='SCIENCE')
    def test_rss_and_atom(self):
        rss=parse_feed((FIX/'rss.xml').read_bytes(),self.source);atom=parse_feed((FIX/'atom.xml').read_bytes(),self.source)
        self.assertEqual(rss[0]['url'],'https://example.org/article');self.assertEqual(rss[0]['summary'],'Short feed summary.')
        self.assertGreater(atom[0]['published'],0);self.assertIn('First paragraph.',atom[0]['text'])
        self.assertNotIn('unwanted',atom[0]['text'])
    def test_invalid_empty_and_entities(self):
        for data in [b'bad',b'<html></html>',b'<!DOCTYPE rss [<!ENTITY a "bad">]><rss/>', '<!DOCTYPE rss [<!ENTITY a "bad">]><rss/>'.encode('utf-16')]:
            with self.assertRaises(Exception):parse_feed(data,self.source)
        self.assertEqual(parse_feed(b'<rss><channel/></rss>',self.source),[])
    def test_extraction_and_paywall_fallback(self):
        body=('A complete paragraph about science and engineering. '*30)
        text,img=extract_article(('<html><nav>noise</nav><article><h1>Heading</h1><p>'+body+'</p><script>bad()</script></article></html>').encode(),'https://example.org')
        self.assertIn('Heading',text);self.assertIn(body.strip(),text);self.assertNotIn('noise',text);self.assertNotIn('bad()',text)
        self.assertEqual(extract_article(('<article>Subscribe to continue '+body+'</article>').encode(),'https://example.org')[0],'')
    def test_images_invalid_and_bomb(self):
        import struct
        with self.assertRaises(ValueError):validate(b'not an image')
        with self.assertRaises(ValueError):validate(b'\x89PNG\r\n\x1a\n'+b'\0'*8+struct.pack('>II',9000,9000))


class CacheTests(Environment):
    def test_default_contract_and_roundtrip(self):
        self.assertEqual({k:self.options[k] for k in ['auto_sync','persist','images','cache_limit_mb','retention_hours']},dict(auto_sync=True,persist=True,images=True,cache_limit_mb=350,retention_hours=24))
        a=self.article(text='offline text '*300);self.assertTrue(self.cache.put(a))
        again=Cache(self.paths,self.options);self.assertEqual(again.read(a['id'])['text'],a['text'])
        self.assertFalse(list(self.paths.state.glob('*-wal')))
    def test_cap_including_temp_and_active_reader(self):
        self.cache.limit=5000
        active=self.article(1,'a'*1700);self.cache.put(active);self.cache.set_active(active['id'])
        for n in range(2,10):
            self.cache.put(self.article(n,'b'*1800))
            self.assertLessEqual(self.cache.size(),5000)
        self.assertIsNotNone(self.cache.read(active['id']));self.assertFalse(list(self.paths.cache.glob('*.tmp')))
        self.assertFalse(self.cache.put(self.article(90,'x'*10000)));self.assertIsNotNone(self.cache.read(active['id']))
    def test_retention_offline_and_per_feed_protection(self):
        old=self.article(1);other=self.article(2,feed='https://other.org/rss')
        self.cache.put(old);self.cache.put(other)
        with self.cache.db() as db:db.execute('UPDATE articles SET saved=?',(time.time()-90000,))
        self.cache.retention([]);self.assertEqual(len(self.cache.rows()),2)
        self.cache.retention([old['feed']]);self.assertEqual(len(self.cache.rows()),2) # Last usable generation.
        self.cache.put(self.article(3));self.cache.set_active(old['id']);self.cache.retention([old['feed']])
        self.assertIsNotNone(self.cache.read(old['id']))
        self.cache.set_active(None);self.cache.retention([old['feed']])
        self.assertIsNone(self.cache.read(old['id']));self.assertIsNotNone(self.cache.read(other['id']))
    def test_purge_preserves_preferences_and_is_idempotent(self):
        before={p.name:p.read_bytes() for p in self.paths.config.iterdir()};self.cache.put(self.article())
        self.cache.purge();self.cache.purge()
        self.assertEqual(self.cache.size(),0);self.assertEqual(self.cache.rows(),[])
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.paths.config.iterdir()})
    def test_failed_atomic_write_removes_temporary(self):
        a=self.article();self.cache.put(a)
        with patch('retronews.cache.os.replace',side_effect=OSError('disk fixture')):
            with self.assertRaises(OSError):self.cache.put(a|{'text':'new'})
        self.assertEqual(self.cache.read(a['id'])['text'],'cached body');self.assertFalse(list(self.paths.cache.glob('*.tmp')))
    def test_metadata_is_separately_accounted(self):
        self.cache.put(self.article());stats=self.cache.stats()
        self.assertEqual(stats['bytes'],sum(p.stat().st_size for p in self.paths.cache.iterdir()))
        self.assertGreater(stats['metadata_bytes'],0)


class FixtureHandler(BaseHTTPRequestHandler):
    requests=[];broken=False;slow_started=threading.Event()
    def log_message(self,*args):pass
    def do_GET(self):
        type(self).requests.append((self.path,dict(self.headers)))
        if self.path=='/robots.txt':return self.reply(b'User-agent: *\nDisallow: /denied\n')
        if self.path=='/slow-feed':
            body=(FIX/'rss.xml').read_bytes();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
            type(self).slow_started.set()
            try:
                for pos in range(0,len(body),20):self.wfile.write(body[pos:pos+20]);self.wfile.flush();time.sleep(.1)
            except (BrokenPipeError,ConnectionResetError):pass
            return
        if self.path=='/feed':
            if type(self).broken:self.send_error(503);return
            if self.headers.get('If-None-Match')=='"fixture"':self.send_response(304);self.end_headers();return
            return self.reply((FIX/'rss.xml').read_bytes(),etag='"fixture"')
        if self.path=='/atom':return self.reply((FIX/'atom.xml').read_bytes())
        if self.path=='/bad':return self.reply(b'<html>not a feed</html>')
        if self.path=='/article':return self.reply(('<article><h1>Complete title</h1><p>'+'Complete offline article with all paragraphs. '*50+'</p><img src="/image.png"></article>').encode())
        if self.path=='/image.png':return self.reply(PNG)
        if self.path=='/oversize':return self.reply(b'a'*10000)
        self.send_error(404)
    def reply(self,body,etag=None):
        self.send_response(200);self.send_header('Content-Length',str(len(body)))
        if etag:self.send_header('etag',etag)
        self.end_headers();self.wfile.write(body)


class NetworkTests(Environment):
    def setUp(self):
        super().setUp();FixtureHandler.requests=[];FixtureHandler.broken=False;FixtureHandler.slow_started.clear()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()
    def source(self,path='/feed'):return dict(name='Fixture',category='SCIENCE',url=self.base+path,prefetch=True)
    def test_sync_prefetch_offline_failure_keeps_complete_text(self):
        e=Engine(self.paths,self.options,[self.source(),self.source('/bad')]);e.sync()
        self.assertEqual(e.connection,'TEILWEISE ONLINE');rows=e.cache.rows();self.assertEqual(len(rows),1)
        a=e.cache.read(rows[0]['id']);self.assertEqual(a['mode'],'extracted');self.assertIn('all paragraphs.',a['text']);self.assertTrue(e.cache.file(a['id'],'.img').exists())
        FixtureHandler.broken=True
        with e.cache.db() as db:db.execute('UPDATE feeds SET last_success=0')
        e.sync();self.assertIn('OFFLINE',e.connection);self.assertEqual(e.cache.read(rows[0]['id'])['text'],a['text'])
        e.close();again=Cache(self.paths,self.options);self.assertEqual(again.read(rows[0]['id'])['text'],a['text']);self.assertTrue(again.file(rows[0]['id'],'.img').exists())
    def test_conditional_requests_and_bad_feeds(self):
        e=Engine(self.paths,self.options|{'prefetch_articles':0},[self.source(),self.source('/atom')]);e.sync()
        self.assertEqual(len(e.cache.rows()),2)
        with e.cache.db() as db:db.execute('UPDATE feeds SET last_success=0')
        e.sync();self.assertTrue(any(h.get('If-None-Match')=='"fixture"' for p,h in FixtureHandler.requests if p=='/feed'))
        self.assertEqual(e.cache.feed_info(self.base+'/feed')['status'],'NOT MODIFIED');e.close()
    def test_robot_denial_and_download_bound(self):
        http=HTTP(timeout=1)
        with self.assertRaises(ValueError):http.get(self.base+'/denied',100,Budget(10000))
        self.assertFalse(any(p=='/denied' for p,h in FixtureHandler.requests))
        with self.assertRaises(ValueError):http.get(self.base+'/oversize',100,Budget(20000))
    def test_auto_sync_background_and_cancel(self):
        e=Engine(self.paths,self.options|{'prefetch_articles':0},[self.source()]);e.start()
        until=time.monotonic()+8
        while not e.cache.rows() and time.monotonic()<until:time.sleep(.05)
        self.assertTrue(e.cache.rows());e.close();self.assertFalse(e.thread.is_alive())
    def test_stop_cancels_inflight_download_and_preserves_old_cache(self):
        old=self.article(text='offline generation survives cancellation');self.cache.put(old)
        e=Engine(self.paths,self.options,[self.source('/slow-feed')]);e.start()
        self.assertTrue(FixtureHandler.slow_started.wait(4))
        started=time.monotonic();e.close()
        self.assertLess(time.monotonic()-started,3)
        self.assertFalse(e.thread.is_alive())
        self.assertFalse(any(t.name.startswith('retronews-http') for t in threading.enumerate()))
        self.assertEqual(e.cache.read(old['id'])['text'],old['text'])
        self.assertFalse(list(self.paths.cache.glob('*.tmp')))
    def test_auto_sync_periodic_scaled_interval(self):
        e=Engine(self.paths,self.options|{'sync_minutes':.001,'prefetch_articles':0},[self.source()]);e.start()
        deadline=time.monotonic()+7
        while sum(p=='/feed' for p,h in FixtureHandler.requests)<2 and time.monotonic()<deadline:time.sleep(.05)
        e.close();self.assertGreaterEqual(sum(p=='/feed' for p,h in FixtureHandler.requests),2)
    def test_disabled_auto_sync(self):
        e=Engine(self.paths,self.options|{'auto_sync':False,'prefetch_articles':0},[self.source()]);e.start();time.sleep(.15)
        self.assertFalse(FixtureHandler.requests);self.assertTrue(e.request_refresh())
        deadline=time.monotonic()+5
        while not e.cache.rows() and time.monotonic()<deadline:time.sleep(.05)
        self.assertTrue(e.cache.rows());e.close()


class ProcessTests(Environment):
    def cli(self,*args):return subprocess.run([sys.executable,'-m','retronews',*args],cwd=ROOT,env=os.environ.copy(),capture_output=True,text=True,timeout=20)
    def launch(self,options=None):
        (self.paths.config/'feeds.json').write_text('[]')
        (self.paths.config/'settings.json').write_text(json.dumps(self.options|{'auto_sync':False}|(options or {})))
        proc=subprocess.Popen([sys.executable,'-m','retronews','run','--headless'],cwd=ROOT,env=os.environ.copy(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(proc.stdout.close);self.addCleanup(proc.stderr.close)
        self.addCleanup(lambda:proc.poll() is None and (proc.kill(),proc.wait()))
        until=time.monotonic()+5
        while not self.paths.socket.exists() and proc.poll() is None and time.monotonic()<until:time.sleep(.05)
        self.assertIsNone(proc.poll());self.assertTrue(self.paths.socket.exists());return proc
    def test_stop_actual_process_exit_keeps_cache_and_no_workers(self):
        a=self.article(text='saved offline article');self.cache.put(a);proc=self.launch()
        self.assertTrue(json.loads(self.cli('status').stdout)['running'])
        self.assertEqual(self.cli('refresh').returncode,0)
        result=self.cli('stop');self.assertEqual(result.returncode,0,result.stderr);proc.wait(timeout=3)
        self.assertEqual(proc.returncode,0);self.assertFalse(Path('/proc',str(proc.pid)).exists())
        self.assertEqual(self.cache.read(a['id'])['text'],a['text']);self.assertFalse(self.paths.socket.exists())
        refresh=self.cli('refresh');self.assertEqual(refresh.returncode,2);self.assertIn('läuft nicht',refresh.stderr)
    def test_clean_running_rejected_then_stop_clean_actual_disk_release(self):
        article=self.article();self.cache.put(article);self.cache.put_image(article['id'],PNG)
        self.assertTrue(self.cache.file(article['id'],'.img').exists());proc=self.launch()
        expected={p.name:p.read_bytes() for p in self.paths.config.iterdir()}
        self.assertEqual(self.cli('clean').returncode,2);self.assertTrue(self.cache.rows())
        result=self.cli('stop','--clean');self.assertEqual(result.returncode,0,result.stderr);proc.wait(timeout=3)
        self.assertFalse(Path('/proc',str(proc.pid)).exists());self.assertEqual(self.cache.size(),0);self.assertEqual(self.cache.rows(),[])
        self.assertEqual(expected,{p.name:p.read_bytes() for p in self.paths.config.iterdir()})
        self.assertEqual(self.cli('clean').returncode,0);self.assertEqual(self.cli('clean').returncode,0)
    def test_second_instance_blocked_and_persist_off(self):
        self.cache.put(self.article());proc=self.launch({'persist':False})
        duplicate=self.cli('run','--headless');self.assertEqual(duplicate.returncode,2)
        self.assertEqual(self.cli('stop').returncode,0);proc.wait(timeout=3)
        self.assertEqual(self.cache.size(),0)


class AdditionalSafetyTests(Environment):
    def test_450mb_is_rejected_and_theme_preferences_survive_clean(self):
        from retronews.config import load
        (self.paths.config/'settings.json').write_text(json.dumps(self.options|{'cache_limit_mb':450}))
        with self.assertRaises(ValueError):load(self.paths)
        (self.paths.config/'settings.json').write_text(json.dumps(self.options))
        custom=self.paths.config/'theme.json';custom.write_text('{"keep":true}')
        self.cache.put(self.article());self.cache.purge();self.assertEqual(custom.read_text(),'{"keep":true}')
    def test_stale_temporary_and_orphan_recovery(self):
        a=self.article();self.cache.put(a)
        (self.paths.cache/('e'*64 + '.json.tmp')).write_bytes(b'partial')
        self.cache.file('f'*64).write_text('orphan')
        again=Cache(self.paths,self.options)
        self.assertEqual(again.read(a['id'])['text'],a['text'])
        self.assertFalse(list(self.paths.cache.glob('*.tmp')));self.assertFalse(again.file('f'*64).exists())
    def test_image_reservation_stays_under_cap(self):
        self.cache.limit=5000;a=self.article();self.cache.put(a);self.cache.set_active(a['id'])
        self.assertTrue(self.cache.put_image(a['id'],b'x'*1800))
        self.assertFalse(self.cache.put_image(a['id'],b'y'*4900))
        self.assertEqual(self.cache.file(a['id'],'.img').read_bytes(),b'x'*1800)
        self.assertLessEqual(self.cache.size(),self.cache.limit)
    def test_theme_contrast_light_dark_and_unsafe_colors(self):
        from retronews.theme import palette,contrast
        theme=self.paths.state/'omarchy/current/theme';theme.mkdir(parents=True)
        for bg,fg in [('#ffffff','#eeeeee'),('#000000','#222222')]:
            (theme/'colors.toml').write_text(f'background="{bg}"\nforeground="{fg}"\naccent="{fg}"\n')
            p=palette();self.assertGreaterEqual(contrast(p['foreground'],p['background']),7)
        (theme/'colors.toml').write_text('background="url(untrusted)"\n')
        self.assertTrue(palette()['background'].startswith('#'))

if __name__=='__main__':unittest.main()
