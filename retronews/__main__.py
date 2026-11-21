import argparse
import concurrent.futures
import json
import signal
import sqlite3
import sys
import time
import urllib.error
from pathlib import Path
from .config import Paths, load
from .cache import Cache
from .control import ownership, request, Server
from .engine import Engine
from .network import HTTP, Budget, private_literal
from .content import feed_root, parse_feed


def validate_sources(feeds, report=None):
    http=HTTP(timeout=5);budget=Budget(64_000_000)
    def check(feed):
        result={k:feed.get(k) for k in ('name','category','url','language','language_basis','enabled','prefetch','setup_status')}
        result['configuration_reason']=feed.get('setup_status','Enabled editorial source' if feed.get('enabled',True) else 'User disabled')
        try:
            code,h,data=http.get(feed['url'],2_000_000,budget)
            result['http_status']=code
            articles=parse_feed(data,feed,50)
            root=feed_root(data)
            declared=root.get('{http://www.w3.org/XML/1998/namespace}lang') or next((n.text for n in root.iter() if n.tag.rsplit('}',1)[-1]=='language'),'') or ''
            result['declared_language']=declared
            result['language_matches_declared']=(declared.lower().split('-')[0].split('_')[0]==feed.get('language')) if declared and feed.get('language')!='unknown' else None
            result.update(status='PASS' if articles else 'EMPTY',items=len(articles),http_status=code, newest=max((a['published'] for a in articles),default=0), duplicate_urls=len(articles)-len({a['url'] for a in articles}))
            result['fresh_within_7_days']=result['newest']>time.time()-7*86400
        except Exception as error:
            result.update(status='FAIL',error=type(error).__name__)
            if isinstance(error,urllib.error.HTTPError):result['http_status']=error.code
            # Only locally authored policy errors; no remote URLs/query values in reports.
            reasons={'robots.txt does not permit this request or is unavailable',
                     'Response exceeds size limit','Compressed HTTP transfer not accepted in PoC',
                     'Not RSS/Atom','XML too large or DTD/entities not permitted'}
            result['reason']=str(error) if str(error) in reasons else ('HTTP '+str(error.code) if isinstance(error,urllib.error.HTTPError) else 'Network/parse failure')
        from urllib.parse import urlsplit
        u=urlsplit(feed['url']);rule=http.robots.get((u.scheme+'://'+u.netloc, private_literal(feed['url'])),(0,False))[1]
        result['robots_policy']='allowed' if rule is True or (rule is not False and rule.can_fetch('RetroNews/0.1',feed['url'])) else 'denied_or_unavailable'
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(check,feeds))
    result={'checked_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'robots_checked':True,
            'scope':'Feed HTTP/XML validity only, no guarantee of future availability or full article access',
            'sources':results}
    if report:Path(report).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(prog='retronews')
    parser.add_argument('command',choices=['run','status','refresh','stop','clean','validate-feeds'],nargs='?',default='run')
    parser.add_argument('--clean',action='store_true',help='with stop: purge only after full process/resource shutdown')
    parser.add_argument('--headless',action='store_true',help='run the same engine without GTK (explicit test/automation mode)')
    parser.add_argument('--report',type=Path,help='validate-feeds JSON report output')
    args=parser.parse_args(argv)
    if args.clean and args.command!='stop':parser.error('--clean requires stop')
    paths=Paths()
    try:
        options,feeds=load(paths)
        if args.command=='validate-feeds':return validate_sources(feeds,args.report)
        if args.command=='run':
            with ownership(paths):
                engine=Engine(paths,options,feeds)
                server=None
                try:
                    if args.headless:
                        import threading
                        done=threading.Event()
                        server=Server(paths,engine,done.set)
                        signal.signal(signal.SIGTERM,lambda *_:done.set());signal.signal(signal.SIGINT,lambda *_:done.set())
                        engine.start();done.wait()
                    else:
                        from .ui import Application
                        app=Application(engine)
                        def stop():
                            from gi.repository import GLib
                            GLib.idle_add(app.quit)
                        server=Server(paths,engine,stop)
                        signal.signal(signal.SIGTERM,lambda *_:stop());signal.signal(signal.SIGINT,lambda *_:stop())
                        app.run([])
                finally:
                    engine.close()
                    if server:server.close()
            return 0
        if args.command=='clean':
            with ownership(paths):Cache(paths,options).purge()
            print(json.dumps({'ok':True,'cache_bytes':0,'preferences_preserved':True}));return 0
        if args.command=='status':
            try:result=request(paths,'status')
            except (OSError,ValueError):
                with ownership(paths):result=Cache(paths,options).stats()|{'running':False,'syncing':False}
            print(json.dumps(result,indent=2));return 0
        if args.command=='refresh':
            try:result=request(paths,'refresh')
            except (FileNotFoundError,ConnectionRefusedError):
                raise RuntimeError('RetroNews läuft nicht; zuerst retronews starten') from None
            print(json.dumps(result));return 0
        if args.command=='stop':
            try:request(paths,'stop')
            except (FileNotFoundError,ConnectionRefusedError):pass
            deadline=time.monotonic()+15
            while True:
                try:
                    with ownership(paths):
                        if args.clean:Cache(paths,options).purge()
                    break
                except RuntimeError:
                    if time.monotonic()>deadline:raise RuntimeError('Stop timeout; cache was NOT cleaned')
                    time.sleep(.1)
            print(json.dumps({'ok':True,'stopped':True,'cleaned':args.clean}));return 0
    except (OSError,RuntimeError,ValueError,RecursionError,sqlite3.Error) as error:
        print('retronews: '+str(error),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
