# vim: ts=4:sw=4:et:cc=120

import datetime
import hashlib
import io
import logging
import os.path
import pickle
import shutil
import tempfile
import uuid

import ace_api

import saq
from saq.analysis import RootAnalysis
from saq.constants import *
from saq.database import acquire_lock, use_db
from saq.test import *
from aceapi.cloudphish.test import CloudphishTestCase, TEST_URL
from saq.util import *

import pytz
import tzlocal

class TestCase(ACEEngineTestCase):

    def setUp(self, *args, **kwargs):
        super().setUp(*args, **kwargs)
        self.start_api_server()

        ace_api.set_default_remote_host(saq.API_PREFIX)
        ace_api.set_default_ssl_ca_path(saq.CONFIG['SSL']['ca_chain_path'])

    def test_ping(self):
        result = ace_api.ping()
        self.assertIsNotNone(result)
        self.assertTrue('result' in result)
        self.assertEquals(result['result'], 'pong')

    def test_get_supported_api_version(self):
        result = ace_api.get_supported_api_version()
        self.assertIsNotNone(result)
        self.assertTrue('result' in result)
        self.assertEquals(result['result'], 1)

    @use_db
    def test_get_valid_companies(self, db, c):
        result = ace_api.get_valid_companies()
        self.assertIsNotNone(result)
        self.assertTrue('result' in result)
        self.assertTrue(isinstance(result['result'], list))

        lookup = {}
        c.execute("SELECT id, name FROM company")
        for _id, _name in c:
            lookup[_id] = _name

        self.assertEquals(len(lookup), len(result['result']))
        for r in result['result']:
            self.assertTrue(r['id'] in lookup and lookup[r['id']] == r['name'])
        result = ace_api.get_valid_companies()
        self.assertIsNotNone(result)
        self.assertTrue('result' in result)
        self.assertTrue(isinstance(result['result'], list))

    def test_get_valid_observables(self):
        from saq.constants import VALID_OBSERVABLE_TYPES, OBSERVABLE_DESCRIPTIONS, DEPRECATED_OBSERVABLES
        result = ace_api.get_valid_observables()
        self.assertIsNotNone(result)
        self.assertTrue('result' in result)
        self.assertTrue(isinstance(result['result'], list))

        for r in result['result']:
            self.assertTrue(r['name'] in VALID_OBSERVABLE_TYPES)
            self.assertEquals(OBSERVABLE_DESCRIPTIONS[r['name']], r['description'])

        active_observables = set(VALID_OBSERVABLE_TYPES) - set(DEPRECATED_OBSERVABLES)
        self.assertEquals(len(active_observables), len(result['result']))

    def test_get_valid_directives(self):
        from saq.constants import VALID_DIRECTIVES, DIRECTIVE_DESCRIPTIONS
        result = ace_api.get_valid_directives()
        self.assertIsNotNone(result)
        self.assertTrue('result' in result)
        self.assertTrue(isinstance(result['result'], list))

        for r in result['result']:
            self.assertTrue(r['name'] in VALID_DIRECTIVES)
            self.assertEquals(DIRECTIVE_DESCRIPTIONS[r['name']], r['description'])

    def _get_submit_time(self):
        return datetime.datetime(2017, 11, 11, hour=7, minute=36, second=1, microsecond=1)

    def _get_localized_submit_time(self):
        return ace_api.LOCAL_TIMEZONE.localize(self._get_submit_time()).astimezone(pytz.UTC)

    def _submit(self, analysis_mode=None,
                      tool=None,
                      tool_instance=None,
                      type=None,
                      description=None,
                      details=None,
                      event_time=None,
                      observables=None,
                      tags=None):

        temp_path = os.path.join(saq.SAQ_HOME, saq.TEMP_DIR, 'submit_test.dat')
        temp_data = os.urandom(1024)

        with open(temp_path, 'wb') as fp:
            fp.write(temp_data)

        try:
            with open(temp_path, 'rb') as fp:
                return ace_api.submit(
                    analysis_mode='test_empty' if analysis_mode is None else analysis_mode, 
                    tool='unittest_tool' if tool is None else tool,
                    tool_instance='unittest_tool_instance' if tool_instance is None else tool_instance,
                    type='unittest_type' if type is None else type,
                    description='testing' if description is None else description,
                    details={'hello': 'world'} if details is None else details,
                    event_time=self._get_submit_time() if event_time is None else event_time,
                    observables=[
                            { 'type': 'ipv4', 'value': '1.2.3.4', 'time': self._get_submit_time(), 'tags': [ 'tag_1', 'tag_2' ], 'directives': [ 'no_scan' ], 'limited_analysis': ['basic_test'] },
                            { 'type': 'user', 'value': 'test_user', 'time': self._get_submit_time() },
                    ] if observables is None else observables,
                    tags=[ 'alert_tag_1', 'alert_tag_2' ] if tags is None else tags,
                    files=[('sample.dat', io.BytesIO(b'Hello, world!')),
                           ('submit_test.dat', fp)])
        finally:
            os.remove(temp_path)

    @use_db
    def test_submit(self, db, c):
        result = self._submit()
        self.assertIsNotNone(result)

        self.assertTrue('result' in result)
        result = result['result']
        self.assertIsNotNone(result['uuid'])
        uuid = result['uuid']

        # make sure this actually uploaded
        root = RootAnalysis(storage_dir=workload_storage_dir(uuid))
        root.load()

        self.assertEquals(root.analysis_mode, 'test_empty')
        self.assertEquals(root.tool, 'unittest_tool')
        self.assertEquals(root.tool_instance, 'unittest_tool_instance')
        self.assertEquals(root.alert_type, 'unittest_type')
        self.assertEquals(root.description, 'testing')
        self.assertEquals(root.details, {'hello': 'world'})
        self.assertEquals(root.event_time, self._get_localized_submit_time())
        self.assertEquals(root.tags[0].name, 'alert_tag_1')
        self.assertEquals(root.tags[1].name, 'alert_tag_2')
        # NOTE that this is 4 instead of 2 since adding a file adds a F_FILE observable type
        self.assertEquals(len(root.all_observables), 4)

        o = root.find_observable(lambda o: o.type == 'ipv4')
        self.assertIsNotNone(o)
        self.assertEquals(o.value, '1.2.3.4')
        self.assertEquals(len(o.tags), 2)
        self.assertTrue(o.has_directive('no_scan'))
        self.assertTrue('basic_test' in o.limited_analysis)

        o = root.find_observable(lambda o: o.type == 'file' and o.value == 'sample.dat')
        self.assertIsNotNone(o)

        with open(os.path.join(root.storage_dir, o.value), 'rb') as fp:
            self.assertEquals(fp.read(), b'Hello, world!')

        o = root.find_observable(lambda o: o.type == 'file' and o.value == 'submit_test.dat')
        self.assertIsNotNone(o)
        self.assertEquals(os.path.getsize(os.path.join(root.storage_dir, o.value)), 1024)

        # we should see a single workload entry
        c.execute("SELECT id, uuid, node_id, analysis_mode FROM workload WHERE uuid = %s", (uuid,))
        row = c.fetchone()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row[0])
        self.assertEquals(row[1], uuid)
        self.assertIsNotNone(row[2])
        self.assertEquals(row[3], 'test_empty')

    @use_db
    def test_resubmit(self, db, c):
        # submit something so we have something to resubmit
        result = self._submit(analysis_mode=ANALYSIS_MODE_CORRELATION)
        self.assertIsNotNone(result)

        self.assertTrue('result' in result)
        result = result['result']
        self.assertIsNotNone(result['uuid'])
        uuid = result['uuid']

        # make sure this actually uploaded
        root = RootAnalysis(storage_dir=storage_dir_from_uuid(uuid))
        root.load()

        self.assertEquals(root.analysis_mode, ANALYSIS_MODE_CORRELATION)
        self.assertEquals(root.tool, 'unittest_tool')
        self.assertEquals(root.tool_instance, 'unittest_tool_instance')
        self.assertEquals(root.alert_type, 'unittest_type')
        self.assertEquals(root.description, 'testing')
        self.assertEquals(root.details, {'hello': 'world'})
        self.assertEquals(root.event_time, self._get_localized_submit_time())
        self.assertEquals(root.tags[0].name, 'alert_tag_1')
        self.assertEquals(root.tags[1].name, 'alert_tag_2')
        # NOTE that this is 4 instead of 2 since adding a file adds a F_FILE observable type
        self.assertEquals(len(root.all_observables), 4)

        o = root.find_observable(lambda o: o.type == 'ipv4')
        self.assertIsNotNone(o)
        self.assertEquals(o.value, '1.2.3.4')
        self.assertEquals(len(o.tags), 2)
        self.assertTrue(o.has_directive('no_scan'))
        self.assertTrue('basic_test' in o.limited_analysis)

        o = root.find_observable(lambda o: o.type == 'file' and o.value == 'sample.dat')
        self.assertIsNotNone(o)

        with open(os.path.join(root.storage_dir, o.value), 'rb') as fp:
            self.assertEquals(fp.read(), b'Hello, world!')

        o = root.find_observable(lambda o: o.type == 'file' and o.value == 'submit_test.dat')
        self.assertIsNotNone(o)
        self.assertEquals(os.path.getsize(os.path.join(root.storage_dir, o.value)), 1024)

        # we should see a single workload entry
        c.execute("SELECT id, uuid, node_id, analysis_mode FROM workload WHERE uuid = %s", (uuid,))
        row = c.fetchone()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row[0])
        self.assertEquals(row[1], uuid)
        self.assertIsNotNone(row[2])
        self.assertEquals(row[3], ANALYSIS_MODE_CORRELATION)

        # now resubmit the alert
        result = ace_api.resubmit_alert(uuid)
        self.assertFalse('error' in result)

    def test_submit_with_utc_timezone(self):
        # make sure we can submit with a UTC timezone already set
        result = self._submit(event_time=self._get_localized_submit_time())
        self.assertIsNotNone(result)

        self.assertTrue('result' in result)
        result = result['result']
        self.assertIsNotNone(result['uuid'])
        uuid = result['uuid']

        root = RootAnalysis(storage_dir=workload_storage_dir(uuid))
        root.load()

        self.assertEquals(root.event_time, self._get_localized_submit_time())

    def test_submit_with_other_timezone(self):
        # make sure we can submit with another timezone already set
        result = self._submit(event_time=self._get_localized_submit_time().astimezone(pytz.timezone('US/Eastern')))
        self.assertIsNotNone(result)

        self.assertTrue('result' in result)
        result = result['result']
        self.assertIsNotNone(result['uuid'])
        uuid = result['uuid']

        root = RootAnalysis(storage_dir=workload_storage_dir(uuid))
        root.load()

        self.assertEquals(root.event_time, self._get_localized_submit_time())

    def test_get_analysis(self):

        result = self._submit()
        self.assertIsNotNone(result)
        self.assertTrue('result' in result)
        result = result['result']
        self.assertIsNotNone(result['uuid'])
        uuid = result['uuid']

        result = ace_api.get_analysis(uuid)
        self.assertIsNotNone(result)
        self.assertTrue('result' in result)
        result = result['result']

        self.assertEquals(result['analysis_mode'], 'test_empty')
        self.assertEquals(result['tool'], 'unittest_tool')
        self.assertEquals(result['tool_instance'], 'unittest_tool_instance')
        self.assertEquals(result['type'], 'unittest_type')
        self.assertEquals(result['description'], 'testing')
        self.assertEquals(result['event_time'], '2017-11-11T07:36:01.000001+0000')
        self.assertEquals(result['tags'][0], 'alert_tag_1')
        self.assertEquals(result['tags'][1], 'alert_tag_2')
        self.assertEquals(len(result['observable_store']), 4)

        # the details should be a file_path reference
        self.assertTrue(isinstance(result['details'], dict))
        self.assertTrue('file_path' in result['details'])
        self.assertTrue(result['details']['file_path'].startswith('RootAnalysis_'))

    def test_get_analysis_details(self):
        
        result = self._submit()
        self.assertIsNotNone(result)
        self.assertTrue('result' in result)
        result = result['result']
        self.assertIsNotNone(result['uuid'])
        uuid = result['uuid']

        result = ace_api.get_analysis(uuid)
        self.assertIsNotNone(result)
        self.assertTrue('result' in result)
        result = result['result']

        details_result = ace_api.get_analysis_details(uuid, result['details']['file_path'])
        self.assertIsNotNone(details_result)
        details_result = details_result['result']
        self.assertTrue('hello' in details_result)
        self.assertEquals(details_result['hello'], 'world')

    def test_get_analysis_file(self):

        result = self._submit()
        self.assertIsNotNone(result)
        self.assertTrue('result' in result)
        result = result['result']
        self.assertIsNotNone(result['uuid'])
        uuid = result['uuid']

        result = ace_api.get_analysis(uuid)
        self.assertIsNotNone(result)
        self.assertTrue('result' in result)
        result = result['result']

        # first test getting a file by uuid
        file_uuid = None
        for o_uuid in result['observables']:
            o = result['observable_store'][o_uuid]
            if o['type'] == 'file' and o['value'] == 'sample.dat':
                file_uuid = o_uuid
                break

        self.assertIsNotNone(file_uuid)

        output_path = os.path.join(saq.SAQ_HOME, saq.TEMP_DIR, 'get_file_test.dat')
        self.assertTrue(ace_api.get_analysis_file(uuid, file_uuid, output_file=output_path))
        with open(output_path, 'rb') as fp:
            self.assertEquals(fp.read(), b'Hello, world!')

        # same thing but with passing a file pointer
        with open(output_path, 'wb') as fp:
            self.assertTrue(ace_api.get_analysis_file(uuid, file_uuid, output_fp=fp))

        # now test by using the file name
        self.assertTrue(ace_api.get_analysis_file(uuid, 'sample.dat', output_file=output_path))
        with open(output_path, 'rb') as fp:
            self.assertEquals(fp.read(), b'Hello, world!')

    def test_get_analysis_status(self):

        result = self._submit()
        self.assertIsNotNone(result)
        self.assertTrue('result' in result)
        result = result['result']
        self.assertIsNotNone(result['uuid'])
        uuid = result['uuid']

        result = ace_api.get_analysis_status(uuid)
        self.assertIsNotNone(result)
        result = result['result']
        self.assertTrue('workload' in result)
        self.assertTrue('delayed_analysis' in result)
        self.assertTrue('locks' in result)
        self.assertTrue('alert' in result)
        self.assertEquals(result['alert'], None)
        self.assertEquals(result['delayed_analysis'], [])
        self.assertIsNone(result['locks'])
        self.assertTrue(isinstance(result['workload']['id'], int))
        self.assertEquals(result['workload']['uuid'], uuid)
        self.assertIsNotNone(result['workload']['node_id'])
        self.assertEquals(result['workload']['analysis_mode'], 'test_empty')
        self.assertTrue(isinstance(parse_event_time(result['workload']['insert_date']), datetime.datetime))

    def test_download(self):
        root = create_root_analysis(uuid=str(uuid.uuid4()))
        root.initialize_storage()
        root.details = { 'hello': 'world' }
        root.save()

        temp_dir = tempfile.mkdtemp(dir=saq.TEMP_DIR)
        try:
            result = ace_api.download(root.uuid, temp_dir)
            self.assertTrue(os.path.join(temp_dir, 'data.json'))
            root = RootAnalysis(storage_dir=temp_dir)
            root.load()
            self.assertEquals(root.details, { 'hello': 'world' })
        finally:
            shutil.rmtree(temp_dir)

    def test_upload(self):
        root = create_root_analysis(uuid=str(uuid.uuid4()), storage_dir=os.path.join(saq.TEMP_DIR, 'unittest'))
        root.initialize_storage()
        root.details = { 'hello': 'world' }
        root.save()

        result = ace_api.upload(root.uuid, root.storage_dir)
        self.assertTrue(result['result'])

        # uploads go straight into saq.DATA_DIR
        # XXX I don't think we need uploads at all
        root = RootAnalysis(storage_dir=storage_dir_from_uuid(root.uuid))
        root.load()

        self.assertEquals(root.details, { 'hello': 'world' })

    def test_clear(self):
        root = create_root_analysis(uuid=str(uuid.uuid4()))
        root.initialize_storage()
        root.details = { 'hello': 'world' }
        root.save()
        self.assertTrue(os.path.exists(root.storage_dir))

        lock_uuid = str(uuid.uuid4())
        self.assertTrue(acquire_lock(root.uuid, lock_uuid))

        result = ace_api.clear(root.uuid, lock_uuid)
        self.assertFalse(os.path.exists(root.storage_dir))

    def test_clear_invalid_lock_uuid(self):
        root = create_root_analysis(uuid=str(uuid.uuid4()))
        root.initialize_storage()
        root.details = { 'hello': 'world' }
        root.save()
        self.assertTrue(os.path.exists(root.storage_dir))

        lock_uuid = str(uuid.uuid4())
        self.assertTrue(acquire_lock(root.uuid, lock_uuid))

        lock_uuid = str(uuid.uuid4())
        with self.assertRaises(Exception):
            self.assertFalse(ace_api.clear(root.uuid, lock_uuid))

        self.assertTrue(os.path.exists(root.storage_dir))

    def test_legacy_submit(self):
        
        alert = ace_api.Alert(description='Test Alert')
        alert.add_observable(F_IPV4, '1.2.3.4', local_time(), directives=[DIRECTIVE_NO_SCAN])
        alert.add_tag('test')
        temp_path = os.path.join(saq.TEMP_DIR, 'test.txt')
        with open(temp_path, 'w') as fp:
            fp.write('test')

        alert.add_attachment_link(temp_path, 'dest/test.txt')
        alert.submit(f'https://{saq.API_PREFIX}', ssl_verification=saq.CONFIG['SSL']['ca_chain_path'])
        self.assertTrue(validate_uuid(alert.uuid))

        root = RootAnalysis(storage_dir=storage_dir_from_uuid(alert.uuid))
        root.load()

        self.assertEquals(root.description, 'Test Alert')
        ipv4_observable = root.find_observable(lambda o: o.type == F_IPV4)
        self.assertIsNotNone(ipv4_observable)
        self.assertEquals(ipv4_observable.value, '1.2.3.4')
        self.assertTrue(ipv4_observable.has_directive(DIRECTIVE_NO_SCAN))
        
        file_observable = root.find_observable(lambda o: o.type == F_FILE)
        self.assertIsNotNone(file_observable)
        self.assertEquals(file_observable.value, 'dest/test.txt')
        with open(os.path.join(root.storage_dir, file_observable.value), 'r') as fp:
            self.assertEquals(fp.read(), 'test')

    def test_legacy_import(self):
        
        from ace_client_lib.client import Alert
        from ace_api import Alert as Alert_2

        # these two should be the same thing
        self.assertTrue(Alert is Alert_2)

    def test_legacy_failed_submit(self):

        self.stop_api_server()
    
        alert = ace_api.Alert(description='Test Alert')
        alert.add_observable(F_IPV4, '1.2.3.4', local_time(), directives=[DIRECTIVE_NO_SCAN])
        alert.add_tag('test')
        temp_path = os.path.join(saq.TEMP_DIR, 'test.txt')
        with open(temp_path, 'w') as fp:
            fp.write('test')

        alert.add_attachment_link(temp_path, 'dest/test.txt')
        with self.assertRaises(Exception):
            alert.submit(f'https://{saq.API_PREFIX}', ssl_verification=saq.CONFIG['SSL']['ca_chain_path'])

        self.assertEquals(log_count('unable to submit '), 1)

        # the .saq_alerts directory should have a single subdirectory
        dir_list = os.listdir('.saq_alerts')
        self.assertEquals(len(dir_list), 1)
        
        # load the alert
        target_path = os.path.join('.saq_alerts', dir_list[0], 'alert')
        with open(target_path, 'rb') as fp:
            new_alert = pickle.load(fp)

        self.assertEquals(new_alert.submit_kwargs, alert.submit_kwargs)

    def test_failed_submit(self):

        self.stop_api_server()

        analysis = ace_api.Analysis(description='Test Analysis submit')
        analysis.add_observable(F_IPV4, '1.2.3.4', local_time(), directives=[DIRECTIVE_NO_SCAN])
        analysis.add_tag('test')
        analysis.add_user('test_user')
        temp_path = os.path.join(saq.TEMP_DIR, 'test.txt')
        with open(temp_path, 'w') as fp:
            fp.write('test')

        analysis.add_file(temp_path, relative_storage_path='dest/test.txt')
        with self.assertRaises(Exception):
            analysis.submit(f'https://{saq.API_PREFIX}', ssl_verification=saq.CONFIG['SSL']['ca_chain_path'])

        self.assertEquals(log_count('unable to submit '), 1)

        # the .saq_alerts directory should have a single subdirectory
        dir_list = os.listdir('.saq_alerts')
        self.assertEquals(len(dir_list), 1)

        # load the analysis object
        target_path = os.path.join('.saq_alerts', dir_list[0], 'alert')
        with open(target_path, 'rb') as fp:
            new_analysis = pickle.load(fp)

        self.assertEquals(new_analysis.submit_kwargs, analysis.submit_kwargs)

    def test_submit_failed_alerts(self):

        self.stop_api_server()
    
        alert = ace_api.Alert(description='Test Alert')
        alert.add_observable(F_IPV4, '1.2.3.4', local_time(), directives=[DIRECTIVE_NO_SCAN])
        alert.add_tag('test')
        temp_path = os.path.join(saq.TEMP_DIR, 'test.txt')
        with open(temp_path, 'w') as fp:
            fp.write('test')

        alert.add_attachment_link(temp_path, 'dest/test.txt')
        with self.assertRaises(Exception):
            uuid = alert.submit(f'https://{saq.API_PREFIX}', ssl_verification=saq.CONFIG['SSL']['ca_chain_path'])

        self.assertEquals(log_count('unable to submit '), 1)

        # the .saq_alerts directory should have a single subdirectory
        dir_list = os.listdir('.saq_alerts')
        self.assertEquals(len(dir_list), 1)
        
        # load the alert
        target_path = os.path.join('.saq_alerts', dir_list[0], 'alert')
        with open(target_path, 'rb') as fp:
            new_alert = pickle.load(fp)

        self.assertEquals(new_alert.submit_kwargs, alert.submit_kwargs)

        # try to submit it using submit_failed_alerts
        self.start_api_server()
        ace_api.submit_failed_alerts(delete_on_success=True)
        
        # this directory should be cleared out
        dir_list = os.listdir('.saq_alerts')
        self.assertEquals(len(dir_list), 0)

    def test_submit_failed_analysis(self):

        self.stop_api_server()

        analysis = ace_api.Analysis(description='Test Analysis')
        analysis.add_observable(F_IPV4, '1.2.3.4', local_time(), directives=[DIRECTIVE_NO_SCAN])
        analysis.add_tag('test')
        temp_path = os.path.join(saq.TEMP_DIR, 'test.txt')
        with open(temp_path, 'w') as fp:
            fp.write('test')

        analysis.add_file(temp_path, relative_storage_path='dest/test.txt')
        with self.assertRaises(Exception):
            uuid = analysis.submit(f'https://{saq.API_PREFIX}', ssl_verification=saq.CONFIG['SSL']['ca_chain_path'])

        self.assertEquals(log_count('unable to submit '), 1)

        # the .saq_alerts directory should have a single subdirectory
        dir_list = os.listdir('.saq_alerts')
        self.assertEquals(len(dir_list), 1)

        # load the alert
        target_path = os.path.join('.saq_alerts', dir_list[0], 'alert')
        with open(target_path, 'rb') as fp:
            new_analysis = pickle.load(fp)

        self.assertEquals(new_analysis.submit_kwargs, analysis.submit_kwargs)

        # did we actually write data to the file?
        data_test = None
        with open(os.path.join('.saq_alerts', new_analysis.uuid, 'dest/test.txt'), 'r') as fp:
            data_test = fp.read()
        self.assertEquals(data_test, 'test')

        # try to submit it using submit_failed_alerts
        self.start_api_server()
        ace_api.submit_failed_alerts(delete_on_success=True)

        # this directory should be cleared out
        dir_list = os.listdir('.saq_alerts')
        self.assertEquals(len(dir_list), 0)

    def test_analysis_file_handling(self):

        analysis = ace_api.Analysis(description='Test Analysis')
        # add a normal file
        normal_file_path = self.create_test_file('normal.txt')
        analysis.add_file(normal_file_path)
        # add a normal file, passing in the file pointer
        fp_file_path = self.create_test_file('fp.txt')
        fp = open(fp_file_path, 'rb')
        analysis.add_file(fp_file_path, fp)
        # add a normal file but tell it to go into a subdirectory
        subdir_file_path = self.create_test_file('subdir.txt')
        analysis.add_file(subdir_file_path, relative_storage_path='subdir/subdir.txt')
        # add a file passing the contents as a string
        analysis.add_file('str.txt', 'This is a string.')
        # add a file passing the contents as a bytes
        analysis.add_file('bytes.txt', b'This is a bytes.')

        result = analysis.submit()

        # make sure it got our files
        io_buffer = io.BytesIO()
        ace_api.get_analysis_file(result.uuid, 'normal.txt', output_fp=io_buffer)
        with open(normal_file_path, 'rb') as fp:
            self.assertEquals(fp.read(), io_buffer.getvalue())

        io_buffer = io.BytesIO()
        ace_api.get_analysis_file(result.uuid, 'fp.txt', output_fp=io_buffer)
        with open(fp_file_path, 'rb') as fp:
            self.assertEquals(fp.read(), io_buffer.getvalue())

        #io_buffer = io.BytesIO()
        #ace_api.get_analysis_file(result.uuid, 'subdir/subdir.txt', output_fp=io_buffer)
        #with open(subdir_file_path, 'rb') as fp:
            #self.assertEquals(fp.read(), io_buffer.getvalue())

        io_buffer = io.BytesIO()
        ace_api.get_analysis_file(result.uuid, 'str.txt', output_fp=io_buffer)
        self.assertEquals(b'This is a string.', io_buffer.getvalue())

        io_buffer = io.BytesIO()
        ace_api.get_analysis_file(result.uuid, 'bytes.txt', output_fp=io_buffer)
        self.assertEquals(b'This is a bytes.', io_buffer.getvalue())

class CloudphishAPITestCase(CloudphishTestCase, ACEEngineTestCase):

    def setUp(self, *args, **kwargs):
        super().setUp(*args, **kwargs)
        self.start_api_server()

        ace_api.set_default_remote_host(saq.API_PREFIX)
        ace_api.set_default_ssl_ca_path(saq.CONFIG['SSL']['ca_chain_path'])

    def test_cloudphish_api(self):
        import saq.cloudphish
        submission_result = ace_api.cloudphish_submit(TEST_URL)
        for key in [ saq.cloudphish.KEY_RESULT,
                     saq.cloudphish.KEY_DETAILS,
                     saq.cloudphish.KEY_STATUS,
                     saq.cloudphish.KEY_ANALYSIS_RESULT,
                     saq.cloudphish.KEY_HTTP_RESULT,
                     saq.cloudphish.KEY_HTTP_MESSAGE,
                     saq.cloudphish.KEY_SHA256_CONTENT,
                     saq.cloudphish.KEY_SHA256_URL,
                     saq.cloudphish.KEY_LOCATION,
                     saq.cloudphish.KEY_FILE_NAME,
                     saq.cloudphish.KEY_UUID, ]:
            self.assertTrue(key in submission_result)

        self.assertEquals(submission_result[saq.cloudphish.KEY_RESULT], saq.cloudphish.RESULT_OK)
        self.assertIsNotNone(submission_result[saq.cloudphish.KEY_DETAILS])
        self.assertEquals(submission_result[saq.cloudphish.KEY_STATUS], saq.cloudphish.STATUS_NEW)
        self.assertEquals(submission_result[saq.cloudphish.KEY_ANALYSIS_RESULT], saq.cloudphish.SCAN_RESULT_UNKNOWN)
        self.assertIsNone(submission_result[saq.cloudphish.KEY_HTTP_RESULT])
        self.assertIsNone(submission_result[saq.cloudphish.KEY_HTTP_MESSAGE])
        self.assertIsNone(submission_result[saq.cloudphish.KEY_SHA256_CONTENT])
        self.assertIsNotNone(submission_result[saq.cloudphish.KEY_SHA256_URL])
        self.assertIsNone(submission_result[saq.cloudphish.KEY_LOCATION])
        self.assertIsNone(submission_result[saq.cloudphish.KEY_FILE_NAME])
        self.assertIsNotNone(submission_result[saq.cloudphish.KEY_UUID])

        # now we start an engine to work on cloudphish analysis
        engine = TestEngine(analysis_pools={ANALYSIS_MODE_CLOUDPHISH: 1}, 
                            local_analysis_modes=[ANALYSIS_MODE_CLOUDPHISH])
        engine.enable_alerting()
        engine.enable_module('analysis_module_crawlphish', ANALYSIS_MODE_CLOUDPHISH)
        engine.enable_module('analysis_module_cloudphish_request_analyzer', ANALYSIS_MODE_CLOUDPHISH)
        # force this analysis to become an alert
        engine.enable_module('analysis_module_forced_detection', ANALYSIS_MODE_CLOUDPHISH)
        #engine.enable_module('analysis_module_detection', ANALYSIS_MODE_CLOUDPHISH)
        engine.controlled_stop()
        engine.start()
        engine.wait()

        submission_result = ace_api.cloudphish_submit(TEST_URL)
        self.assertEquals(submission_result[saq.cloudphish.KEY_RESULT], saq.cloudphish.RESULT_OK)
        self.assertIsNotNone(submission_result[saq.cloudphish.KEY_DETAILS])
        self.assertEquals(submission_result[saq.cloudphish.KEY_STATUS], saq.cloudphish.STATUS_ANALYZED)
        self.assertEquals(submission_result[saq.cloudphish.KEY_ANALYSIS_RESULT], saq.cloudphish.SCAN_RESULT_ALERT)
        self.assertEquals(submission_result[saq.cloudphish.KEY_HTTP_RESULT], 200)
        self.assertEquals(submission_result[saq.cloudphish.KEY_HTTP_MESSAGE], 'OK')
        self.assertIsNotNone(submission_result[saq.cloudphish.KEY_SHA256_CONTENT])
        self.assertIsNotNone(submission_result[saq.cloudphish.KEY_SHA256_URL])
        self.assertIsNotNone(submission_result[saq.cloudphish.KEY_LOCATION])
        self.assertEquals(submission_result[saq.cloudphish.KEY_FILE_NAME], 'Payment_Advice.pdf')
        self.assertIsNotNone(submission_result[saq.cloudphish.KEY_UUID])

        # attempt to download the contents of the url
        output_path = os.path.join(saq.TEMP_DIR, 'cloudphish.download')
        download_result = ace_api.cloudphish_download(sha256=submission_result[saq.cloudphish.KEY_SHA256_URL], 
                                                      output_path=output_path)
        
        # make sure we can download the file
        with open(output_path, 'rb') as fp:
            data = fp.read()
        hasher = hashlib.sha256()
        hasher.update(data)
        self.assertEquals(hasher.hexdigest().lower(), submission_result[saq.cloudphish.KEY_SHA256_CONTENT].lower())

        # make sure we can clear the alert for this url
        self.assertTrue(ace_api.cloudphish_clear_alert(sha256=submission_result[saq.cloudphish.KEY_SHA256_URL]))

        # and verify that
        submission_result = ace_api.cloudphish_submit(TEST_URL)
        self.assertEquals(submission_result[saq.cloudphish.KEY_ANALYSIS_RESULT], saq.cloudphish.SCAN_RESULT_CLEAR)


class EventsAPITestCase(ACEEngineTestCase):

    def setUp(self, *args, **kwargs):
        super().setUp(*args, **kwargs)
        self.start_api_server()

        ace_api.set_default_remote_host(saq.API_PREFIX)
        ace_api.set_default_ssl_ca_path(saq.CONFIG['SSL']['ca_chain_path'])

    def test_get_open_events(self):
        result = ace_api.get_open_events()
        self.assertIsNotNone(result)
        self.assertEquals(result, [])

    @use_db
    def test_update_event_status(self, db, c):
        # Create an event
        c.execute("""
            INSERT INTO `events`
            (`creation_date`,
            `name`,
            `type`,
            `vector`,
            `prevention_tool`,
            `remediation`,
            `status`,
            `comment`,
            `campaign_id`)
            VALUES
            ("2019-03-06",
            "test event",
            "phish",
            "corporate email",
            "response team",
            "not remediated",
            "OPEN",
            "blah blah blah",
            1);""")
        db.commit()

        c.execute("SELECT id FROM events WHERE name='test event'")
        event_id = c.fetchone()[0]

        result = ace_api.update_event_status(event_id, 'CLOSED')
        self.assertIsNotNone(result)
        self.assertEqual(result['status'], 'CLOSED')
