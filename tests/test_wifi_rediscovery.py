import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from leafroute.wifi import advertised_wifi_addresses, resolve_remembered_wifi, subnet_hosts, local_wifi_addresses

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
    def setUp(self):
        self.local_patch = patch('leafroute.wifi.local_wifi_addresses', AsyncMock(return_value=[]))
        self.local_patch.start()
        self.addCleanup(self.local_patch.stop)

    async def test_no_announcement_finds_only_selected_phone_on_local_network(self):
        correct, wrong = Client('phone1'), Client('other-phone')
        async def open_phone(host, identity):
            self.assertEqual(identity, TARGET['id'])
            return correct if host == '10.1.2.99' else wrong
        report = Mock()
        with patch('leafroute.wifi.discover_wifi_ip', AsyncMock(side_effect=ValueError())), \
             patch('leafroute.wifi.advertised_wifi_addresses', AsyncMock(return_value=[])), \
             patch('leafroute.wifi.local_wifi_addresses', AsyncMock(return_value=['10.1.2.8', '10.1.2.99'])) as local, \
             patch('leafroute.wifi.open_paired_wifi', open_phone):
            result = await resolve_remembered_wifi(TARGET, report)
        self.assertEqual(result['host'], '10.1.2.99')
        self.assertTrue(correct.closed and wrong.closed)
        local.assert_awaited_once_with(TARGET['host'])
        self.assertIn('區網', report.call_args.args[0])

    async def test_local_search_failure_retains_clear_error(self):
        with patch('leafroute.wifi.discover_wifi_ip', AsyncMock(side_effect=ValueError())), \
             patch('leafroute.wifi.advertised_wifi_addresses', AsyncMock(return_value=[])), \
             patch('leafroute.wifi.local_wifi_addresses', AsyncMock(side_effect=OSError())):
            with self.assertRaisesRegex(ValueError, '手動輸入新 IP'):
                await resolve_remembered_wifi(TARGET)

    async def test_cancelled_local_search_propagates(self):
        with patch('leafroute.wifi.discover_wifi_ip', AsyncMock(side_effect=ValueError())), \
             patch('leafroute.wifi.advertised_wifi_addresses', AsyncMock(return_value=[])), \
             patch('leafroute.wifi.local_wifi_addresses', AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await resolve_remembered_wifi(TARGET)

    def test_subnets_ignore_vpn_public_linklocal_self_and_respect_small_prefix(self):
        hosts = subnet_hosts([{'IPAddress': '26.1.2.3', 'PrefixLength': 8},
                              {'IPAddress': '169.254.2.3', 'PrefixLength': 16},
                              {'IPAddress': '8.8.8.8', 'PrefixLength': 24},
                              {'IPAddress': '192.168.0.2', 'PrefixLength': 30}])
        self.assertEqual(hosts, ['192.168.0.1'])

    def test_subnets_are_bounded_and_prioritize_remembered_network(self):
        hosts = subnet_hosts([{'IPAddress': '10.1.2.1', 'PrefixLength': 8},
                              {'IPAddress': '10.1.2.2', 'PrefixLength': 24},
                              {'IPAddress': '172.16.0.2', 'PrefixLength': 16},
                              {'IPAddress': '192.168.0.110', 'PrefixLength': 24}], '192.168.0.106')
        self.assertTrue(hosts[0].startswith('192.168.0.'))
        self.assertLessEqual(len(hosts), 508)
        self.assertNotIn('10.1.2.1', hosts)
        self.assertNotIn('10.1.2.2', hosts)
        self.assertFalse(any(h.startswith('172.16.') for h in hosts))

    async def test_local_tcp_probe_uses_only_apple_port_and_closes_sockets(self):
        self.local_patch.stop()
        writer = Mock()
        writer.wait_closed = AsyncMock()
        with patch('leafroute.wifi.local_wifi_hosts', return_value=['10.1.2.8']), \
             patch('leafroute.wifi.asyncio.open_connection', AsyncMock(return_value=(None, writer))) as connect:
            self.assertEqual(await local_wifi_addresses(TARGET['host']), ['10.1.2.8'])
        connect.assert_awaited_once_with('10.1.2.8', 62078)
        writer.close.assert_called_once()
        writer.wait_closed.assert_awaited_once()

    async def announcements(self, responses):
        queue=asyncio.Queue()
        transport=Mock()
        self.last_transport=transport
        sent=[]
        counts={}
        async def send(transports, query):
            sent.append(query)
            counts[query]=counts.get(query,0)+1
            packets=responses.get(query,[])
            if callable(packets):packets=packets(counts[query])
            for packet in packets:
                await queue.put((packet,('10.1.2.3',5353)))
        def parse(packet):
            if isinstance(packet,BaseException):raise packet
            return packet
        module=SimpleNamespace(MOBDEV2_SERVICE_NAME='_apple-mobdev2._tcp.local.',QTYPE_PTR=12,QTYPE_SRV=33,QTYPE_A=1,
                               _open_mdns_sockets=AsyncMock(return_value=([(transport,None)],queue)),
                               _send_query_all=send,build_query=lambda name,kind,unicast=False:(name,kind),parse_mdns_message=parse)
        with patch.dict(sys.modules,{'pymobiledevice3.bonjour':module}):
            result=await advertised_wifi_addresses(timeout=.04,retry_interval=.01)
        transport.close.assert_called_once()
        return result,sent

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
        service='_apple-mobdev2._tcp.local.'
        name='phone.'+service
        records=[{'type':12,'name':service,'ptrdname':name},{'type':33,'name':name,'target':'phone.local.','port':32498}]
        records += [{'type':1,'name':'phone.local.','address':ip} for ip in ['10.1.2.3','10.1.2.3','8.8.8.8','127.0.0.1','invalid']+[f'10.1.2.{i}' for i in range(4,30)]]
        result,_=await self.announcements({(service,12):[records]})
        self.assertEqual(len(result),12)
        self.assertEqual(len(set(result)),12)
        self.assertTrue(set(result)<=set(f'10.1.2.{i}' for i in range(3,30)))

    async def test_non_lockdown_advertisement_port_is_not_dropped(self):
        service='_apple-mobdev2._tcp.local.'
        name='phone.'+service
        result,sent=await self.announcements({
            (service,12):[[{'type':12,'name':service,'ptrdname':name}]],
            (name,33):[[{'type':33,'name':name,'target':'phone.local.','port':32498}]],
            ('phone.local.',1):[[{'type':1,'name':'phone.local.','address':'192.168.0.104'}]]})
        self.assertEqual(result,['192.168.0.104'])
        self.assertIn((name,33),sent)
        self.assertIn(('phone.local.',1),sent)

    async def test_retries_resolve_late_answers_and_ignore_unrelated_addresses(self):
        service='_apple-mobdev2._tcp.local.'
        name='phone.'+service
        records=[{'type':12,'name':service,'ptrdname':name},{'type':33,'name':name,'target':'phone.local.','port':32498},
                 {'type':1,'name':'other.local.','address':'192.168.0.55'},{'type':1,'name':'phone.local.','address':'192.168.0.106'}]
        result,sent=await self.announcements({(service,12):lambda count:[records] if count>=2 else []})
        self.assertEqual(result,['192.168.0.106'])
        self.assertGreaterEqual(sent.count((service,12)),2)

    async def test_malformed_packet_does_not_break_discovery(self):
        service='_apple-mobdev2._tcp.local.'
        result,_=await self.announcements({(service,12):[ValueError('malformed'),[]]})
        self.assertEqual(result,[])

    async def test_cancelled_discovery_closes_sockets(self):
        service='_apple-mobdev2._tcp.local.'
        with self.assertRaises(asyncio.CancelledError):
            await self.announcements({(service,12):[asyncio.CancelledError()]})
        self.last_transport.close.assert_called_once()

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
