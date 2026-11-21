"""Geometry/contrast contracts alongside real allocation smokes."""
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,Gdk,GdkPixbuf
from retronews.ui import ReaderPicture,configure_text_rendering
from retronews.theme import contrast,readable,css

class LayoutTests(unittest.TestCase):
    def test_portrait_and_landscape_fit_reader_viewport(self):
        if Gdk.Display.get_default() is None:
            self.skipTest('GTK display required for widget contract')
        for width,height in ((400,800),(1600,600)):
            picture=ReaderPicture()
            picture.set_pixbuf(GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,False,8,width,height))
            for viewport in ((1000,600),(360,300)):
                picture.fit_viewport(*viewport)
                _,w,_,_=picture.measure(Gtk.Orientation.HORIZONTAL,-1)
                minimum,h,_,_=picture.measure(Gtk.Orientation.VERTICAL,w)
                self.assertGreater(h,1)
                self.assertEqual(minimum,h) # A scrolling reader must not squash the image.
                self.assertLessEqual(h,viewport[1]*.4)
                self.assertLessEqual(w,min(640,viewport[0]))
                self.assertAlmostEqual(w/h,width/height,delta=.05)
    def test_font_rasterization_respects_hint_metrics(self):
        import cairo
        from gi.repository import PangoCairo
        if Gdk.Display.get_default() is None:
            self.skipTest('GTK display required for font rendering')
        settings=Gtk.Settings.get_default()
        if not settings.find_property('gtk-font-rendering'):
            self.assertFalse(configure_text_rendering())
            return
        rendering=settings.get_property('gtk-font-rendering')
        hinting=settings.get_property('gtk-hint-font-metrics')
        try:
            settings.set_property('gtk-hint-font-metrics',True)
            configure_text_rendering()
            context=Gtk.Label(label='ÄÖÜ gjpqy').get_pango_context()
            options=PangoCairo.context_get_font_options(context)
            self.assertEqual(options.get_hint_metrics(),cairo.HINT_METRICS_ON)
        finally:
            settings.set_property('gtk-font-rendering',rendering)
            settings.set_property('gtk-hint-font-metrics',hinting)

    def test_active_category_text_contrast_dark_and_light(self):
        for background,accent in (('#10191c','#83bdb0'),('#fafafa','#315b73'),('#111111','#121212')):
            text=readable(background,accent)
            self.assertGreaterEqual(contrast(text,accent),7)
            rendered=css(dict(background=background,foreground='#999999',accent=accent,warning='#dddd00'))
            self.assertIn('category-active label',rendered)
            self.assertIn(text,rendered)

if __name__=='__main__':unittest.main()
