import unittest
import test_home
from leafroute.geo import distance

class JoystickTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_home.HomeTests.setUp
    tearDown = test_home.HomeTests.tearDown
    async def test_step_speed_and_direction(self):
        original=list(self.engine.position)
        await self.engine.command('joystick',{'bearing':90,'speed':18,'strength':1})
        self.assertAlmostEqual(distance(original,self.engine.position),1,places=4)
        self.assertGreater(self.engine.position[1],original[1])
        original=list(self.engine.position)
        await self.engine.command('joystick',{'bearing':0,'speed':18,'strength':.5})
        self.assertAlmostEqual(distance(original,self.engine.position),.5,places=4)
        self.assertEqual(self.device.positions,[])
        last=list(self.engine.position)
        await self.engine.advance(1)
        self.assertEqual(self.engine.position,last)
    async def test_reject_invalid_and_running(self):
        for values in [{'bearing':360,'speed':5},{'bearing':0,'speed':31},{'bearing':True,'speed':5},{'bearing':0,'speed':5,'strength':0}]:
            with self.assertRaises(ValueError): await self.engine.command('joystick',values)
        self.engine.status='running'
        with self.assertRaises(ValueError): await self.engine.command('joystick',{'bearing':0,'speed':5})
    async def test_device_step_and_failure(self):
        self.engine.mode='device';self.engine.connected=True
        await self.engine.command('joystick',{'bearing':180,'speed':5})
        self.assertEqual(len(self.device.positions),1)
        self.device.fail=True
        with self.assertRaises(ValueError): await self.engine.command('joystick',{'bearing':0,'speed':5})
        self.assertFalse(self.engine.connected)
