"""Pure metadata tests; no Tk, network, audio, subprocesses or media files."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
from pathlib import Path
import hashlib
import json
import tempfile
import threading
import time
import unittest
from diagnostic_async import DiagnosticAuditPending, LatestJsonDiagnostics


def wait(predicate, timeout=2.):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.002)
    raise AssertionError('Timed out waiting for diagnostic worker')


class Diagnostics(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'LIVE_STATUS.yaml'
        self.writers = []

    def writer(self, sampler, fault_sampler=None, **kwargs):
        writer = LatestJsonDiagnostics(self.path, sampler, fault_sampler, **kwargs)
        self.writers.append(writer)
        return writer

    def tearDown(self):
        for writer in self.writers:
            self.assertTrue(writer.close(2.))

    def test_request_is_independent_of_blocked_sampler_and_coalesces(self):
        entered = threading.Event(); release = threading.Event(); calls = []
        self.addCleanup(release.set)
        def sample():
            calls.append(threading.get_ident())
            if len(calls) == 1:
                entered.set(); release.wait(1.)
            return dict(utc='unchanged-source-time', samples=len(calls))
        writer = self.writer(sample)
        self.assertTrue(writer.request()); self.assertTrue(entered.wait(1.))
        finished = threading.Event()
        def requests():
            for _ in range(100):
                self.assertTrue(writer.request())
            finished.set()
        thread = threading.Thread(target=requests); thread.start()
        self.assertTrue(finished.wait(.2), 'GUI requests waited for the blocked sampler')
        thread.join(1.)
        state = writer.snapshot()
        self.assertTrue(state['inflight']); self.assertTrue(state['pending'])
        self.assertEqual(state['coalesced_requests'], 99)
        self.assertEqual(len(calls), 1)
        release.set(); wait(lambda: writer.snapshot()['writes'] == 2)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(x != threading.get_ident() for x in calls))
        self.assertEqual(store.loads(self.path.read_text(encoding='utf-8-sig'))['utc'], 'unchanged-source-time')
        self.assertFalse(self.path.with_name('LIVE_STATUS.yaml.writing').exists())

    def test_close_has_bounded_wait_and_cancels_only_unstarted_request(self):
        entered = threading.Event(); release = threading.Event()
        self.addCleanup(release.set)
        def sample():
            entered.set(); release.wait(1.)
            return dict(utc='source', samples=4)
        writer = self.writer(sample); writer.request(); self.assertTrue(entered.wait(1.))
        writer.request()
        start = time.perf_counter(); self.assertFalse(writer.close(.01))
        self.assertLess(time.perf_counter() - start, .2)
        self.assertFalse(writer.request())
        state = writer.snapshot()
        self.assertTrue(state['inflight']); self.assertFalse(state['pending'])
        self.assertEqual(state['discarded_on_close'], 1)
        release.set(); self.assertTrue(writer.close(1.))
        self.assertEqual(writer.snapshot()['samples_started'], 1)
        self.assertEqual(writer.snapshot()['writes'], 1)
        self.assertFalse(writer.snapshot()['worker_alive'])

    def test_atomic_replace_failure_preserves_last_good_then_recovers(self):
        sample = dict(utc='old-source', value=1)
        writer = self.writer(lambda: dict(sample))
        writer.request(); wait(lambda: writer.snapshot()['writes'] == 1)
        old = self.path.read_bytes(); original = writer._write_atomic; fail = [True]
        def write(path, encoded):
            if path == self.path and fail[0]:
                fail[0] = False
                raise PermissionError('injected replace sharing violation')
            return original(path, encoded)
        writer._write_atomic = write
        sample.update(utc='new-source', value=2); writer.request()
        wait(lambda: writer.snapshot()['errors'] == 1 and writer.error_path.is_file())
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(writer.snapshot()['last_error']['stage'], 'write')
        self.assertTrue(writer.snapshot()['worker_alive'])
        self.assertTrue(writer.error_path.is_file())
        writer.request(); wait(lambda: writer.snapshot()['writes'] == 2)
        self.assertEqual(store.loads(self.path.read_text(encoding='utf-8-sig'))['utc'], 'new-source')

    def test_sampler_exception_is_reported_without_killing_worker(self):
        bad = [True]
        def sample():
            if bad[0]:
                raise RuntimeError('injected sampler error')
            return dict(utc='valid', samples=42)
        writer = self.writer(sample); writer.request()
        wait(lambda: writer.snapshot()['errors'] == 1)
        self.assertEqual(writer.snapshot()['last_error']['stage'], 'sample')
        self.assertFalse(self.path.exists())
        bad[0] = False; writer.request(); wait(lambda: writer.snapshot()['writes'] == 1)

    def test_binary_buffers_rejected_without_text_encoding_or_overwriting(self):
        payload = dict(utc='source', samples=192000)
        writer = self.writer(lambda: dict(payload)); writer.request()
        wait(lambda: writer.snapshot()['writes'] == 1); old = self.path.read_bytes()
        for index, buffer in enumerate((b'private', bytearray(b'private'), memoryview(b'private')), 1):
            payload['buffer'] = buffer; writer.request()
            wait(lambda: writer.snapshot()['errors'] == index)
            self.assertEqual(self.path.read_bytes(), old)
            self.assertNotIn('private', writer.error_path.read_text())
        self.assertEqual(writer.snapshot()['last_error']['stage'], 'validate')

    def test_nonfinite_cycles_and_json_size_are_visible_errors(self):
        current = [dict(value=float('nan'))]
        writer = self.writer(lambda: current[0], max_json_bytes=1024)
        writer.request(); wait(lambda: writer.snapshot()['errors'] == 1)
        cyclic = {}; cyclic['cycle'] = cyclic
        current[0] = cyclic; writer.request(); wait(lambda: writer.snapshot()['errors'] == 2)
        current[0] = dict(big='x' * 2000); writer.request(); wait(lambda: writer.snapshot()['errors'] == 3)
        self.assertIn('byte bound', writer.snapshot()['last_error']['message'])
        self.assertFalse(self.path.exists())

    def test_first_fault_is_sampled_once_and_never_overwritten_on_restart(self):
        failure = dict(child_generation=13, reason='underflow', at=100.25)
        sample = dict(utc='light-time', host_preview=dict(last_failure=failure))
        calls = []
        def audit():
            calls.append(True)
            return dict(host_preview=dict(last_failure=dict(failure)), counter=123)
        writer = self.writer(lambda: sample, audit)
        writer.request(); wait(lambda: writer.snapshot()['fault_writes'] == 1)
        fault = next(self.path.parent.glob('FAULT_*.yaml')); digest = hashlib.sha256(fault.read_bytes()).hexdigest()
        record = store.loads(fault.read_text(encoding='utf-8-sig'))
        self.assertTrue(record['audit_identity_verified'])
        self.assertEqual(record['observed_snapshot_utc'], 'light-time')
        writer.request(); wait(lambda: writer.snapshot()['writes'] == 2)
        self.assertEqual(len(calls), 1)
        self.assertTrue(writer.close(1.))
        other = self.writer(lambda: sample, lambda: self.fail('Existing first fault was resampled'))
        other.request(); wait(lambda: other.snapshot()['existing_fault_files'] == 1)
        self.assertEqual(hashlib.sha256(fault.read_bytes()).hexdigest(), digest)

    def test_changed_fault_identity_is_not_misattributed_or_retried_each_tick(self):
        failure = dict(child_generation=1, reason='old', at=2.)
        sample = dict(host_preview=dict(last_failure=failure)); calls = []
        def audit():
            calls.append(True)
            return dict(last_failure=dict(child_generation=2, reason='new', at=3.))
        writer = self.writer(lambda: sample, audit)
        writer.request(); wait(lambda: writer.snapshot()['fault_errors'] == 1)
        self.assertFalse(list(self.path.parent.glob('FAULT_*.yaml')))
        self.assertEqual(writer.snapshot()['last_error']['stage'], 'fault')
        writer.request(); wait(lambda: writer.snapshot()['writes'] == 2)
        self.assertEqual(len(calls), 1)

    def test_recent_fault_memory_is_bounded_and_source_timestamps_not_fabricated(self):
        failure = dict(child_generation=0, reason='test', at=0.)
        def light():return dict(utc='fixed', host_preview=dict(last_failure=dict(failure)))
        def audit():return dict(host_preview=dict(last_failure=dict(failure)))
        writer = self.writer(light, audit)
        for index in range(18):
            failure.update(child_generation=index, at=float(index)); writer.request()
            wait(lambda: writer.snapshot()['fault_writes'] == index + 1)
        self.assertEqual(writer.snapshot()['recent_fault_keys'], 16)
        self.assertEqual(store.loads(self.path.read_text(encoding='utf-8-sig'))['utc'], 'fixed')

    def test_error_report_failure_and_close_from_sampler_are_bounded(self):
        entered = threading.Event(); allow = threading.Event(); holder = {}
        def sample():
            entered.set(); allow.wait(1.)
            self.assertFalse(holder['writer'].close(0))
            raise RuntimeError('after self close')
        writer = self.writer(sample); holder['writer'] = writer
        writer._write_atomic = lambda *args: (_ for _ in ()).throw(PermissionError('injected all writes'))
        writer.request(); self.assertTrue(entered.wait(1.)); allow.set()
        wait(lambda: writer.snapshot()['closed'])
        self.assertEqual(writer.snapshot()['error_report_errors'], 1)
        self.assertTrue(writer.close(0))

    def test_pending_twice_then_final_samples_full_only_once(self):
        failure = dict(child_generation=13, reason='native_underflow', at=5.25)
        sample = dict(utc='first-light-time', host_preview=dict(last_failure=failure))
        checks = []; full_copies = []
        def audit():
            checks.append(True)
            if len(checks) <= 2:
                raise DiagnosticAuditPending('native closed, post final and join still pending')
            full_copies.append(True)
            return dict(host_preview=dict(last_failure=dict(failure)), final=True, process_joined=True)
        writer = self.writer(lambda: sample, audit)
        for attempt in (1, 2):
            writer.request(); wait(lambda: writer.snapshot()['fault_pending_checks'] == attempt)
            state = writer.snapshot()
            self.assertEqual(state['errors'], 0)
            self.assertEqual(state['fault_errors'], 0)
            self.assertEqual(state['fault_samples'], 0)
            self.assertEqual(state['recent_fault_keys'], 0, 'Pending was permanently consumed as seen')
            self.assertEqual(state['fault_audit_pending']['attempts'], attempt)
            self.assertFalse(list(self.path.parent.glob('FAULT_*.yaml')))
        writer.request(); wait(lambda: writer.snapshot()['fault_writes'] == 1)
        self.assertEqual(len(checks), 3); self.assertEqual(len(full_copies), 1)
        self.assertEqual(writer.snapshot()['fault_samples'], 1)
        self.assertIsNone(writer.snapshot()['fault_audit_pending'])
        record = store.loads(next(self.path.parent.glob('FAULT_*.yaml')).read_text(encoding='utf-8-sig'))
        self.assertFalse(record['final_audit_pending']); self.assertTrue(record['audit']['final'])
        self.assertTrue(record['audit']['process_joined']); self.assertEqual(record['pending_attempts'], 3)
        writer.request(); wait(lambda: writer.snapshot()['writes'] == 4)
        self.assertEqual(len(checks), 3); self.assertEqual(len(full_copies), 1)

    def test_pending_attempt_limit_writes_explicit_unavailable_metadata(self):
        failure = dict(child_generation=3, reason='underflow', at=7.)
        sample = dict(utc='original-time', host_preview=dict(last_failure=failure)); checks = []
        def audit():
            checks.append(True)
            raise DiagnosticAuditPending('final publication unavailable')
        writer = self.writer(lambda: sample, audit, fault_pending_attempts=3)
        for attempt in (1, 2, 3):
            writer.request(); wait(lambda: writer.snapshot()['fault_pending_checks'] == attempt)
        wait(lambda: writer.snapshot()['fault_pending_records'] == 1)
        state = writer.snapshot()
        self.assertEqual(state['errors'], 0); self.assertEqual(state['fault_samples'], 0)
        self.assertEqual(state['fault_writes'], 0)
        self.assertEqual(state['fault_pending_exhausted'], 1)
        self.assertIsNone(state['fault_audit_pending'])
        record = store.loads(next(self.path.parent.glob('FAULT_*.yaml')).read_text(encoding='utf-8-sig'))
        self.assertTrue(record['final_audit_pending']); self.assertEqual(record['final_audit_status'], 'pending_exhausted')
        self.assertEqual(record['pending_limit_reason'], 'attempt_limit'); self.assertIsNone(record['audit'])
        self.assertIsNone(record['audit_sampled_utc']); self.assertFalse(record['audit_identity_verified'])
        self.assertEqual(record['observed_snapshot_utc'], 'original-time')
        writer.request(); wait(lambda: writer.snapshot()['writes'] == 4)
        self.assertEqual(len(checks), 3)

    def test_pending_time_limit_is_checked_on_next_request_without_resampling(self):
        failure = dict(child_generation=8, reason='underflow', at=10.)
        sample = dict(host_preview=dict(last_failure=failure)); checks = []
        def audit():
            checks.append(True)
            raise DiagnosticAuditPending('child final pending')
        writer = self.writer(lambda: sample, audit)
        writer.request(); wait(lambda: writer.snapshot()['fault_pending_checks'] == 1)
        # Move only this test helper's elapsed-time origin; no real five-second
        # sleep, device clock, source timestamp or full audit is fabricated.
        with writer._condition:
            writer._pending_fault['first_ns'] -= 6_000_000_000
        writer.request(); wait(lambda: writer.snapshot()['fault_pending_records'] == 1)
        self.assertEqual(len(checks), 1)
        record = store.loads(next(self.path.parent.glob('FAULT_*.yaml')).read_text(encoding='utf-8-sig'))
        self.assertEqual(record['pending_limit_reason'], 'time_limit')
        self.assertEqual(record['pending_attempts'], 1); self.assertTrue(record['final_audit_pending'])

    def test_closing_preserves_cheap_pending_health_without_claiming_final(self):
        failure = dict(child_generation=9, reason='underflow', at=11.)
        sample = dict(host_preview=dict(last_failure=failure))
        def audit():raise DiagnosticAuditPending('final not ready')
        writer = self.writer(lambda: sample, audit)
        writer.request(); wait(lambda: writer.snapshot()['fault_pending_checks'] == 1)
        self.assertTrue(writer.close(1.))
        self.assertTrue(writer.snapshot()['closed'])
        self.assertIsNotNone(writer.snapshot()['fault_audit_pending'])
        self.assertFalse(list(self.path.parent.glob('FAULT_*.yaml')))

    def test_pending_expiry_remains_bounded_if_current_light_failure_disappears(self):
        failure = dict(child_generation=19, reason='underflow', at=11.)
        sample = dict(host_preview=dict(last_failure=failure))
        def audit():raise DiagnosticAuditPending('old child final still pending')
        writer = self.writer(lambda: sample, audit)
        writer.request(); wait(lambda: writer.snapshot()['fault_pending_checks'] == 1)
        with writer._condition:
            writer._pending_fault['first_ns'] -= 6_000_000_000
        sample['host_preview']['last_failure'] = None
        writer.request(); wait(lambda: writer.snapshot()['fault_pending_records'] == 1)
        record = store.loads(next(self.path.parent.glob('FAULT_*.yaml')).read_text(encoding='utf-8-sig'))
        self.assertEqual(record['pending_limit_reason'], 'time_limit_without_current_failure')
        self.assertEqual(record['observed_failure']['child_generation'], 19)
        self.assertTrue(record['final_audit_pending'])


if __name__ == '__main__':
    unittest.main()
