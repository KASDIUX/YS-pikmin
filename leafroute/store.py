import json
import os
import tempfile
import re
from pathlib import Path
from .geo import Route, point
from .routing import route_meta
from .device import same_id

DEFAULT_CATEGORIES = ({'id': 'mushroom', 'name': '菇點'},
                      {'id': 'postcard', 'name': '明信片'},
                      {'id': 'point', 'name': '純點'})


def category_name(value):
    if not isinstance(value, str):
        raise ValueError('分類名稱需為 1～24 個字。')
    name = value.strip()
    if not 1 <= len(name) <= 24 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError('分類名稱需為 1～24 個字，且不可包含換行或控制字元。')
    return name


def favorite_categories(data):
    """Keep legacy IDs stable so existing favorites never need to be rewritten."""
    categories = [dict(c) for c in DEFAULT_CATEGORIES]
    if 'favorite_categories' not in data:
        return categories
    items = data['favorite_categories']
    if not isinstance(items, list) or len(items) > 50:
        raise ValueError('收藏分類資料不正確。')
    seen = set()
    for item in items:
        identity = item.get('id') if isinstance(item, dict) else None
        if not isinstance(identity, str) or not (identity in ('mushroom', 'postcard', 'point') or re.fullmatch(r'custom-[0-9a-f]{32}', identity)) or identity in seen:
            raise ValueError('收藏分類識別碼不正確。')
        seen.add(identity)
        category = {'id': identity, 'name': category_name(item.get('name'))}
        existing = next((c for c in categories if c['id'] == identity), None)
        if existing is not None:
            existing.update(category)
        else:
            categories.append(category)
    names = [c['name'].casefold() for c in categories]
    if len(categories) > 50 or len(names) != len(set(names)):
        raise ValueError('收藏分類名稱重複或數量超過上限。')
    return categories


def wifi_profiles(data):
    """Migrate the old single remembered phone without changing location records."""
    records = []
    items = data.get('wifi_devices', [])
    items = list(items) if isinstance(items, list) else []
    if isinstance(data.get('wifi_device'), dict):
        items.append(data['wifi_device'])
    for item in items:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ('id', 'name', 'ios', 'host', 'transport')) or not item['id']:
            continue
        if not any(same_id(item['id'], old['id']) for old in records):
            profile = {k: item[k] for k in ('id', 'name', 'ios', 'host', 'transport')}
            alias = item.get('alias')
            if isinstance(alias, str) and 1 <= len(alias.strip()) <= 40 and not any(ord(c) < 32 or ord(c) == 127 for c in alias):
                profile['alias'] = alias.strip()
            records.append(profile)
    return records


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
                categories = favorite_categories(loaded)
                for kind in self.data:
                    for item in loaded[kind]:
                        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not isinstance(item.get('name'), str):
                            raise ValueError('invalid record')
                        if kind == 'favorites':
                            item['position'] = point(item.get('position'))
                            category = item.get('category', 'point')
                            if not isinstance(category, str) or not any(c['id'] == category for c in categories):
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
                    self.data['wifi_device'] = next((dict(p) for p in wifi_profiles(loaded) if same_id(p['id'], wifi['id'])), None)
                self.data['wifi_devices'] = wifi_profiles(loaded)
                self.data['favorite_categories'] = categories
            except (ValueError, OSError):
                self.warning = '收藏資料無法讀取，原檔已保留。請先備份並修復 data/library.json，再重新啟動。'
        self.data.setdefault('favorite_categories', favorite_categories({}))

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
