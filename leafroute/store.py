import json
import os
import tempfile
from pathlib import Path
from .geo import Route, point
from .routing import route_meta


class Store:
    """Atomic saves; a damaged file is preserved and never silently overwritten."""
    def __init__(self, path):
        self.path = Path(path)
        self.warning = ''
        self.data = {'favorites': [], 'routes': []}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(loaded, dict) or not all(isinstance(loaded.get(k), list) for k in self.data):
                    raise ValueError('invalid schema')
                for kind in self.data:
                    for item in loaded[kind]:
                        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not isinstance(item.get('name'), str):
                            raise ValueError('invalid record')
                        if kind == 'favorites':
                            item['position'] = point(item.get('position'))
                            category = item.get('category', 'point')
                            if category not in ('mushroom', 'postcard', 'point'):
                                raise ValueError('invalid favorite category')
                            item['category'] = category
                        else:
                            route = Route(item.get('points'))
                            item['points'], item['distance'] = route.points, route.length
                            item['meta'] = route_meta(item.get('meta'))
                home = loaded.get('home')
                if home is not None:
                    home = point(home)
                self.data = {k: loaded[k] for k in self.data}
                self.data['home'] = home
                wifi = loaded.get('wifi_device')
                if isinstance(wifi,dict) and all(isinstance(wifi.get(k),str) for k in ('id','name','ios','host','transport')):
                    self.data['wifi_device'] = {k:wifi[k] for k in ('id','name','ios','host','transport')}
            except (ValueError, OSError):
                self.warning = '收藏資料無法讀取，原檔已保留。請先備份並修復 data/library.json，再重新啟動。'

    def save(self, data):
        if self.warning:
            raise ValueError(self.warning)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.library-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            self.data = data
        finally:
            if os.path.exists(name):
                os.unlink(name)
