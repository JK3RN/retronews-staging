"""Real GTK allocations and rendered snapshots; no desktop configuration changes.

The compositor owns the outer window. Constrained child allocations exercise
1040x760, 760x600 and 400x700 inside that real surface, without WM commands.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_env import desktop_env
from retronews.config import Paths,load
from retronews.engine import Engine
from retronews.ui import Application
from retronews.theme import css
from gi.repository import Gtk,Gdk,GLib,GdkPixbuf,Graphene

OUT=Path(os.environ.get('RETRONEWS_QA_OUTPUT',Path(__file__).resolve().parents[1]/'docs/qa_completion'))
GLYPHS='ÄÖÜ ÅÉ HAMBURG 0123456789 gjpqy'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rn-layout-') as tmp:
        with patch.dict(os.environ, desktop_env(Path(tmp))):
            paths=Paths();options,_=load(paths);options['auto_sync']=False
            feeds = [
                dict(url='https://example.org/world', name='World Fixture', category='WORLD'),
                dict(url='https://example.org/tech', name='Tech Fixture', category='TECH'),
            ]
            engine=Engine(paths,options,feeds)
            article=dict(id=hashlib.sha256(b'layout').hexdigest(),feed=feeds[0]['url'],title=GLYPHS+' · Lesbarkeit',url='https://example.org/offline',source=GLYPHS,category='WORLD',published=0,summary='Local fixture',text=(GLYPHS+' · Vollständig gespeicherter Absatz.\n\n')*80,mode='extracted',image_url='')
            engine.cache.put(article)
            # Portrait exposes the old Picture minimum-height/natural-size problem.
            portrait=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,False,8,400,800);portrait.fill(0x527965ff)
            engine.cache.put_image(article['id'],portrait.save_to_bufferv('png',[],[])[1])
            app=Application(engine);records=[];errors=[]
            # Layout is applied and checked in separate frame callbacks.
            cases=[(1040,760,'dark',14,'list'),(1040,760,'dark',14,'reader'),(760,600,'light',14,'reader'),(400,700,'light',14,'reader'),(400,700,'dark',18,'list')]
            current=[None];test_palette=[None]
            from retronews.theme import palette
            test_palette[0]=palette()
            palette_patch=patch('retronews.ui.palette',side_effect=lambda:test_palette[0]);palette_patch.start()
            def apply_case():
                if not cases:
                    app.quit();return False
                w,h,theme,font,mode=cases.pop(0);current[0]=(w,h,theme,font,mode)
                if mode=='reader':
                    app.select_category('ALL');app.open_row(app.list.get_row_at_index(0))
                else:app.back()
                colors=dict(background='#10191c' if theme=='dark' else '#fafafa',foreground='#dce7e8' if theme=='dark' else '#111111',accent='#83bdb0' if theme=='dark' else '#315b73',warning='#dfb979',critical='#e58a8a',origin='TEST')
                # Suppress theme polling for the short layout stage; real theme reload
                # is tested independently by smoke_gtk.py.
                test_palette[0]=colors;app.theme=colors
                app.provider.load_from_data((css(colors)+f'\n.retronews * {{ font-size: {font}px; }}').encode())
                app.picture.fit_viewport(w-28,max(1,h-260))
                for category in ('ALL','WORLD','TECH','ALL'):
                    app.select_category(category)
                    assert [n for n,b in app.category_buttons.items() if b.get_active()]==[category]
                    assert app.category_buttons[category].get_label().startswith('● ')
                app.category_buttons['TECH'].grab_focus()
                assert app.category=='ALL' and app.category_buttons['ALL'].get_active()
                if mode=='reader':app.open_row(app.list.get_row_at_index(0))
                app.window.get_child().allocate(w,h,-1,None)
                GLib.timeout_add(100,check_case);return False
            def check_case():
                try:
                    w,h,theme,font,mode=current[0]
                    root=app.window.get_child();root.allocate(w,h,-1,None)
                    # Use the actual reader viewport, rather than an estimated outer height.
                    app.picture.fit_viewport(app.reader_scroll.get_width(),app.reader_scroll.get_height())
                    root.allocate(w,h,-1,None)
                    labels=[]
                    def walk(widget):
                        if isinstance(widget,Gtk.Label) and widget.get_mapped() and widget.has_css_class('text-safe'):
                            ink,logical=widget.get_layout().get_pixel_extents();x,y=widget.get_layout_offsets()
                            # Gtk allocation is CSS content size; Pango offsets are in
                            # those content coordinates. Insets are additional CSS space.
                            assert y+ink.y>=-1,(widget.get_label()[:40],y,ink.y)
                            assert y+ink.y+ink.height<=widget.get_height()+1,(widget.get_label()[:40],widget.get_height(),y,ink.y,ink.height)
                            labels.append(dict(text=widget.get_label()[:55],height=widget.get_height(),ink_top=y+ink.y,ink_bottom=y+ink.y+ink.height,font=widget.get_pango_context().get_font_description().to_string()))
                        child=widget.get_first_child()
                        while child:walk(child);child=child.get_next_sibling()
                    walk(root)
                    assert len(labels)>=5
                    root.allocate(w,h,-1,None)
                    if mode=='reader':
                        assert app.picture.get_height()<=int(app.reader_scroll.get_height()*.4)+1,(app.picture.get_height(),app.reader_scroll.get_height())
                        assert app.picture.get_width()<=640
                        assert app.picture.get_height()>10
                        assert abs(app.picture.get_width()/app.picture.get_height()-.5)<.03
                        assert app.reader_scroll.get_vadjustment().get_upper()>app.reader_scroll.get_height()
                    snapshot=Gtk.Snapshot()
                    background=Gdk.RGBA();background.parse(test_palette[0]['background'])
                    rect=Graphene.Rect();rect.init(0,0,w,h);snapshot.append_color(background,rect)
                    app.window.snapshot_child(root,snapshot);node=snapshot.to_node()
                    path=OUT/f'layout_{w}_{h}_{theme}_{font}_{mode}.png'
                    assert node is not None
                    app.window.get_renderer().render_texture(node,None).save_to_png(str(path))
                    records.append(dict(requested=[w,h],allocated=[root.get_width(),root.get_height()],theme=theme,font=font,mode=mode,labels=labels,image=[app.picture.get_width(),app.picture.get_height()],viewport=[app.reader_scroll.get_width(),app.reader_scroll.get_height()],snapshot=path.name,pass_=True))
                except Exception as exc:
                    import traceback
                    errors.append(traceback.format_exc())
                GLib.idle_add(apply_case);return False
            def start():
                # Keep polling from overwriting explicit test CSS/allocations.
                app.tick=lambda:False
                GLib.idle_add(apply_case);return False
            GLib.timeout_add(700,start)
            try:app.run([])
            finally:
                palette_patch.stop();engine.close()
            result=dict(gtk=f'{Gtk.get_major_version()}.{Gtk.get_minor_version()}',backend=type(Gdk.Display.get_default()).__name__,scale=app.window.get_scale_factor(),cases=records,errors=errors,pass_=not errors and len(records)==5)
            (OUT/'layout.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result['pass_'] else 1

if __name__=='__main__':raise SystemExit(main())
