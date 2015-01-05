from ConfigParser import RawConfigParser, SafeConfigParser, NoSectionError, DuplicateSectionError, NoOptionError
import os
import re
from StringIO import StringIO
import sys

from localconfig.utils import is_float, is_int, is_long, is_bool, is_none, is_config, CONFIG_KEY_RE, to_bool

NON_ALPHA_NUM = re.compile('[^A-Za-z0-9]')
NO_DEFAULT_VALUE = 'NO-DEFAULT-VALUE'


class DotNotationConfig(object):
  """
  Wrapper for ConfigParser that allows configs to be accessed thru a dot notion method with data type support.
  """

  LAST_COMMENT_KEY = 'LAST_COMMENT_KEY'

  class SectionAccessor(object):
    """
    Provides access (read/write/iter) for a config section.
    """

    def __init__(self, config, section):
        self._config = config
        self._section = section

    def __getattr__(self, key):
      """
      Get config value

      :param str key: Config key to get value for
      """
      return self._config.get(self._section, key)

    def __setattr__(self, key, value):
      """
      Set config value

      :param str key: Config key to set value for
      :param str value: Config value to set to
      """
      if key in ['_config', '_section']:
        super(DotNotationConfig.SectionAccessor, self).__setattr__(key, value)
      else:
        return self._config.set(self._section, key, value)

    def __iter__(self):
      return self._config.items(self._section)

  def __init__(self, last_source=None, interpolation=False, kv_sep=' = ', indent_spaces=4, compact_form=False):
    """
    :param file/str last_source: Last config source file name. This source is only read when an attempt to read a
                                 config value is made (delayed reading, hence "last") if it exists.
                                 It is also the default target file location for :meth:`self.save`
                                 Defaults to ~/.config/<PROGRAM_NAME> (if exists)
    :param bool interpolation: Support interpolation (use SafeConfigParser instead of RawConfigParser)
    :param str kv_sep: When serializing, separator used for key and value.
    :param int indent_spaces: When serializing, number of spaces to use when indenting a value spanning multiple lines.
    :param bool compact_form: Serialize in compact form, such as no new lines between each config key.
    """
    if not last_source and sys.argv:
      user_source = os.path.join('~', '.config', os.path.basename(sys.argv[0]))
      if os.path.exists(user_source):
        last_source = user_source

    #: User config file name
    self._last_source = last_source and os.path.expanduser(last_source)

    #: Indicate if `self._last_source` has been read
    self._last_source_read = False

    #: Parser instance from ConfigParser that does the underlying config parsing
    self._parser = SafeConfigParser() if interpolation else RawConfigParser()

    #: A dict that maps (section, key) to its comment.
    self._comments = {}

    #: A dict that maps dot notation section.key to its actual (section, key)
    self._dot_keys = {}

    #: Seperator for key/value. Used for save only.
    self._kv_sep = kv_sep

    #: Number of spaces to use when indenting a value spanning multiple lines.
    self._indent_spaces = indent_spaces

    #: Save in compact form (no newline between keys)
    self._compact_form = compact_form

    #: Cache to avoid transforming value too many times
    self._value_cache = {}

  @classmethod
  def _to_dot_key(cls, section, key=None):
    """ Return the section and key in dot notation format. """
    if key:
      return (NON_ALPHA_NUM.sub('_', section.lower()), NON_ALPHA_NUM.sub('_', key.lower()))
    else:
      return NON_ALPHA_NUM.sub('_', section.lower())

  def _add_dot_key(self, section, key=None):
    """
    :param str section: Config section
    :param str key: Config key
    """
    if key:
      self._dot_keys[self._to_dot_key(section, key)] = (section, key)
    else:
      self._dot_keys[self._to_dot_key(section)] = section

  def read(self, source):
    """
    Reads and parses the config source

    :param file/str source: Config source string, file name, or file pointer.
    """

    if (isinstance(source, str) or isinstance(source, unicode)) and is_config(source):
      source_fp = StringIO(source)
    elif isinstance(source, file) or isinstance(source, StringIO):
      source_fp = source
    else:
      source_fp = open(source)

    self._parser.readfp(source_fp)
    self._parse_extra(source_fp)

  def __str__(self):
    self._read_last_source()

    output = []
    extra_newline = '' if self._compact_form else '\n'

    for section in self._parser.sections():
      if section in self._comments:
        output.append(self._comments[section])
      elif output:
        output.append('')

      output.append('[%s]%s' % (section, extra_newline))

      for key, value in self._parser.items(section):
        if (section, key) in self._comments:
          output.append(self._comments[(section, key)])
        value = ('\n' + ' ' * self._indent_spaces).join(value.split('\n'))
        output.append('%s%s%s%s' % (key, self._kv_sep, value, extra_newline))

    if self.LAST_COMMENT_KEY in self._comments:
      output.append(self._comments[self.LAST_COMMENT_KEY])

    return '\n'.join(output)


  def save(self, target_file=None, as_template=False):
    """
    Save the config

    :param str target_file: File to save to. Defaults to `self._last_source` if set
    :param bool as_template: Save the config with all keys and sections commented out for user to modify
    :raise AttributeError: if target file is not provided and `self._last_source` is not set
    """
    if not target_file:
      if not self._last_source:
        raise AttributeError('target_file is required when last source is not set during instantiation')
      target_file = self._last_source

    output = str(self)

    if as_template:
      output_tmpl = []
      for line in output.split('\n'):
        if line and not line.startswith('#'):
          line = '# %s' % line
        output_tmpl.append(line)
      output = '\n'.join(output_tmpl)

    with open(target_file, 'w') as fp:
      fp.write(output)

  def _parse_extra(self, fp):
    """ Parse and store the config comments and create maps for dot notion lookup """

    comment = ''
    section = ''

    fp.seek(0)
    for line in fp:
      line = line.rstrip()

      if not line:
        if comment:
          comment += '\n'
        continue

      if line.startswith('#'):  # Comment
        comment += line + '\n'
        continue

      if line.startswith('['):  # Section
        section = line.strip('[]')
        self._add_dot_key(section)
        if comment:
          self._comments[section] = comment.rstrip()

      elif CONFIG_KEY_RE.match(line):  # Config
        key = line.split('=', 1)[0].strip()
        self._add_dot_key(section, key)
        if comment:
          self._comments[(section, key)] = comment.rstrip()

      comment = ''

    if comment:
      self._comments[self.LAST_COMMENT_KEY] = comment

  def get(self, section, key, default=NO_DEFAULT_VALUE):
    """
    Get config value with data type transformation (from str)

    :param str section: Section to get config for.
    :param str key: Key to get config for.
    :param default: Default value for key if key was not found.
    :return: Value for the section/key or `default` if set and key does not exist.
    :raise NoOptionError: if the key does not exist and no default value is set.
    """
    self._read_last_source()

    if (section, key) in self._dot_keys:
      section, key = self._dot_keys[(section, key)]

    try:
      value = self._parser.get(section, key)
    except Exception:
      if default == NO_DEFAULT_VALUE:
        raise
      else:
        return default

    return self._typed_value(value)

  def set(self, section, key, value, comment=None):
    """
    Set config value with data type transformation (to str)

    :param str section: Section to set config for
    :param str key: Key to set config for
    :param value: Value for key. It can be any primitive type.
    :param str comment: Comment for the key
    """

    self._read_last_source()

    if (section, key) in self._dot_keys:
      section, key = self._dot_keys[(section, key)]
    elif section in self._dot_keys:
      section = self._dot_keys[section]

    if not isinstance(value, str):
      value = str(value)

    self._parser.set(section, key, value)

    self._add_dot_key(section, key)
    if comment:
      self._set_comment(section, comment, key)

  def _read_last_source(self):
    if not self._last_source_read and self._last_source:
      self.read(self._last_source)
      self._last_source_read = True

  def _typed_value(self, value):
    """ Transform string value to an actual data type of the same value. """

    if value not in self._value_cache:
      new_value = value
      if is_int(value):
        new_value = int(value)
      elif is_float(value):
        new_value = float(value)
      elif is_long(value):
        new_value = long(value)
      elif is_bool(value):
        new_value = to_bool(value)
      elif is_none(value):
        new_value = None
      self._value_cache[value] = new_value

    return self._value_cache[value]

  def __getattr__(self, section):
    """
    Get a section

    :param str section: Section to get
    :rtype: :class:`DotNotationConfig.SectionAccessor`
    :raise NoSectionError: if section does not exist
    """
    if section in self._dot_keys:
      return self.SectionAccessor(self, section)
    raise NoSectionError(section)

  def __iter__(self):
    self._read_last_source()

    for section in self._parser.sections():
      yield section

  def add_section(self, section, comment=None):
    """
    Add a section

    :param str section: Section to add
    :raise DuplicateSectionError: if section already exist.
    """
    self._read_last_source()

    if self._to_dot_key(section) in self._dot_keys:
      raise DuplicateSectionError(section)

    self._parser.add_section(section)
    self._add_dot_key(section)
    if comment:
      self._set_comment(section, comment)

  def _set_comment(self, section, comment, key=None):
    """
    Set a comment for section or key

    :param str section: Section to add comment to
    :param str comment: Comment to add
    :param str key: Key to add comment to
    """

    if '\n' in comment:
      comment = '\n# '.join(comment.split('\n'))
    comment = '# ' + comment

    if key:
      self._comments[(section, key)] = comment
    else:
      self._comments[section] = comment

  def items(self, section):
    """
    Items for section with data type transformation (from str)

    :param str section: Section to get items for.
    :return: Generator of (key, value) for the section
    """
    self._read_last_source()

    if section in self._dot_keys:
      section = self._dot_keys[section]

    for item in self._parser.items(section):
      key, value = item
      value = self._typed_value(value)
      yield (key, value)

