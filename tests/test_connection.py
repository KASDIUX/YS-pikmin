import asyncio, unittest
from unittest.mock import AsyncMock, patch
from leafroute.engine import Engine
from leafroute.tunnel import ensure_tunnel

class MemoryStore:
    warning=''
    data={'favorites':[],'routes':[]}
class Phone:
    info=None
    active=False
    def __init__(self): self.connections=[]
    async def connect(self,target): self.connections.append(target);self.info=target
    async def close(self): self.info=None
    async def clear(self): self.active=False

PHONE={'id':'phone-1','name':'Test iPhone','ios':'26.6.2','transport':'USB'}
class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_connect_reuses_flow_without_location(self):
        e=Engine(MemoryStore(),Phone())
        with patch('leafroute.engine.discover',AsyncMock(return_value={'devices':[PHONE]})),patch('leafroute.engine.ensure_tunnel',AsyncMock()) as tunnel:
            result=await e.command('auto-connect',{'transport':'USB'})
            self.assertEqual(result['connection_phase'],'connecting')
            await e.connection_task
            self.assertTrue(e.connected)
            self.assertFalse(e.device.active)
            tunnel.assert_awaited_once()
            await e.command('disconnect',{})
            self.assertFalse(e.connected)
    async def test_multiple_requires_choice(self):
        e=Engine(MemoryStore(),Phone())
        with patch('leafroute.engine.discover',AsyncMock(return_value={'devices':[PHONE,{**PHONE,'id':'phone2'}]})),patch('leafroute.engine.ensure_tunnel',AsyncMock()) as tunnel:
            await e.command('auto-connect',{})
            await e.connection_task
            self.assertEqual(e.connection_phase,'choose')
            self.assertFalse(e.connected)
            tunnel.assert_not_awaited()
    async def test_cancel_and_duplicate(self):
        e=Engine(MemoryStore(),Phone());waiting=asyncio.Event()
        async def tunnel(*args): waiting.set();await asyncio.Event().wait()
        with patch('leafroute.engine.discover',AsyncMock(return_value={'devices':[PHONE]})),patch('leafroute.engine.ensure_tunnel',tunnel):
            await e.command('auto-connect',{})
            task=e.connection_task
            await waiting.wait()
            await e.command('auto-connect',{})
            self.assertIs(task,e.connection_task)
            with self.assertRaises(ValueError): await e.command('start',{})
            await asyncio.wait_for(e.command('disconnect',{}),1)
            self.assertTrue(task.cancelled())
            self.assertEqual(e.device.connections,[])
    async def test_no_device_and_uac_failure(self):
        for devices,error in [([],None),([PHONE],ValueError('UAC cancelled'))]:
            e=Engine(MemoryStore(),Phone())
            with patch('leafroute.engine.discover',AsyncMock(return_value={'devices':devices})),patch('leafroute.engine.ensure_tunnel',AsyncMock(side_effect=error)):
                await e.command('auto-connect',{})
                await e.connection_task
                self.assertEqual(e.connection_phase,'error')
                self.assertFalse(e.connected)
    async def test_existing_tunnel_not_restarted(self):
        with patch('leafroute.tunnel.read_tunnels',return_value={'phone1':[{'ready':True}]}),patch('leafroute.tunnel.launch_tunnel') as launch:
            await ensure_tunnel('phone-1',lambda _:None)
            launch.assert_not_called()
    async def test_legacy_skips_tunnel(self):
        e=Engine(MemoryStore(),Phone())
        with patch('leafroute.engine.discover',AsyncMock(return_value={'devices':[{**PHONE,'ios':'16.7'}]})),patch('leafroute.engine.ensure_tunnel',AsyncMock()) as tunnel:
            await e.command('auto-connect',{})
            await e.connection_task
            self.assertTrue(e.connected)
            tunnel.assert_not_awaited()
