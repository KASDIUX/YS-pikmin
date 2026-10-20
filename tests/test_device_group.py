import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

from leafroute.device_group import DeviceGroup
from leafroute.engine import Engine


TARGETS = [{'id': f'phone-{i}', 'name': f'Test {i}', 'ios': '18.1.1', 'transport': 'USB'} for i in (1, 2)]


class Phone:
    def __init__(self):
        self.info = None
        self.active = False
        self.positions = []
        self.clears = self.closes = 0
        self.fail_connect = self.fail_set = self.fail_clear = False
        self.wait_connect = self.wait_set = None

    async def connect(self, target):
        self.info = target
        if self.wait_connect:
            await self.wait_connect.wait()
        if self.fail_connect:
            raise OSError('test failure')

    async def set(self, position):
        self.active = True
        if self.wait_set:
            await self.wait_set.wait()
        if self.fail_set:
            raise OSError('test failure')
        self.positions.append(list(position))

    async def clear(self):
        self.clears += 1
        if self.fail_clear:
            raise OSError('test failure')
        self.active = False

    async def close(self):
        self.closes += 1
        self.info = None


class MemoryStore:
    warning = ''

    def __init__(self):
        self.data = {'favorites': [], 'routes': [], 'home': [25, 121]}

    def save(self, data):
        self.data = copy.deepcopy(data)


class SyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.phones = [Phone(), Phone()]
        pool = iter(self.phones)
        self.group = DeviceGroup(lambda: next(pool))
        self.engine = Engine(MemoryStore(), self.group)
        self.engine.devices = copy.deepcopy(TARGETS)

    async def connect(self):
        await self.engine.command('connect', {'ids': [t['id'] for t in TARGETS]})

    async def test_default_engine_uses_group_and_single_phone_remains_supported(self):
        self.assertIsInstance(Engine(MemoryStore()).device, DeviceGroup)
        await self.group.connect(TARGETS[0])
        self.assertEqual(self.group.info['count'], 1)
        self.assertFalse(self.group.active)
        await self.group.close()

    async def test_two_connections_send_no_coordinates_and_snapshot_is_independent(self):
        await self.connect()
        state = self.engine.snapshot()
        self.assertTrue(state['connected'])
        self.assertEqual(len(state['connected_devices']), 2)
        self.assertTrue(all(d['connected'] for d in state['connected_devices']))
        self.assertEqual([p.positions for p in self.phones], [[], []])
        state['connected_devices'][0]['connected'] = False
        self.assertTrue(self.group.states[0]['connected'])

    async def test_position_home_joystick_and_nudge_send_identical_coordinates(self):
        await self.connect()
        for action, payload in [('position', {'position': [25.1, 121.1]}), ('go-home', {}),
                                ('joystick', {'bearing': 90, 'speed': 8}), ('nudge', {'bearing': 0})]:
            await self.engine.command(action, payload)
            self.assertEqual(self.phones[0].positions, self.phones[1].positions)
            self.assertEqual(self.phones[0].positions[-1], self.engine.position)
        self.assertEqual(len(self.phones[0].positions), 4)

    async def test_route_speed_pause_and_stop_apply_to_both(self):
        await self.connect()
        await self.engine.command('route', {'points': [[25, 121], [25.01, 121.01]]})
        await self.engine.command('settings', {'speed': 12})
        await self.engine.command('start', {})
        await self.engine.advance(1)
        self.assertEqual(self.phones[0].positions, self.phones[1].positions)
        traveled = self.engine.traveled
        await self.engine.command('pause', {})
        await self.engine.advance(1)
        self.assertEqual(self.engine.traveled, traveled)
        await self.engine.command('restore', {})
        self.assertEqual([p.clears for p in self.phones], [1, 1])
        self.assertFalse(self.group.active)

    async def test_one_send_failure_pauses_all_and_does_not_claim_shared_position(self):
        await self.connect()
        await self.engine.command('route', {'points': [[25, 121], [25.01, 121.01]]})
        await self.engine.command('start', {})
        original = list(self.engine.position)
        progress = self.engine.progress
        self.phones[1].fail_set = True
        await self.engine.advance(1)
        self.assertEqual(self.engine.status, 'paused')
        self.assertFalse(self.engine.connected)
        self.assertEqual(self.engine.position, original)
        self.assertEqual(self.engine.progress, progress)
        self.assertIn('部分手機', self.engine.message)
        self.assertTrue(self.engine.snapshot()['connected_devices'][1]['error'])
        counts = [len(p.positions) for p in self.phones]
        await self.engine.advance(1)
        self.assertEqual([len(p.positions) for p in self.phones], counts)
        await self.engine.command('disconnect', {})
        self.assertEqual([p.clears for p in self.phones], [1, 1])
        self.assertTrue(all(p.closes for p in self.phones))

    async def test_send_is_concurrent_and_timed_out_phone_stops_whole_group(self):
        await self.connect()
        waiting = asyncio.Event()
        self.phones[0].wait_set = waiting
        task = asyncio.create_task(self.group.set([25.1, 121.1]))
        for _ in range(20):
            if self.phones[1].positions:
                break
            await asyncio.sleep(.001)
        self.assertEqual(self.phones[1].positions, [[25.1, 121.1]])
        waiting.set()
        await task
        self.phones[0].wait_set = None
        self.phones[1].set = AsyncMock(side_effect=TimeoutError())
        with self.assertRaisesRegex(ValueError, 'Test 2'):
            await self.group.set([25.2, 121.2])
        self.assertFalse(self.group.states[1]['connected'])

    async def test_clear_attempts_both_even_when_first_fails_and_retains_unknown_state(self):
        await self.connect()
        await self.group.set([25, 121])
        self.phones[0].fail_clear = True
        with self.assertRaisesRegex(ValueError, '未能確認所有手機已還原'):
            await self.engine.command('restore', {})
        self.assertEqual([p.clears for p in self.phones], [1, 1])
        self.assertFalse(self.engine.connected)
        self.assertTrue(self.group.states[0]['simulation_active'])
        self.assertFalse(self.group.states[1]['simulation_active'])
        self.assertTrue(self.group.active)

    async def test_stop_after_partial_send_still_attempts_both_and_only_recovers_after_ack(self):
        await self.connect()
        self.phones[1].fail_set = True
        with self.assertRaises(ValueError):
            await self.engine.command('position', {'position': [25.1, 121.1]})
        self.assertFalse(self.engine.connected)
        await self.engine.command('restore', {})
        self.assertEqual([p.clears for p in self.phones], [1, 1])
        self.assertFalse(self.group.active)
        self.assertTrue(self.engine.connected)
        self.assertTrue(all(s['connected'] for s in self.group.states))
        self.assertEqual(self.engine.connection_phase, 'connected')

    async def test_disconnect_failure_still_closes_both(self):
        await self.connect()
        await self.group.set([25, 121])
        self.phones[0].fail_clear = True
        with self.assertRaises(ValueError):
            await self.engine.command('disconnect', {})
        self.assertTrue(all(p.closes for p in self.phones))
        self.assertFalse(self.engine.connected)
        self.assertTrue(self.engine.snapshot()['simulation_active'])
        self.assertTrue(self.engine.snapshot()['connected_devices'][0]['simulation_active'])

    async def test_second_connect_failure_closes_first_and_second_without_sending(self):
        self.phones[1].fail_connect = True
        with self.assertRaisesRegex(ValueError, 'Test 2'):
            await self.connect()
        self.assertFalse(self.engine.connected)
        self.assertTrue(all(p.closes for p in self.phones))
        self.assertTrue(all(not s['connected'] for s in self.group.states))
        self.assertEqual([p.positions for p in self.phones], [[], []])

    async def test_cancel_during_second_connection_closes_both(self):
        self.phones[1].wait_connect = asyncio.Event()
        task = asyncio.create_task(self.connect())
        for _ in range(30):
            if self.phones[1].info:
                break
            await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(all(p.closes for p in self.phones))
        self.assertFalse(self.engine.connected)

    async def test_duplicates_unknown_ids_and_more_than_two_are_rejected_before_connection(self):
        for ids in [['phone-1', 'phone1'], ['phone-1', 'missing'], ['phone-1'] * 3, [], 'phone-1', [True]]:
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                await self.engine.command('connect', {'ids': ids})
        self.assertEqual([p.info for p in self.phones], [None, None])

    async def test_auto_connect_requires_explicit_selection_then_prepares_each_tunnel(self):
        with patch('leafroute.engine.discover', AsyncMock(return_value={'devices': TARGETS})), \
             patch('leafroute.engine.ensure_tunnel', AsyncMock()) as tunnel:
            await self.engine.command('auto-connect', {'transport': 'USB'})
            await self.engine.connection_task
            self.assertEqual(self.engine.connection_phase, 'choose')
            tunnel.assert_not_awaited()
            await self.engine.command('auto-connect', {'transport': 'USB', 'ids': ['phone-1', 'phone-2']})
            await self.engine.connection_task
            self.assertTrue(self.engine.connected)
            self.assertEqual([c.args[0] for c in tunnel.await_args_list], ['phone-1', 'phone-2'])
            self.assertEqual([p.positions for p in self.phones], [[], []])

    async def test_unplugged_selected_phone_never_falls_back_to_other_phone(self):
        with patch('leafroute.engine.discover', AsyncMock(return_value={'devices': [TARGETS[0]]})), \
             patch('leafroute.engine.ensure_tunnel', AsyncMock()) as tunnel:
            await self.engine.command('auto-connect', {'ids': ['phone-1', 'phone-2']})
            await self.engine.connection_task
            self.assertEqual(self.engine.connection_phase, 'error')
            self.assertFalse(self.engine.connected)
            tunnel.assert_not_awaited()
            self.assertEqual([p.info for p in self.phones], [None, None])

    async def test_single_manual_ip_cannot_be_used_for_two_phones(self):
        with self.assertRaisesRegex(ValueError, '單一 Wi-Fi IP'):
            await self.engine.command('auto-connect', {'transport': 'Network', 'host': '10.1.2.3', 'ids': ['phone-1', 'phone-2']})
        self.assertIsNone(self.engine.connection_task)

    async def test_shutdown_clears_and_closes_both(self):
        await self.connect()
        await self.group.set([25, 121])
        await self.engine.shutdown()
        self.assertEqual([p.clears for p in self.phones], [1, 1])
        self.assertTrue(all(p.closes for p in self.phones))

    async def test_reconnect_same_phones_can_clear_uncertain_position(self):
        await self.connect()
        await self.group.set([25, 121])
        self.phones[0].fail_clear = True
        with self.assertRaises(ValueError):
            await self.engine.command('disconnect', {})
        self.group.factory = Phone
        with self.assertRaisesRegex(ValueError, '原手機'):
            await self.group.connect_many([{**TARGETS[0], 'id': 'different'}])
        await self.group.connect_many(TARGETS)
        self.assertTrue(self.group.active)
        await self.group.clear()
        self.assertFalse(self.group.active)
