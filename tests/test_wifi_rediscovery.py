import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from leafroute.wifi import advertised_wifi_addresses, resolve_remembered_wifi

TARGET={'id':'phone-1','name':'Old phone','ios':'18.1.1','transport':'WiFiIP','host':'10.1.2.3'}


class Client:
    def __init__(self, identity):
        self.udid=identity
        self.all_values={'DeviceName':'Actual phone'}
        self.product_version='18.1.1'
        self.closed=False
    async def __aenter__(self):return self
    async def __aexit__(self,*_):self.closed=True


class RediscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_ip_is_verified_without_browsing(self):
        probe=AsyncMock(return_value=TARGET)
        with patch('leafroute.wifi.discover_wifi_ip',probe), patch('leafroute.wifi.advertised_wifi_addresses',AsyncMock()) as browse:
            self.assertEqual(await resolve_remembered_wifi(TARGET),TARGET)
        probe.assert_awaited_once_with('10.1.2.3',TARGET,selected_id='phone-1')
        browse.assert_not_awaited()

    async def test_changed_ip_accepts_only_selected_identity_and_closes_probe(self):
        wrong,correct=Client('other-phone'),Client('phone1')
        async def open_phone(host,identity):
            self.assertEqual(identity,'phone-1')
            return wrong if host=='10.1.2.8' else correct
        with patch('leafroute.wifi.discover_wifi_ip',AsyncMock(side_effect=ValueError('old IP unavailable'))), \
             patch('leafroute.wifi.advertised_wifi_addresses',AsyncMock(return_value=['10.1.2.8','10.1.2.99'])), \
             patch('leafroute.wifi.open_paired_wifi',open_phone):
            result=await resolve_remembered_wifi(TARGET)
        self.assertEqual(result['host'],'10.1.2.99')
        self.assertEqual(result['id'],'phone1')
        self.assertTrue(wrong.closed and correct.closed)

    async def test_no_saved_ip_can_use_advertisements(self):
        with patch('leafroute.wifi.discover_wifi_ip',AsyncMock()) as old, \
             patch('leafroute.wifi.advertised_wifi_addresses',AsyncMock(return_value=['10.1.2.99'])), \
             patch('leafroute.wifi.open_paired_wifi',AsyncMock(return_value=Client('phone-1'))):
            result=await resolve_remembered_wifi({**TARGET,'host':''})
        old.assert_not_awaited()
        self.assertEqual(result['host'],'10.1.2.99')

    async def test_unavailable_or_wrong_phone_never_becomes_selected_device(self):
        for failure in (OSError(),None):
            with patch('leafroute.wifi.discover_wifi_ip',AsyncMock(side_effect=ValueError('old IP failed'))), \
                 patch('leafroute.wifi.advertised_wifi_addresses',AsyncMock(return_value=['10.1.2.8'])), \
                 patch('leafroute.wifi.open_paired_wifi',AsyncMock(side_effect=failure,return_value=Client('other-phone'))):
                with self.assertRaisesRegex(ValueError,'重新搜尋也未找到'):
                    await resolve_remembered_wifi(TARGET)

    async def test_browse_failure_reports_manual_fallback(self):
        with patch('leafroute.wifi.discover_wifi_ip',AsyncMock(side_effect=ValueError('old IP failed'))), \
             patch('leafroute.wifi.advertised_wifi_addresses',AsyncMock(side_effect=OSError())):
            with self.assertRaisesRegex(ValueError,'手動輸入新 IP'):
                await resolve_remembered_wifi(TARGET)

    async def test_cancellation_does_not_probe_more_devices(self):
        with patch('leafroute.wifi.discover_wifi_ip',AsyncMock(side_effect=asyncio.CancelledError())), \
             patch('leafroute.wifi.advertised_wifi_addresses',AsyncMock()) as browse:
            with self.assertRaises(asyncio.CancelledError):
                await resolve_remembered_wifi(TARGET)
        browse.assert_not_awaited()

    async def test_advertisements_are_bounded_deduplicated_private_ipv4_only(self):
        addresses=[SimpleNamespace(ip=ip) for ip in ['10.1.2.3','10.1.2.3','8.8.8.8','127.0.0.1','fe80::1','invalid']+[f'10.1.2.{i}' for i in range(4,30)]]
        services=[SimpleNamespace(port=1234,addresses=[SimpleNamespace(ip='10.9.9.9')]),SimpleNamespace(port=62078,addresses=addresses)]
        browse=AsyncMock(return_value=services)
        with patch.dict(sys.modules,{'pymobiledevice3.bonjour':SimpleNamespace(browse_mobdev2=browse)}):
            result=await advertised_wifi_addresses()
        self.assertEqual(result,[f'10.1.2.{i}' for i in range(3,15)])
        browse.assert_awaited_once_with(timeout=2)

    async def test_success_cancels_and_awaits_other_pending_probes(self):
        entered,cancelled=asyncio.Event(),asyncio.Event()
        async def open_phone(host, identity):
            if host=='10.1.2.8':
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            await entered.wait()
            return Client('phone-1')
        with patch('leafroute.wifi.discover_wifi_ip',AsyncMock(side_effect=ValueError())), \
             patch('leafroute.wifi.advertised_wifi_addresses',AsyncMock(return_value=['10.1.2.8','10.1.2.99'])), \
             patch('leafroute.wifi.open_paired_wifi',open_phone):
            result=await resolve_remembered_wifi(TARGET)
        self.assertEqual(result['host'],'10.1.2.99')
        self.assertTrue(cancelled.is_set())
