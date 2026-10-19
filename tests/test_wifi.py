import asyncio,unittest,json
from unittest.mock import AsyncMock,patch
from pathlib import Path
import test_home
from leafroute.wifi import private_ipv4,discover_wifi_ip,WifiTunnel
from leafroute.store import Store

class WifiTests(unittest.IsolatedAsyncioTestCase):
    setUp=test_home.HomeTests.setUp
    tearDown=test_home.HomeTests.tearDown
    async def test_only_explicit_private_ipv4(self):
        for host in ['192.168.0.103','10.1.2.3','172.16.1.2']:self.assertEqual(private_ipv4(host),host)
        for host in ['127.0.0.1','8.8.8.8','localhost','http://192.168.0.1','192.168.0.1:80','224.0.0.1',None,{},'::1']:
            with self.assertRaises(ValueError):private_ipv4(host)
    async def test_initial_requires_trusted_identity(self):
        with patch('leafroute.device.discover',AsyncMock(return_value={'devices':[]})):
            with self.assertRaises(ValueError):await discover_wifi_ip('192.168.0.103')
    async def test_invalid_ip_does_not_start_job(self):
        with self.assertRaises(ValueError):await self.engine.command('auto-connect',{'transport':'Network','host':'8.8.8.8'})
        self.assertEqual(self.engine.connection_phase,'idle')
        self.assertIsNone(self.engine.connection_task)
    async def test_ip_job_uses_verified_target_and_skips_shared_usb_tunnel(self):
        target={'id':'trusted','name':'Phone','ios':'26.6.2','host':'192.168.0.103','transport':'WiFiIP'}
        async def connect(t):self.device.info=t
        self.device.connect=connect
        with patch('leafroute.engine.discover_wifi_ip',AsyncMock(return_value=target)),patch('leafroute.engine.ensure_tunnel',AsyncMock()) as shared:
            await self.engine.command('auto-connect',{'transport':'Network','host':target['host']})
            await self.engine.connection_task
            self.assertTrue(self.engine.connected)
            self.assertEqual(Store(self.path).data['wifi_device']['host'],target['host'])
            shared.assert_not_called()
            self.assertFalse(self.device.active)
    async def test_helper_failure_stops_lease(self):
        target={'id':'trusted','host':'192.168.0.103'}
        # Use a workspace-owned directory to keep tests isolated and avoid Windows temp ACL issues.
        folder=Path(self.tmp.name)/'helper';folder.mkdir()
        def fail(_):
            (folder/'result.json').write_text(json.dumps({'error':'Handshake rejected'}))
        with patch('leafroute.wifi.tempfile.mkdtemp',return_value=str(folder)),patch('leafroute.tunnel.launch_admin',side_effect=fail):
            tunnel=WifiTunnel(target)
            with self.assertRaisesRegex(ValueError,'Handshake rejected'):await tunnel.__aenter__()
            self.assertTrue((folder/'stop').exists())
            self.assertTrue(tunnel.heartbeat.cancelled())
