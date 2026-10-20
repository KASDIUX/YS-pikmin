import json, unittest
from pathlib import Path
from leafroute.engine import Engine
from leafroute.store import Store
from test_leafroute import FakeDevice, temporary_directory

class HomeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=temporary_directory();self.path=Path(self.tmp.name)/'library.json'
        self.device=FakeDevice();self.engine=Engine(Store(self.path),self.device)
    def tearDown(self): self.tmp.cleanup()
    async def test_restart_starts_at_saved_home_in_preview_only(self):
        for home in ([25.1,121.5],[0,0]):
            with self.subTest(home=home):
                await self.engine.command('set-home',{'position':home})
                device=FakeDevice()
                restarted=Engine(Store(self.path),device)
                self.assertEqual(restarted.position,home)
                self.assertEqual(restarted.snapshot()['home'],home)
                self.assertEqual(restarted.mode,'preview')
                self.assertFalse(restarted.connected)
                self.assertEqual(device.positions,[])
                restarted.position[0]+=1
                self.assertEqual(restarted.store.data['home'],home)
    async def test_restart_without_home_uses_default_and_preserves_old_library(self):
        raw=json.dumps({'favorites':[],'routes':[]})
        self.path.write_text(raw)
        device=FakeDevice()
        restarted=Engine(Store(self.path),device)
        self.assertEqual(restarted.position,[25.033964,121.564468])
        self.assertEqual(self.path.read_text(),raw)
        self.assertEqual(device.positions,[])
    async def test_home_persists_without_moving(self):
        original=list(self.engine.position)
        await self.engine.command('set-home',{'position':[25.1,121.5]})
        self.assertEqual(self.engine.position,original)
        self.assertEqual(self.device.positions,[])
        self.assertEqual(Store(self.path).data['home'],[25.1,121.5])
        await self.engine.command('set-home',{'position':[24,120]})
        self.assertEqual(Store(self.path).data['home'],[24,120])
    async def test_preview_and_device_return(self):
        await self.engine.command('set-home',{'position':[25.1,121.5]})
        await self.engine.command('go-home',{})
        self.assertEqual(self.engine.position,[25.1,121.5])
        self.assertEqual(self.device.positions,[])
        self.engine.mode='device';self.engine.connected=True
        await self.engine.command('go-home',{})
        self.assertEqual(self.device.positions,[[25.1,121.5]])
    async def test_missing_invalid_running_disconnected(self):
        with self.assertRaises(ValueError): await self.engine.command('go-home',{})
        with self.assertRaises(ValueError): await self.engine.command('set-home',{'position':[999,2]})
        await self.engine.command('set-home',{'position':[25,121]})
        self.engine.status='running'
        with self.assertRaises(ValueError): await self.engine.command('go-home',{})
        self.engine.status='idle';self.engine.mode='device';self.engine.connected=False
        with self.assertRaises(ValueError): await self.engine.command('go-home',{})
        self.assertEqual(self.device.positions,[])
    async def test_device_failure_does_not_change_position(self):
        await self.engine.command('set-home',{'position':[25,121]})
        original=list(self.engine.position)
        self.engine.mode='device';self.engine.connected=True;self.device.fail=True
        with self.assertRaises(ValueError): await self.engine.command('go-home',{})
        self.assertEqual(self.engine.position,original)
        self.assertFalse(self.engine.connected)
    async def test_old_and_invalid_home_data(self):
        self.path.write_text(json.dumps({'favorites':[],'routes':[]}))
        self.assertIsNone(Store(self.path).data.get('home'))
        raw=json.dumps({'favorites':[],'routes':[],'home':[999,0]})
        self.path.write_text(raw)
        store=Store(self.path)
        self.assertTrue(store.warning)
        with self.assertRaises(ValueError): store.save({'favorites':[],'routes':[]})
        self.assertEqual(self.path.read_text(),raw)
