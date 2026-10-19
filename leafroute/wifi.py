"""Explicit private-IP Wi-Fi transport using existing host pairing records only."""
import asyncio
from contextlib import AsyncExitStack
import ipaddress
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from .device import same_id


def private_ipv4(value):
    try:
        ip = ipaddress.IPv4Address(str(value).strip())
    except ipaddress.AddressValueError:
        raise ValueError('請輸入手機的區網 IPv4 位址，例如 192.168.0.103。') from None
    if not any(ip in ipaddress.ip_network(net) for net in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):
        raise ValueError('僅接受區網 IP；不接受公網、回送或廣播位址。')
    return str(ip)


async def open_paired_wifi(host, identity):
    from pymobiledevice3.lockdown import create_using_tcp
    from pymobiledevice3.pair_records import get_preferred_pair_record
    from pymobiledevice3.common import get_home_folder
    record = await get_preferred_pair_record(identity, get_home_folder())
    if not record:
        raise ValueError('尚無這支手機的信任配對，請先用 USB 信任電腦。')
    client = await create_using_tcp(private_ipv4(host), identifier=identity, pair_record=record, autopair=False)
    if not client.paired or not same_id(client.udid, identity):
        await client.close()
        raise ValueError('手機的配對或識別碼不符，已取消連接。')
    return client


async def discover_wifi_ip(host, known=None):
    from .device import discover
    host = private_ipv4(host)
    candidates = []
    if isinstance(known, dict) and isinstance(known.get('id'), str):
        candidates.append(known)
    for device in (await discover('USB'))['devices']:
        if not any(same_id(device['id'], item['id']) for item in candidates):
            candidates.append(device)
    if not candidates:
        raise ValueError('首次指定 IP 時，請先用 USB 接上已信任的手機，讓程式記住識別碼；之後可拔線使用。')
    for candidate in candidates[:8]:
        try:
            async with await asyncio.wait_for(open_paired_wifi(host, candidate['id']), 5) as client:
                return {'id': client.udid, 'name': client.all_values.get('DeviceName','iPhone'),
                        'ios': client.product_version, 'transport': 'WiFiIP', 'host': host}
        except (OSError, asyncio.TimeoutError):
            continue
        except Exception:
            continue
    raise ValueError('無法以既有配對連上此 IP。請確認手機已解鎖、同一區網，且 IP 屬於已信任的 iPhone。')


def publish(folder, data):
    temporary = folder/'result.tmp'
    temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    os.replace(temporary,folder/'result.json')


class WifiTunnel:
    def __init__(self, target):
        self.target = target
        self.folder = None
        self.heartbeat = None
    async def __aenter__(self):
        from .tunnel import launch_admin_async
        self.folder = Path(tempfile.mkdtemp(prefix='ys-pikmin-wifi-'))
        (self.folder/'request.json').write_text(json.dumps({'host':private_ipv4(self.target['host']), 'id':self.target['id']}),encoding='utf-8')
        async def beat():
            while True:
                (self.folder/'lease').touch()
                await asyncio.sleep(1)
        self.heartbeat = asyncio.create_task(beat())
        (self.folder/'lease').touch()
        prefix = [] if getattr(sys,'frozen',False) else [str(Path(__file__).resolve().parents[1]/'start.py')]
        try:
            await launch_admin_async(prefix+['--wifi-helper',str(self.folder)])
            for _ in range(35):
                file = self.folder/'result.json'
                if file.exists():
                    if file.stat().st_size > 8192:
                        raise ValueError('無線通道回應過大。')
                    result = json.loads(file.read_text(encoding='utf-8'))
                    if result.get('error'):
                        raise ValueError(result['error'])
                    if result.get('ready'):
                        if not same_id(result.get('id',''),self.target['id']):
                            raise ValueError('無線通道識別碼不符。')
                        ipaddress.IPv6Address(result['address'])
                        if type(result['port']) is not int or not 1<=result['port']<=65535:
                            raise ValueError('無線通道連接埠不正確。')
                        return result
                await asyncio.sleep(.5)
            raise ValueError('無線通道逾時，請確認已允許管理員提示；可先改用 USB。')
        except BaseException:
            await self.__aexit__(None,None,None)
            raise
    async def __aexit__(self,*_):
        if self.folder:
            (self.folder/'stop').touch()
        if self.heartbeat:
            self.heartbeat.cancel()
            try: await self.heartbeat
            except asyncio.CancelledError: pass
        # Child exits on stop or an expired heartbeat; no credentials are stored here.


async def helper(folder):
    from pymobiledevice3.remote.tunnel_service import CoreDeviceTunnelProxy
    from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
    folder = Path(folder).resolve()
    if folder.parent != Path(tempfile.gettempdir()).resolve() or not folder.name.startswith('ys-pikmin-wifi-'):
        raise ValueError('不接受的通道工作目錄。')
    def leased():
        try: return not (folder/'stop').exists() and time.time()-(folder/'lease').stat().st_mtime<8
        except OSError: return False
    try:
        raw = (folder/'request.json').read_text(encoding='utf-8')
        if len(raw)>2048: raise ValueError('不正確的通道請求。')
        request = json.loads(raw)
        identity = request['id']
        if not isinstance(identity,str) or not identity.replace('-','').isalnum() or len(identity)>80:
            raise ValueError('裝置識別碼不正確。')
        if not leased(): return
        async with AsyncExitStack() as stack:
            async with asyncio.timeout(15):
                client = await stack.enter_async_context(await open_paired_wifi(request['host'],identity))
                proxy = await CoreDeviceTunnelProxy.create(client)
                stack.push_async_callback(proxy.close)
                tunnel = await stack.enter_async_context(proxy.start_tcp_tunnel())
                async with RemoteServiceDiscoveryService((tunnel.address,tunnel.port)) as rsd:
                    if not same_id(rsd.udid,identity): raise ValueError('裝置識別碼不符。')
            if leased(): publish(folder,{'ready':True,'address':tunnel.address,'port':tunnel.port,'id':identity})
            while leased(): await asyncio.sleep(.5)
    except BaseException as error:
        if isinstance(error,asyncio.CancelledError): raise
        detail = str(error) if isinstance(error,ValueError) else type(error).__name__
        publish(folder,{'error':'Wi-Fi 定位通道建立失敗（'+detail+'）。手機雖可無線配對，但尚未完成定位服務連線；請先改用 USB。'})
    finally:
        (folder/'closed').touch()
