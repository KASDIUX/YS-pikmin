import json,unittest
from pathlib import Path
import test_home
from leafroute.store import Store

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
