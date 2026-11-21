"""No cookies, authentication or paywall workarounds; bounded cancellable requests."""
from functools import partial
import http.client
import ipaddress
import socket
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urlsplit
from .content import web_url

USER_AGENT = 'RetroNews/0.1 (personal offline RSS reader)'


class Cancelled(Exception):
    pass


class Budget:
    def __init__(self, amount):
        self.remaining = int(amount)
        self.lock = threading.Lock()

    def take(self, amount):
        with self.lock:
            if amount > self.remaining:
                raise ValueError('Sync bandwidth budget reached')
            self.remaining -= amount


def origin(url):
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80)


def private_literal(url):
    host = urlsplit(url).hostname
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    return origin(url) if address.is_private or address.is_loopback else None


def connect_public(address, timeout, source_address=None):
    """Connect to the validated sockaddr without resolving the name again."""
    host, port = address
    last_error = None
    deadline = time.monotonic() + timeout
    for family, kind, proto, _, target in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        ip = ipaddress.ip_address(target[0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Connection deadline exceeded')
        try:
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(remaining)
                if source_address:
                    sock.bind(source_address)
                sock.connect(target)
                sock.settimeout(timeout)
            except BaseException:
                sock.close()
                raise
            return sock
        except OSError as error:
            last_error = error
    if last_error:
        raise last_error
    raise ValueError('Automatic requests require a public address')


def guarded_connection(connection_type, host, **kwargs):
    connection = connection_type(host, **kwargs)
    connection._create_connection = connect_public
    return connection


guarded_http = partial(guarded_connection, http.client.HTTPConnection)
guarded_https = partial(guarded_connection, http.client.HTTPSConnection)


class HTTP:
    def __init__(self, stop=None, timeout=5):
        self.stop = stop or threading.Event()
        self.timeout = timeout
        self.robots = {}
        self.host_locks = {}
        self.lock = threading.RLock()
        self.last = {}
        self.delays = {}

    def get(self, url, maximum, budget, headers=None, robots=True, *, subscription=None):
        url = web_url(url)
        if not url:
            raise ValueError('Only unauthenticated HTTP(S) URLs allowed')
        trusted = private_literal(subscription or url)
        if robots and not self.allowed(url, budget, trusted):
            raise ValueError('robots.txt does not permit this request or is unavailable')
        host = urlsplit(url).netloc
        with self.lock:
            guard = self.host_locks.setdefault(host, threading.RLock())
        with guard:
            wait = max(0, self.last.get(host, 0) + self.delays.get(urlsplit(url).scheme+'://'+host, 1.0) - time.monotonic())
            if self.stop.wait(wait):
                raise Cancelled()
            self.last[host] = time.monotonic()
            return self._get(url, maximum, budget, headers, trusted=trusted)

    def _get(self, url, maximum, budget, headers=None, check_redirect=True, trusted=None):
        if self.stop.is_set():
            raise Cancelled()
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT,
                                                       'Accept-Encoding': 'identity'} | (headers or {}))
        start = time.monotonic()
        try:
            owner = self

            class SafeRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                    if owner.stop.is_set():
                        raise Cancelled()
                    if time.monotonic() - start > owner.timeout * 2:
                        raise TimeoutError('Redirect deadline exceeded')
                    newurl = web_url(newurl)
                    if not newurl:
                        raise ValueError('Redirect disallowed by URL policy')
                    same_origin = origin(req.full_url) == origin(newurl)
                    if origin(req.full_url)[0] == 'https' and origin(newurl)[0] != 'https':
                        raise ValueError('HTTPS downgrade refused')
                    if not check_redirect and not same_origin:
                        raise ValueError('Cross-origin robots redirect refused')
                    if check_redirect and not owner.allowed(newurl, budget, trusted):
                        raise ValueError('Redirect disallowed by robots policy')
                    redirect = super().redirect_request(req, fp, code, msg, hdrs, newurl)
                    if redirect and not same_origin:
                        redirect.remove_header('If-none-match')
                        redirect.remove_header('If-modified-since')
                    return redirect

            class GuardedHTTP(urllib.request.HTTPHandler):
                def http_open(self, req):
                    if origin(req.full_url) == trusted:
                        return super().http_open(req)
                    return self.do_open(guarded_http, req)

            class GuardedHTTPS(urllib.request.HTTPSHandler):
                def https_open(self, req):
                    if origin(req.full_url) == trusted:
                        return super().https_open(req)
                    return self.do_open(guarded_https, req, context=self._context)

            response = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), SafeRedirect(), GuardedHTTP(), GuardedHTTPS()
            ).open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            if error.code == 304:
                error.close()
                return 304, {'ETag': error.headers.get('ETag',''), 'Last-Modified': error.headers.get('Last-Modified','')}, b''
            error.close()
            raise
        with response:
            if not web_url(response.url):
                raise ValueError('Unsafe redirect target')
            if response.headers.get('Content-Encoding', 'identity') not in ('identity', ''):
                raise ValueError('Compressed HTTP transfer not accepted in PoC')
            if response.status != 200:
                raise ValueError('Unexpected HTTP response status')
            length = response.headers.get('Content-Length')
            length = int(length) if length is not None else None
            if length is not None and not 0 <= length <= maximum:
                raise ValueError('Response exceeds size limit')
            chunks, total = [], 0
            while True:
                if self.stop.is_set():
                    raise Cancelled()
                if time.monotonic() - start > self.timeout * 2:
                    raise TimeoutError('Response deadline exceeded')
                chunk = response.read1(min(16384, maximum - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                budget.take(len(chunk))
                if total > maximum:
                    raise ValueError('Response exceeds size limit')
                chunks.append(chunk)
            if length is not None and total != length:
                raise ValueError('Incomplete HTTP response')
            return response.status, {'ETag': response.headers.get('ETag',''), 'Last-Modified': response.headers.get('Last-Modified',''), 'Content-Type': response.headers.get('Content-Type','')}, b''.join(chunks)

    def allowed(self, url, budget, trusted=None):
        u = urlsplit(url)
        origin = u.scheme + '://' + u.netloc
        # Serialize robots discovery; origins are cached for at most one hour.
        with self.lock:
            key = (origin, trusted)
            old = self.robots.get(key)
            if old and time.monotonic() - old[0] < 3600:
                rule = old[1]
                return rule is True or (rule is not False and rule.can_fetch(USER_AGENT, url))
            try:
                code, headers, body = self._get(origin + '/robots.txt', 256_000, budget, check_redirect=False, trusted=trusted)
                rule = urllib.robotparser.RobotFileParser()
                if b'<html' in body[:2048].lower():raise ValueError('Invalid robots document')
                rule.parse(body.decode(errors='replace').splitlines())
                delay=rule.crawl_delay(USER_AGENT) or rule.crawl_delay('*') or 1
                rate=rule.request_rate(USER_AGENT) or rule.request_rate('*')
                self.delays[origin]=max(1,delay,(rate.seconds/rate.requests) if rate and rate.requests else 1)
            except urllib.error.HTTPError as e:
                rule = e.code in (404, 410)
            except (OSError, ValueError, http.client.HTTPException):
                rule = False
            self.robots[key] = (time.monotonic(), rule)
            return rule is True or (rule is not False and rule.can_fetch(USER_AGENT, url))
