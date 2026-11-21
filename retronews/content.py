"""Bounded RSS/Atom parsing and conservative semantic HTML extraction."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
import datetime as dt
import email.utils
import hashlib
import re
import xml.etree.ElementTree as ET


def web_url(value, base=''):
    if not value or not value.strip() or len(value) > 2048 or any(ord(c) < 32 for c in value):
        return ''
    try:
        value = urljoin(base, (value or '').strip())
        u = urlsplit(value)
        port = u.port
    except ValueError:
        return ''
    if (len(value) > 2048 or u.scheme not in ('http', 'https') or not u.hostname
            or u.username is not None or u.password is not None or port == 0
            or any(c.isspace() or c in '\\%' for c in u.netloc)):
        return ''
    return urlunsplit((u.scheme, u.netloc, u.path, u.query, ''))


def image_url(value, base='', attrs=None):
    """Conservative candidate filter; downloaded bytes remain authoritative."""
    url = web_url(value, base)
    if not url:
        return ''
    path = urlsplit(url).path.lower()
    if re.search(r'(?:^|[/_.-])(logo|favicon|avatar|tracking|pixel|spacer|icon)(?:[/_.-]|$)', path):
        return ''
    if re.search(r'\.(svg|gif|ico|webp|avif)(?:$)', path):
        return ''
    for key in ('width', 'height'):
        value = (attrs or {}).get(key, '')
        if str(value).isdigit() and int(value) < 64:
            return ''
    return url


def clean_text(text):
    return ''.join(c for c in text if c in '\n\t' or ord(c) >= 32).strip()[:160_000]


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.semantic = []
        self.images = []
        self.meta_images = []
        self.candidates = []
        self.frames = []
        self.blocked = {'script', 'style', 'nav', 'footer', 'header', 'form', 'noscript', 'svg', 'aside'}

    def handle_starttag(self, tag, attrs):
        tag = tag.rsplit(':', 1)[-1]
        a = dict(attrs)
        marker = ' '.join((a.get('class') or '', a.get('id') or '')).lower()
        excluded = bool(re.search(r'(?:^|[ _-])(cookie|consent|newsletter|social|share|related|advertisement|sidebar|navigation)(?:$|[ _-])', marker)) or a.get('aria-hidden')=='true' or 'hidden' in a
        semantic = tag=='article' or a.get('itemprop')=='articleBody' or bool(re.search(r'(?:^|[ _-])(entry-content|post-content|article-body|article-content)(?:$|[ _-])',marker))
        void = tag in ('br','hr','img','meta','link','input','source','wbr','embed','area','base','param','col')
        if not void:
            if len(self.frames) >= 48:
                raise ValueError('HTML nesting exceeds limit')
            parts = [] if semantic else None
            if parts is not None:self.candidates.append(parts)
            self.frames.append((tag, excluded, parts))
        if tag == 'img' and not excluded and not any(f[1] or f[0] in self.blocked for f in self.frames):
            candidate = image_url(a.get('data-src') or a.get('src'), 'https://placeholder.invalid/', a)
            if candidate and len(self.images) < 64:
                self.images.append(a.get('data-src') or a.get('src'))
        if tag == 'meta' and (a.get('property') or a.get('name') or '').lower() in ('og:image', 'og:image:url', 'og:image:secure_url', 'twitter:image', 'twitter:image:src'):
            if len(self.meta_images) < 64:
                self.meta_images.append(a.get('content') or '')
        if tag not in ('br', 'hr', 'img', 'meta', 'link', 'input', 'source', 'wbr', 'embed', 'area', 'base', 'param', 'col'):
            self.stack.append(tag)
        if tag in ('p', 'div', 'br', 'h1', 'h2', 'h3', 'li', 'blockquote', 'section'):
            self.handle_data('\n\n' if tag != 'li' else '\n• ')

    def handle_endtag(self, tag):
        tag = tag.rsplit(':', 1)[-1]
        if any(f[0]==tag for f in self.frames):
            idx=max(i for i,f in enumerate(self.frames) if f[0]==tag)
            self.frames=self.frames[:idx]
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]
        if tag in ('p', 'div', 'h1', 'h2', 'h3', 'li'):
            self.handle_data('\n\n')

    def handle_data(self, data):
        if any(t in self.blocked for t in self.stack) or any(f[1] for f in self.frames):
            return
        self.parts.append(data)
        for _,_,parts in self.frames:
            if parts is not None:parts.append(data)
        if any(t in ('article', 'main') for t in self.stack):
            self.semantic.append(data)

    def text(self, semantic=False):
        return clean_text(re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n',
                                 ''.join(self.semantic if semantic else self.parts)))


def html_text(value):
    parser = TextHTML()
    parser.feed((value or '')[:200_000])
    return parser.text(), parser.meta_images[::-1] + parser.images


def timestamp(value):
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (ValueError, TypeError):
        try:
            parsed = dt.datetime.fromisoformat((value or '').replace('Z', '+00:00'))
        except ValueError:
            return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    try:
        stamp = parsed.timestamp()
        # Leave room for local timezone conversion near minimum/maximum dates.
        return stamp if -62135510400 < stamp < 253402128000 else 0
    except (OverflowError, OSError, ValueError):
        return 0


def local(tag):
    return tag.rsplit('}', 1)[-1].lower()


def child(node, name):
    return next((c for c in node if local(c.tag) == name), None)


def text_of(node, name):
    c = child(node, name)
    if c is None:
        return ''
    # Atom XHTML constructs retain paragraph boundaries and discard active tags.
    if len(c):
        return re.sub(r'(<\/?)(?:ns\d+:)', r'\1', ''.join(ET.tostring(x, encoding='unicode') for x in c))
    return c.text or ''


class FeedTree(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.elements = 0

    def start(self, tag, attrs):
        self.depth += 1
        self.elements += 1
        if self.depth > 64 or self.elements > 50_000:
            raise ValueError('XML structure exceeds limit')
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)

    def doctype(self, name, pubid, system):
        raise ValueError('DTD not permitted')


def feed_root(data):
    if len(data) > 2_000_000 or re.search(rb'<!\s*(DOCTYPE|ENTITY)', data.replace(b'\x00', b''), re.I):
        raise ValueError('XML too large or DTD/entities not permitted')
    return ET.fromstring(data, parser=ET.XMLParser(target=FeedTree()))


def parse_feed(data, source, limit=20):
    root = feed_root(data)
    if local(root.tag) not in ('rss', 'feed', 'rdf'):
        raise ValueError('Not RSS/Atom')
    nodes = [n for n in root.iter() if local(n.tag) in ('item', 'entry')]
    result = []
    seen = set()
    for node in nodes:
        if len(result) >= int(limit):
            break
        url = text_of(node, 'link')
        if local(node.tag) == 'entry':
            url = next((c.get('href', '') for c in node if local(c.tag) == 'link'
                        and c.get('rel', 'alternate') == 'alternate'), '')
        url = web_url(url, source['url'])
        if not url or url in seen:
            continue
        seen.add(url)
        title = html_text(text_of(node, 'title'))[0][:500] or '(Ohne Titel)'
        summary, pics = html_text(text_of(node, 'description') or text_of(node, 'summary'))
        content, cpics = html_text(text_of(node, 'encoded') or text_of(node, 'content'))
        for c in node.iter():
            if local(c.tag) in ('thumbnail', 'content', 'enclosure', 'link'):
                candidate = c.get('url') or c.get('href')
                if candidate and (local(c.tag) == 'thumbnail' or c.get('type', '').startswith('image/') or (local(c.tag) == 'content' and not c.get('type') and c.get('medium', 'image') == 'image' and c.tag.startswith('{http://search.yahoo.com/mrss/}'))):
                    if image_url(candidate, url, c.attrib):pics.append(candidate)
        result.append(dict(id=hashlib.sha256((source['url']+'\0'+url).encode()).hexdigest(),
                           url=url, title=title, summary=summary, text=content or summary,
                           mode='feed_content' if content and len(content) > len(summary) + 150 else 'summary',
                           published=timestamp(text_of(node, 'pubdate') or text_of(node, 'published') or text_of(node, 'updated')),
                           image_url=next((image_url(u, url) for u in pics+cpics if image_url(u, url)), ''),
                           source=source['name'], category=source['category'], feed=source['url']))
    return result


def extract_article(data, base, summary=''):
    parser = TextHTML()
    parser.feed(data.decode('utf-8', errors='replace'))
    candidates=[clean_text(re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+','\n\n',''.join(parts))) for parts in parser.candidates]
    text = max(candidates,key=len) if candidates else parser.text(semantic=True)
    paywall = re.search(r'(subscribe to (?:continue|read)|sign in to read|abonnieren.*weiterlesen|enable javascript)', text, re.I)
    raw_prompt = re.search(r'(subscribe to (?:continue|read)|sign in to read|abonnieren.{0,40}weiterlesen)', parser.text(), re.I)
    if len(text) < max(500, len(summary) * 1.15) or paywall or raw_prompt:
        return '', ''
    image = next((image_url(x, base) for x in parser.meta_images[::-1] + parser.images if image_url(x, base)), '')
    return text, image
