"""Validated coordinates, great-circle playback, and bounded GPX exchange."""
import bisect
import math
import xml.etree.ElementTree as ET

EARTH = 6_371_008.8
MAX_POINTS = 10000
MAX_GPX_BYTES = 2_000_000


def point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("請輸入緯度、經度兩個數字。")
    if any(isinstance(x, bool) for x in value):
        raise ValueError("座標不能是布林值。")
    try:
        lat, lon = map(float, value)
    except (TypeError, ValueError):
        raise ValueError("座標必須是數字。") from None
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("緯度須介於 -90～90，經度須介於 -180～180。")
    return [lat, lon]


def distance(a, b):
    p, q = math.radians(a[0]), math.radians(b[0])
    h = math.sin((q-p)/2)**2 + math.cos(p)*math.cos(q)*math.sin(math.radians(b[1]-a[1])/2)**2
    return 2*EARTH*math.asin(math.sqrt(max(0, min(1, h))))


def destination(start, bearing, meters):
    lat, lon = map(math.radians, start)
    angle, course = meters/EARTH, math.radians(bearing)
    out_lat = math.asin(max(-1, min(1, math.sin(lat)*math.cos(angle)+math.cos(lat)*math.sin(angle)*math.cos(course))))
    out_lon = lon + math.atan2(math.sin(course)*math.sin(angle)*math.cos(lat), math.cos(angle)-math.sin(lat)*math.sin(out_lat))
    return [math.degrees(out_lat), (math.degrees(out_lon)+180)%360-180]


def interpolate(a, b, fraction):
    if fraction <= 0:
        return list(a)
    if fraction >= 1:
        return list(b)
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    delta = math.radians(b[1]-a[1])
    bearing = math.degrees(math.atan2(math.sin(delta)*math.cos(lat2), math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(delta)))
    return destination(a, bearing, distance(a, b)*fraction)


class Route:
    def __init__(self, values):
        if not isinstance(values, list) or not 2 <= len(values) <= MAX_POINTS:
            raise ValueError(f"路線需要 2～{MAX_POINTS} 個點。")
        self.points = []
        for value in values:
            p = point(value)
            if not self.points or distance(self.points[-1], p) > 0.001:
                self.points.append(p)
        self.cumulative = [0.0]
        for a, b in zip(self.points, self.points[1:]):
            self.cumulative.append(self.cumulative[-1]+distance(a, b))
        self.length = self.cumulative[-1]
        if self.length < 0.1:
            raise ValueError("路線太短；請加入不同的位置。")

    def at(self, meters):
        if meters <= 0:
            return list(self.points[0])
        if meters >= self.length:
            return list(self.points[-1])
        i = bisect.bisect_right(self.cumulative, meters)-1
        fraction = (meters-self.cumulative[i])/(self.cumulative[i+1]-self.cumulative[i])
        return interpolate(self.points[i], self.points[i+1], fraction)


def read_gpx(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_GPX_BYTES:
        raise ValueError("GPX 檔案上限為 2 MB。")
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError("不支援含 DTD 或實體宣告的 GPX。")
    try:
        root = ET.fromstring(text)
        tag = lambda e: e.tag.rsplit('}', 1)[-1]
        if tag(root) != 'gpx':
            raise ValueError("檔案不是 GPX。")
        segments = [[e for e in segment if tag(e) == 'trkpt'] for segment in root.iter() if tag(segment) == 'trkseg']
        segments = [s for s in segments if s]
        if len(segments) > 1:
            raise ValueError("這份 GPX 含多段軌跡。請先拆成單段檔案，避免把不相連的路段接在一起。")
        if segments:
            nodes = segments[0]
        else:
            routes = [[e for e in r if tag(e) == 'rtept'] for r in root if tag(r) == 'rte']
            routes = [r for r in routes if r]
            if len(routes) > 1:
                raise ValueError("這份 GPX 含多條路線，請先選擇一條匯出。")
            nodes = routes[0] if routes else [e for e in root if tag(e) == 'wpt']
        return Route([[e.attrib['lat'], e.attrib['lon']] for e in nodes]).points
    except (ET.ParseError, KeyError):
        raise ValueError("GPX 格式不完整，或缺少經緯度。") from None


def write_gpx(points):
    route = Route(points)
    ns = 'http://www.topografix.com/GPX/1/1'
    ET.register_namespace('', ns)
    root = ET.Element(f'{{{ns}}}gpx', version='1.1', creator='YS-pikmin')
    track = ET.SubElement(root, f'{{{ns}}}trk')
    ET.SubElement(track, f'{{{ns}}}name').text = 'YS-pikmin route'
    segment = ET.SubElement(track, f'{{{ns}}}trkseg')
    for lat, lon in route.points:
        ET.SubElement(segment, f'{{{ns}}}trkpt', lat=f'{lat:.8f}', lon=f'{lon:.8f}')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
