"""One shared position, at most two independently tracked device sessions."""
import asyncio
import copy

from .device import Device, friendly_error, same_id


def validate_targets(targets):
    if not isinstance(targets, list) or not 1 <= len(targets) <= 2:
        raise ValueError('請選擇 1～2 台 iPhone。')
    identities = []
    for target in targets:
        identity = target.get('id') if isinstance(target, dict) else None
        if not isinstance(identity, str) or not identity:
            raise ValueError('手機識別碼不正確，請重新掃描。')
        if any(same_id(identity, old) for old in identities):
            raise ValueError('同一台 iPhone 不可重複選取。')
        identities.append(identity)


class DeviceGroup:
    def __init__(self, factory=Device):
        self.factory = factory
        self.sessions = []
        self.states = []

    @property
    def info(self):
        connected = [s for s in self.states if s['connected']]
        if not connected:
            return None
        return {**connected[0], 'name': ' ＋ '.join(s['name'] for s in connected),
                'count': len(connected)}

    @property
    def active(self):
        return any(d.active for d in self.sessions) or any(s['simulation_active'] for s in self.states)

    def snapshot(self):
        return copy.deepcopy(self.states)

    async def connect(self, target):
        return await self.connect_many([target])

    async def connect_many(self, targets):
        validate_targets(targets)
        if self.sessions and self.active:
            raise ValueError('請先停止並還原目前裝置，再重新連接。')
        unresolved = [s for s in self.states if s['simulation_active']]
        if any(not any(same_id(s['id'], t['id']) for t in targets) for s in unresolved):
            raise ValueError('原手機仍可能保留模擬定位，請選回相同手機重新連接後還原。')
        await self.close()
        self.states = [{**copy.deepcopy(t), 'connected': False, 'simulation_active': any(same_id(t['id'], s['id']) for s in unresolved),
                        'last_position': None, 'error': ''} for t in targets]
        try:
            for i, target in enumerate(targets):
                device = self.factory()
                self.sessions.append(device)
                await asyncio.wait_for(device.connect(target), 65 if target['transport'] == 'WiFiIP' else 18)
                device.active = self.states[i]['simulation_active']
                self.states[i].update(device.info or {})
                self.states[i]['connected'] = True
        except BaseException as error:
            # A partial connection never becomes a usable one-phone group.
            self.states[i]['error'] = '連接已取消。' if isinstance(error, asyncio.CancelledError) else friendly_error(error)
            await self.close()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise ValueError(f'{self.states[i]["name"]} 連接失敗：{friendly_error(error)}；同步連線未建立。') from error
        return self.info

    async def _operation(self, method, position=None):
        if not self.sessions:
            if method == 'clear' and not any(s['simulation_active'] for s in self.states):
                return
            raise ValueError('手機尚未連接；請重新連接後再操作。')
        if method == 'set' and not all(s['connected'] for s in self.states):
            raise ValueError('同步裝置已斷線，請先停止並重新連接。')

        async def one(index, device):
            state = self.states[index]
            try:
                if method == 'set':
                    state['simulation_active'] = True
                    await asyncio.wait_for(device.set(list(position)), 4)
                    state['last_position'] = list(position)
                else:
                    await asyncio.wait_for(device.clear(), 3)
                    state['simulation_active'] = False
                state['connected'] = True
                state['error'] = ''
            except BaseException as error:
                state['simulation_active'] = bool(device.active or state['simulation_active'])
                state['connected'] = False
                state['error'] = '操作已取消；結果未確認。' if isinstance(error, asyncio.CancelledError) else friendly_error(error)
                raise

        # Dispatch the same coordinate to every phone; there is no atomic GPS API.
        results = await asyncio.gather(*(one(i, d) for i, d in enumerate(self.sessions)), return_exceptions=True)
        failures = [i for i, result in enumerate(results) if isinstance(result, BaseException)]
        if failures:
            failed_names = '、'.join(self.states[i]['name'] for i in failures)
            action = '同步傳送失敗，已停止後續移動；部分手機可能已到新座標' if method == 'set' else '未能確認所有手機已還原定位'
            raise ValueError(f'{action}。未完成：{failed_names}。請檢查各台手機，再停止連接或重新連接。')

    async def set(self, position):
        await self._operation('set', position)

    async def clear(self):
        await self._operation('clear')

    async def check_connection(self):
        async def one(index, device):
            state = self.states[index]
            if not state['connected']:
                return
            try:
                await asyncio.wait_for(device.check_connection(), 3)
            except Exception as error:
                state['connected'] = False
                state['error'] = friendly_error(error)
        await asyncio.gather(*(one(i, d) for i, d in enumerate(self.sessions)))
        if not self.sessions or not all(s['connected'] for s in self.states):
            raise ConnectionError('定位服務已中斷或無回應。')

    async def close(self):
        async def close_one(device):
            try:
                await asyncio.wait_for(device.close(), .4)
            except Exception:
                pass
        sessions, self.sessions = self.sessions, []
        try:
            await asyncio.gather(*(close_one(d) for d in sessions))
        finally:
            for state in self.states:
                state['connected'] = False
