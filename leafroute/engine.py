import asyncio
import copy
import math
import time
import uuid

from .device import Device, discover, friendly_error, package_version
from .tunnel import ensure_tunnel
from .wifi import discover_wifi_ip, private_ipv4
from .geo import Route, destination, distance, point, read_gpx
from .routing import RoadPlanner, route_meta


class Engine:
    def __init__(self, store, device=None):
        self.store = store
        self.device = device or Device()
        self.lock = asyncio.Lock()
        self.mode = 'preview'
        self.connected = False
        self.devices = []
        self.position = list(store.data.get('home') or [25.033964, 121.564468])
        self.route = None
        self.route_meta = route_meta()
        self.route_revision = 0
        self.planner = RoadPlanner()
        self.play_route = None
        self.speed = 5.0
        self.loop_route = False
        self.status = 'idle'
        self.progress = 0.0
        self.traveled = 0.0
        self.laps = 0
        self.message = store.warning or '預覽模式已就緒；不會改變手機定位。'
        self.error = False
        self.last_time = time.monotonic()
        self.closed = False
        self.connection_task = None
        self.connection_phase = "idle"
        self.connection_message = "尚未連接裝置"

    def snapshot(self):
        total = self.play_route.length if self.play_route else (self.route.length if self.route else 0)
        return {'mode': self.mode, 'connected': self.connected, 'device': self.device.info,
                'position': list(self.position), 'home': copy.deepcopy(self.store.data.get('home')), 'status': self.status, 'speed': self.speed,
                'loop': self.loop_route, 'progress': self.progress, 'distance': total,
                'route_distance': self.route.length if self.route else 0,
                'route_revision': self.route_revision, 'route_meta': self.route_meta,
                'point_count': len(self.route.points) if self.route else 0,
                'traveled': self.traveled, 'laps': self.laps, 'message': self.message,
                'error': self.error, 'simulation_active': self.device.active,
                'package_version': package_version(), 'devices': self.devices,
                'wifi_host': (self.store.data.get('wifi_device') or {}).get('host',''), 'connection_phase': self.connection_phase, 'connection_message': self.connection_message}

    async def _apply(self, position):
        if self.mode == 'device':
            if not self.connected:
                raise ValueError('手機尚未連接；位置未送出。')
            try:
                await asyncio.wait_for(self.device.set(position), 5)
            except Exception:
                self.connected = False
                raise
        self.position = list(position)

    def _not_running(self):
        if self.status == 'running':
            raise ValueError('請先暫停路線，再變更位置或路線。')

    def _new_route(self, points, meta=None):
        self._not_running()
        validated_route = Route(points) if points else None
        validated_meta = route_meta(meta)
        self.route = validated_route
        self.route_meta = validated_meta
        self.route_revision += 1
        self.play_route = None
        self.progress = self.traveled = 0.0
        self.laps = 0
        self.status = 'idle'

    async def _auto_connect(self, transport, selected_id, host=None):
        try:
            if transport == 'Network' and host:
                target = await discover_wifi_ip(host, self.store.data.get('wifi_device'))
                updated = copy.deepcopy(self.store.data)
                updated['wifi_device'] = target
                self.store.save(updated)
                self.devices = [target]
            else:
                result = await discover(transport)
                self.devices = result['devices']
            choices = self.devices
            if selected_id:
                choices = [d for d in choices if d['id'] == selected_id]
            if not choices:
                raise ValueError('未找到此連線方式的 iPhone。請接上 USB、解鎖並信任電腦；Wi-Fi 需先完成配對。')
            if len(choices) > 1:
                self.connection_phase = 'choose'
                self.connection_message = '找到多部 iPhone，請選擇要連接的裝置，再按連接。'
                return
            target = choices[0]
            def report(message):
                self.connection_message = message
            if target['transport'] != 'WiFiIP' and (not target.get('ios') or int(target['ios'].split('.')[0]) >= 17):
                await asyncio.wait_for(ensure_tunnel(target['id'], report), 90)
            report('正在建立 Wi-Fi 定位通道，若出現管理員提示請允許…' if target['transport']=='WiFiIP' else '正在建立定位服務連線…')
            async with self.lock:
                await self._command('connect', {'id': target['id']})
                if self.device.active:
                    await asyncio.wait_for(self.device.clear(), 5)
                self.connection_phase = 'connected'
                self.connection_message = '已連接 ' + target['name'] + ' · ' + target['transport']
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.connected:
                await self.device.close()
                self.connected = False
            self.connection_phase = 'error'
            self.connection_message = friendly_error(exc)
        finally:
            if self.connection_phase == 'connecting':
                self.connection_phase = 'idle'

    async def command(self, action, data):
        if self.closed:
            raise ValueError('程式正在結束。')
        if action == 'auto-connect':
            async with self.lock:
                self._not_running()
                if self.connected or (self.connection_task and not self.connection_task.done()):
                    return self.snapshot()
                transport = data.get('transport', 'USB')
                if transport not in ('USB', 'Network'):
                    raise ValueError('不支援的連線方式。')
                host = data.get('host') if transport == 'Network' else None
                if host:
                    host = private_ipv4(host)
                self.connection_phase = 'connecting'
                self.connection_message = '正在掃描 iPhone…'
                self.connection_task = asyncio.create_task(self._auto_connect(transport, data.get('id'), host))
                return self.snapshot()
        if action == 'disconnect':
            task = self.connection_task
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self.connection_phase = 'idle'
            self.connection_message = '已停止連接'
        elif self.connection_task and not self.connection_task.done() and action in ('connect', 'scan', 'start', 'position', 'nudge', 'go-home', 'joystick'):
            raise ValueError('正在連接手機，請等待完成或按停止連接。')
        if action == 'plan':
            # Network I/O must never hold the device/control lock: stop stays responsive.
            async with self.lock:
                self._not_running()
                revision = self.route_revision
            result = await asyncio.to_thread(self.planner.plan, data.get('points'),
                                            data.get('profile', 'foot'), data.get('round_trip', False))
            return {**result, 'revision': revision}
        async with self.lock:
            try:
                self.error = False
                result = await self._command(action, data)
                self.error = False
                return result if result is not None else self.snapshot()
            except Exception as exc:
                if self.mode == 'device' and not isinstance(exc, ValueError):
                    self.connected = False
                if self.mode == 'device' and not self.connected and self.status == 'running':
                    self.status = 'paused'
                self.error = True
                self.message = friendly_error(exc)
                if action == 'disconnect':
                    self.connection_phase = 'error'
                    self.connection_message = self.message
                raise ValueError(self.message) from exc

    async def _command(self, action, data):
        if action == 'set-home':
            position = point(data.get('position'))
            updated = copy.deepcopy(self.store.data)
            updated['home'] = position
            self.store.save(updated)
            self.message = '住家位置已儲存在本機；手機位置未改變。'
            return self.snapshot()
        if action == 'go-home':
            self._not_running()
            home = self.store.data.get('home')
            if home is None:
                raise ValueError('請先在地圖選擇住家位置，再按「設定住家」。')
            await self._apply(point(home))
            self.play_route = None
            self.progress = 0.0
            self.status = 'idle'
            self.message = '預覽位置已移到住家。' if self.mode == 'preview' else '住家座標已傳送至手機定位服務。'
            return self.snapshot()
        if action == 'scan':
            self._not_running()
            result = await discover()
            self.devices = result['devices']
            self.message = result['warning'] or f'找到 {len(self.devices)} 部裝置。'
            return result
        if action == 'connect':
            self._not_running()
            if self.connected:
                raise ValueError('請先中斷目前連線。')
            target = next((d for d in self.devices if d['id'] == data.get('id')), None)
            if target is None:
                raise ValueError('請先掃描並選擇一部 iPhone。')
            await self.device.close()
            try:
                await asyncio.wait_for(self.device.connect(target), 65 if target['transport']=='WiFiIP' else 18)
            except BaseException:
                await self.device.close()
                raise
            self.mode = 'device'
            self.connected = True
            self.status = 'idle'
            self.play_route = None
            self.progress = self.traveled = 0.0
            self.message = '已連接定位服務。尚未傳送座標；畫面座標不是手機的即時 GPS 讀值。'
        elif action == 'disconnect':
            self.status = 'idle'
            try:
                if self.device.active:
                    await asyncio.wait_for(self.device.clear(), 5)
            finally:
                await self.device.close()
                self.connected = False
            self.mode = 'preview'
            self.message = '已中斷連線，回到預覽模式。'
        elif action == 'restore':
            self.status = 'idle'
            if self.mode == 'device':
                if not self.connected:
                    raise ValueError('尚未連接，無法送出還原定位指令。請重新連接後重試；必要時重新啟動 iPhone。')
                await asyncio.wait_for(self.device.clear(), 5)
                self.message = '已送出停止模擬定位指令；請在手機地圖確認實際位置。'
            else:
                self.message = '預覽已停止。'
            self.play_route = None
            self.progress = 0.0
        elif action == 'position':
            self._not_running()
            await self._apply(point(data.get('position')))
            self.play_route = None
            self.progress = 0.0
            self.status = 'idle'
            self.message = '預覽位置已更新。' if self.mode == 'preview' else '座標已傳送至手機定位服務。'
        elif action == 'joystick':
            self._not_running()
            bearing = data.get('bearing')
            speed = data.get('speed')
            strength = data.get('strength', 1)
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (bearing, speed, strength)):
                raise ValueError('搖桿參數不正確。')
            if not 0 <= bearing < 360 or not 0.5 <= speed <= 30 or not 0 < strength <= 1:
                raise ValueError('搖桿速度須介於 0.5～30 km/h。')
            # One bounded step per request; no autonomous movement or queued loop.
            await self._apply(destination(self.position, bearing, speed / 3.6 * 0.2 * strength))
            self.play_route = None
            self.progress = 0.0
            self.status = 'idle'
            self.message = '搖桿位置已更新。'
        elif action == 'nudge':
            self._not_running()
            bearing = float(data.get('bearing', 0))
            if not math.isfinite(bearing) or bearing not in (0, 45, 90, 135, 180, 225, 270, 315):
                raise ValueError('方向不正確。')
            await self._apply(destination(self.position, bearing, 5))
            self.play_route = None
            self.progress = 0.0
            self.status = 'idle'
            self.message = '已微調 5 公尺。'
        elif action == 'route':
            if 'expected_revision' in data and data['expected_revision'] != self.route_revision:
                raise ValueError('路線已在其他操作中變更，這次規劃結果未套用。')
            self._new_route(data.get('points'), data.get('meta'))
            self.message = '道路路線已載入。開始前請套用路線起點；不會直線連接遠處位置。' if self.route_meta['kind'] == 'road' else '路線已更新。手動畫線為點與點之間的直線，不會自動沿道路。'
            return {'points': self.route.points if self.route else [], 'meta': self.route_meta, 'state': self.snapshot()}
        elif action == 'import':
            self._new_route(read_gpx(data.get('gpx')), {'kind': 'gpx'})
            self.message = 'GPX 已匯入。'
            return {'points': self.route.points, 'meta': self.route_meta, 'state': self.snapshot()}
        elif action == 'settings':
            speed = float(data.get('speed', self.speed))
            if not math.isfinite(speed) or not 0.5 <= speed <= 120:
                raise ValueError('速度須介於 0.5～120 km/h。')
            loop = data.get('loop', self.loop_route)
            if not isinstance(loop, bool):
                raise ValueError('循環設定不正確。')
            if loop != self.loop_route:
                self._not_running()
                self.play_route = None
                self.progress = 0.0
                self.status = 'idle'
            self.speed, self.loop_route = speed, loop
            self.message = '行走設定已更新。'
        elif action == 'start':
            if not self.route:
                raise ValueError('請先畫出或匯入至少兩個路徑點。')
            if self.status == 'running':
                return
            if self.mode == 'device' and not self.connected:
                raise ValueError('手機尚未連接，無法開始移動。')
            if self.status != 'paused' or not self.play_route:
                values = [list(p) for p in self.route.points]
                road = self.route_meta['kind'] == 'road'
                if road and distance(self.position, values[0]) > 2:
                    raise ValueError('目前座標不在道路路線起點。請按「選取起點」再按「套用」，確認位置後開始。')
                if road and self.loop_route and distance(values[-1], values[0]) > 2:
                    raise ValueError('這條道路路線沒有回程。請勾選「循環回程」後重新按「沿道路規劃」。')
                if not road and distance(self.position, values[0]) > 0.1:
                    values.insert(0, list(self.position))
                if not road and self.loop_route and distance(values[-1], values[0]) > 0.1:
                    values.append(list(values[0]))
                if len(values) > 10000:
                    raise ValueError('路線過長，請保留少於 9,999 個點以容納起點與回程。')
                self.play_route = Route(values)
                self.progress = self.traveled = 0.0
                self.laps = 0
            self.status = 'running'
            self.last_time = time.monotonic()
            self.message = '預覽行走中。' if self.mode == 'preview' else '手機定位服務已開始接收路線座標。'
        elif action == 'pause':
            if self.status == 'running':
                self.status = 'paused'
            self.message = '已暫停預覽，可繼續行走。' if self.mode == 'preview' else '已暫停；手機保留最後送出的模擬位置。'
        elif action == 'save':
            kind = data.get('kind')
            if kind not in ('favorites', 'routes'):
                raise ValueError('儲存類型不正確。')
            name = str(data.get('name', '')).strip()
            if not 1 <= len(name) <= 60:
                raise ValueError('名稱需為 1～60 個字。')
            record = {'id': uuid.uuid4().hex, 'name': name}
            if kind == 'favorites':
                record['position'] = point(data.get('position', self.position))
                category = data.get('category', 'point')
                if category not in ('mushroom', 'postcard', 'point'):
                    raise ValueError('收藏類型須為菇點、明信片或純點。')
                record['category'] = category
            else:
                if not self.route:
                    raise ValueError('尚未建立路線。')
                record['points'] = self.route.points
                record['distance'] = self.route.length
                record['meta'] = self.route_meta
            updated = copy.deepcopy(self.store.data)
            if len(updated[kind]) >= (500 if kind == 'favorites' else 100):
                raise ValueError('收藏數量已達上限，請先刪除不需要的項目。')
            updated[kind].append(record)
            self.store.save(updated)
            self.message = '已儲存在本機。'
            return self.store.data
        elif action == 'delete':
            kind = data.get('kind')
            if kind not in ('favorites', 'routes'):
                raise ValueError('儲存類型不正確。')
            updated = copy.deepcopy(self.store.data)
            updated[kind] = [r for r in updated[kind] if r.get('id') != data.get('id')]
            self.store.save(updated)
            self.message = '收藏已刪除。'
            return self.store.data
        else:
            raise ValueError('不支援的操作。')

    async def advance(self, elapsed):
        """Called under the command lock. Never catch up after sleep or a slow device."""
        if self.status != 'running' or not self.play_route:
            return
        step = self.speed/3.6*max(0, min(elapsed, 1.0))
        next_progress = self.progress+step
        laps = 0
        if self.loop_route:
            laps = int(next_progress//self.play_route.length)
            next_progress %= self.play_route.length
        else:
            next_progress = min(next_progress, self.play_route.length)
            step = next_progress-self.progress
        target = self.play_route.at(next_progress)
        try:
            await self._apply(target)
        except Exception as exc:
            self.status = 'paused'
            self.error = True
            self.message = friendly_error(exc)+' 未自動切換為預覽；請檢查手機位置。'
            return
        self.progress = next_progress
        self.traveled += step
        self.laps += laps
        if not self.loop_route and self.progress >= self.play_route.length:
            self.status = 'completed'
            self.message = '路線已完成；手機模式保留終點位置。可按「還原定位」結束模擬。'

    async def ticker(self):
        while not self.closed:
            await asyncio.sleep(1.0 if self.mode == 'device' else 0.25)
            async with self.lock:
                now = time.monotonic()
                await self.advance(now-self.last_time)
                self.last_time = time.monotonic()

    async def shutdown(self):
        self.closed = True
        self.status = 'idle'
        if self.connection_task and not self.connection_task.done():
            self.connection_task.cancel()
            try:
                await self.connection_task
            except asyncio.CancelledError:
                pass
        async with self.lock:
            self.closed = True
            self.status = 'idle'
            try:
                if self.device.active:
                    await asyncio.wait_for(self.device.clear(), 1.5)
            finally:
                await asyncio.wait_for(self.device.close(), .5)
