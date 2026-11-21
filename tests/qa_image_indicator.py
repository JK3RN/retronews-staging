"""Display QA for offline indicators; isolated XDG paths, no HTTP requests.

Run outside the restricted sandbox: python tests/qa_image_indicator.py
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib
from retronews.config import Paths, load
from retronews.engine import Engine
from retronews.ui import Application
from retronews.theme import css


def main():
    # Gtk.init_check alone can report success without a usable display here.
    Gtk.init()
    if Gdk.Display.get_default() is None:
        print(json.dumps({'status': 'BLOCKED', 'reason': 'No usable GTK display'}))
        return 2
    with tempfile.TemporaryDirectory(prefix='rn-indicator-') as tmp:
        env={key:str(Path(tmp)/name) for key,name in (
            ('XDG_CONFIG_HOME','config'),('XDG_CACHE_HOME','cache'),
            ('XDG_STATE_HOME','state'),('XDG_RUNTIME_DIR','run'))}
        with patch.dict(os.environ,env):
            paths=Paths();options,_=load(paths);options['auto_sync']=False
            feeds=[dict(url='https://example.org/'+lang,name=lang,language=lang,category='TECH') for lang in ('de','en')]
            engine=Engine(paths,options,feeds)
            for lang in ('de','en'):
                for image in (False,True):
                    ident=hashlib.sha256(f'{lang}-{image}'.encode()).hexdigest()
                    a=dict(id=ident,feed='https://example.org/'+lang,title=f'{lang}: image={image}',url='https://example.org/article',source=lang,category='TECH',published=0,summary='Offline fixture',text='Offline text',mode='summary',image_url='')
                    engine.cache.put(a)
                    if image:engine.cache.put_image(ident,(Path(__file__).parent/'fixtures/image.png').read_bytes())
            app=Application(engine);records=[];errors=[];mutations=[]
            configs={p.name:p.read_bytes() for p in paths.config.iterdir()}
            colors=[None]
            from retronews.theme import palette
            colors[0]=palette()
            palette_patch=patch('retronews.ui.palette',side_effect=lambda:colors[0]);palette_patch.start()
            cases=[(width,lang,category,theme) for width in (1040,400) for lang in ('DE','EN','MIXED') for category in ('ALL','TECH') for theme in ('dark','light')]
            def apply():
                if not cases:
                    try:
                        app.back();app.language='MIXED';app.select_category('ALL')
                        a=next(a for a in app.rows if 'image=False' in a['title'])
                        def symbol():
                            row=next(app.list.get_row_at_index(i) for i,r in enumerate(app.rows) if r['id']==a['id'])
                            return row.get_child().get_first_child().get_first_child().get_label()=='▣'
                        assert not symbol()
                        engine.cache.put_image(a['id'],(Path(__file__).parent/'fixtures/image.png').read_bytes())
                        engine.revision+=1;app.tick();assert symbol();mutations.append('download_visible')
                        engine.cache.put_image(a['id'],b'corrupt image bytes')
                        engine.revision+=1;app.tick();assert not symbol();mutations.append('invalidated_hidden')
                        engine.cache.purge();engine.revision+=1;app.tick()
                        assert not app.rows and engine.cache.size()==0
                        assert configs=={p.name:p.read_bytes() for p in paths.config.iterdir()}
                        mutations.append('clean_no_rows_config_preserved')
                    except Exception as error:errors.append(str(error))
                    app.quit();return False
                width,lang,category,theme=cases.pop(0)
                colors[0]=dict(background='#10191c' if theme=='dark' else '#fafafa',foreground='#dce7e8' if theme=='dark' else '#111111',accent='#83bdb0' if theme=='dark' else '#315b73',warning='#dfb979',critical='#e58a8a',origin='FIXTURE')
                app.theme=colors[0];app.provider.load_from_data(css(colors[0]).encode())
                app.language=lang;app.select_category(category)
                assert app.category_buttons[category].get_active()
                app.window.get_child().allocate(width,700,-1,None)
                def check():
                    try:
                        count=0
                        for i,a in enumerate(app.rows):
                            row=app.list.get_row_at_index(i)
                            app.list.select_row(row)
                            metadata=row.get_child().get_first_child()
                            first=metadata.get_first_child()
                            available=engine.cache.image_available(a['id'])
                            self_expected='image=True' in a['title']
                            assert available==self_expected, 'Real decoder availability mismatch'
                            assert (first.get_label()=='▣')==available
                            if available:
                                assert first.get_tooltip_text()=='Bild offline verfügbar'
                                assert first.get_width()>0
                                assert first.get_width()>=first.measure(Gtk.Orientation.HORIZONTAL,-1)[0]
                                ink,_=first.get_layout().get_pixel_extents();x,y=first.get_layout_offsets()
                                assert y+ink.y>=-1 and y+ink.y+ink.height<=first.get_height()+1
                                count+=1
                        assert count>0 and len(app.rows)>count
                        records.append(dict(width=width,language=lang,category=category,theme=theme,indicators=count,rows=len(app.rows)))
                        GLib.idle_add(apply)
                    except Exception as error:errors.append(str(error));app.quit()
                    return False
                GLib.timeout_add(150,check)
                return False
            GLib.timeout_add(300,apply)
            try:app.run([])
            finally:engine.close();palette_patch.stop()
            print(json.dumps(dict(status='FAIL' if errors else 'PASS',cases=records,mutations=mutations,errors=errors),indent=2))
            return int(bool(errors))


if __name__=='__main__':raise SystemExit(main())
