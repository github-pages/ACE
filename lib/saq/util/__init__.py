# vim: sw=4:ts=4:et:cc=120
#
# various utility functions
#

import collections
import datetime
import functools
import json
import logging
import os, os.path
import re
import signal
import tempfile
import urllib

import saq
from saq.constants import *

import psutil
import pytz
import requests

CIDR_REGEX = re.compile(r'^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(/[0-9]{1,2})?$')
CIDR_WITH_NETMASK_REGEX = re.compile(r'^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}/[0-9]{1,2}$')
URL_REGEX_B = re.compile(rb'(((?:(?:https?|ftp)://)[A-Za-z0-9\.\-]+)((?:\/[\+~%\/\.\w\-_]*)?\??(?:[\-\+=&;%@\.\w_:\?]*)#?(?:[\.\!\/\\\w:%\?&;=-]*))?(?<!=))', re.I)
URL_REGEX_STR = re.compile(r'(((?:(?:https?|ftp)://)[A-Za-z0-9\.\-]+)((?:\/[\+~%\/\.\w\-_]*)?\??(?:[\-\+=&;%@\.\w_:\?]*)#?(?:[\.\!\/\\\w:%\?&;=-]*))?(?<!=))', re.I)

def create_directory(path):
    """Creates the given directory and returns the path."""
    if not os.path.isdir(path):
        os.makedirs(path)

    return path

def is_ipv4(value):
    """Returns True if the given value is a dotted-quad IP address or CIDR notation."""
    return CIDR_REGEX.match(value) is not None

def add_netmask(value):
    """Returns a CIDR notation value for the given ipv4 or CIDR notation (adds the network if it's missing.)"""
    if CIDR_WITH_NETMASK_REGEX.match(value):
        return value

    # we assume it is a single host
    return '{}/32'.format(value)

def is_subdomain(src, dst):
    """Returns True if src is equal to or a subdomain of dst."""
    src = src.lower()
    src = src.split('.')
    src.reverse()
    
    dst = dst.lower()
    dst = dst.split('.')
    dst.reverse()

    for index in range(len(dst)):
        try:
            if src[index] != dst[index]:
                return False
        except IndexError:
            return False

    return True

def is_url(value):
    if isinstance(value, str):
        if URL_REGEX_STR.match(value):
            return True

        return False
    else:
        if URL_REGEX_B.match(value):
            return True

        return False

def iterate_fqdn_parts(fqdn, reverse=False):
    """For a.b.c.d iterates d, c.d, b.c.d, a.b.c.d."""
    parsed_fqdn = fqdn.split('.')
    parsed_fqdn.reverse()
    for i in range(1, len(parsed_fqdn) + 1):
        partial_fqdn = parsed_fqdn[:i]
        partial_fqdn.reverse()
        partial_fqdn = '.'.join(partial_fqdn)
        yield partial_fqdn

    raise StopIteration()

def human_readable_size(size):
    from math import log2

    _suffixes = ['bytes', 'K', 'M', 'G', 'T', 'E', 'Z']

    # determine binary order in steps of size 10 
    # (coerce to int, // still returns a float)
    order = int(log2(size) / 10) if size else 0
    # format file size
    # (.4g results in rounded numbers for exact matches and max 3 decimals, 
    # should never resort to exponent values)
    return '{:.4g} {}'.format(size / (1 << (order * 10)), _suffixes[order])

def create_timedelta(timespec):
    """Utility function to translate DD:HH:MM:SS into a timedelta object."""
    duration = timespec.split(':')
    seconds = int(duration[-1])
    minutes = 0
    hours = 0
    days = 0

    if len(duration) > 1:
        minutes = int(duration[-2])
    if len(duration) > 2:
        hours = int(duration[-3])
    if len(duration) > 3:
        days = int(duration[-4])

    return datetime.timedelta(days=days, seconds=seconds, minutes=minutes, hours=hours)

RE_ET_FORMAT = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} [+-][0-9]{4}$')
RE_ET_OLD_FORMAT = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$')
RE_ET_JSON_FORMAT = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3,6}[+-][0-9]{4}$')
RE_ET_OLD_JSON_FORMAT = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3,6}$')
RE_ET_ISO_FORMAT = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3,6}[+-][0-9]{2}:[0-9]{2}$')

def parse_event_time(event_time):
    """Return the datetime object for the given event_time."""
    # remove any leading or trailing whitespace
    event_time = event_time.strip()

    if RE_ET_FORMAT.match(event_time):
        return datetime.datetime.strptime(event_time, event_time_format_tz)
    elif RE_ET_OLD_FORMAT.match(event_time):
        return saq.LOCAL_TIMEZONE.localize(datetime.datetime.strptime(event_time, event_time_format))
    elif RE_ET_JSON_FORMAT.match(event_time):
        return datetime.datetime.strptime(event_time, event_time_format_json_tz)
    elif RE_ET_ISO_FORMAT.match(event_time):
        # we just need to remove the : in the timezone specifier
        # this has been fixed in python 3.7
        event_time = event_time[:event_time.rfind(':')] + event_time[event_time.rfind(':') + 1:]
        return datetime.datetime.strptime(event_time, event_time_format_json_tz)
    elif RE_ET_OLD_JSON_FORMAT.match(event_time):
        return saq.LOCAL_TIMEZONE.localize(datetime.datetime.strptime(event_time, event_time_format_json))
    else:
        raise ValueError("invalid date format {}".format(event_time))

UUID_REGEX = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
def storage_dir_from_uuid(uuid):
    """Returns the path (relative to SAQ_HOME) to the storage directory for the given uuid."""
    validate_uuid(uuid)
    return os.path.relpath(os.path.join(saq.DATA_DIR, saq.SAQ_NODE, uuid[0:3], uuid), start=saq.SAQ_HOME)

def workload_storage_dir(uuid):
    """Returns the path (relative to SAQ_HOME) to the storage directory for the current engien for the given uuid."""
    validate_uuid(uuid)
    if saq.CONFIG['service_engine']['work_dir']:
        return os.path.join(saq.CONFIG['service_engine']['work_dir'], uuid)
    else:
        return storage_dir_from_uuid(uuid)

def validate_uuid(uuid):
    if not UUID_REGEX.match(uuid):
        raise ValueError("invalid UUID {}".format(uuid))

    return True

def local_time():
    """Returns datetime.datetime.now() in UTC time zone."""
    return saq.LOCAL_TIMEZONE.localize(datetime.datetime.now()).astimezone(pytz.UTC)

def format_iso8601(d):
    """Given datetime d, return an iso 8601 formatted string YYYY-MM-DDTHH:mm:ss.fff-zz:zz"""
    assert isinstance(d, datetime.datetime)
    d, f, z = d.strftime('%Y-%m-%dT%H:%M:%S %f %z').split()
    return f'{d}.{f[:3]}-{z[1:3]}:{z[3:]}'

def abs_path(path):
    """Given a path, return SAQ_HOME/path if path is relative, or path if path is absolute."""
    if os.path.isabs(path):
        return path

    return os.path.join(saq.SAQ_HOME, path)


def kill_process_tree(pid, sig=signal.SIGTERM, include_parent=True,
                      timeout=None, on_terminate=None):
    """Kill a process tree (including grandchildren) with signal
    "sig" and return a (gone, still_alive) tuple.
    "on_terminate", if specified, is a callabck function which is
    called as soon as a child terminates.
    """

    if pid == os.getpid():
        raise RuntimeError("I refuse to kill myself")

    parent = psutil.Process(pid)
    children = parent.children(recursive=True)
    if include_parent:
        children.append(parent)

    for p in children:
        p.send_signal(sig)

    gone, alive = psutil.wait_procs(children, timeout=timeout,
                                    callback=on_terminate)
    return (gone, alive)

def json_parse(fileobj, decoder=json.JSONDecoder(), buffersize=2048):
    """Utility iterator function that yields JSON objects from a file that contains multiple JSON objects.
       The iterator returns a tuple of (json_object, next_position) where next_position is the position in the
       file the next parsing would take place at."""
    buffer = ''
    reference_position = fileobj.tell() # remember where we're starting
    for chunk in iter(functools.partial(fileobj.read, buffersize), ''):
        buffer += chunk
        processed_buffer_size = 0
        while buffer:
            try:
                # index is where we stopped parsing at (where we'll start next time)
                result, index = decoder.raw_decode(buffer)

                buffer_size = len(buffer)
                buffer = buffer[index:].lstrip()
                processed_buffer_size = buffer_size - len(buffer)
                reference_position += processed_buffer_size

                yield result, reference_position

            except ValueError:
                # Not enough data to decode, read more
                break


def fang(url):
    """Re-fangs a url that has been de-fanged.
    If url does not match the defang format, it returns the original string."""
    _formats = ['hxxp', 'hXXp']
    for item in _formats:
        if url.startswith(item):
            return f"http{url[4:]}"
    return url


#
# How this works:
# Let's say we're watching a file that another system is writing to. At some point it decides to roll the file over
# and start a new file. We don't want that to happen while we're in the middle of reading/process it.
# So we create a HARD LINK to the file named file_name.monitor.
# When the other system moves the file to the side, we still have the hard link to the original file.
# And now we can tell that it rolled over since the inode for the hard link we have will be different than the 
# inode for the new file.
#

class FileMonitorLink(object):
    """Utility class to track when a file has been modified."""

    FILE_NEW = 'new'
    FILE_MODIFIED = 'modified'
    FILE_UNMODIFIED = 'unmodified'
    FILE_DELETED = 'deleted'
    FILE_MOVED = 'moved'

    def __init__(self, path):
        self.path = path
        self.link_path = None
        self.create_link()
        self.last_stat = None

    def create_link(self):
        if self.link_path is not None:
            return

        if os.path.exists(self.path):
            count = 0
            while True:
                try:
                    self.link_path = os.path.join(saq.TEMP_DIR, f'{os.path.basename(self.path)}.monitor-{count}')
                    os.link(self.path, self.link_path)
                    logging.debug(f"created file monitor for {self.path} linked to {self.link_path}")
                    break
                except FileExistsError:
                    count += 1
                    continue
                except Exception as e:
                    logging.error(f"unable to create link {self.link_path} for file monitor for {self.path}: {e}")
                    report_exception()

    def delete_link(self):
        if self.link_path is not None:
            try:
                os.unlink(self.link_path)
            except FileNotFoundError:
                pass
            except Exception as e:
                logging.error(f"unable to delete monitor link {self.link_path} for {self.path}: {e}")

    def close(self):
        self.delete_link()

    def status(self):
        """Returns FileMonitorLink.FILE_NEW if this is the first call to status(),
                   FileMonitorLink.FILE_MOVED if the path points to a different file than it was before,
                   FileMonitorLink.FILE_MODIFIED if the file has been modified,
                   FileMonitorLink.FILE_DELETED if the file has been deleted,
                   FileMonitorLink.FILE_UNMODIFIED if the file has not changed."""

        old_stat = self.last_stat

        try:
            self.last_stat = os.stat(self.path)
        except FileNotFoundError:
            return FileMonitorLink.FILE_DELETED

        self.create_link()

        # is this the first time we've called status() for this file?
        if old_stat is None:
            return FileMonitorLink.FILE_NEW

        link_stat = os.stat(self.link_path)
        # did the inode change?
        if link_stat.st_ino != self.last_stat.st_ino:
            return FileMonitorLink.FILE_MOVED

        # same inode -- did the last modified timestamp change?
        if old_stat.st_mtime != self.last_stat.st_mtime:
            return FileMonitorLink.FILE_MODIFIED

        # did the size of the file change?
        if old_stat.st_size != self.last_stat.st_size:
            return FileMonitorLink.FILE_MODIFIED

        return FileMonitorLink.FILE_UNMODIFIED


class RegexObservableParser:
    """Helper class to handle regex and observable mapping.

    If capture_groups are not specified, then the parse will just do a
    'findall'.

    If capture_groups are specified, it will use the indexes to pull
    out capture groups in the order they are in the capture_groups kwarg.
    
    Attributes
    ----------
    regex: str
        Regular expression to be used for parsing. Take care to include
        capture groups.
    observable_type: str
        Observable type from saq.constants.
    matches: list
        Observables gathered from parser.
    capture_groups: list
        Integers signifying which capture groups to extract.
        They will be extracted in the order they are specified in this
        attribute.
    delimiter: str
        Used to join the capture groups.
        ex: delimeter of '_' might be used to join two email address
        like 'email1@email.com_email2@email2.com'.
    """
    def __init__(self, regex, observable_type, 
                 capture_groups=None, 
                 delimiter='_', 
                 tags=None, 
                 directives=None,
                 re_compile_options=0):
        self.regex = re.compile(regex, re_compile_options)
        self.observable_type = observable_type
        self.matches = None
        self.capture_groups = capture_groups
        self.delimiter = delimiter
        self.tags = tags or []
        self.directives = directives or []
    
    def _parse(self, text):
        """Extracts an observable(s) from the text.
        
        This can be overridden in a subclass if your regex logic
        requires somthing different. For example, if you need to
        join your capture groups through a combination of different
        delimiters, you'll need to override this.
        """
        if self.capture_groups is None:
            self.matches = self.regex.findall(text)
            return

        _matches = self.regex.search(text)

        if _matches is None:
            self.matches = []
            return

        # Create the desired string from the capture groups.
        # Handy for conversation observables.
        # IndexError or AttributeError handled in parent method.
        _formatted = self.delimiter.join(
            [_matches.group(capture_group) for capture_group in self.capture_groups]
        )

        self.matches = [_formatted]

    def parse(self, text):
        """Get a list of non-overlapping matches."""
        try:
            self._parse(text)
        except (IndexError, AttributeError):
            # IndexError -> You tried to find a capture group that
            #     didn't exist.
            # AttributeError -> The regex search did not return a
            #     result (so Nonetype returned) and then you tried to
            #     pull out a capture group from a NoneType.
            self.matches = []


class RegexObservableParserGroup:
    """API for turning parsed data sources into
        observables ready for submission.

    Add RegexObservableParsers and then parse your content. This class
        runs the content through each parser and converts the results
        into observables.
    """

    def __init__(self, tags=None):
        self.parsers = []
        self._directives_map = None
        self._tags_map = None
        self._observable_map = None
        self._observables = []
        self.tags = tags or []

    def add(self, regex, observable_type, capture_groups=None, delimiter='_',
            override_class=None, tags=None, directives=None, re_compile_options=0):
        """Add a parser to the list of parsers for this group.
            
            You may pass in a subclass if you want to customize
            how the regex results are handled.
        """
        _parser_class = override_class or RegexObservableParser

        _tags = tags or self.tags # If no tags given, add group tag.

        parser = _parser_class(
            regex,
            observable_type,
            capture_groups=capture_groups,
            delimiter=delimiter,
            tags=_tags,
            directives=directives,
            re_compile_options=re_compile_options,
        )

        self.parsers.append(parser)

    def parse_content(self, content):
        """Iterate through parsers and extract observables from the
            content.
        """

        self._observable_map = {_parser.observable_type: set() for _parser in self.parsers}
        
        self._directives_map = {} # Keeps track of observable/directives pairs
        self._tags_map = {} # Keeps track of observable/tag pairs
        
        for parser in self.parsers:
            parser.parse(content)
            for match in parser.matches:
                _match = match.strip()

                if parser.observable_type == F_URL:
                    # hxxp/hXXp to http for analysis
                    _match = fang(_match)

                # Note we're adding to a set... so any duplicates will be
                #    filtered out automatically.
                self._observable_map[parser.observable_type].add(_match)
                self._directives_map[_match] = parser.directives
                self._tags_map[_match] = parser.tags

    @property
    def observable_map(self):
        if self._observable_map is not None:
            return self._observable_map
        raise ValueError("Must parse content before an observable map can be generated.")

    @property
    def observables(self):
        # Return the observables ready for submission to ACE.
        if self._observables:
            return self._observables

        for observable_type, observable_set in self._observable_map.items():
            for observable in observable_set:
                observable_details = {
                    'type': observable_type,
                    'value': observable,
                    'tags': self._tags_map[observable],
                    'directives': self._directives_map[observable],
                }
                self._observables.append(observable_details)

        return self._observables


class CustomSSLAdapter(requests.adapters.HTTPAdapter):
    """Adapter to override certificate verifications.

    >>> import requests
    >>>
    >>> from saq.util import SSLDecryptAdapter
    >>>
    >>> adapter = SSLDecryptAdapter()
    >>> adapter.add_cert('hello.world.com', '/home/certguy/my/cert.crt')
    >>>
    >>> session = reqeusts.Session()
    >>>
    >>> session.mount('https://', adapter)
    >>> session.post('https://hello.world.com', etc.)

    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # If the FQDN is not found in the mapping, then we want
        #   to verify the SSL cert as normal.
        self.cert_file_map = collections.defaultdict(lambda: True)

    def add_cert(self, fqdn, cert):
        """Add an FQDN and Cert pair to the cert file map."""
        self.cert_file_map[fqdn] = cert

    def cert_verify(self, conn, url, verify, cert):
        """Override the HTTPAdapter 'cert_verify' to include specific certificates
        for the FQDN/Cert pairs added by 'add_cert()'.
        """

        if self.cert_file_map is None:
            raise ValueError("missing certificate file map")
        hostname = urllib.parse.urlparse(url).hostname
        super().cert_verify(conn=conn, url=url, verify=self.cert_file_map[hostname], cert=cert)

