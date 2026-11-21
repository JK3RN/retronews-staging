"""Read-only Omarchy theme integration; no theme or desktop configuration changes."""
from pathlib import Path
import os
import re
import tomllib


def palette():
    colors=dict(background='#10191c', foreground='#dce7e8', accent='#83bdb0', warning='#dfb979', critical='#e58a8a', origin='DARK FALLBACK')
    for base in (Path(os.getenv('XDG_STATE_HOME', Path.home()/'.local/state')),
                 Path(os.getenv('XDG_CONFIG_HOME', Path.home()/'.config'))):
        path=base/'omarchy/current/theme/colors.toml'
        try:
            data=tomllib.loads(path.read_text())
            for key,alt in [('background','color0'),('foreground','color7'),('accent','color4'),('warning','color3'),('critical','color1')]:
                value=data.get(key,data.get(alt,colors[key]))
                if isinstance(value,str) and re.fullmatch('#[0-9a-fA-F]{6}',value):colors[key]=value
            colors['foreground']=readable(colors['foreground'],colors['background'])
            colors['accent']=readable(colors['accent'],colors['background'],4.5)
            colors['origin']='SYSTEMTHEME'
            return colors
        except (OSError, ValueError):pass
    return colors


def css(c):
    return f'''
window.retronews {{ background: {c['background']}; color: {c['foreground']}; }}
.retronews * {{ font-family: monospace; font-size: 14px; color: {c['foreground']}; }}
.retronews .brand {{ font-size: 22px; font-weight: bold; color: {c['accent']}; }}
.retronews .dim {{ opacity: .82; font-size: 12px; }}
.retronews .title {{ font-size: 20px; font-weight: bold; }}
.retronews .mode {{ color: {c['accent']}; }}
.retronews .error {{ color: {c['warning']}; }}
.retronews button {{ background: transparent; background-image: none; border: 1px solid alpha({c['foreground']},.3); border-radius: 0; padding: 6px 10px; box-shadow: none; }}
.retronews button.category-active {{ background: {c['accent']}; border-color: {c['accent']}; font-weight: bold; }}
.retronews button.category-active label {{ color: {readable(c['background'],c['accent'])}; }}
.retronews button.category-active:hover {{ background: {c['accent']}; }}
.retronews button:focus-visible {{ outline: 1px dashed {c['foreground']}; outline-offset: -3px; }}
.retronews button:hover {{ background: alpha({c['accent']},.12); }}
.retronews row {{ padding: 10px 8px; border-left: 2px solid transparent; }}
.retronews row:selected {{ background: alpha({c['accent']},.13); border-left-color: {c['accent']}; }}
.retronews row:focus-visible {{ outline: 1px solid {c['accent']}; outline-offset: -1px; }}
.retronews list, .retronews textview, .retronews textview text {{ background: transparent; }}
.retronews textview, .retronews .body {{ font-size: 16px; }}
.retronews separator {{ background: alpha({c['foreground']},.25); min-height: 1px; }}
'''


def contrast(a,b):
    def luminance(s):
        channels=[int(s[i:i+2],16)/255 for i in (1,3,5)]
        channels=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in channels]
        return sum(v*w for v,w in zip(channels,(.2126,.7152,.0722)))
    lo,hi=sorted((luminance(a),luminance(b)))
    return (hi+.05)/(lo+.05)


def readable(color,background,minimum=7):
    endpoint=max(('#000000','#ffffff'),key=lambda v:contrast(v,background))
    for step in range(21):
        c='#'+''.join(f'{round(int(color[i:i+2],16)*(1-step/20)+int(endpoint[i:i+2],16)*step/20):02x}' for i in (1,3,5))
        if contrast(c,background)>=minimum:return c
    return endpoint
