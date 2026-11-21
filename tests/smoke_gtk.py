#!/usr/bin/env python3
"""Real display GTK smoke with isolated offline cache; no user sources/settings touched."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_env import desktop_env
from retronews.config import Paths,DEFAULTS,load
from retronews.engine import Engine
from retronews.images import pixbuf
from retronews.theme import contrast
from retronews.ui import Application
from gi.repository import GLib,Gdk


def main():
    with tempfile.TemporaryDirectory(prefix='retronews-gtk-') as tmp:
        base=Path(tmp)
        os.environ.update(desktop_env(base))
        paths=Paths();options,feeds=load(paths);options['auto_sync']=False
        engine=Engine(paths,options,[dict(url='https://example.org/feed',category='SCIENCE',language='de',name='Local Fixture'),dict(url='https://example.org/en',category='SCIENCE',language='en',name='English')])
        text='OFFLINE COMPLETE ARTICLE\n\n'+'A long paragraph preserved without a network connection.\n\n'*200+'END OF CACHED ARTICLE'
        for i in range(2):
            article=dict(id=hashlib.sha256(str(i).encode()).hexdigest(),feed='https://example.org/feed',title=f'Offline headline {i}',url=f'https://example.org/article/{i}',source='Local Fixture',category='SCIENCE',published=time.time()+i,summary='Short summary',text=text,mode='extracted',image_url='https://example.org/image.png')
            engine.cache.put(article);engine.cache.put_image(article['id'],(Path(__file__).parent/'fixtures/image.png').read_bytes())
        app=Application(engine);out={'gtk_started':False,'keyboard_handler':False,'offline_full_text':False,'theme_reload':False,'filtered_offline_image':False,'scroll_extent':False,'help':False,'help_buffer_stable':False,'stop':False}
        failure=[]
        def check():
            try:
                out['gtk_started']=app.window.get_mapped()
                assert app.language=='MIXED' and len(app.rows)==2
                app.key(None,Gdk.keyval_from_name('l'),0,0)
                assert app.language=='DE' and len(app.rows)==2
                assert 'SPRACHE DE' in app.status_label.get_label()
                app.key(None,Gdk.keyval_from_name('l'),0,0)
                assert app.language=='EN' and not app.rows
                app.key(None,Gdk.keyval_from_name('Tab'),0,0)
                assert app.category=='SCIENCE' and not app.rows
                app.key(None,Gdk.keyval_from_name('l'),0,0)
                assert app.language=='MIXED' and len(app.rows)==2
                out['language_filter']=True
                app.key(None,Gdk.keyval_from_name('Down'),0,0);app.key(None,Gdk.keyval_from_name('Return'),0,0)
                assert app.mode=='reader' and engine.cache.active==app.article['id']
                out['keyboard_handler']=True
                assert app.text.get_label()==text;out['offline_full_text']=True
                filtered=pixbuf(engine.cache.file(app.article['id'],'.img'),app.theme)
                assert max(filtered.get_pixels())<=190;assert app.picture.get_visible();out['filtered_offline_image']=True
                app.key(None,Gdk.keyval_from_name('i'),0,0);assert app.original
                app.key(None,Gdk.keyval_from_name('i'),0,0);assert not app.original
                app.key(None,Gdk.keyval_from_name('h'),0,0);assert app.mode=='help';out['help']=True
                changes=[];token=app.helpview.get_buffer().connect('changed',lambda *_:changes.append(True))
                app.tick();app.tick();assert not changes
                app.helpview.get_buffer().disconnect(token);out['help_buffer_stable']=True
                app.key(None,Gdk.keyval_from_name('Escape'),0,0);assert app.mode=='reader'
                theme=base/'state/omarchy/current/theme';theme.mkdir(parents=True)
                (theme/'colors.toml').write_text('background="#fafafa"\nforeground="#eeeeee"\naccent="#bbbbbb"\n')
                app.tick();assert app.theme['background']=='#fafafa';assert contrast(app.theme['foreground'],app.theme['background'])>=7
                out['theme_reload']=True
                GLib.timeout_add(300,finish)
            except Exception as e:failure.append(repr(e));app.quit()
            return False
        def finish():
            try:
                adj=app.reader_scroll.get_vadjustment();assert adj.get_upper()>adj.get_page_size()*2
                app.key(None,Gdk.keyval_from_name('j'),0,0);assert adj.get_value()>0
                out['scroll_extent']=True
                app.key(None,Gdk.keyval_from_name('Escape'),0,0);assert app.mode=='list';assert engine.cache.active is None
                app.key(None,Gdk.keyval_from_name('q'),0,0)
            except Exception as e:failure.append(repr(e));app.quit()
            return False
        GLib.timeout_add(700,check)
        try:app.run([])
        finally:engine.close()
        out['stop']=not engine.thread.is_alive();assert engine.cache.stats()['articles']==2
        out['errors']=failure;out['pass']=not failure and all(v for k,v in out.items() if k not in ('errors','pass'))
        print(json.dumps(out,indent=2))
        return 0 if out['pass'] else 1


if __name__=='__main__':raise SystemExit(main())
