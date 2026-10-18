import asyncio
import io
import json
from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError, URLError

from leafroute.engine import Engine
from leafroute.geo import destination
from leafroute.routing import RoadPlanner, build_request, parse_response, route_meta
from leafroute.store import Store
from test_leafroute import FakeDevice, temporary_directory

A = [25, 121]
B = destination(A, 90, 100)


def payload(points=None):
    points = points or [A, B]
    return {'code': 'Ok', 'routes': [{'geometry': {'type': 'LineString',
            'coordinates': [[p[1], p[0]] for p in points]}}],
            'waypoints': [{'location': [p[1], p[0]]} for p in points]}


class RoutingTests(unittest.TestCase):
    def test_profiles_use_different_servers_and_lon_lat(self):
        for profile in ['foot','bike','car']:
            request, values = build_request([A,B], profile)
            self.assertIn('/routed-'+profile+'/route/v1/driving/121.0000000,25.0000000;',request.full_url)
            self.assertEqual(values,[A,B])
            self.assertIn('LeafRoute',request.get_header('User-agent'))

    def test_round_trip_is_routed_not_a_straight_connector(self):
        _, values=build_request([A,B],'foot',True)
        self.assertEqual(values,[A,B,A])
        _, closed=build_request([A,B,A],'foot',True)
        self.assertEqual(len(closed),3)

    def test_invalid_input_never_makes_network_request(self):
        calls=[]
        def opener(*args,**kwargs):calls.append(1)
        planner=RoadPlanner(opener)
        for values,profile in [([A],'foot'),([A,B]*13,'foot'),([A,[26,122]],'foot'),([A,B],'unknown')]:
            with self.assertRaises(ValueError):planner.plan(values,profile)
        self.assertEqual(calls,[])

    def test_valid_geometry_and_snap_distance(self):
        points,snap=parse_response(payload(),[A,B])
        self.assertEqual(points,[A,B]);self.assertEqual(snap,0)
        result=RoadPlanner(lambda *a,**k:io.BytesIO(json.dumps(payload()).encode())).plan([A,B],'foot')
        self.assertEqual(result['meta']['waypoints'],[A,B])
        self.assertEqual(result['meta']['kind'],'road')

    def test_rejects_missing_or_nonfinite_geometry_and_excessive_snap(self):
        bad=[{}, {'code':'NoRoute'}, {'code':'NoSegment'}, {'code':'Ok','routes':[]},
             payload([[float('nan'),121],B]), payload([[26,121],B])]
        broken=payload();broken['waypoints']=[];bad.append(broken)
        for value in bad:
            with self.subTest(value=value),self.assertRaises(ValueError):parse_response(value,[A,B])

    def test_rate_limit_has_no_automatic_retry(self):
        calls=[]
        def opener(*a,**k):calls.append(1);return io.BytesIO(json.dumps(payload()).encode())
        planner=RoadPlanner(opener);planner.plan([A,B],'foot')
        with self.assertRaises(ValueError):planner.plan([A,B],'foot')
        self.assertEqual(len(calls),1)

    def test_http_failure_timeout_and_garbage_are_actionable(self):
        errors=[HTTPError('https://example.invalid',429,'',{},None),URLError('offline'),TimeoutError()]
        for error in errors:
            def opener(*a,**k):raise error
            with self.subTest(error=error),self.assertRaises(ValueError):RoadPlanner(opener).plan([A,B],'foot')
        with self.assertRaises(ValueError):RoadPlanner(lambda *a,**k:io.BytesIO(b'<html>')).plan([A,B],'foot')

    def test_oversized_reply_is_rejected(self):
        with self.assertRaises(ValueError):
            RoadPlanner(lambda *a,**k:io.BytesIO(b' '*4_000_001)).plan([A,B],'foot')


class RoutingEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=temporary_directory();self.addCleanup(self.temp.cleanup)
        self.engine=Engine(Store(Path(self.temp.name)/'library.json'),FakeDevice())
        await self.engine.command('route',{'points':[A,B]})

    async def test_network_wait_does_not_block_stop(self):
        started,release=threading.Event(),threading.Event()
        def plan(*args):
            started.set();release.wait(3)
            return {'points':[A,B], 'meta':route_meta()}
        self.engine.planner.plan=plan
        pending=asyncio.create_task(self.engine.command('plan',{'points':[A,B]}))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait,1))
            result=await asyncio.wait_for(self.engine.command('restore',{}),.5)
            self.assertEqual(result['status'],'idle')
        finally:
            release.set();await pending

    async def test_stale_result_cannot_overwrite_new_route(self):
        self.engine.planner.plan=lambda *a:{'points':[A,B],'meta':route_meta()}
        result=await self.engine.command('plan',{'points':[A,B]})
        other=destination(A,0,50)
        await self.engine.command('route',{'points':[A,other]})
        with self.assertRaises(ValueError):
            await self.engine.command('route',{'points':result['points'],'expected_revision':result['revision']})
        self.assertEqual(self.engine.route.points,[A,other])

    async def test_road_start_and_loop_do_not_add_unrouted_segments(self):
        e=self.engine
        meta={'kind':'road','profile':'foot','waypoints':[A,B],'round_trip':False}
        await e.command('route',{'points':[A,B],'meta':meta})
        with self.assertRaises(ValueError):await e.command('start',{})
        await e.command('position',{'position':A})
        await e.command('settings',{'loop':True})
        with self.assertRaises(ValueError):await e.command('start',{})
        await e.command('route',{'points':[A,B,A],'meta':{**meta,'round_trip':True}})
        await e.command('start',{})
        self.assertEqual(e.play_route.points,[A,B,A])

    async def test_saved_road_metadata_survives_restart(self):
        meta={'kind':'road','profile':'bike','waypoints':[A,B],'round_trip':False}
        await self.engine.command('route',{'points':[A,B],'meta':meta})
        await self.engine.command('save',{'kind':'routes','name':'道路測試'})
        saved=Store(Path(self.temp.name)/'library.json')
        self.assertEqual(saved.data['routes'][0]['meta'],meta)

    async def test_routing_failure_keeps_phone_and_original_route(self):
        def fail(*args):raise ValueError('offline')
        self.engine.planner.plan=fail
        self.engine.connected=True;self.engine.mode='device'
        with self.assertRaises(ValueError):await self.engine.command('plan',{'points':[A,B]})
        self.assertTrue(self.engine.connected)
        self.assertEqual(self.engine.route.points,[A,B])


if __name__=='__main__':unittest.main()
