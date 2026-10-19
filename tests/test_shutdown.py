import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import AsyncMock,patch
from leafroute.lifecycle import HelperLease,ParentProcess,valid_lease,watch_server,close_owned_tunnels,ConsoleCloseHandler
from leafroute.engine import Engine
from test_connection import MemoryStore,Phone
from test_leafroute import temporary_directory

class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_reset_precedes_device_close_and_rejects_later_commands(self):
        phone=Phone();phone.active=True;calls=[]
        async def reset():calls.append('reset');phone.active=False
        async def close():calls.append('close')
        phone.clear=reset;phone.close=close
        engine=Engine(MemoryStore(),phone)
        await engine.shutdown()
        self.assertEqual(calls,['reset','close'])
        with self.assertRaises(ValueError):await engine.command('start',{})
    async def test_reset_failure_still_closes_device(self):
        phone=Phone();phone.active=True
        phone.clear=AsyncMock(side_effect=OSError('disconnected'))
        phone.close=AsyncMock()
        engine=Engine(MemoryStore(),phone)
        with self.assertRaises(OSError):await engine.shutdown()
        phone.close.assert_awaited_once()
    async def test_shutdown_cancels_pending_connection(self):
        engine=Engine(MemoryStore(),Phone())
        engine.connection_task=asyncio.create_task(asyncio.sleep(30))
        await engine.shutdown()
        self.assertTrue(engine.connection_task.cancelled())

class LifecycleTests(unittest.TestCase):
    def test_lease_stop_and_expiry(self):
        lease=HelperLease()
        try:
            self.assertTrue(valid_lease(lease.folder))
            self.assertEqual(json.loads((lease.folder/'request.json').read_text())['parent_pid'],os.getpid())
            lease.stopped.set();lease.thread.join(1)
            os.utime(lease.folder/'lease',(time.time()-10,time.time()-10))
            self.assertFalse(valid_lease(lease.folder))
            lease.close();self.assertTrue((lease.folder/'stop').exists())
        finally:
            lease.close();lease.thread.join(1)
    def test_monitor_graceful_shutdown_and_bounded_fallback(self):
        class Server:should_exit=False
        server=Server();done=threading.Event();forced=[]
        watch_server(server,lambda:False,done,forced.append,grace=.01)
        self.assertTrue(server.should_exit);self.assertEqual(forced,[1])
        server=Server();done=threading.Event();done.set();forced=[]
        watch_server(server,lambda:False,done,forced.append,grace=.01)
        self.assertFalse(server.should_exit);self.assertEqual(forced,[])
    def test_parent_handle_detects_termination(self):
        parent=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        handle=ParentProcess(parent.pid)
        try:
            self.assertTrue(handle.alive())
            parent.terminate();parent.wait(5)
            self.assertFalse(handle.alive())
        finally:
            handle.close()
            if parent.poll() is None:parent.kill();parent.wait()
    def test_only_owned_helpers_receive_stop(self):
        from leafroute import lifecycle
        owned=HelperLease();external=HelperLease()
        try:
            with patch.object(lifecycle,'_owned',[owned]):close_owned_tunnels(wait=0)
            self.assertTrue((owned.folder/'stop').exists())
            self.assertFalse((external.folder/'stop').exists())
        finally:
            owned.close();external.close();owned.thread.join(1);external.thread.join(1)
    @unittest.skipUnless(os.name=='nt','Windows console callback')
    def test_console_close_uses_cleanup_but_ctrl_c_uses_python(self):
        calls=[]
        with ConsoleCloseHandler(lambda:calls.append('close')) as handler:
            self.assertFalse(handler.callback(0))
            self.assertTrue(handler.callback(2))
        self.assertEqual(calls,['close'])

class AppCloseTests(unittest.TestCase):
    def test_console_close_cleans_phone_once_and_stops_server(self):
        from leafroute.server import App
        tmp=temporary_directory()
        app=App(Path(__file__).resolve().parents[1],tmp.name,0)
        phone=Phone();phone.active=True;events=[]
        async def reset():events.append('reset');phone.active=False
        async def close():events.append('device-close')
        phone.clear=reset;phone.close=close;app.engine.device=phone
        try:
            with patch('leafroute.lifecycle.close_owned_tunnels',side_effect=lambda **_:events.append('tunnel-close')):
                if os.name=='nt':
                    with ConsoleCloseHandler(app.close) as handler:handler.callback(2)
                else:app.close()
                app.close()
            self.assertEqual(events,['reset','device-close','tunnel-close'])
            self.assertTrue(app._closed.is_set())
            self.assertFalse(app.thread.is_alive())
            self.assertEqual(app.http.fileno(),-1)
        finally:
            app.close();tmp.cleanup()

class PendingAdminTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_pending_uac_does_not_wait_for_confirmation(self):
        from leafroute.tunnel import launch_admin_async
        entered=threading.Event();release=threading.Event()
        def prompt(_):entered.set();release.wait(5)
        with patch('leafroute.tunnel.launch_admin',side_effect=prompt):
            task=asyncio.create_task(launch_admin_async([]))
            for _ in range(100):
                if entered.is_set():break
                await asyncio.sleep(.005)
            self.assertTrue(entered.is_set())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,.2)
            release.set()
            await asyncio.sleep(.02)
