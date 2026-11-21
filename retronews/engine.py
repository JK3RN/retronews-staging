"""One bounded background synchronizer; cached UI reads never wait for the network."""
import concurrent.futures
import threading
import time
from .cache import Cache
from .content import parse_feed, extract_article
from .network import HTTP, Budget, Cancelled
from .images import validate


class Engine:
    def __init__(self, paths, options, feeds, http=None):
        self.options, self.feeds = options, feeds
        self.cache=Cache(paths, options)
        self.stop_event=threading.Event();self.wake=threading.Event();self.guard=threading.Lock()
        self.http=http or HTTP(self.stop_event, options['timeout_seconds'])
        self.busy=False;self.pending=False;self.revision=0;self.connection='UNGEPRÜFT';self.last_cycle=None
        self.thread=None

    def start(self):
        self.thread=threading.Thread(target=self.loop,name='retronews-sync',daemon=False)
        self.thread.start()
        if self.options['auto_sync']:self.request_refresh()

    def request_refresh(self):
        with self.guard:
            if self.busy or self.pending or self.stop_event.is_set():return False
            self.pending=True;self.wake.set();return True

    def loop(self):
        next_auto=time.monotonic()+self.options['sync_minutes']*60
        while not self.stop_event.is_set():
            self.wake.wait(min(1,max(0,next_auto-time.monotonic())) if self.options['auto_sync'] else 1)
            self.wake.clear()
            if self.stop_event.is_set():break
            with self.guard:
                manual=self.pending
                self.pending=False
            if manual or (self.options['auto_sync'] and time.monotonic()>=next_auto):
                try:
                    self.sync(manual)
                except Exception:
                    self.connection='SYNC-FEHLER · Cache bleibt erhalten'
                    self.revision+=1
                next_auto=time.monotonic()+self.options['sync_minutes']*60

    def sync(self, manual=True):
        with self.guard:
            if self.busy:return
            self.busy=True
        budget=Budget(self.options['sync_budget_mb']*1_000_000)
        successful=[];prefetch=[];failed=0
        def fetch(feed):
            old=self.cache.feed_info(feed['url'])
            # Explicit refresh retries failures immediately; successful sources get a 60-second floor.
            if old.get('last_success',0)>time.time()-(60 if manual else self.options['sync_minutes']*60):
                return feed,'WAIT',{},[]
            backoff=min(3600,60*2**min(old.get('failures',0),6))
            if not manual and old.get('failures') and old.get('last_attempt',0)>time.time()-backoff:
                return feed,'WAIT',{},[]
            headers={k:v for k,v in [('If-None-Match',old.get('etag')),('If-Modified-Since',old.get('modified'))] if v}
            try:
                code,h,data=self.http.get(feed['url'],2_000_000,budget,headers)
                if code==304:
                    # An empty cache cannot be repaired by a 304 response.
                    if not any(r['feed']==feed['url'] for r in self.cache.rows()):
                        code,h,data=self.http.get(feed['url'],2_000_000,budget,{})
                    else:return feed,'NOT MODIFIED',h,[]
                articles=parse_feed(data,feed,self.options['items_per_feed'])
                return feed,'OK' if articles else 'EMPTY FEED',h,articles
            except Cancelled:raise
            except Exception as error:
                # Do not reproduce credential/query values from remote exception strings.
                return feed,'ERROR · '+type(error).__name__,{},[]
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3,thread_name_prefix='retronews-http') as pool:
                futures=[pool.submit(fetch,f) for f in self.feeds if f.get('enabled',True)]
                for future in concurrent.futures.as_completed(futures):
                    if self.stop_event.is_set():
                        for f in futures:f.cancel()
                        break
                    try:feed,status,headers,articles=future.result()
                    except Cancelled:continue
                    if status=='WAIT':continue
                    self.cache.feed_result(feed,status,headers)
                    if status in ('OK','NOT MODIFIED'):successful.append(feed['url'])
                    else:failed+=1
                    if status=='NOT MODIFIED':
                        for row in self.cache.rows():
                            if row['feed']==feed['url']:
                                cached=self.cache.read(row['id'])
                                if cached:prefetch.append((feed,cached))
                    for a in articles:
                        previous=self.cache.read(a['id'])
                        if previous and previous['mode']!='summary' and a['mode']=='summary':
                            a['text'],a['mode']=previous['text'],previous['mode']
                        if self.cache.put(a):prefetch.append((feed,a))
                    self.revision+=1
            # Load newest articles BEFORE opening them, bounded across the whole sync.
            prefetch.sort(key=lambda pair:pair[1]['published'],reverse=True)
            # A high-volume feed must not consume every offline preload slot.
            seen=set();first=[];remaining=[]
            for pair in prefetch:
                if pair[0]['url'] in seen:remaining.append(pair)
                else:first.append(pair);seen.add(pair[0]['url'])
            prefetch=first+remaining
            for feed,a in prefetch[:int(self.options['prefetch_articles'])]:
                if self.stop_event.is_set():break
                changed=False
                try:
                    if feed.get('prefetch',False) and a['mode']!='extracted':
                        code,h,data=self.http.get(a['url'],1_000_000,budget,subscription=feed['url'])
                        text,image=extract_article(data,a['url'],a['summary'])
                        if text:a['text'],a['mode']=text,'extracted';changed=True
                        if image:a['image_url']=image;changed=True
                    if changed:self.cache.put(a)
                    if self.options['images'] and a['image_url'] and not self.cache.file(a['id'],'.img').exists():
                        code,h,data=self.http.get(a['image_url'],int(self.options['image_limit_mb']*1_000_000),budget,subscription=feed['url'])
                        validate(data)
                        self.cache.put_image(a['id'],data)
                except Cancelled:break
                except Exception:pass # Article/feed text is already safely persisted.
                self.revision+=1
            self.cache.retention(successful)
            if successful:self.connection='ONLINE' if not failed else 'TEILWEISE ONLINE'
            elif failed:self.connection='OFFLINE / VERBINDUNGSFEHLER'
            self.last_cycle=time.time()
        finally:
            with self.guard:self.busy=False
            self.revision+=1

    def status(self):
        return self.cache.stats() | dict(running=True,syncing=self.busy or self.pending,
                                         connection=self.connection,auto_sync=self.options['auto_sync'],
                                         persist=self.options['persist'],images=self.options['images'])

    def close(self):
        self.stop_event.set();self.wake.set()
        if self.thread:self.thread.join()
        self.cache.set_active(None)
        if not self.options['persist']:self.cache.purge()
