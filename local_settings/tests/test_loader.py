import os
import unittest
from collections import OrderedDict

from ..loader import Loader
from ..types import LocalSetting


LOCAL_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'local.cfg#test')


class Base(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.loader = Loader(LOCAL_SETTINGS_FILE)


class TestPathParsing(Base):

    def test_simple_path(self):
        segments = self.loader._parse_path('XYZ')
        self.assertEqual(segments, ['XYZ'])

    def test_dotted_path(self):
        segments = self.loader._parse_path('XYZ.abc')
        self.assertEqual(segments, ['XYZ', 'abc'])

    def test_multi_dotted_path(self):
        segments = self.loader._parse_path('XYZ.abc.x.y.z')
        self.assertEqual(segments, ['XYZ', 'abc', 'x', 'y', 'z'])

    def test_compound_path_at_end(self):
        segments = self.loader._parse_path('XYZ.(a.b.c)')
        self.assertEqual(segments, ['XYZ', 'a.b.c'])

    def test_compound_path_in_middle(self):
        segments = self.loader._parse_path('XYZ.(a.b.c).d')
        self.assertEqual(segments, ['XYZ', 'a.b.c', 'd'])

    def test_non_dotted_compound_path(self):
        segments = self.loader._parse_path('XYZ.(abc)')
        self.assertEqual(segments, ['XYZ', 'abc'])

    def test_multi_non_dotted_compound_path_at_end(self):
        segments = self.loader._parse_path('XYZ.(a).(b).(c)')
        self.assertEqual(segments, ['XYZ', 'a', 'b', 'c'])

    def test_multi_non_dotted_compound_path_in_middle(self):
        segments = self.loader._parse_path('XYZ.(a).(b).(c).dddd')
        self.assertEqual(segments, ['XYZ', 'a', 'b', 'c', 'dddd'])

    def test_complex_path(self):
        segments = self.loader._parse_path('XYZ.(a).(b.b).c.(d)')
        self.assertEqual(segments, ['XYZ', 'a', 'b.b', 'c', 'd'])


class TestLoading(Base):

    def test_loading(self):
        local_setting = LocalSetting('default value')
        self.assertEqual(local_setting.default, 'default value')
        self.assertEqual(local_setting.value, 'default value')

        settings = self.loader.load({
            'LOCAL_SETTING': local_setting,
        })

        expected = OrderedDict((
            ('PACKAGE', 'local_settings'),

            ('LOCAL_SETTING', 'local value'),

            ('A', OrderedDict((
                ('b', OrderedDict((
                    ('c', 1),
                    ('d', 2),
                ))),
            ))),

            ('X', OrderedDict((
                ('y', OrderedDict((
                    ('z', '1'),
                ))),
            ))),

            ('LIST', ['a', 'b']),

            ('TEMPLATES', [
                OrderedDict((
                    ('BACKEND', 'package.module.Class'),
                    ('OPTIONS', OrderedDict((
                        ('context_processors', ['a.b', 'x.y.z']),
                    ))),
                )),
            ])
        ))
        self.assertEqual(local_setting.default, 'default value')
        self.assertEqual(local_setting.value, 'local value')
        self.assertEqual(list(settings.items()), list(expected.items()))