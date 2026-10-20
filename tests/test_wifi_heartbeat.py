import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from leafroute.wifi import WifiHeartbeat

class WifiHeartbeatTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, recv):
        service=SimpleNamespace(recv_plist=AsyncMock(side_effect=recv),send_plist=AsyncMock(),close=AsyncMock())
        client=SimpleNamespace(start_lockdown_service=AsyncMock(return_value=service))
        return client,service

    async def test_first_reply_precedes_ready_and_close_cancels_reader(self):
        block=asyncio.Event()
        count=0
        async def recv():
            nonlocal count
            count+=1
            if count>1:await block.wait()
            return {'Command':'Marco'}
        client,service=self.fixture(recv)
        async with WifiHeartbeat(client) as heartbeat:
            service.send_plist.assert_awaited_once_with({'Command':'Polo'})
            task=heartbeat.task
            await asyncio.sleep(0)
            heartbeat.check()
        self.assertTrue(task.done())
        service.close.assert_awaited_once()

    async def test_first_exchange_failure_closes_service(self):
        async def recv():raise ConnectionError('phone disconnected')
        client,service=self.fixture(recv)
        with self.assertRaises(ConnectionError):
            async with WifiHeartbeat(client):self.fail('Must not report ready')
        service.send_plist.assert_not_awaited()
        service.close.assert_awaited_once()

    async def test_later_failure_is_reported_to_tunnel_owner(self):
        count=0
        async def recv():
            nonlocal count
            count+=1
            if count>1:raise ConnectionError('heartbeat lost')
            return {'Command':'Marco'}
        client,service=self.fixture(recv)
        async with WifiHeartbeat(client) as heartbeat:
            await asyncio.sleep(0)
            with self.assertRaisesRegex(ConnectionError,'heartbeat lost'):heartbeat.check()
        service.close.assert_awaited_once()

    async def test_cancel_during_first_exchange_closes_service(self):
        started=asyncio.Event()
        async def recv():
            started.set()
            await asyncio.Event().wait()
        client,service=self.fixture(recv)
        heartbeat=WifiHeartbeat(client)
        task=asyncio.create_task(heartbeat.__aenter__())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        service.close.assert_awaited_once()
