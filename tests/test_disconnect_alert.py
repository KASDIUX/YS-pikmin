import copy
import asyncio
import unittest
from unittest.mock import AsyncMock

from leafroute.device import Device
from leafroute.device_group import DeviceGroup
from leafroute.engine import Engine


class Store:
    warning = ''
    data = {'favorites': [], 'routes': []}

    def save(self, data):
        self.data = copy.deepcopy(data)


class DisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.phones = [Device(), Device()]
        for phone in self.phones:
            phone.connect = AsyncMock()
            phone.check_connection = AsyncMock()
        pool = iter(self.phones)
        self.group = DeviceGroup(lambda: next(pool))
        self.engine = Engine(Store(), self.group)
        self.engine.devices = [{'id': str(i), 'name': f'Phone {i}', 'transport': 'USB'} for i in (1, 2)]
        await self.engine.command('connect', {'ids': ['1', '2']})
        self.engine.last_health_check = 0

    async def test_idle_loss_names_only_failed_phone_and_sends_no_coordinates(self):
        self.phones[1].check_connection.side_effect = OSError()
        await self.engine.check_connection(10)
        state = self.engine.snapshot()
        self.assertFalse(state['connected'])
        self.assertEqual(state['disconnect_alert']['devices'], ['Phone 2'])
        self.assertFalse(state['simulation_active'])
        self.assertTrue(state['connected_devices'][0]['connected'])

    async def test_loss_pauses_and_preserves_uncertain_simulation(self):
        self.engine.status = 'running'
        self.phones[1].active = True
        self.phones[1].check_connection.side_effect = TimeoutError()
        await self.engine.check_connection(10)
        self.assertEqual(self.engine.status, 'paused')
        self.assertTrue(self.engine.snapshot()['simulation_active'])
        self.assertIn('還原定位', self.engine.disconnect_alert['message'])

    async def test_healthy_check_and_interval_do_not_alert(self):
        await self.engine.check_connection(10)
        await self.engine.check_connection(11)
        self.assertTrue(self.engine.connected)
        self.assertIsNone(self.engine.disconnect_alert)
        self.phones[0].check_connection.assert_awaited_once()

    async def test_no_repeat_after_loss(self):
        self.phones[0].check_connection.side_effect = OSError()
        await self.engine.check_connection(10)
        original = self.engine.snapshot()['disconnect_alert']
        await self.engine.check_connection(20)
        self.engine._connection_lost()
        self.assertEqual(self.engine.disconnect_alert, original)

    async def test_intentional_disconnect_does_not_alert(self):
        await self.engine.command('disconnect', {})
        await self.engine.check_connection(10)
        self.assertIsNone(self.engine.disconnect_alert)
        self.phones[0].check_connection.assert_not_awaited()

    async def test_send_failure_alerts_immediately(self):
        self.phones[0].set = AsyncMock(side_effect=OSError())
        self.phones[1].set = AsyncMock()
        with self.assertRaises(ValueError):
            await self.engine.command('position', {'position': [25, 121]})
        self.assertEqual(self.engine.disconnect_alert['devices'], ['Phone 1'])
        self.assertFalse(self.engine.connected)

    async def test_initial_connection_failure_does_not_alert(self):
        engine = Engine(Store(), DeviceGroup())
        with self.assertRaises(ValueError):
            await engine.command('connect', {'ids': ['missing']})
        self.assertIsNone(engine.disconnect_alert)

    async def test_reconnection_can_trigger_a_new_alert(self):
        self.engine._connection_lost()
        first_id = self.engine.disconnect_alert['id']
        self.group.factory = Device
        # Use mocked sessions again; no real device service is contacted.
        fresh = Device()
        fresh.connect = AsyncMock()
        fresh.check_connection = AsyncMock(side_effect=OSError())
        self.group.factory = lambda: fresh
        await self.engine.command('connect', {'ids': ['1']})
        self.assertIsNone(self.engine.disconnect_alert)
        self.engine.last_health_check = 0
        await self.engine.check_connection(10)
        self.assertGreater(self.engine.disconnect_alert['id'], first_id)

    async def test_failed_intentional_restore_does_not_alert(self):
        self.phones[0].active = True
        self.phones[0].clear = AsyncMock(side_effect=OSError())
        with self.assertRaises(ValueError):
            await self.engine.command('disconnect', {})
        self.assertIsNone(self.engine.disconnect_alert)
        self.assertFalse(self.engine.connected)

    async def test_disconnect_during_inflight_health_failure_does_not_alert(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def failing_probe():
            entered.set()
            await release.wait()
            raise OSError()
        self.phones[0].check_connection.side_effect = failing_probe
        async def health_under_lock():
            async with self.engine.lock:
                await self.engine.check_connection(10)
        health = asyncio.create_task(health_under_lock())
        await entered.wait()
        disconnect = asyncio.create_task(self.engine.command('disconnect', {}))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(health, disconnect)
        self.assertIsNone(self.engine.disconnect_alert)

    async def test_readonly_device_check(self):
        device = Device()
        device.dvt = object()
        device.health = AsyncMock()
        device.set = AsyncMock()
        device.clear = AsyncMock()
        await device.check_connection()
        device.health.mach_time_info.assert_awaited_once()
        device.set.assert_not_awaited()
        device.clear.assert_not_awaited()

    async def test_legacy_readonly_check(self):
        device = Device()
        device.legacy = AsyncMock()
        await device.check_connection()
        device.legacy.get_value.assert_awaited_once_with(key='DeviceName')
