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

    async def test_alias_preserves_actual_name_ip_and_other_phone(self):
        before = copy.deepcopy(self.store.data)
        await self.engine.command('wifi-profile-rename', {'id':'phone1', 'name':'  我的手機  '})
        profiles = wifi_profiles(self.store.data)
        self.assertEqual(profiles[0], {**PROFILES[0], 'alias':'我的手機'})
        self.assertEqual(profiles[1], PROFILES[1])
        self.assertEqual(self.store.data['wifi_device'], profiles[0])
        self.assertEqual(self.store.data['favorites'], before['favorites'])
        self.assertEqual(self.store.data['home'], before['home'])
        self.assertFalse(self.engine.connected)

    async def test_alias_survives_reconnect_metadata_and_restart(self):
        with temporary_directory() as folder:
            store = Store(Path(folder)/'library.json')
            engine = Engine(store, DeviceGroup(Phone))
            engine._remember_phones(PROFILES)
            await engine.command('wifi-profile-rename', {'id':'phone-1','name':'家人的手機'})
            engine._remember_phones([{**PROFILES[0], 'host':'10.1.2.8','name':'New actual iPhone name'}])
            restored = Store(store.path)
            profile = next(p for p in wifi_profiles(restored.data) if p['id']=='phone-1')
            self.assertEqual(profile['alias'], '家人的手機')
            self.assertEqual(profile['name'], 'New actual iPhone name')
            self.assertEqual(profile['host'], '10.1.2.8')
            self.assertEqual(restored.data['wifi_device']['alias'], '家人的手機')

    async def test_delete_updates_legacy_pointer_without_resurrection(self):
        with temporary_directory() as folder:
            store = Store(Path(folder)/'library.json')
            engine = Engine(store, DeviceGroup(Phone))
            engine._remember_phones(PROFILES)
            await engine.command('wifi-profile-delete', {'id':'phone1'})
            self.assertEqual(wifi_profiles(Store(store.path).data), [PROFILES[1]])
            self.assertEqual(store.data['wifi_device']['id'], 'phone-2')
            await engine.command('wifi-profile-delete', {'id':'phone-2'})
            self.assertEqual(wifi_profiles(Store(store.path).data), [])
            self.assertNotIn('wifi_device', store.data)
            engine._remember_phones([PROFILES[0]])
            self.assertEqual(wifi_profiles(Store(store.path).data), [PROFILES[0]])

    async def test_deleting_non_legacy_profile_keeps_other_name_and_ip(self):
        self.store.data['wifi_devices'][0]['alias']='保留的手機'
        self.store.data['favorite_categories']=[{'id':'point','name':'純點'}]
        before = copy.deepcopy(self.store.data)
        await self.engine.command('wifi-profile-delete', {'id':'phone-2'})
        profiles = wifi_profiles(self.store.data)
        self.assertEqual(profiles, [{**PROFILES[0], 'alias':'保留的手機'}])
        for key in ('favorites','routes','home','favorite_categories'):
            self.assertEqual(self.store.data[key], before[key])

    async def test_legacy_only_profile_rename_and_delete(self):
        self.store.data.pop('wifi_devices')
        await self.engine.command('wifi-profile-rename', {'id':'phone-1','name':'舊手機'})
        self.assertEqual(wifi_profiles(self.store.data), [{**PROFILES[0], 'alias':'舊手機'}])
        await self.engine.command('wifi-profile-delete', {'id':'phone-1'})
        self.assertEqual(wifi_profiles(self.store.data), [])

    async def test_invalid_alias_or_identity_never_changes_memory(self):
        before = copy.deepcopy(self.store.data)
        for name in ('', ' ', 'a'*41, 'line\nbreak', None, []):
            with self.assertRaises(ValueError):
                await self.engine.command('wifi-profile-rename', {'id':'phone-1','name':name})
            self.assertEqual(self.store.data, before)
        for action in ('wifi-profile-rename','wifi-profile-delete'):
            for identity in (None, [], '', 'unknown'):
                with self.assertRaises(ValueError):
                    await self.engine.command(action, {'id':identity,'name':'Test'})
                self.assertEqual(self.store.data, before)

    async def test_active_and_connecting_sessions_reject_profile_changes(self):
        before = copy.deepcopy(self.store.data)
        for mode in ('connected','active','connecting'):
            self.engine.connected = mode=='connected'
            self.engine.device.states = [{'simulation_active':mode=='active'}]
            task = None
            if mode=='connecting':
                import asyncio
                task=asyncio.create_task(asyncio.sleep(100))
                self.engine.connection_task=task
            try:
                for action in ('wifi-profile-rename','wifi-profile-delete'):
                    with self.assertRaises(ValueError):
                        await self.engine.command(action, {'id':'phone-1','name':'Test'})
                    self.assertEqual(self.store.data, before)
            finally:
                if task:
                    task.cancel()
                    try: await task
                    except BaseException: pass
                    self.engine.connection_task=None

    async def test_failed_save_does_not_change_in_memory_profiles(self):
        before=copy.deepcopy(self.store.data)
        with patch.object(self.store, 'save', side_effect=OSError('test write failure')):
            for action in ('wifi-profile-rename','wifi-profile-delete'):
                with self.assertRaises(ValueError):
                    await self.engine.command(action, {'id':'phone-1','name':'Test'})
                self.assertEqual(self.store.data,before)

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
        with patch('leafroute.engine.resolve_remembered_wifi', probe):
            await self.auto({'ip_mode': 'auto', 'wifi_id': 'phone-2'}, [])
        probe.assert_awaited_once()
        self.assertEqual(probe.call_args.args[0], {**PROFILES[1], 'remembered_ip': True})
        self.assertTrue(self.engine.connected)

    async def test_changed_ip_updates_selected_profile_and_preserves_alias(self):
        self.store.data['wifi_devices'][1]['alias']='家人的手機'
        fresh={**PROFILES[1], 'host':'10.1.2.99'}
        with patch('leafroute.engine.resolve_remembered_wifi', AsyncMock(return_value=fresh)):
            await self.auto({'ip_mode':'auto','wifi_id':'phone-2'}, [])
        records=wifi_profiles(self.store.data)
        selected=next(p for p in records if p['id']=='phone-2')
        self.assertEqual(selected['host'],'10.1.2.99')
        self.assertEqual(selected['alias'],'家人的手機')
        self.assertEqual(next(p for p in records if p['id']=='phone-1'),PROFILES[0])

    async def test_missing_saved_ip_can_search_and_connect(self):
        self.store.data['wifi_devices'][1]['host']=''
        fresh={**PROFILES[1], 'host':'10.1.2.99'}
        with patch('leafroute.engine.resolve_remembered_wifi', AsyncMock(return_value=fresh)):
            await self.auto({'ip_mode':'auto','wifi_id':'phone-2'}, [])
        self.assertTrue(self.engine.connected)
        self.assertEqual(next(p for p in wifi_profiles(self.store.data) if p['id']=='phone-2')['host'],'10.1.2.99')

    async def test_failed_rediscovery_does_not_overwrite_saved_phone_or_ip(self):
        before=copy.deepcopy(self.store.data)
        with patch('leafroute.engine.resolve_remembered_wifi',AsyncMock(side_effect=ValueError('not found'))):
            await self.auto({'ip_mode':'auto','wifi_id':'phone-2'}, [])
        self.assertFalse(self.engine.connected)
        self.assertEqual(self.engine.connection_phase,'error')
        self.assertEqual(self.store.data,before)

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
