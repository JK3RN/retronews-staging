"""Bounded image validation and a small local RetroView-inspired duotone."""
import struct


def dimensions(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24:
        return struct.unpack('>II', data[16:24])
    if data.startswith(b'\xff\xd8'):
        pos = 2
        while pos + 4 <= len(data):
            if data[pos] != 255:
                break
            code = data[pos+1]
            if code == 255:
                pos += 1
                continue
            if code in (0xd8, 0xd9) or 0xd0 <= code <= 0xd7:
                pos += 2
                continue
            length = int.from_bytes(data[pos+2:pos+4], 'big')
            if length < 2:
                break
            if code in (0xc0, 0xc1, 0xc2) and pos+9 <= len(data):
                h, w = struct.unpack('>HH', data[pos+5:pos+9])
                return w, h
            pos += 2 + length
    raise ValueError('Only bounded PNG/JPEG images supported')


def validate(data):
    w, h = dimensions(data)
    if min(w, h) < 3 or w * h > 16_000_000 or max(w, h) > 8192:
        raise ValueError('Image exceeds dimension limit')
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
    loader = GdkPixbuf.PixbufLoader.new()
    loader.connect('size-prepared', lambda l, width, height: l.set_size(min(width, 640), max(1, int(height * min(1, 640/width)))))
    loader.write(data)
    loader.close()
    if loader.get_pixbuf() is None:
        raise ValueError('Invalid image')
    return True


def pixbuf(path, palette, original=False):
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf, GLib
    source = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), 640, 400, True)
    if original:
        return source
    data = source.get_pixels();stride=source.get_rowstride();channels=source.get_n_channels()
    width, height = source.get_width(), source.get_height()
    def rgb(s):return [int(s[i:i+2], 16) for i in (1,3,5)]
    # Capped endpoints prevent white image backgrounds, even in a light theme.
    bg=[min(110, c) for c in rgb(palette['background'])]
    fg=[min(190, c) for c in rgb(palette['accent'])]
    output=bytearray(width*height*3)
    for y in range(height):
        for x in range(width):
            at=y*stride+x*channels;r,g,b=data[at:at+3]
            lum=(r*.2126+g*.7152+b*.0722)/255
            alpha=data[at+3]/255 if channels==4 else 1
            for c in range(3):output[(y*width+x)*3+c]=round(bg[c]+(fg[c]-bg[c])*lum*alpha)
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(output)),GdkPixbuf.Colorspace.RGB,False,8,width,height,width*3)
