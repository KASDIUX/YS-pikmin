import json,unittest
from pathlib import Path
import test_home
from leafroute.store import Store
from leafroute.store import favorite_categories

class FavoriteCategoryTests(unittest.IsolatedAsyncioTestCase):
    setUp=test_home.HomeTests.setUp
    tearDown=test_home.HomeTests.tearDown
    async def test_categories_persist_without_moving(self):
        original=list(self.engine.position)
        for category in ('mushroom','postcard','point'):
            await self.engine.command('save',{'kind':'favorites','name':category,'position':[25,121],'category':category})
        self.assertEqual([r['category'] for r in Store(self.path).data['favorites']],['mushroom','postcard','point'])
        self.assertEqual(self.engine.position,original)
        self.assertEqual(self.device.positions,[])
    async def test_old_favorite_defaults_to_point(self):
        self.path.write_text(json.dumps({'favorites':[{'id':'old','name':'Old','position':[25,121]}],'routes':[]}))
        store=Store(self.path)
        self.assertEqual(store.data['favorites'][0]['category'],'point')
        self.assertFalse(store.warning)
    async def test_invalid_category_rejected_without_save(self):
        for category in ('other',None,[],123):
            with self.assertRaises(ValueError):
                await self.engine.command('save',{'kind':'favorites','name':'Invalid','category':category})
        self.assertFalse(self.path.exists())

    async def test_custom_category_save_favorite_and_restart(self):
        result = await self.engine.command('category-save', {'name': '  大電蘑菇  '})
        category = result['category']
        self.assertEqual(category['name'], '大電蘑菇')
        self.assertTrue(category['id'].startswith('custom-'))
        await self.engine.command('save', {'kind':'favorites', 'name':'附近公園', 'position':[25,121], 'category':category['id']})
        store = Store(self.path)
        self.assertFalse(store.warning)
        self.assertEqual(store.data['favorite_categories'][-1], category)
        self.assertEqual(store.data['favorites'][0]['category'], category['id'])
        self.assertEqual(self.device.positions, [])

    async def test_rename_keeps_favorites_and_other_data(self):
        await self.engine.command('set-home', {'position':[25,121]})
        await self.engine.command('save', {'kind':'favorites', 'name':'舊收藏', 'position':[24,120], 'category':'mushroom'})
        before = json.loads(self.path.read_text(encoding='utf-8'))
        await self.engine.command('category-save', {'id':'mushroom', 'name':'我的菇點'})
        after = Store(self.path)
        self.assertFalse(after.warning)
        self.assertEqual(after.data['favorites'], before['favorites'])
        self.assertEqual(after.data['home'], before['home'])
        self.assertEqual(after.data['routes'], before['routes'])
        self.assertEqual(after.data['favorite_categories'][0]['name'], '我的菇點')
        self.assertEqual(self.device.positions, [])

    async def test_legacy_categories_migrate_without_rewriting_file(self):
        original = json.dumps({'favorites':[{'id':'old','name':'舊菇點','position':[25,121],'category':'mushroom'}], 'routes':[], 'home':[24,120]})
        self.path.write_text(original, encoding='utf-8')
        store = Store(self.path)
        self.assertFalse(store.warning)
        self.assertEqual(len(store.data['favorite_categories']), 3)
        self.assertEqual(store.data['favorites'][0]['category'], 'mushroom')
        self.assertEqual(self.path.read_text(encoding='utf-8'), original)

    async def test_invalid_names_duplicates_and_unknown_rename_are_atomic(self):
        await self.engine.command('category-save', {'name':'Travel'})
        before = self.path.read_bytes()
        for payload in ({'name':''}, {'name':' '*5}, {'name':'x'*25}, {'name':None}, {'name':'line\nbreak'}, {'name':' travel '}, {'name':'菇點'}, {'id':'missing','name':'新名字'}):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    await self.engine.command('category-save', payload)
                self.assertEqual(self.path.read_bytes(), before)

    async def test_move_existing_favorite_to_custom_category(self):
        await self.engine.command('save', {'kind':'favorites', 'name':'舊收藏', 'position':[25,121]})
        original = dict(self.engine.store.data['favorites'][0])
        category = (await self.engine.command('category-save', {'name':'日本旅行'}))['category']
        await self.engine.command('favorite-category', {'id':original['id'], 'category':category['id']})
        favorite = Store(self.path).data['favorites'][0]
        self.assertEqual(favorite, {**original, 'category':category['id']})
        self.assertEqual(self.device.positions, [])
        before = self.path.read_bytes()
        for payload in ({'id':'missing','category':category['id']}, {'id':original['id'],'category':'unknown'}, {'id':original['id'],'category':[]}):
            with self.assertRaises(ValueError):
                await self.engine.command('favorite-category', payload)
            self.assertEqual(self.path.read_bytes(), before)

    async def test_damaged_categories_preserve_original_file(self):
        for categories in (None, [{'id':'custom-bad','name':'bad'}], [{'id':'point','name':'菇點'}], [{'id':'point','name':'純點'},{'id':'point','name':'重複'}]):
            self.path.write_text(json.dumps({'favorites':[], 'routes':[], 'favorite_categories':categories}), encoding='utf-8')
            before = self.path.read_bytes()
            store = Store(self.path)
            self.assertTrue(store.warning)
            with self.assertRaises(ValueError):
                store.save(store.data)
            self.assertEqual(self.path.read_bytes(), before)

    async def test_category_limit_rejects_without_changing_data(self):
        for i in range(47):
            await self.engine.command('category-save', {'name':f'分類{i}'})
        self.assertEqual(len(favorite_categories(self.engine.store.data)), 50)
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            await self.engine.command('category-save', {'name':'超出上限'})
        self.assertEqual(self.path.read_bytes(), before)
