"""Desktop window hosting the local workbench; no browser or exposed Python API."""
import ctypes
import os
from pathlib import Path
import sys
import threading


def log_folder():
    return Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'YS-pikmin'/'logs'


def configure_windowed_streams():
    if sys.stdout is not None and sys.stderr is not None:return
    if os.name=='nt' and len(sys.argv)>1 and sys.argv[1] in ('--device-cli','--prepare-device'):
        kernel=ctypes.WinDLL('kernel32')
        attached=kernel.AttachConsole(ctypes.c_uint32(-1).value)
        if not attached and sys.argv[1]=='--prepare-device':attached=kernel.AllocConsole()
        if attached:
            sys.stdout=open('CONOUT$','w',encoding='utf-8',buffering=1)
            sys.stderr=sys.stdout
            sys.stdin=open('CONIN$','r',encoding='utf-8')
            return
    try:
        folder=log_folder();folder.mkdir(parents=True,exist_ok=True)
        stream=open(folder/f'{os.getpid()}.log','a',encoding='utf-8',buffering=1)
    except OSError:
        stream=open(os.devnull,'w',encoding='utf-8')
    if sys.stdout is None:sys.stdout=stream
    if sys.stderr is None:sys.stderr=stream


def show_message(message,error=False):
    if os.name=='nt':
        ctypes.windll.user32.MessageBoxW(None,str(message),'YS-pikmin',0x10 if error else 0x40)
    else:print(message,flush=True)


def run_window(app):
    try:import webview
    except ImportError as error:
        raise RuntimeError('缺少獨立視窗元件。原始碼版請安裝 requirements-ui.txt；EXE 請完整解壓縮。') from error
    webview.settings['ALLOW_DOWNLOADS']=True  # GPX export uses a save dialog.
    webview.settings['ALLOW_FILE_URLS']=False
    window=webview.create_window('YS-pikmin',app.origin,width=1440,height=960,
        min_size=(960,680),maximized=True,background_color='#f5f5ef',text_select=True)
    server=threading.Thread(target=app.http.serve_forever,kwargs={'poll_interval':.1},daemon=True)
    server.start()
    close_lock=threading.Lock()
    closed=False
    def close():
        nonlocal closed
        with close_lock:
            if closed:return
            closed=True
            try:app.http.shutdown()
            finally:
                app.close()
                server.join(timeout=.3)
    window.events.closing+=close
    window.events.loaded+=lambda:print('獨立視窗介面已載入。',flush=True)
    try:
        webview.start(gui='edgechromium',debug=False,private_mode=True)
    except Exception as error:
        raise RuntimeError('無法啟動獨立視窗。請確認 Microsoft Edge WebView2 執行階段已安裝。') from error
    finally:close()
