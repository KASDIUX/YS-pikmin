"""Explicit private-IP Wi-Fi transport using existing host pairing records only."""
import asyncio
from contextlib import AsyncExitStack
import ipaddress
import json
import os
import re
import struct
import subprocess
from pathlib import Path
import sys
import tempfile
import time
from .device import same_id


class WifiHeartbeat:
    """Answer the phone's Marco/Polo exchange before opening Wi-Fi services."""
    def __init__(self, client):
        self.client = client
        self.service = None
        self.task = None

    async def __aenter__(self):
        try:
            self.service = await asyncio.wait_for(
                self.client.start_lockdown_service('com.apple.mobile.heartbeat'), 5)
            # A live socket alone is insufficient: finish the first exchange before
            # CoreDeviceProxy is opened, then retain the heartbeat for its lifetime.
            await asyncio.wait_for(self.service.recv_plist(), 5)
            await asyncio.wait_for(self.service.send_plist({'Command':'Polo'}), 3)
            self.task = asyncio.create_task(self._run())
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def _run(self):
        while True:
            await self.service.recv_plist()
            await self.service.send_plist({'Command':'Polo'})

    def check(self):
        if self.task and self.task.done():
            self.task.result()
            raise ConnectionError('Wi-Fi 保活連線已結束。')

    async def __aexit__(self, *_):
        if self.task:
            self.task.cancel()
            try: await self.task
            except (asyncio.CancelledError, Exception): pass
            self.task = None
        if self.service:
            try: await asyncio.wait_for(self.service.close(), 2)
            finally: self.service = None


def private_ipv4(value):
    try:
        ip = ipaddress.IPv4Address(str(value).strip())
    except ipaddress.AddressValueError:
        raise ValueError('請輸入手機的區網 IPv4 位址，例如 10.1.2.3。') from None
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


async def discover_wifi_ip(host, known=None, selected_id=None):
    from .device import discover
    host = private_ipv4(host)
    candidates = []
    records = [known] if isinstance(known, dict) else known if isinstance(known, list) else []
    candidates.extend(item for item in records if isinstance(item, dict) and isinstance(item.get('id'), str))
    if selected_id:
        candidates = [d for d in candidates if same_id(d['id'], selected_id)]
        if not candidates:
            raise ValueError('選擇的手機尚未記住，請先用 USB 連接一次。')
    for device in ([] if selected_id else (await discover('USB'))['devices']):
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


async def advertised_wifi_addresses(timeout=6.0, retry_interval=2.0):
    """Read Apple Wi-Fi service announcements; never sweep a subnet."""
    from pymobiledevice3.bonjour import (MOBDEV2_SERVICE_NAME, QTYPE_PTR, QTYPE_SRV,
                                       QTYPE_A, _open_mdns_sockets, _send_query_all,
                                       build_query, parse_mdns_message)
    transports, queue = await _open_mdns_sockets()
    instances, hosts, announced = set(), set(), {}
    addresses = []
    loop = asyncio.get_running_loop()
    deadline, next_retry = loop.time()+timeout, 0
    pending = {(MOBDEV2_SERVICE_NAME, QTYPE_PTR)}
    try:
        while loop.time() < deadline:
            if loop.time() >= next_retry:
                pending.update((name, QTYPE_SRV) for name in instances)
                pending.update((name, QTYPE_A) for name in hosts)
                pending.add((MOBDEV2_SERVICE_NAME, QTYPE_PTR))
                next_retry = loop.time()+retry_interval
            for name, kind in pending:
                await _send_query_all(transports, build_query(name, kind, unicast=False))
            pending.clear()
            try:
                packet, _ = await asyncio.wait_for(queue.get(), min(next_retry, deadline)-loop.time())
            except asyncio.TimeoutError:
                continue
            try:
                records = parse_mdns_message(packet)
            except (ValueError, IndexError, struct.error, UnicodeError):
                continue
            for rr in records:
                if rr.get('type') == QTYPE_PTR and rr.get('name') == MOBDEV2_SERVICE_NAME:
                    name = rr.get('ptrdname')
                    if isinstance(name, str) and name.endswith('.'+MOBDEV2_SERVICE_NAME) and len(instances) < 24:
                        if name not in instances:
                            pending.add((name, QTYPE_SRV))
                        instances.add(name)
                elif rr.get('type') == QTYPE_SRV and rr.get('name') in instances:
                    # The announced port need not be lockdown's TCP port 62078.
                    name = rr.get('target')
                    if isinstance(name, str) and name.endswith('.local.') and len(hosts) < 24:
                        if name not in hosts:
                            pending.add((name, QTYPE_A))
                        hosts.add(name)
                elif rr.get('type') == QTYPE_A and isinstance(rr.get('name'), str):
                    try:
                        host = private_ipv4(rr.get('address'))
                    except ValueError:
                        continue
                    if len(announced) < 48 or rr['name'] in announced:
                        announced.setdefault(rr['name'], set()).add(host)
            for name in hosts:
                for host in sorted(announced.get(name, ())):
                    if host not in addresses:
                        addresses.append(host)
                    if len(addresses) >= 12:
                        return addresses
        return addresses
    finally:
        for transport, _ in transports:
            transport.close()


def local_wifi_hosts(previous_host=''):
    """Bound discovery to two directly attached RFC1918 IPv4 segments."""
    if os.name != 'nt':
        return []
    powershell = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | Where-Object AddressState -eq Preferred | Select-Object IPAddress,PrefixLength | ConvertTo-Json -Compress"
    result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, timeout=6,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    return subnet_hosts(json.loads(result.stdout), previous_host)


def subnet_hosts(interfaces, previous_host=''):
    """Use actual interface prefixes, capped at /24 even on large networks."""
    interfaces = [interfaces] if isinstance(interfaces, dict) else interfaces
    networks, own = [], set()
    if not isinstance(interfaces, list):
        return []
    for interface in interfaces[:32]:
        try:
            address = private_ipv4(interface['IPAddress'])
            prefix = interface['PrefixLength']
            if type(prefix) is not int or not 8 <= prefix <= 30:
                continue
            own.add(address)
            network = ipaddress.ip_network(f'{address}/{max(prefix, 24)}', strict=False)
            if network not in networks:
                networks.append(network)
        except (ValueError, TypeError, KeyError):
            continue
    try:
        previous = ipaddress.IPv4Address(previous_host)
        networks.sort(key=lambda network: previous not in network)
    except ipaddress.AddressValueError:
        pass
    return [str(host) for network in networks[:2] for host in network.hosts() if str(host) not in own]


async def local_wifi_addresses(previous_host=''):
    """Probe only Apple's TCP port; these candidates still need paired identity checks."""
    hosts = await asyncio.to_thread(local_wifi_hosts, previous_host)
    semaphore = asyncio.Semaphore(32)
    async def probe(host):
        async with semaphore:
            writer = None
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(host, 62078), .45)
                return host
            except (OSError, asyncio.TimeoutError):
                return None
            finally:
                if writer:
                    writer.close()
                    try:
                        await asyncio.wait_for(writer.wait_closed(), .2)
                    except (OSError, asyncio.TimeoutError):
                        pass
    tasks = [asyncio.create_task(probe(host)) for host in hosts[:508]]
    try:
        results = await asyncio.gather(*tasks)
        return [host for host in results if host]
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def resolve_remembered_wifi(target, report=None):
    """Recover a changed IP using the selected phone's existing pairing only."""
    if target.get('host'):
        try:
            return await discover_wifi_ip(target['host'], target, selected_id=target['id'])
        except Exception:
            pass
    if report:
        report('記錄的 IP 無法使用，正在重新搜尋這支 iPhone 的區網位址…')
    try:
        addresses = await advertised_wifi_addresses()
    except Exception:
        addresses = []
    semaphore = asyncio.Semaphore(4)

    async def probe(host):
        async with semaphore:
            try:
                # Announcements are hints, not proof of device identity.
                async with asyncio.timeout(3):
                    async with await open_paired_wifi(host, target['id']) as client:
                        if not same_id(client.udid, target['id']):
                            return None
                        return {'id': client.udid, 'name': client.all_values.get('DeviceName', 'iPhone'),
                                'ios': client.product_version, 'transport': 'WiFiIP', 'host': host}
            except Exception:
                return None

    async def verify(hosts):
        tasks = [asyncio.create_task(probe(host)) for host in hosts]
        try:
            for task in asyncio.as_completed(tasks):
                result = await task
                if result:
                    return result
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
    result = await verify(addresses)
    if result:
        return result
    if report:
        report('未收到可用的手機公告，正在區網尋找並核對所選 iPhone，請保持解鎖…')
    try:
        candidates = await local_wifi_addresses(target.get('host', ''))
    except Exception:
        candidates = []
    try:
        async with asyncio.timeout(12):
            result = await verify([host for host in candidates if host not in addresses])
    except asyncio.TimeoutError:
        result = None
    if result:
        return result
    raise ValueError('舊 IP 無法連線，重新搜尋也未找到這支 iPhone。已嘗試裝置公告與區網搜尋；請保持解鎖並連同一區網，必要時手動輸入新 IP 或先用 USB 連接。')


def publish(folder, data):
    temporary = folder/'result.tmp'
    temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    os.replace(temporary,folder/'result.json')


def prepare_helper_folder(folder):
    # Python's private temp directories can grant OWNER RIGHTS only. Files made
    # by the elevated child then become unreadable to the unelevated owner.
    # Grant only this Windows user inheritable access to our own private folder.
    if os.name != 'nt': return
    system = Path(os.environ['SystemRoot'])/'System32'
    options = dict(capture_output=True, text=True, errors='replace', check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    account = subprocess.run([str(system/'whoami.exe'), '/user', '/fo', 'csv', '/nh'], **options)
    match = re.search(r'\bS-1-5-(?:\d+-)*\d+\b', account.stdout)
    if not match: raise ValueError('無法確認本機使用者權限。')
    subprocess.run([str(system/'icacls.exe'), str(folder), '/grant:r',
                    '*'+match.group(0)+':(OI)(CI)F'], **options)


class WifiTunnel:
    def __init__(self, target):
        self.target = target
        self.folder = None
        self.heartbeat = None
    async def __aenter__(self):
        from .tunnel import launch_admin_async
        self.folder = Path(tempfile.mkdtemp(prefix='ys-pikmin-wifi-'))
        prepare_helper_folder(self.folder)
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
                heartbeat = await stack.enter_async_context(WifiHeartbeat(client))
                proxy = await CoreDeviceTunnelProxy.create(client)
                stack.push_async_callback(proxy.close)
                tunnel = await stack.enter_async_context(proxy.start_tcp_tunnel())
                async with RemoteServiceDiscoveryService((tunnel.address,tunnel.port)) as rsd:
                    if not same_id(rsd.udid,identity): raise ValueError('裝置識別碼不符。')
            if leased(): publish(folder,{'ready':True,'address':tunnel.address,'port':tunnel.port,'id':identity})
            while leased():
                heartbeat.check()
                await asyncio.sleep(.5)
    except BaseException as error:
        if isinstance(error,asyncio.CancelledError): raise
        detail = str(error) if isinstance(error,ValueError) else type(error).__name__
        publish(folder,{'error':'Wi-Fi 定位通道建立失敗（'+detail+'）。手機雖可無線配對，但尚未完成定位服務連線；請先改用 USB。'})
    finally:
        (folder/'closed').touch()
