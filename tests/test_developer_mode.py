import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from leafroute.developer_mode import DeveloperModeBackend, DeveloperModeError


class DeveloperModeTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, *, enabled=False, paired=True, identity='USB-phone', connected=True, ios='18.1.1'):
        mux = SimpleNamespace(serial='USB-phone', connection_type='USB')
        service = SimpleNamespace(send_recv_plist=AsyncMock(return_value={'success':True}), close=AsyncMock())
        client = SimpleNamespace(udid=identity, paired=paired, product_version=ios,
            all_values={'DeviceName':'Test phone'}, close=AsyncMock(), get_value=AsyncMock(return_value=enabled),
            start_lockdown_service=AsyncMock(return_value=service))
        devices = AsyncMock(return_value=[mux] if connected else [])
        connect = AsyncMock(return_value=client)
        return DeveloperModeBackend(devices,connect), client, service, connect, devices

    async def test_reveal_only_sends_reveal_and_closes_connections(self):
        backend, client, service, connect, _ = self.fixture()
        phone, revealed = await backend.reveal('USB-phone')
        self.assertTrue(revealed)
        self.assertFalse(phone.enabled)  # Revealing the option is not enabling the mode.
        connect.assert_awaited_once_with(serial='USB-phone', connection_type='USB', autopair=False)
        service.send_recv_plist.assert_awaited_once_with({'action':0})
        service.close.assert_awaited_once()
        client.close.assert_awaited_once()

    async def test_already_enabled_never_starts_amfi_service(self):
        backend, client, service, *_ = self.fixture(enabled=True)
        phone, revealed = await backend.reveal('USB-phone')
        self.assertTrue(phone.enabled)
        self.assertFalse(revealed)
        client.start_lockdown_service.assert_not_awaited()
        service.send_recv_plist.assert_not_awaited()
        client.close.assert_awaited_once()

    async def test_identity_mismatch_cannot_reveal(self):
        backend, client, service, *_ = self.fixture(identity='another-phone')
        with self.assertRaisesRegex(DeveloperModeError,'識別碼不符'):
            await backend.reveal('USB-phone')
        service.send_recv_plist.assert_not_awaited()
        client.close.assert_awaited_once()

    async def test_untrusted_phone_cannot_reveal(self):
        backend, client, service, *_ = self.fixture(paired=False)
        with self.assertRaisesRegex(DeveloperModeError,'尚未信任'):
            await backend.reveal('USB-phone')
        service.send_recv_plist.assert_not_awaited()
        client.close.assert_awaited_once()

    async def test_unplugged_target_cannot_select_other_device(self):
        backend, _, service, connect, _ = self.fixture(connected=False)
        with self.assertRaisesRegex(DeveloperModeError,'原本選擇的手機'):
            await backend.reveal('USB-phone')
        connect.assert_not_awaited()
        service.send_recv_plist.assert_not_awaited()

    async def test_network_devices_are_excluded(self):
        backend, _, _, connect, devices = self.fixture()
        devices.return_value = [SimpleNamespace(serial='network-phone', connection_type='Network')]
        phones, warning = await backend.scan()
        self.assertEqual(phones,[])
        self.assertEqual(warning,'')
        connect.assert_not_awaited()

    async def test_unknown_status_is_not_treated_as_off(self):
        backend, client, service, *_ = self.fixture(enabled=None)
        with self.assertRaisesRegex(DeveloperModeError,'無法確認'):
            await backend.reveal('USB-phone')
        service.send_recv_plist.assert_not_awaited()
        client.close.assert_awaited_once()

    async def test_rejection_has_no_enable_fallback(self):
        backend, client, service, *_ = self.fixture()
        service.send_recv_plist.return_value={'success':False}
        with self.assertRaisesRegex(DeveloperModeError,'未確認'):
            await backend.reveal('USB-phone')
        service.send_recv_plist.assert_awaited_once_with({'action':0})
        service.close.assert_awaited_once()
        client.close.assert_awaited_once()

    async def test_timeout_closes_and_does_not_retry_with_enable(self):
        backend, client, service, *_ = self.fixture()
        service.send_recv_plist.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):await backend.reveal('USB-phone')
        service.send_recv_plist.assert_awaited_once_with({'action':0})
        service.close.assert_awaited_once()
        client.close.assert_awaited_once()

    async def test_recheck_reads_true_status_without_amfi(self):
        backend, client, service, *_ = self.fixture(enabled=True)
        phone=await backend.inspect('USB-phone')
        self.assertTrue(phone.enabled)
        client.start_lockdown_service.assert_not_awaited()
        service.send_recv_plist.assert_not_awaited()

    async def test_old_ios_never_starts_amfi(self):
        backend, client, service, *_ = self.fixture(ios='15.8.1')
        phone, revealed=await backend.reveal('USB-phone')
        self.assertFalse(phone.supported)
        self.assertFalse(revealed)
        client.get_value.assert_not_awaited()
        service.send_recv_plist.assert_not_awaited()

    async def test_cancel_closes_service_and_client(self):
        backend, client, service, *_ = self.fixture()
        started=asyncio.Event()
        async def pending(_):
            started.set()
            await asyncio.Event().wait()
        service.send_recv_plist.side_effect=pending
        task=asyncio.create_task(backend.reveal('USB-phone'))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        service.close.assert_awaited_once()
        client.close.assert_awaited_once()
