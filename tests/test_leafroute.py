import asyncio
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from leafroute.engine import Engine
from leafroute.geo import Route, destination, distance, point, read_gpx, write_gpx
from leafroute.server import App
from leafroute.store import Store


def temporary_directory():
    return tempfile.TemporaryDirectory(dir=os.environ.get('LEAFROUTE_TEST_TMP'))


class GeoTests(unittest.TestCase):
    def test_coordinate_validation(self):
        for bad in ([91,0], [0,181], [float('nan'),0], [0,float('inf')], [True,0], [], '1,2'):
            with self.subTest(bad=bad), self.assertRaises(ValueError): point(bad)
        self.assertEqual(point([-90,-180]),[-90,-180])

    def test_great_circle_and_dateline(self):
        self.assertAlmostEqual(distance([0,0],[0,1]),111195.08,places=1)
        route=Route([[0,179.9],[0,-179.9]])
        self.assertLess(route.length,23000)
        self.assertAlmostEqual(abs(route.at(route.length/2)[1]),180,places=6)
        self.assertAlmostEqual(distance([80,15],destination([80,15],90,5)),5,places=5)

    def test_duplicates_and_endpoints(self):
        route=Route([[0,0],[0,0],[0,.01]])
        self.assertEqual(len(route.points),2)
        self.assertEqual(route.at(1e9),[0,.01])
        with self.assertRaises(ValueError): Route([[1,1],[1,1]])

    def test_gpx_roundtrip(self):
        points=[[25.033964,121.564468],[25.035,121.566]]
        self.assertEqual(read_gpx(write_gpx(points).decode()),points)
        self.assertEqual(read_gpx('<gpx><rte><rtept lat="-1" lon="-2"/><rtept lat="-3" lon="-4"/></rte></gpx>'),[[-1,-2],[-3,-4]])

    def test_gpx_rejects_disjoint_and_hostile_input(self):
        bad=['<!DOCTYPE gpx [<!ENTITY a "x">]><gpx/>', '<gpx><', '<wrong/>', '<gpx><wpt lat="NaN" lon="0"/></gpx>',
             '<gpx><trk><trkseg><trkpt lat="0" lon="0"/></trkseg><trkseg><trkpt lat="1" lon="1"/></trkseg></trk></gpx>']
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError): read_gpx(value)


class FakeDevice:
    def __init__(self):
        self.info={'name':'Test phone'};self.active=False;self.fail=False;self.positions=[];self.clears=0
    async def set(self,position):
        self.active=True
        if self.fail: raise ConnectionError('disconnected')
        self.positions.append(position)
    async def clear(self):
        if self.fail: raise ConnectionError('disconnected')
        self.active=False;self.clears+=1
    async def close(self): self.info=None


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=temporary_directory();self.addCleanup(self.temp.cleanup)
        self.fake=FakeDevice();self.engine=Engine(Store(Path(self.temp.name)/'library.json'),self.fake)
        self.start=[25,121]
        await self.engine.command('position',{'position':self.start})
        await self.engine.command('route',{'points':[self.start,destination(self.start,90,100)]})
        await self.engine.command('settings',{'speed':3.6})

    async def test_constant_speed_pause_and_resume(self):
        e=self.engine;await e.command('start',{})
        for _ in range(10): await e.advance(1)
        self.assertAlmostEqual(e.traveled,10)
        self.assertAlmostEqual(distance(self.start,e.position),10,places=4)
        await e.command('pause',{});await e.advance(100)
        self.assertAlmostEqual(e.traveled,10)
        await e.command('start',{});await e.advance(1)
        self.assertAlmostEqual(e.traveled,11)

    async def test_sleep_does_not_jump(self):
        await self.engine.command('start',{});await self.engine.advance(3600)
        self.assertAlmostEqual(self.engine.traveled,1)

    async def test_start_connects_current_position_and_loop_returns(self):
        e=self.engine;await e.command('position',{'position':destination(self.start,270,10)})
        await e.command('settings',{'loop':True});await e.command('start',{})
        self.assertAlmostEqual(e.play_route.length,220,places=2)
        self.assertEqual(e.play_route.points[0],e.play_route.points[-1])
        e.progress=e.play_route.length-.5;await e.advance(1)
        self.assertEqual(e.laps,1);self.assertAlmostEqual(e.progress,.5,places=4)

    async def test_device_failure_stops_and_never_falls_back(self):
        e=self.engine;e.mode='device';e.connected=True;await e.command('start',{})
        self.fake.fail=True;await e.advance(1)
        self.assertEqual(e.mode,'device');self.assertEqual(e.status,'paused')
        self.assertFalse(e.connected);self.assertEqual(e.position,self.start)
        self.assertEqual(e.traveled,0);self.assertTrue(e.error)

    async def test_restore_failure_not_reported_as_success(self):
        e=self.engine;e.mode='device';e.connected=True;self.fake.active=True;self.fake.fail=True
        with self.assertRaises(ValueError): await e.command('restore',{})
        self.assertTrue(e.error);self.assertTrue(self.fake.active)

    async def test_pause_retains_simulated_position_restore_clears(self):
        e=self.engine;e.mode='device';e.connected=True;await e.command('start',{});await e.advance(1)
        await e.command('pause',{});self.assertTrue(self.fake.active)
        await e.command('restore',{});self.assertFalse(self.fake.active);self.assertEqual(self.fake.clears,1)

    async def test_saved_data_survives_restart(self):
        await self.engine.command('save',{'kind':'favorites','name':'測試收藏','position':self.start})
        await self.engine.command('save',{'kind':'routes','name':'測試路線'})
        new=Store(Path(self.temp.name)/'library.json')
        self.assertEqual(new.data['favorites'][0]['name'],'測試收藏')
        self.assertEqual(len(new.data['routes'][0]['points']),2)

    async def test_invalid_speed_and_edit_while_running(self):
        for value in [0,float('nan'),121]:
            with self.assertRaises(ValueError): await self.engine.command('settings',{'speed':value})
        await self.engine.command('start',{})
        with self.assertRaises(ValueError): await self.engine.command('position',{'position':[0,0]})


class StoreTests(unittest.TestCase):
    def test_corrupt_library_is_preserved(self):
        with temporary_directory() as folder:
            path=Path(folder)/'library.json';path.write_text('{broken')
            store=Store(path)
            self.assertTrue(store.warning)
            with self.assertRaises(ValueError):store.save({'favorites':[],'routes':[]})
            self.assertEqual(path.read_text(),'{broken')


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=temporary_directory()
        cls.app=App(Path(__file__).resolve().parents[1],cls.temp.name,0)
        cls.thread=threading.Thread(target=cls.app.http.serve_forever,daemon=True);cls.thread.start()
        cls.opener=build_opener(ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.app.http.shutdown();cls.thread.join();cls.app.close();cls.temp.cleanup()

    def request(self,path,data=None,headers=None):
        body=json.dumps(data).encode() if data is not None else None
        request=Request(self.app.origin+path,data=body,headers={'Content-Type':'application/json',**(headers or {})})
        return self.opener.open(request,timeout=5)

    def test_requires_token_and_same_origin(self):
        for headers in [{},{'X-LeafRoute-Token':self.app.token,'Origin':'https://evil.example'},{'Host':'evil.example','X-LeafRoute-Token':self.app.token}]:
            with self.subTest(headers=headers),self.assertRaises(HTTPError) as error:
                self.request('/api/position',{'position':[0,0]},headers)
            self.assertEqual(error.exception.code,403)

    def test_preview_route_export(self):
        headers={'X-LeafRoute-Token':self.app.token,'Origin':self.app.origin}
        with self.request('/api/route',{'points':[[25,121],[25.01,121.01]]},headers) as response:
            self.assertEqual(response.status,200)
        with self.request('/api/export') as response:
            self.assertEqual(len(read_gpx(response.read().decode())),2)
        with self.request('/api/state') as response:
            self.assertEqual(json.load(response)['mode'],'preview')

    def test_static_files_do_not_expose_source_or_data(self):
        for path in ['/../start.py','/data/library.json','/leafroute/device.py']:
            with self.subTest(path=path),self.assertRaises(HTTPError) as error:self.request(path)
            self.assertEqual(error.exception.code,404)

    def test_second_instance_cannot_share_listening_port(self):
        with self.assertRaises(OSError):
            App(Path(__file__).resolve().parents[1],self.temp.name,self.app.port)


if __name__=='__main__':unittest.main()
