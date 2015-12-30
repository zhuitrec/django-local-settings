import os
import re
from collections import Mapping, OrderedDict, Sequence
from itertools import takewhile

from django.utils.module_loading import import_string

from six import string_types

from .base import Base
from .exc import SettingsFileNotFoundError
from .types import LocalSetting
from .util import NO_DEFAULT, NO_DEFAULT as PLACEHOLDER


class Loader(Base):

    def __init__(self, file_name=None, section=None, extender=None):
        super(Loader, self).__init__(file_name, section, extender)
        if not os.path.exists(self.file_name):
            raise SettingsFileNotFoundError(file_name)
        # Registry of local settings with a value in the settings file
        self.registry = {}

    def read_file(self):
        """Read settings from specified ``section`` of config file."""
        parser = self._make_parser()
        with open(self.file_name) as fp:
            parser.read_file(fp)
        extends = parser[self.section].get('extends')
        settings = OrderedDict()
        if extends:
            extends = self._parse_setting(extends)
            if isinstance(extends, str):
                extends = [extends]
            for e in reversed(extends):
                settings.update(self.__class__(e, extender=self).read_file())
        settings_from_file = parser[self.section]
        remove = [k for k in settings if k in settings_from_file]
        for k in remove:
            del settings[k]
        settings.update(settings_from_file)
        return settings

    def load(self, base_settings: Mapping) -> OrderedDict:
        """Merge local settings from file with ``base_settings``.

        Returns a new OrderedDict containing the base settings and the
        loaded settings. Ordering is:

            - base settings
            - settings from extended file(s), if any
            - settings from file

        When a setting is overridden, it gets moved to the end.

        """
        if not os.path.exists(self.file_name):
            self.print_warning(
                'Local settings file `{0}` not found'.format(self.file_name))
            return

        settings = OrderedDict((k, v) for (k, v) in base_settings.items() if k.isupper())

        for name, value in self.read_file().items():
            value = self._parse_setting(value)

            def visit(obj, segment, item, next_segment, args):
                # Ensure newer settings are ordered after older settings
                # so interpolation that depends on older settings will
                # work in a deterministic way.
                if isinstance(obj, OrderedDict):
                    obj.move_to_end(segment)
                if next_segment is None:  # Reached setting
                    obj[segment] = value
                    # If there's already a LocalSetting in this slot, set the
                    # value of that LocalSetting and put it in the registry so
                    # it can be easily retrieved later.
                    if isinstance(item, LocalSetting):
                        item.value = value
                        self.registry[item] = segment

            self._traverse(settings, name, visit=visit, create_missing=True, default=None)

        settings.pop('extends', None)
        self._interpolate(settings, settings)
        self._import_from_string(settings)
        self._append_extras(settings)
        return settings

    def _parse_path(self, path):
        """Parse ``path`` into segments.

        Paths must start with a WORD (i.e., a top level Django setting
        name). Path segments are separated by dots. Compound path
        segments (i.e., a name with a dot in it) can be grouped inside
        parentheses.

        Examples::

            >>> loader = Loader()
            >>> loader._parse_path('WORD')
            ['WORD']
            >>> loader._parse_path('WORD.x')
            ['WORD', 'x']
            >>> loader._parse_path('WORD.(x)')
            ['WORD', 'x']
            >>> loader._parse_path('WORD.(x.y)')
            ['WORD', 'x.y']
            >>> loader._parse_path('WORD.(x.y).z')
            ['WORD', 'x.y', 'z']

        An example of where compound names are actually useful is in
        logger settings::

            LOGGING.loggers.(package.module).handlers = ["console"]
            LOGGING.loggers.(package.module).level = "DEBUG"

        Any segment that looks like an int will be converted to an int.
        Segments that start with a leading '0' followed by other digits
        will not be converted.

        """
        segments = []
        ipath = iter(path)
        for char in ipath:
            segment, end = ([], ')') if char == '(' else ([char], '.')
            # Note: takewhile() consumes the end character
            segment.extend(takewhile(lambda c: c != end, ipath))
            segment = ''.join(segment)
            segment = self._convert_name(segment)
            segments.append(segment)
            if end == ')':
                # Consume dot after right paren
                next(ipath, None)
        return segments

    def _traverse(self, obj, name, visit=None, args=None, last_only=False, create_missing=False,
                  default=NO_DEFAULT):
        """Traverse to the item specified by ``name``.

        If no ``visit`` function is passed, this will simply retrieve
        the value of the item specified by ``name``. Otherwise...

        For each object along the way, starting with ``obj``, call
        ``visit`` with the following args:

            - Current object
            - Key (next key to retrieve from current object)
            - Value (value of next key)
            - Next key
            - ``args``

        As an example, imagine ``obj`` is the following settings dict::

            {
                'PANTS': {
                    'types': ['jeans', 'slacks'],
                    'total': 10,
                }
            }

        Then calling this method with ``name='PANTS.types.0'`` would
        result in the following calls to ``visit``::

            visit(
                obj,
                'PANTS',
                {'types': ['jeans', 'slacks'], 'total': 10},
                'types',
                args)

            visit(
                {'types': ['jeans', 'slacks'], 'total': 10},
                'types',
                ['jeans', 'slacks'],
                0,
                args)

            visit(
                ['jeans', 'slacks'],
                0,
                'jeans',
                NO_DEFAULT,
                args)

        Generally, the ``visit`` function shouldn't return anything
        other than ``None``; if it does, the returned value will become
        the next object instead of getting the next object from the
        current object. This is esoteric and should probably be ignored.

        In the common case where you just want to process the value of
        the setting specified by ``name``, pass ``last_only=True``.

        To create missing items on the way to the ``name``d item, pass
        ``create_missing=True``. This will insert an item for each
        missing segment in ``name``. The type and value of item that
        will be inserted for a missing segment depends on the *next*
        segment. If a ``default`` value is passed, the ``name``d item
        will be set to this value; otherwise, a default default will
        be used. See :meth:`_get_or_create_segment` for more info.

        """
        segments = self._parse_path(name)
        visit_segments = visit is not None
        visit_all = not last_only
        for segment, next_segment in zip(segments, segments[1:] + [None]):
            last = next_segment is None
            if create_missing:
                segment_default = default if last else NO_DEFAULT
                val = self._get_or_create_segment(obj, segment, next_segment, segment_default)
            else:
                val = obj[segment]
            if visit_segments and (visit_all or last):
                result = visit(obj, segment, val, next_segment, args)
                obj = result if result is not None else val
            else:
                obj = val
        return obj

    def _get_or_create_segment(self, obj, segment, next_segment, default=NO_DEFAULT) -> object:
        """Get ``obj[segment]``; create ``obj[segment]`` if missing.

        The default value for a missing segment is based on the *next*
        segment, unless a ``default`` is explicitly passed.

        If the next segment is an int, the default will be a list with
        the indicated number of items. Otherwise the default will be
        an OrderedDict.

        """
        if default is NO_DEFAULT:
            if isinstance(next_segment, int):
                default = [PLACEHOLDER] * (next_segment + 1)
            else:
                default = OrderedDict()
        if isinstance(obj, Mapping):
            if segment not in obj:
                obj[segment] = default
        elif isinstance(obj, Sequence):
            while segment >= len(obj):
                obj.append(PLACEHOLDER)
            if obj[segment] is PLACEHOLDER:
                obj[segment] = default
        return obj[segment]

    def _interpolate(self, v, settings):
        if isinstance(v, string_types):
            v = v.format(**settings)
        elif isinstance(v, Mapping):
            for k in v:
                new_k = k.format(**settings)
                v[new_k] = self._interpolate(v[k], settings)
                if k != new_k:
                    del v[k]
        elif isinstance(v, Sequence):
            v = v.__class__(self._interpolate(item, settings) for item in v)
        return v

    def _import_from_string(self, settings):
        import_ = settings.get('IMPORT_FROM_STRING')
        if not import_:
            return

        def visit(obj, key, val, next_key, args):
            if isinstance(val, string_types):
                obj[key] = import_string(val)

        for name in import_:
            self._traverse(settings, name, visit, last_only=True)

    def _append_extras(self, settings):
        extras = settings.get('EXTRA')
        if not extras:
            return

        def visit(obj, key, val, next_key, args):
            if not isinstance(val, Sequence):
                raise TypeError('EXTRA only works with list-type settings')
            extra_val = args['extra_val']
            if extra_val is not None:
                obj[key] = val + extra_val

        for name, extra_val in extras.items():
            visit_args = {'extra_val': extra_val}
            self._traverse(settings, name, visit, args=visit_args, last_only=True)

    def _convert_name(self, name):
        """Convert ``name`` to int if it looks like an int.

        Otherwise, return it as is.

        """
        if re.search('^\d+$', name):
            if len(name) > 1 and name[0] == '0':
                # Don't treat strings beginning with "0" as ints
                return name
            return int(name)
        return name
