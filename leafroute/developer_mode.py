"""USB-only Developer Mode inspection and reveal; all enable steps stay on the phone."""
import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import importlib.metadata

SUPPORTED_VERSION = '9.29.1'


class DeveloperModeError(ValueError):
    pass


@dataclass(frozen=True)
class Phone:
    identity: str
    name: str
    ios: str
    enabled: bool | None
    supported: bool


def same_phone(a, b):
    return a.replace('-', '').lower() == b.replace('-', '').lower()


def status_text(phone):
    if not phone.supported: return '此版本不需要'
    if phone.enabled is True: return '已開啟'
    if phone.enabled is False: return '尚未開啟'
    return '無法確認，請重新檢查'


class DeveloperModeBackend:
    def __init__(self, list_devices=None, connect=None):
        self._list_devices = list_devices
        self._connect = connect

    def _load(self):
        if self._list_devices is not None and self._connect is not None: return
        try:
            version = importlib.metadata.version('pymobiledevice3')
            if version != SUPPORTED_VERSION:
                raise DeveloperModeError('通訊套件版本不符；請使用完整 EXE 包，或安裝 requirements-device.txt。')
            from pymobiledevice3.usbmux import list_devices
            from pymobiledevice3.lockdown import create_using_usbmux
        except (ImportError, importlib.metadata.PackageNotFoundError) as error:
            raise DeveloperModeError('缺少裝置通訊元件；請完整解壓縮 EXE，或安裝 requirements-device.txt。') from error
        self._list_devices, self._connect = list_devices, create_using_usbmux

    async def _usb(self):
        self._load()
        devices = await asyncio.wait_for(self._list_devices(), 5)
        return [d for d in devices if d.connection_type == 'USB']

    @asynccontextmanager
    async def _client(self, identity):
        devices = await self._usb()
        if not any(same_phone(d.serial, identity) for d in devices):
            raise DeveloperModeError('原本選擇的手機不在 USB 清單，請接回該手機並重新掃描。')
        client = await asyncio.wait_for(self._connect(
            serial=identity, connection_type='USB', autopair=False), 8)
        try:
            if not client.paired:
                raise DeveloperModeError('手機尚未信任這部電腦；請先在 Apple Devices 與手機完成信任配對。')
            if not same_phone(client.udid, identity):
                raise DeveloperModeError('手機識別碼不符，請重新掃描與選擇。')
            yield client
        finally:
            with suppress(Exception):
                await asyncio.wait_for(client.close(), 2)

    async def _describe(self, client):
        ios = str(client.product_version or '')
        try: major = int(ios.split('.')[0])
        except ValueError:
            raise DeveloperModeError('無法讀取 iOS 版本，請重新掃描。') from None
        enabled = None
        if major >= 16:
            value = await asyncio.wait_for(client.get_value(
                'com.apple.security.mac.amfi', 'DeveloperModeStatus'), 5)
            if type(value) is not bool:
                raise DeveloperModeError('無法確認開發者模式狀態，請先在手機設定查看。')
            enabled = value
        return Phone(client.udid, str(client.all_values.get('DeviceName', 'iPhone')),
                     ios, enabled, major >= 16)

    async def inspect(self, identity):
        async with self._client(identity) as client:
            return await self._describe(client)

    async def scan(self):
        devices = await self._usb()
        results = await asyncio.gather(*(self.inspect(d.serial) for d in devices[:8]),
                                       return_exceptions=True)
        phones = [p for p in results if isinstance(p, Phone)]
        failed = sum(isinstance(p, BaseException) for p in results)
        warnings = []
        if failed:
            warnings.append(f'{failed} 台 USB 裝置未能確認信任或狀態。請解鎖手機，並用 Apple Devices 完成信任配對。')
        if len(devices) > 8: warnings.append('一次最多列出 8 台手機，請拔除多餘裝置。')
        return phones, ' '.join(warnings)

    async def reveal(self, identity):
        # Recheck the selected USB identity and actual state immediately before action.
        async with self._client(identity) as client:
            phone = await self._describe(client)
            if not phone.supported or phone.enabled:
                return phone, False
            service = await asyncio.wait_for(
                client.start_lockdown_service('com.apple.amfi.lockdown'), 5)
            try:
                reply = await asyncio.wait_for(service.send_recv_plist({'action': 0}), 5)
                if not isinstance(reply, dict) or not reply.get('success'):
                    raise DeveloperModeError('手機未確認選項顯示成功；請解鎖手機後重試。')
            finally:
                with suppress(Exception):
                    await asyncio.wait_for(service.close(), 2)
            return phone, True


def friendly_error(error):
    if isinstance(error, DeveloperModeError): return str(error)
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return '手機回應逾時。請保持解鎖、檢查 USB 傳輸線，然後重新掃描。'
    return f'無法完成操作（{type(error).__name__}）。請確認 Apple Devices 能看到已解鎖的手機。'
