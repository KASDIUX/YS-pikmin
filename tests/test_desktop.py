import sys,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from leafroute.desktop import run_window,configure_windowed_streams
from test_leafroute import temporary_directory

class Event:
    def __init__(self):self.handlers=[]
    def __iadd__(self,handler):self.handlers.append(handler);return self
    def fire(self):
        for handler in self.handlers:handler()

class DesktopTests(unittest.TestCase):
    def setup_fake(self,failure=False):
        self.events=SimpleNamespace(closing=Event(),loaded=Event())
        self.window=SimpleNamespace(events=self.events)
        self.requests=[];self.stop=threading.Event();self.started=threading.Event()
        def serve(**kwargs):self.started.set();self.stop.wait(2)
        def shutdown():self.requests.append('http-stop');self.stop.set()
        def close():self.requests.append('app-close')
        self.app=SimpleNamespace(origin='http://127.0.0.1:12345',http=SimpleNamespace(serve_forever=serve,shutdown=shutdown),close=close)
        def create(title,url,**kwargs):
            self.assertEqual(url,self.app.origin);self.assertTrue(kwargs['maximized'])
            self.assertNotIn('js_api',kwargs)
            return self.window
        def start(**kwargs):
            self.assertTrue(self.started.wait(1));self.assertEqual(kwargs['gui'],'edgechromium')
            self.assertFalse(kwargs['debug'])
            if failure:raise RuntimeError('missing engine')
            self.events.loaded.fire();self.events.closing.fire()
        return SimpleNamespace(settings={},create_window=create,start=start)
    def test_native_close_stops_backend_once(self):
        module=self.setup_fake()
        with patch.dict(sys.modules,{'webview':module}):run_window(self.app)
        self.assertEqual(self.requests,['http-stop','app-close'])
        self.assertTrue(module.settings['ALLOW_DOWNLOADS'])
        self.assertFalse(module.settings['ALLOW_FILE_URLS'])
    def test_renderer_failure_also_cleans_up(self):
        module=self.setup_fake(True)
        with patch.dict(sys.modules,{'webview':module}):
            with self.assertRaisesRegex(RuntimeError,'WebView2'):run_window(self.app)
        self.assertEqual(self.requests,['http-stop','app-close'])
    def test_missing_ui_dependency_reports_action(self):
        with patch.dict(sys.modules,{'webview':None}):
            with self.assertRaisesRegex(RuntimeError,'requirements-ui'):run_window(None)
    def test_windowed_executable_has_writable_diagnostics(self):
        tmp=temporary_directory();stream=None
        try:
            with patch('leafroute.desktop.log_folder',return_value=Path(tmp.name)),patch.object(sys,'stdout',None),patch.object(sys,'stderr',None),patch.object(sys,'argv',['app.exe']):
                configure_windowed_streams();stream=sys.stdout
                self.assertIs(sys.stdout,sys.stderr)
                stream.write('startup test\n');stream.flush()
            stream.close()
            self.assertIn('startup test',next(Path(tmp.name).glob('*.log')).read_text())
        finally:
            if stream and not stream.closed:stream.close()
            tmp.cleanup()
