"""Native GTK instrument surface; no rendering or networking dependency between layers."""
import datetime as dt
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk, Gdk, Gio, GLib, Pango
from .theme import palette, css
from .images import pixbuf
from .language import MODES, filter_rows

HELP='''RETRO NEWS · FIELD MANUAL

↑ / ↓  oder J / K    Schlagzeile auswählen; im Artikel scrollen
Enter                Artikel aus dem lokalen Cache lesen
Esc                  Zur Schlagzeilenliste / Hilfe schließen
R                    Aktualisieren (asynchron; alte Daten bleiben lesbar)
Q                    Sauber beenden, Cache behalten (Standard)
H / ?                Diese Hilfe
Tab                  Nächste Kategorie
L                    Sprache MIXED / DE / EN (in der Liste)
I                    Bild zwischen Themefilter und Original umschalten

ARTIKELTEXT = automatisch aus semantischen HTML-Bereichen extrahiert;
Vollständigkeit bleibt ungeprüft. FEED-TEXT kann ebenfalls gekürzt sein.
ZUSAMMENFASSUNG = nur die verfügbare Feed-Zusammenfassung.
Quelle und Original-URL bleiben immer sichtbar.

ONLINE / TEILWEISE ONLINE / OFFLINE beziehen sich auf den letzten
Feed-Abruf, nicht auf eine pauschale Internet-Garantie.
R respektiert eine Mindestpause von 60 Sekunden nach erfolgreichem Abruf;
fehlgeschlagene Quellen können manuell sofort erneut versucht werden.

Auto-Sync AN · Persistenz AN · Offline-Bilder AN
Cache 350 MB (dezimal) · Retention 24 h
Alte Daten bleiben bei Ausfällen erhalten, solange das Limit reicht.
Aktiv gelesene Artikel sind vor Eviction geschützt.

CLI: retronews status | refresh | stop | stop --clean | clean
stop: AUS, Cache bleibt. stop --clean: AUS, danach Cache weg.
clean: nur im gestoppten Zustand; Quellen/Einstellungen bleiben.
Einstellungen und Quellen: ~/.config/retronews/ (XDG_CONFIG_HOME).
'''


def stamp(value):
    if value:
        try:
            return dt.datetime.fromtimestamp(value).strftime('%d.%m. %H:%M')
        except (ValueError, OSError, OverflowError):
            pass
    return 'ZEIT UNBEKANNT'


def label(text='',classes=(),wrap=False):
    item=Gtk.Label(label=text,xalign=0,selectable=False,wrap=wrap)
    item.add_css_class('text-safe')
    if wrap:item.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for name in classes:item.add_css_class(name)
    return item


def configure_text_rendering():
    """Honor desktop font hinting inside this process (GTK >= 4.16).

    Automatic HiDPI rendering changed the raster bounds despite correct Pango
    allocations. MANUAL follows the existing low-level font settings; it does
    not change the font, theme, renderer backend or any persistent setting.
    """
    settings=Gtk.Settings.get_default()
    if settings and settings.find_property('gtk-font-rendering'):
        settings.set_property('gtk-font-rendering',Gtk.FontRendering.MANUAL)
        return True
    return False


class ReaderPicture(Gtk.Picture):
    """Constrain the widget itself, not just its decoded bitmap's natural size."""
    def __init__(self):
        super().__init__()
        self.bounds=(640,180)
        self.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.set_can_shrink(True)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)

    def fit_viewport(self,width,height):
        bounds=(max(1,min(640,width)),max(1,min(300,int(height*.38))))
        if bounds!=self.bounds:
            self.bounds=bounds
            self.queue_resize()

    def do_measure(self,orientation,for_size):
        width,height=self.bounds
        paintable=self.get_paintable()
        if paintable:
            iw,ih=paintable.get_intrinsic_width(),paintable.get_intrinsic_height()
            if iw>0 and ih>0:
                scale=min(1,width/iw,height/ih)
                width,height=max(1,int(iw*scale)),max(1,int(ih*scale))
        natural=width if orientation==Gtk.Orientation.HORIZONTAL else height
        return (natural if orientation==Gtk.Orientation.VERTICAL else 1,natural,-1,-1)


class Application(Gtk.Application):
    def __init__(self,engine):
        super().__init__(application_id='local.retronews.Reader',flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.engine=engine;self.rows=[];self.category='ALL';self.mode='list';self.previous_mode='list'
        self.language='MIXED';self.positions={}
        self.article=None;self.original=False;self.last_revision=-1;self.theme=None;self.help_visible=False

    def do_activate(self):
        configure_text_rendering()
        self.window=Gtk.ApplicationWindow(application=self,title='RETRO NEWS // Offline Reader')
        self.window.add_css_class('retronews');self.window.set_default_size(1040,760)
        root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
        for side in ['top','bottom','start','end']:getattr(root,'set_margin_'+side)(14)
        self.window.set_child(root)
        top=Gtk.Box(spacing=14);top.append(label('RETRO // NEWS',('brand',)));top.append(label('LOCAL FIRST  /  NEWS RECEIVER',('dim',),True))
        root.append(top)
        actions=Gtk.Box(spacing=8)
        for name,callback in [('R  SYNC',lambda *_:self.engine.request_refresh()),('H  HELP',lambda *_:self.show_help()),('ESC  BACK',lambda *_:self.back()),('Q  STOP',lambda *_:self.quit())]:
            b=Gtk.Button(label=name);b.connect('clicked',callback);actions.append(b)
        action_scroll=Gtk.ScrolledWindow();action_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,Gtk.PolicyType.NEVER)
        action_scroll.set_child(actions);root.append(action_scroll)
        self.categories=['ALL']+list(dict.fromkeys(f['category'] for f in self.engine.feeds))
        catbox=Gtk.Box(spacing=6);self.category_buttons={}
        for name in self.categories:
            b=Gtk.ToggleButton(label='  '+name);b.connect('clicked',lambda _,n=name:self.select_category(n));catbox.append(b)
            self.category_buttons[name]=b
        self.update_categories()
        catscroll=Gtk.ScrolledWindow();catscroll.set_policy(Gtk.PolicyType.AUTOMATIC,Gtk.PolicyType.NEVER);catscroll.set_child(catbox);root.append(catscroll)
        root.append(Gtk.Separator())
        self.stack=Gtk.Stack();self.stack.set_vexpand(True);root.append(self.stack)
        self.list=Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.connect('row-activated',lambda _,row:self.open_row(row))
        self.list_scroll=Gtk.ScrolledWindow();self.list_scroll.set_child(self.list)
        self.stack.add_named(self.list_scroll,'list')
        self.reader=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
        self.reader_title=label('',('title',),True);self.reader.append(self.reader_title)
        self.metadata=label('',('dim',),True);self.metadata.set_selectable(True);self.reader.append(self.metadata)
        self.mode_label=label('',('mode',),True);self.reader.append(self.mode_label)
        self.picture=ReaderPicture();self.reader.append(self.picture)
        self.text=label('',wrap=True);self.text.set_yalign(0);self.text.set_selectable(True)
        self.text.add_css_class('body');self.text.set_margin_bottom(24)
        self.reader.append(self.text)
        self.reader_scroll=Gtk.ScrolledWindow();self.reader_scroll.set_child(self.reader);self.stack.add_named(self.reader_scroll,'reader')
        helpview=Gtk.TextView(editable=False,cursor_visible=False,wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.helpview=helpview
        helpview.get_buffer().set_text(HELP)
        helpscroll=Gtk.ScrolledWindow();helpscroll.set_child(helpview);self.stack.add_named(helpscroll,'help');self.help_scroll=helpscroll
        root.append(Gtk.Separator())
        self.status_label=label('',('dim',),True);root.append(self.status_label)
        self.hint=label('J/K  SELECT   ENTER  READ   L  LANGUAGE   R  SYNC   H  HELP   Q  STOP',('mode',),True);root.append(self.hint)
        keys=Gtk.EventControllerKey();keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE);keys.connect('key-pressed',self.key);self.window.add_controller(keys)
        self.provider=Gtk.CssProvider();Gtk.StyleContext.add_provider_for_display(self.window.get_display(),self.provider,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window.connect('close-request',lambda *_:False)
        self.tick();self.window.present();self.list.grab_focus()
        GLib.timeout_add(500,self.tick)
        # First frame + cached content are ready before any HTTP starts.
        GLib.idle_add(self.start_sync)

    def start_sync(self):
        self.engine.start();return False

    def tick(self):
        current=palette()
        if self.theme!=current:
            self.theme=current;self.provider.load_from_data(css(current).encode())
            if self.article:self.update_picture()
        if self.last_revision!=self.engine.revision:
            self.populate();self.last_revision=self.engine.revision
        if self.reader_scroll.get_height()>0:
            self.picture.fit_viewport(self.reader_scroll.get_width(),self.reader_scroll.get_height())
        status=self.engine.status()
        self.status_label.set_label(f"SPRACHE {self.language}  ·  {'SYNC LÄUFT' if status['syncing'] else status['connection']}  ·  LETZTER ERFOLG {stamp(status['last_success'])}  ·  {status['articles']} ARTIKEL / {status['extracted_articles']} ARTIKELTEXTE / {status['feed_content_articles']} FEED-TEXTE  ·  {status['bytes']/1_000_000:.2f}/{status['limit_bytes']/1_000_000:g} MB  ·  {self.theme['origin']}")
        if self.mode=='help':
            buffer=self.helpview.get_buffer();text=self.help_text(status)
            if buffer.get_text(buffer.get_start_iter(),buffer.get_end_iter(),True)!=text:
                adjustment=self.help_scroll.get_vadjustment();position=adjustment.get_value()
                buffer.set_text(text)
                def restore_help_scroll():
                    adjustment.set_value(max(0,min(position,adjustment.get_upper()-adjustment.get_page_size())))
                    return False
                GLib.idle_add(restore_help_scroll)
        return True

    def help_text(self,status=None):
        status=status or self.engine.status()
        infos={f['url']:f for f in status['feeds']}
        lines=[]
        for source in self.engine.feeds:
            state=infos.get(source['url'],{})
            result=state.get('status','NOCH NICHT ABGERUFEN') if source.get('enabled',True) else 'DEAKTIVIERT / SETUP UNBESTÄTIGT'
            lines.append(f"{source['category']} / {source.get('language','unknown').upper()} / {source['name']} — {result} — {stamp(state.get('last_success'))}\n{source['url']}")
        return HELP+'\nQUELLEN / LETZTER ERFOLG\n\n'+'\n\n'.join(lines)

    def populate(self):
        selected=self.list.get_selected_row();selected_id=getattr(selected,'article_id',None)
        self.rows=filter_rows(self.engine.cache.rows(),self.engine.feeds,self.language,self.category)
        row=self.list.get_first_child()
        while row:
            nxt=row.get_next_sibling();self.list.remove(row);row=nxt
        select=None
        for article in self.rows:
            box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3)
            meta=f"{'LU' if article['seen'] else 'NEU'}  {article['category']}  /  {article['source']}  /  {stamp(article['published'])}"
            metadata=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=6)
            if self.engine.cache.image_available(article['id']):
                indicator=label('▣',('dim',))
                indicator.set_tooltip_text('Bild offline verfügbar')
                metadata.append(indicator)
            details=label(meta,('dim',));details.set_hexpand(True)
            details.set_ellipsize(Pango.EllipsizeMode.END);details.set_width_chars(1)
            metadata.append(details)
            box.append(metadata);box.append(label(article['title'],(),True))
            item=Gtk.ListBoxRow();item.article_id=article['id'];item.set_child(box);self.list.append(item)
            if article['id']==selected_id:select=item
        if not self.rows:
            empty=Gtk.ListBoxRow();empty.set_selectable(False);empty.set_activatable(False)
            empty.set_child(label('KEINE TREFFER · L SPRACHE / TAB KATEGORIE / R SYNC / H HILFE',('dim',),True));self.list.append(empty)
        else:self.list.select_row(select or self.list.get_row_at_index(0))

    def cycle_language(self):
        if self.mode!='list':return
        selected=self.list.get_selected_row()
        self.positions[(self.category,self.language)]=(getattr(selected,'article_id',None),self.list_scroll.get_vadjustment().get_value())
        self.language=MODES[(MODES.index(self.language)+1)%len(MODES)]
        self.populate()
        ident,position=self.positions.get((self.category,self.language),(None,0))
        for i,row in enumerate(self.rows):
            if row['id']==ident:self.list.select_row(self.list.get_row_at_index(i))
        def restore():
            self.list_scroll.get_vadjustment().set_value(position)
            return False
        GLib.idle_add(restore)
        self.tick()

    def update_categories(self):
        for name,button in self.category_buttons.items():
            active=name==self.category
            button.set_label(('● ' if active else '  ')+name)
            if active:button.add_css_class('category-active')
            else:button.remove_css_class('category-active')
            button.set_active(active)

    def select_category(self,name):
        self.category=name
        self.update_categories()
        if self.mode=='help':self.mode=self.previous_mode
        self.back();self.populate()

    def open_row(self,row):
        ident=getattr(row,'article_id',None)
        if not ident:return
        self.engine.cache.set_active(ident)
        article=self.engine.cache.read(ident,mark=True)
        if not article:self.engine.cache.set_active(None);return
        self.article=article;self.original=False;self.mode='reader';self.stack.set_visible_child_name('reader')
        self.reader_title.set_label(article['title'])
        self.metadata.set_label(f"{article['category']} / {article['source']} / {stamp(article['published'])}\n{article['url']}")
        self.mode_label.set_label({'summary':'ZUSAMMENFASSUNG · kein Volltext verfügbar',
                                  'feed_content':'FEED-TEXT · Vollständigkeit ungeprüft',
                                  'extracted':'ARTIKELTEXT · automatisch extrahiert, Vollständigkeit ungeprüft'}[article['mode']])
        self.text.set_label(article['text'] or 'Diese Quelle liefert keinen lesbaren Text. Original-URL siehe oben.')
        self.update_picture();self.reader_scroll.get_vadjustment().set_value(0)
        self.last_revision=-1

    def update_picture(self):
        path=self.engine.cache.file(self.article['id'],'.img')
        if not self.engine.cache.image_available(self.article['id']):
            self.picture.set_visible(False)
            return
        try:
            self.picture.set_pixbuf(pixbuf(path,self.theme or palette(),self.original));self.picture.set_visible(True)
        except Exception:self.picture.set_visible(False)

    def back(self):
        if self.mode=='help':
            self.mode=self.previous_mode;self.stack.set_visible_child_name(self.mode);return
        self.mode='list';self.stack.set_visible_child_name('list');self.article=None
        self.engine.cache.set_active(None);self.populate();self.list.grab_focus()

    def show_help(self):
        if self.mode!='help':self.previous_mode=self.mode
        self.mode='help';self.helpview.get_buffer().set_text(self.help_text());self.stack.set_visible_child_name('help')

    def key(self,controller,keyval,keycode,state):
        if state & (Gdk.ModifierType.CONTROL_MASK|Gdk.ModifierType.ALT_MASK):return False
        name=Gdk.keyval_name(keyval)
        if name in ('q','Q'):self.quit()
        elif name in ('r','R'):self.engine.request_refresh()
        elif name in ('h','H','question'):self.show_help()
        elif name in ('l','L') and self.mode=='list':self.cycle_language()
        elif name=='Escape':self.back()
        elif name=='Tab':self.select_category(self.categories[(self.categories.index(self.category)+1)%len(self.categories)])
        elif name in ('i','I') and self.mode=='reader':self.original=not self.original;self.update_picture()
        elif name in ('Return','KP_Enter') and self.mode=='list':
            row=self.list.get_selected_row()
            if row:self.open_row(row)
        elif name in ('j','k','J','K','Up','Down'):
            step=-1 if name in ('k','K','Up') else 1
            if self.mode=='list' and self.rows:
                row=self.list.get_selected_row();idx=row.get_index() if row else 0
                self.list.select_row(self.list.get_row_at_index(max(0,min(len(self.rows)-1,idx+step))))
                self.list.get_selected_row().grab_focus()
            elif self.mode in ('reader','help'):
                adj=(self.reader_scroll if self.mode=='reader' else self.help_scroll).get_vadjustment();adj.set_value(max(0,min(adj.get_upper()-adj.get_page_size(),adj.get_value()+step*80)))
        else:return False
        return True
