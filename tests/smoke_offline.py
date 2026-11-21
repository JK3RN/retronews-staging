"""Prefetch over local HTTP, then restart a real GTK process with a failing transport."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_env import desktop_env
from retronews.config import Paths,load
from retronews.engine import Engine
ROOT=Path(__file__).resolve().parents[1]


def reader():
    from retronews.ui import Application
    from gi.repository import GLib,Gdk
    class Offline:
        calls=0
        def get(self,*args,**kwargs):
            self.calls+=1;raise OSError('Simulated offline transport')
    paths=Paths();options,feeds=load(paths);http=Offline();engine=Engine(paths,options,feeds,http=http)
    before={p.name:p.read_bytes() for p in paths.cache.iterdir()}
    app=Application(engine);errors=[];checks={}
    def check():
        try:
            assert app.language=='MIXED'
            for expected in ('DE','EN','MIXED'):
                app.key(None,Gdk.KEY_l,0,0);assert app.language==expected
            assert http.calls==0
            app.open_row(app.list.get_row_at_index(0))
            assert app.article['mode']=='extracted' and 'all paragraphs.' in app.text.get_label()
            assert app.picture.get_visible()
            app.key(None,Gdk.KEY_l,0,0);assert app.language=='MIXED' and app.mode=='reader'
            app.key(None,Gdk.KEY_i,0,0);assert app.original
            app.key(None,Gdk.KEY_i,0,0);assert not app.original
            assert http.calls==0
            app.key(None,Gdk.KEY_r,0,0)
            checks['offline_restart_text_image_filter_no_network']=True
            GLib.timeout_add(100,poll)
        except Exception as exc:errors.append(repr(exc));app.quit()
        return False
    deadline=time.monotonic()+8
    def poll():
        try:
            if engine.busy or engine.pending:
                assert time.monotonic()<deadline;return True
            assert http.calls>0 and 'OFFLINE' in engine.connection
            assert before=={p.name:p.read_bytes() for p in paths.cache.iterdir()}
            assert 'all paragraphs.' in app.text.get_label() and app.picture.get_visible()
            checks['failed_refresh_cache_and_open_reader_preserved']=True
            app.quit()
        except Exception as exc:errors.append(repr(exc));app.quit()
        return False
    GLib.timeout_add(700,check)
    with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('Unexpected real network')):
        try:app.run([])
        finally:engine.close()
    assert engine.thread is not None and not engine.thread.is_alive()
    assert not any(t.name.startswith('retronews-http') for t in threading.enumerate())
    print(json.dumps(dict(checks=checks,errors=errors,pass_=not errors),indent=2))
    return 1 if errors else 0


def main():
    from http.server import ThreadingHTTPServer
    from test_reader import FixtureHandler
    with tempfile.TemporaryDirectory(prefix='rn-offline-') as tmp:
        with patch.dict(os.environ, desktop_env(Path(tmp))):
            paths=Paths();options,_=load(paths);options['auto_sync']=False
            server=ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler);thread=threading.Thread(target=server.serve_forever);thread.start()
            feeds=[dict(name='Local prefetch',category='SCIENCE',language='de',url=f'http://127.0.0.1:{server.server_port}/feed',prefetch=True)]
            (paths.config/'settings.json').write_text(json.dumps(options));(paths.config/'feeds.json').write_text(json.dumps(feeds))
            engine=Engine(paths,options,feeds)
            try:
                engine.sync();assert engine.cache.stats()['extracted_articles']==1 and engine.cache.stats()['cached_images']==1
                with engine.cache.db() as db:db.execute('UPDATE feeds SET last_success=0')
            finally:engine.close();server.shutdown();server.server_close();thread.join()
            # Server gone; independent GTK process reopens the persisted generation.
            return subprocess.run([sys.executable,__file__,'--reader'],cwd=ROOT,env=dict(os.environ),timeout=20).returncode

if __name__=='__main__':raise SystemExit(reader() if '--reader' in sys.argv else main())
