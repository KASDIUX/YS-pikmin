import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from leafroute.engine import Engine
from leafroute.device_group import DeviceGroup
from leafroute.store import Store, wifi_profiles
from leafroute.wifi import discover_wifi_ip
from test_device_group import MemoryStore, Phone, TARGETS
from test_leafroute import temporary_directory

PROFILES = [{**t, 'host': f'10.1.2.{i}', 'transport': 'WiFiIP'} for i, t in enumerate(TARGETS, 3)]


class Client:
    def __init__(self, target):
        self.udid = target['id']
        self.all_values = {'DeviceName': target['name']}
        self.product_version = target['ios']

    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.store.data['wifi_devices'] = copy.deepcopy(PROFILES)
        self.store.data['wifi_device'] = copy.deepcopy(PROFILES[0])
        self.engine = Engine(self.store, DeviceGroup(Phone))

    async def auto(self, payload, discovered):
        with patch('leafroute.engine.discover', AsyncMock(return_value={'devices': discovered})), \
             patch('leafroute.engine.ensure_tunnel', AsyncMock()):
            await self.engine.command('auto-connect', {'transport': 'Network', **payload})
            await self.engine.connection_task

    async def test_old_single_phone_migrates_and_keeps_favorites_routes_home(self):
        with temporary_directory() as folder:
            path = Path(folder) / 'library.json'
            data = {'favorites': [{'id': 'f', 'name': 'keep', 'position': [25, 121]}],
                    'routes': [], 'home': [25, 121], 'wifi_device': PROFILES[0]}
            path.write_text(json.dumps(data), encoding='utf-8')
            store = Store(path)
            self.assertEqual(store.data['wifi_devices'], [PROFILES[0]])
            self.assertEqual(store.data['favorites'][0]['name'], 'keep')
            self.assertEqual(store.data['home'], [25, 121])
            store.save(store.data)
            self.assertEqual(Store(path).data['wifi_devices'], [PROFILES[0]])

    async def test_profiles_deduplicate_identity_and_do_not_keep_pairing_secrets(self):
        records = wifi_profiles({'wifi_devices': [PROFILES[0], {**PROFILES[0], 'id': 'phone1', 'pair_record': 'not saved'}, None],
                                 'wifi_device': PROFILES[0]})
        self.assertEqual(len(records), 1)
        self.assertNotIn('pair_record', records[0])

    async def test_switching_phone_updates_only_its_own_ip_and_keeps_both_on_reload(self):
        with temporary_directory() as folder:
            store = Store(Path(folder) / 'library.json')
            e = Engine(store, DeviceGroup(Phone))
            e._remember_phones(PROFILES)
            e._remember_phones([{**PROFILES[1], 'host': '10.1.2.9'}])
            records = Store(store.path).data['wifi_devices']
            self.assertEqual({d['id']: d['host'] for d in records}, {'phone-1': '10.1.2.3', 'phone-2': '10.1.2.9'})
            e._remember_phones([TARGETS[1]])
            self.assertEqual(next(d['host'] for d in wifi_profiles(store.data) if d['id'] == 'phone-2'), '10.1.2.9')

    async def test_auto_ignores_stale_host_and_uses_selected_live_phone(self):
        live = {**TARGETS[1], 'transport': 'Network'}
        with patch('leafroute.engine.discover_wifi_ip', AsyncMock()) as manual:
            await self.auto({'ip_mode': 'auto', 'host': '8.8.8.8', 'wifi_id': 'phone-2'}, [live])
            manual.assert_not_awaited()
        self.assertTrue(self.engine.connected)
        self.assertEqual(self.engine.device.states[0]['id'], 'phone-2')

    async def test_manual_selected_phone_is_strict_and_other_profile_is_not_substituted(self):
        probe = AsyncMock(return_value={**PROFILES[1], 'host': '10.1.2.8'})
        with patch('leafroute.engine.discover_wifi_ip', probe):
            await self.auto({'ip_mode': 'manual', 'host': '10.1.2.8', 'wifi_id': 'phone-2'}, [])
        probe.assert_awaited_once_with('10.1.2.8', PROFILES[1], selected_id='phone-2')
        self.assertEqual(self.engine.device.states[0]['id'], 'phone-2')

    async def test_auto_fallback_verifies_remembered_ip_and_identity(self):
        probe = AsyncMock(return_value=PROFILES[1])
        with patch('leafroute.engine.discover_wifi_ip', probe):
            await self.auto({'ip_mode': 'auto', 'wifi_id': 'phone-2'}, [])
        probe.assert_awaited_once_with('10.1.2.4', {**PROFILES[1], 'remembered_ip': True}, selected_id='phone-2')
        self.assertTrue(self.engine.connected)

    async def test_new_phone_auto_does_not_fall_back_to_previous_phone(self):
        with patch('leafroute.engine.discover_wifi_ip', AsyncMock()) as probe:
            await self.auto({'ip_mode': 'auto', 'wifi_new': True}, [{**TARGETS[0], 'transport': 'Network'}])
            probe.assert_not_awaited()
        self.assertFalse(self.engine.connected)
        self.assertEqual(self.engine.connection_phase, 'error')

    async def test_auto_can_select_two_remembered_phones_after_discovery(self):
        live = [{**t, 'transport': 'Network'} for t in TARGETS]
        await self.auto({'ip_mode': 'auto'}, live)
        self.assertEqual(self.engine.connection_phase, 'choose')
        await self.auto({'ip_mode': 'auto', 'ids': ['phone-1', 'phone-2']}, live)
        self.assertTrue(self.engine.connected)
        self.assertEqual(len(self.engine.device.states), 2)

    async def test_manual_new_phone_does_not_receive_old_known_profile(self):
        new = {**PROFILES[1], 'id': 'new-phone'}
        probe = AsyncMock(return_value=new)
        with patch('leafroute.engine.discover_wifi_ip', probe):
            await self.auto({'ip_mode': 'manual', 'host': new['host'], 'wifi_new': True}, [])
        probe.assert_awaited_once_with(new['host'], None, selected_id=None)
        self.assertEqual(len(wifi_profiles(self.store.data)), 3)

    async def test_invalid_choice_mode_or_empty_manual_ip_starts_no_job(self):
        for payload in [{'wifi_id': 'gone'}, {'wifi_new': 'yes'}, {'ip_mode': 'other'},
                        {'ip_mode': 'manual', 'host': ''}, {'wifi_id': 'phone-1', 'wifi_new': True}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                await self.engine.command('auto-connect', {'transport': 'Network', **payload})
            self.assertIsNone(self.engine.connection_task)

    async def test_selected_identity_probe_never_tries_other_phone_or_usb_fallback(self):
        probe = AsyncMock(side_effect=OSError('different phone or unreachable'))
        with patch('leafroute.wifi.open_paired_wifi', probe), \
             patch('leafroute.device.discover', AsyncMock()) as scan:
            with self.assertRaises(ValueError):
                await discover_wifi_ip('10.1.2.3', PROFILES, selected_id='phone-2')
            scan.assert_not_awaited()
        probe.assert_awaited_once_with('10.1.2.3', 'phone-2')

    async def test_selected_probe_success_returns_actual_phone_and_current_host(self):
        with patch('leafroute.wifi.open_paired_wifi', AsyncMock(return_value=Client(PROFILES[1]))):
            result = await discover_wifi_ip('10.1.2.9', PROFILES, selected_id='phone-2')
        self.assertEqual(result['id'], 'phone-2')
        self.assertEqual(result['host'], '10.1.2.9')
