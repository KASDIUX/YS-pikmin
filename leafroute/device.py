"""Optional, persistent pymobiledevice3 9.29.1 adapter; no automatic pairing or elevation."""
import asyncio
import importlib.metadata
import json
import struct
from contextlib import AsyncExitStack
from urllib.request import ProxyHandler, build_opener

SUPPORTED_VERSION = '9.29.1'


def package_version():
    try:
        return importlib.metadata.version('pymobiledevice3')
    except importlib.metadata.PackageNotFoundError:
        return None


def read_tunnels():
    # Local requests must not inherit a corporate/system HTTP proxy.
    opener = build_opener(ProxyHandler({}))
    with opener.open('http://127.0.0.1:49151/', timeout=2) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError('tunneld 回應過大。')
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError('tunneld 回應格式不正確。')
    return result


def same_id(a, b):
    return a.replace('-', '').lower() == b.replace('-', '').lower()


async def discover():
    version = package_version()
    if version != SUPPORTED_VERSION:
        return {'devices': [], 'warning': f'手機模式需要 pymobiledevice3 {SUPPORTED_VERSION}（目前：{version or "未安裝"}）。請執行 Install_Device_Support.cmd。'}
    from pymobiledevice3.usbmux import list_devices
    from pymobiledevice3.lockdown import create_using_usbmux
    devices, warnings = {}, []

    async def inspect(mux):
        entry = {'id': mux.serial, 'name': 'iPhone', 'ios': '', 'transport': mux.connection_type}
        try:
            async with await create_using_usbmux(serial=mux.serial, connection_type=mux.connection_type, autopair=False) as client:
                entry['name'] = client.all_values.get('DeviceName', 'iPhone')
                entry['ios'] = client.product_version
        except Exception:
            entry['name'] = 'iPhone（請先解鎖並信任電腦）'
        return entry

    try:
        muxes = await asyncio.wait_for(list_devices(), 4)
        entries = await asyncio.gather(*(asyncio.wait_for(inspect(m), 5) for m in muxes[:8]), return_exceptions=True)
        for entry in entries:
            if isinstance(entry, dict):
                # Prefer USB when the same phone is also discoverable over Wi-Fi.
                if entry['id'] not in devices or entry['transport'] == 'USB':
                    devices[entry['id']] = entry
    except Exception:
        warnings.append('無法讀取 Apple 裝置服務；請確認 Apple Devices 已安裝並可看到手機。')
    try:
        tunnels = await asyncio.to_thread(read_tunnels)
        for udid, details in tunnels.items():
            if details and not any(same_id(udid, key) for key in devices):
                devices[udid] = {'id': udid, 'name': 'iPhone（通道已就緒）', 'ios': '', 'transport': 'Tunnel'}
        tunnel_ready = bool(tunnels)
    except Exception:
        tunnel_ready = False
    if not tunnel_ready:
        warnings.append('iOS 17 以上需要通道：請以系統管理員身分執行 Start_Tunnel.cmd，保持該視窗開啟。')
    return {'devices': list(devices.values()), 'warning': ' '.join(warnings)}


class Device:
    def __init__(self):
        self.stack = None
        self.location = None
        self.legacy = None
        self.info = None
        self.active = False

    async def connect(self, target):
        if package_version() != SUPPORTED_VERSION:
            raise ValueError(f'請先安裝 pymobiledevice3 {SUPPORTED_VERSION}。')
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
        stack = AsyncExitStack()
        try:
            version = target.get('ios', '')
            lockdown = None
            if target['transport'] != 'Tunnel':
                lockdown = await stack.enter_async_context(await create_using_usbmux(
                    serial=target['id'], connection_type=target['transport'], autopair=False))
                version = lockdown.product_version
            if version and int(version.split('.')[0]) < 17:
                self.legacy = lockdown
                # Verify service availability before reporting a successful connection.
                service = await lockdown.start_lockdown_developer_service('com.apple.dt.simulatelocation')
                await service.close()
            else:
                try:
                    tunnels = await asyncio.to_thread(read_tunnels)
                except Exception:
                    raise ValueError('尚未找到 iOS 通道。請以系統管理員身分執行 Start_Tunnel.cmd，稍候再連接。') from None
                details = next((items for udid, items in tunnels.items() if same_id(udid, target['id'])), [])
                if not details:
                    raise ValueError('此 iPhone 尚未建立通道。請確認已開啟開發者模式，並啟動 Start_Tunnel.cmd。')
                detail = details[0]
                rsd = await stack.enter_async_context(RemoteServiceDiscoveryService((detail['tunnel-address'], int(detail['tunnel-port']))))
                if not same_id(rsd.udid, target['id']):
                    raise ValueError('裝置識別碼不相符，已取消連接。')
                version = rsd.product_version
                dvt = await stack.enter_async_context(DvtProvider(rsd))
                self.location = await stack.enter_async_context(LocationSimulation(dvt))
            self.stack = stack
            self.info = {**target, 'ios': version}
            return self.info
        except BaseException:
            await stack.aclose()
            self.location = self.legacy = None
            raise

    async def _legacy_send(self, command, position=None):
        service = await self.legacy.start_lockdown_developer_service('com.apple.dt.simulatelocation')
        try:
            message = struct.pack('>I', command)
            if position is not None:
                for number in position:
                    encoded = str(number).encode('ascii')
                    message += struct.pack('>I', len(encoded))+encoded
            await service.sendall(message)
        finally:
            await service.close()

    async def set(self, position):
        # Mark as possibly active before I/O; a lost acknowledgement is not a reset.
        self.active = True
        if self.location:
            await self.location.set(*position)
        elif self.legacy:
            await self._legacy_send(0, position)
        else:
            raise ValueError('手機尚未連接。')

    async def clear(self):
        if self.location:
            await self.location.clear()
        elif self.legacy:
            await self._legacy_send(1)
        elif self.active:
            raise ValueError('連線已中斷，無法送出還原定位指令。')
        self.active = False

    async def close(self):
        try:
            if self.stack:
                await self.stack.aclose()
        finally:
            self.stack = self.location = self.legacy = self.info = None


def friendly_error(error):
    if isinstance(error, ValueError):
        return str(error)
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return '裝置回應逾時，移動已停止。請解鎖手機並檢查連線。'
    name = type(error).__name__
    return f'手機操作失敗（{name}）。請確認已信任電腦、開啟開發者模式、啟動通道；若開發者映像尚未掛載，執行 Prepare_Device.cmd。'
