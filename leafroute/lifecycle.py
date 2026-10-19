"""Bounded shutdown for the console and the tunnel owned by this app."""
import atexit
import ctypes
import json
import os
from pathlib import Path
import tempfile
import threading
import time


class HelperLease:
    def __init__(self):
        self.folder = Path(tempfile.mkdtemp(prefix='ys-pikmin-tunnel-'))
        (self.folder/'request.json').write_text(json.dumps({'parent_pid':os.getpid()}),encoding='utf-8')
        (self.folder/'lease').touch()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._beat,daemon=True)
        self.thread.start()

    def _beat(self):
        while not self.stopped.wait(.5):
            try: (self.folder/'lease').touch()
            except OSError: return

    def close(self):
        self.stopped.set()
        try: (self.folder/'stop').touch()
        except OSError: pass


def valid_lease(folder):
    try:
        age = time.time()-(folder/'lease').stat().st_mtime
        return not (folder/'stop').exists() and 0 <= age < 4
    except OSError:
        return False


class ParentProcess:
    """Hold a process handle so PID reuse cannot keep a helper alive."""
    def __init__(self,pid):
        self.pid = pid
        self.handle = None
        if type(pid) is not int or pid <= 0 or pid == os.getpid():
            raise ValueError('不正確的主程式識別碼。')
        if os.name == 'nt':
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL('kernel32',use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.restype = wintypes.BOOL
            self.handle = self.kernel.OpenProcess(0x00100000,False,pid)

    def alive(self):
        if os.name == 'nt':
            return bool(self.handle) and self.kernel.WaitForSingleObject(self.handle,0) == 258
        try: os.kill(self.pid,0);return True
        except OSError: return False

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def watch_server(server,should_live,done,force_exit=os._exit,grace=3):
    while not done.wait(.2):
        if not should_live():
            server.should_exit = True
            if not done.wait(grace):
                force_exit(1)  # Only this helper; never terminate a discovered/shared service.
            return


def run_tunnel_helper(directory):
    folder = Path(directory).resolve()
    if folder.parent != Path(tempfile.gettempdir()).resolve() or not folder.name.startswith('ys-pikmin-tunnel-'):
        raise ValueError('不接受的通道工作目錄。')
    parent = None
    done = threading.Event()
    try:
        request = (folder/'request.json').read_text(encoding='utf-8')
        if len(request)>1024: raise ValueError('通道請求過大。')
        parent = ParentProcess(json.loads(request)['parent_pid'])
        if not parent.alive() or not valid_lease(folder): return
        from pymobiledevice3.tunneld.server import TunneldRunner
        from pymobiledevice3.remote.common import TunnelProtocol
        import uvicorn
        runner = TunneldRunner('127.0.0.1',49151,protocol=TunnelProtocol.TCP)
        server = uvicorn.Server(uvicorn.Config(runner._app,host='127.0.0.1',port=49151,
                                loop='asyncio',timeout_graceful_shutdown=1,access_log=False))
        monitor = threading.Thread(target=watch_server,
            args=(server,lambda:parent.alive() and valid_lease(folder),done),daemon=True)
        monitor.start()
        server.run()
    finally:
        done.set()
        if parent: parent.close()
        (folder/'closed').touch()


_owned = []
_owned_lock = threading.Lock()


def new_owned_tunnel():
    lease = HelperLease()
    with _owned_lock: _owned.append(lease)
    return lease


def close_owned_tunnels(wait=.7):
    with _owned_lock:
        leases = list(_owned)
    for lease in leases: lease.close()
    deadline = time.monotonic()+wait
    while leases and time.monotonic()<deadline:
        leases = [lease for lease in leases if not (lease.folder/'closed').exists()]
        if leases: time.sleep(.05)


atexit.register(close_owned_tunnels,0)


class ConsoleCloseHandler:
    def __init__(self,close):
        self.close = close
        self.callback = None

    def __enter__(self):
        if os.name == 'nt':
            from ctypes import wintypes
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.DWORD)
            self.kernel = ctypes.WinDLL('kernel32',use_last_error=True)
            self.kernel.SetConsoleCtrlHandler.argtypes = [callback_type,wintypes.BOOL]
            self.kernel.SetConsoleCtrlHandler.restype = wintypes.BOOL
            def handle(event):
                if event not in (2,5,6): return False  # Ctrl+C uses the normal Python finally path.
                try: self.close()
                except BaseException: pass
                return True
            self.callback = callback_type(handle)
            if not self.kernel.SetConsoleCtrlHandler(self.callback,True):
                raise ctypes.WinError(ctypes.get_last_error())
        return self

    def __exit__(self,*_):
        if self.callback:
            self.kernel.SetConsoleCtrlHandler(self.callback,False)
            self.callback = None
