"""Explicit, low-volume routing against the FOSSGIS OSRM service."""
import json
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .geo import Route, distance, point

PROFILES = {'foot': '步行', 'bike': '騎車', 'car': '開車'}
BASE_URL = 'https://routing.openstreetmap.de'
MAX_RESPONSE = 4_000_000


def control_points(values):
    if not isinstance(values, list) or not 2 <= len(values) <= 25:
        raise ValueError('道路規劃需要 2～25 個途經點。請用少量點標示起點、途經點與終點。')
    points = [point(p) for p in values]
    route = Route(points)
    if route.length > 100_000:
        raise ValueError('單次規劃的途經點總直線距離上限為 100 公里。請拆成較短路線。')
    return points


def route_meta(value=None):
    if value is None:
        return {'kind': 'manual'}
    if not isinstance(value, dict) or value.get('kind') not in ('manual', 'gpx', 'road'):
        raise ValueError('路線類型不正確。')
    if value['kind'] != 'road':
        return {'kind': value['kind']}
    if value.get('profile') not in PROFILES or not isinstance(value.get('round_trip', False), bool):
        raise ValueError('道路模式不正確。')
    return {'kind': 'road', 'profile': value['profile'],
            'waypoints': control_points(value.get('waypoints')),
            'round_trip': value.get('round_trip', False)}


def build_request(values, profile, round_trip=False):
    points = control_points(values)
    if profile not in PROFILES or not isinstance(round_trip, bool):
        raise ValueError('請選擇步行、騎車或開車。')
    query_points = [list(p) for p in points]
    if round_trip and distance(query_points[0], query_points[-1]) > 0.1:
        query_points.append(list(query_points[0]))
    coords = ';'.join(f'{lon:.7f},{lat:.7f}' for lat, lon in query_points)
    query = urlencode({'overview': 'full', 'geometries': 'geojson', 'steps': 'false',
                       'alternatives': 'false', 'generate_hints': 'false',
                       'radiuses': ';'.join(['150']*len(query_points))})
    # The path segment is historical; each routed-* server has its own actual profile.
    url = f'{BASE_URL}/routed-{profile}/route/v1/driving/{coords}?{query}'
    return Request(url, headers={'User-Agent': 'LeafRoute/0.2.0 (local desktop route planner)',
                                 'Accept': 'application/json'}), query_points


def parse_response(payload, requested):
    if not isinstance(payload, dict):
        raise ValueError('道路服務回應格式不正確，原路線未變更。')
    code = payload.get('code')
    if code != 'Ok':
        message = {'NoRoute': '找不到可連通的道路，請移動路徑點或更換模式。',
                   'NoSegment': '有路徑點距離可用道路太遠，請把點移到道路附近。'}.get(code, '道路服務無法完成這次規劃，請檢查路徑點。')
        raise ValueError(message)
    try:
        geometry = payload['routes'][0]['geometry']
        coords = geometry['coordinates']
        if geometry.get('type') != 'LineString' or not isinstance(coords, list) or not 2 <= len(coords) <= 9998:
            raise ValueError('道路路線過長或格式不正確，請縮短規劃範圍。')
        if any(not isinstance(c, list) or len(c) != 2 for c in coords):
            raise ValueError('道路服務回傳了無效座標。')
        route = Route([[c[1], c[0]] for c in coords])
        waypoints = payload['waypoints']
        if len(waypoints) != len(requested):
            raise ValueError('道路服務的途經點數量不相符。')
        snapped = [point([w['location'][1], w['location'][0]]) for w in waypoints]
        snap_distance = max(distance(a, b) for a, b in zip(requested, snapped))
        if snap_distance > 151:
            raise ValueError('規劃結果偏離路徑點超過 150 公尺，請重新選點。')
        if distance(route.points[0], snapped[0]) > 5 or distance(route.points[-1], snapped[-1]) > 5:
            raise ValueError('道路服務回傳的路線起終點不相符。')
        return route.points, snap_distance
    except (KeyError, IndexError, TypeError):
        raise ValueError('道路服務回應不完整，原路線未變更。') from None


class RoadPlanner:
    def __init__(self, opener=None):
        self.opener = opener or urlopen
        self.lock = threading.Lock()
        self.last_request = float('-inf')

    def plan(self, values, profile, round_trip=False):
        request, query_points = build_request(values, profile, round_trip)
        if not self.lock.acquire(blocking=False):
            raise ValueError('另一個道路規劃仍在進行，請稍候。')
        try:
            if time.monotonic()-self.last_request < 2:
                raise ValueError('請稍候兩秒再規劃，避免頻繁查詢公共服務。')
            self.last_request = time.monotonic()
            started = time.monotonic()
            try:
                with self.opener(request, timeout=10) as response:
                    chunks, size = [], 0
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_RESPONSE:
                            raise ValueError('道路服務回應過大，請縮短規劃範圍。')
                        if time.monotonic()-started > 15:
                            raise TimeoutError()
                        chunks.append(chunk)
                payload = json.loads(b''.join(chunks))
            except HTTPError as exc:
                if exc.code == 429:
                    raise ValueError('道路服務目前限制查詢次數，請稍後再試；原路線保留。') from None
                if exc.code == 400:
                    try:
                        payload = json.loads(exc.read(65536))
                    except (ValueError, OSError):
                        raise ValueError('道路服務無法處理這組路徑點。') from None
                else:
                    raise ValueError('道路服務暫時無法使用，請稍後再試；原路線保留。') from None
            except (TimeoutError, URLError, OSError):
                raise ValueError('道路查詢逾時或無法連線；原路線保留，請確認網路後重試。') from None
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError('道路服務回應不是有效資料，原路線保留。') from None
            points, snap_distance = parse_response(payload, query_points)
            if round_trip and distance(points[0], points[-1]) > 2:
                raise ValueError('道路服務未形成完整回程，請調整起點後重試。')
            return {'points': points, 'meta': route_meta({'kind': 'road', 'profile': profile,
                    'waypoints': values, 'round_trip': round_trip}),
                    'snap_distance': round(snap_distance, 1), 'provider': 'FOSSGIS / OSRM'}
        finally:
            self.lock.release()
