"""One advisory lifetime lock and a private AF_UNIX command endpoint."""
import contextlib
import fcntl
import json
import os
import socket
import threading
import time


@contextlib.contextmanager
def ownership(paths):
    paths.prepare()
    if paths.lock.is_symlink():raise RuntimeError('Refusing symlink instance lock')
    fd=os.open(paths.lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('RetroNews läuft; clean ist erst nach stop erlaubt') from None
        yield
    finally:os.close(fd)


def request(paths, command):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(str(paths.socket))
        client.sendall(json.dumps({'command':command}).encode()+b'\n')
        data=bytearray()
        while len(data)<256_000:
            chunk=client.recv(4096)
            if not chunk:break
            data.extend(chunk)
            if b'\n' in data:break
        return json.loads(data)


class Server:
    def __init__(self, paths, engine, on_stop):
        self.paths,self.engine,self.on_stop=paths,engine,on_stop
        self.done=threading.Event()
        paths.socket.unlink(missing_ok=True) # Owner lock is already held by the caller.
        self.socket=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.socket.bind(str(paths.socket));paths.socket.chmod(0o600)
        self.socket.listen(4);self.socket.settimeout(.25)
        self.thread=threading.Thread(target=self.loop,name='retronews-control',daemon=False)
        self.thread.start()

    def loop(self):
        while not self.done.is_set():
            try:client,_=self.socket.accept()
            except socket.timeout:continue
            except OSError:break
            with client:
                client.settimeout(1)
                try:
                    payload=bytearray()
                    while len(payload)<4096 and b'\n' not in payload:
                        chunk=client.recv(1024)
                        if not chunk:break
                        payload.extend(chunk)
                    command=json.loads(payload).get('command')
                    if command=='status':result=self.engine.status()|{'pid':os.getpid()}
                    elif command=='refresh':result={'ok':True,'accepted':self.engine.request_refresh()}
                    elif command=='stop':result={'ok':True,'stopping':True}
                    else:result={'ok':False,'error':'Unknown control command'}
                    client.sendall(json.dumps(result).encode()+b'\n')
                    if command=='stop':self.on_stop()
                except (ValueError,OSError):continue

    def close(self):
        self.done.set();self.socket.close();self.thread.join()
        self.paths.socket.unlink(missing_ok=True)
