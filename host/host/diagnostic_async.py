"""One bounded, asynchronous JSON diagnostic writer; never owns audio or UI.

``request()`` merely coalesces a pending request. A single daemon performs all
sampling, validation, encoding and filesystem work. Use one instance per output
path. Source ``utc``/sample timestamps are never replaced by a fresh GUI tick.
``close`` seals requests and cancels an unstarted request; it does not interrupt
an in-progress sampler or fabricate a final snapshot.
"""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import threading
import time


def _utc():
    return datetime.now(timezone.utc).isoformat()


class DiagnosticAuditPending(RuntimeError):
    """Cheap readiness check: final forensic data does not exist yet."""


def _validate_metadata(value, *, max_items=100000, max_depth=64):
    """Reject media buffers, non-JSON objects, cycles and nonfinite numbers.

    Integer sample *counts* are valid. No bytes are decoded, repr'd or encoded
    as text, and no ndarray or image is converted into a list.
    """
    active = set()
    remaining = max_items

    def visit(item, depth):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > max_depth:
            raise ValueError('Diagnostic metadata exceeds structural bounds')
        if isinstance(item, (bytes, bytearray, memoryview)):
            raise TypeError('Binary/media buffers are forbidden in diagnostics')
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError('Nonfinite diagnostic number')
            return
        if not isinstance(item, (dict, list, tuple)):
            raise TypeError('Unsupported diagnostic metadata type: ' + type(item).__name__)
        identity = id(item)
        if identity in active:
            raise ValueError('Cyclic diagnostic metadata')
        active.add(identity)
        try:
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise TypeError('Diagnostic object keys must be strings')
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
        finally:
            active.remove(identity)

    visit(value, 0)


def _failure(data):
    host = data.get('host_preview', data)
    failure = host.get('last_failure') if isinstance(host, dict) else None
    if not isinstance(failure, dict) or not failure.get('reason'):
        return None
    return {key: failure.get(key) for key in ('child_generation', 'reason', 'at')}


class LatestJsonDiagnostics:
    """Compact latest status plus optional once-per-failure full audit.

    ``sampler()`` and optional ``fault_sampler()`` must return JSON metadata
    dictionaries and may not access Tk. All calls occur on this worker.
    Fault files are named by the stable failure key, are never overwritten,
    and include the key of the light observation. A full audit with a known
    different failure identity is rejected, not attributed to the old failure.
    A cheap ``DiagnosticAuditPending`` check is retried only on later requests,
    up to eight attempts or five elapsed seconds by default. Exhaustion writes
    metadata explicitly saying the final audit is unavailable, never an initial
    full snapshot disguised as final. Closing retains pending status in health.
    """
    def __init__(self, output_path, sampler, fault_sampler=None, *, max_json_bytes=262144,
                 fault_pending_attempts=8, fault_pending_seconds=5.):
        if not callable(sampler) or fault_sampler is not None and not callable(fault_sampler):
            raise TypeError('Diagnostic samplers must be callable')
        if type(max_json_bytes) is not int or max_json_bytes < 1024:
            raise ValueError('max_json_bytes must be an integer >=1024')
        if type(fault_pending_attempts) is not int or not 1 <= fault_pending_attempts <= 64:
            raise ValueError('fault_pending_attempts must be an integer in 1..64')
        if isinstance(fault_pending_seconds, bool) or not isinstance(fault_pending_seconds, (int, float)) or not math.isfinite(fault_pending_seconds) or not 0 < fault_pending_seconds <= 60:
            raise ValueError('fault_pending_seconds must be finite in (0,60]')
        self.output_path = Path(output_path)
        self.error_path = self.output_path.with_name(self.output_path.stem + '_ERROR.yaml')
        self.sampler = sampler
        self.fault_sampler = fault_sampler
        self.max_json_bytes = max_json_bytes
        self.fault_pending_attempts = fault_pending_attempts
        self.fault_pending_seconds = float(fault_pending_seconds)
        self._condition = threading.Condition()
        self._pending = False
        self._inflight = False
        self._closing = False
        self._closed = False
        self._seen_faults = OrderedDict()
        self._pending_fault = None
        self._stats = dict(requests=0, coalesced_requests=0, rejected_after_close=0,
            discarded_on_close=0, samples_started=0, samples_completed=0, writes=0,
            errors=0, consecutive_errors=0, error_report_errors=0, fault_samples=0, fault_writes=0,
            existing_fault_files=0, fault_errors=0, fault_attempts=0,
            fault_pending_checks=0, fault_pending_exhausted=0, fault_pending_records=0,
            last_written_request=None,
            last_success_utc=None, last_source_utc=None, last_error=None)
        self._timings = {stage + '_' + kind + '_ms': 0.
            for stage in ('sample', 'validate', 'encode', 'write', 'fault')
            for kind in ('max', 'last', 'total')}
        self._worker = threading.Thread(target=self._run, name='latest-json-diagnostics', daemon=True)
        self._worker.start()

    def request(self):
        """Accept/coalesce one request; no sampler, serialization or I/O here."""
        with self._condition:
            if self._closing or self._closed:
                self._stats['rejected_after_close'] += 1
                return False
            self._stats['requests'] += 1
            if self._pending:
                self._stats['coalesced_requests'] += 1
            self._pending = True
            self._condition.notify()
            return True

    def snapshot(self):
        with self._condition:
            result = dict(self._stats)
            if result['last_error'] is not None:
                result['last_error'] = dict(result['last_error'])
            result.update(self._timings)
            result.update(pending=self._pending, inflight=self._inflight,
                closing=self._closing, closed=self._closed,
                worker_alive=self._worker.is_alive(), recent_fault_keys=len(self._seen_faults),
                output_path=str(self.output_path))
            pending_fault = self._pending_fault
            result['fault_audit_pending'] = None if pending_fault is None else dict(
                failure=dict(pending_fault['failure']), attempts=pending_fault['attempts'],
                age_s=max(0., (time.perf_counter_ns() - pending_fault['first_ns']) / 1e9),
                reason=pending_fault['reason'])
            return result

    def close(self, timeout=2.):
        """Seal and join at most timeout; False means an operation is pending."""
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout < 0):
            raise ValueError('close timeout must be finite and nonnegative, or None')
        with self._condition:
            self._closing = True
            if self._pending:
                self._stats['discarded_on_close'] += 1
            self._pending = False
            self._condition.notify_all()
        if threading.current_thread() is self._worker:
            return False
        self._worker.join(timeout)
        return not self._worker.is_alive()

    def _timed(self, key, operation):
        start = time.perf_counter_ns()
        try:
            return operation()
        finally:
            elapsed = (time.perf_counter_ns() - start) / 1e6
            with self._condition:
                self._timings[key] = max(self._timings[key], elapsed)
                self._timings[key.replace('_max_', '_last_')] = elapsed
                self._timings[key.replace('_max_', '_total_')] += elapsed

    def _encode(self, data, *, validated=False):
        if not validated:
            _validate_metadata(data)
        encoded = (store.dumps(data, ensure_ascii=True, allow_nan=False,
            separators=(',', ':')) + '\n').encode('utf-8')
        if len(encoded) > self.max_json_bytes:
            raise ValueError('Diagnostic JSON exceeds byte bound')
        return encoded

    def _write_atomic(self, path, encoded):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.writing')
        with temporary.open('wb') as stream:
            stream.write(encoded)
        temporary.replace(path)

    def _error(self, stage, error, request):
        detail = dict(stage=stage, type=type(error).__name__, message=str(error)[:512],
            utc=_utc(), request=request)
        with self._condition:
            self._stats['errors'] += 1
            self._stats['consecutive_errors'] += 1
            self._stats['last_error'] = detail
        try:
            self._write_atomic(self.error_path, self._encode(detail))
        except Exception:
            with self._condition:
                self._stats['error_report_errors'] += 1

    @staticmethod
    def _fault_path(directory, identity):
        digest = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
        return directory / ('FAULT_' + digest + '.yaml')

    def _mark_fault_seen(self, identity):
        with self._condition:
            if self._pending_fault is not None and self._pending_fault['identity'] == identity:
                self._pending_fault = None
            self._seen_faults[identity] = None
            self._seen_faults.move_to_end(identity)
            while len(self._seen_faults) + int(self._pending_fault is not None) > 16:
                self._seen_faults.popitem(last=False)

    def _finish_pending_fault(self, state, request, limit_reason):
        """Write an explicit unavailable-final record; never an initial audit."""
        path = self._fault_path(self.output_path.parent, state['identity'])
        elapsed = max(0., (time.perf_counter_ns() - state['first_ns']) / 1e9)
        try:
            if path.exists():
                with self._condition:
                    self._stats['existing_fault_files'] += 1
            else:
                record = dict(observed_failure=state['failure'],
                    observed_snapshot_utc=state['observed_snapshot_utc'],
                    audit_identity_verified=False, audit_sampled_utc=None, audit=None,
                    final_audit_pending=True, final_audit_status='pending_exhausted',
                    pending_limit_reason=limit_reason, pending_reason=state['reason'],
                    pending_attempts=state['attempts'], pending_elapsed_s=elapsed,
                    pending_limits=dict(attempts=self.fault_pending_attempts,
                        seconds=self.fault_pending_seconds))
                self._write_atomic(path, self._encode(record))
                with self._condition:
                    self._stats['fault_pending_records'] += 1
            with self._condition:
                self._stats['fault_pending_exhausted'] += 1
            self._mark_fault_seen(state['identity'])
            return True
        except Exception as error:
            # Retain the small pending state. A later request may retry this
            # metadata write, never call the expensive full sampler again.
            with self._condition:
                self._stats['fault_errors'] += 1
            self._error('fault_pending_write', error, request)
            return False

    def _fault(self, data, request):
        if self.fault_sampler is None:
            return
        with self._condition:
            if self._closing:
                return
            pending = self._pending_fault
        failure = _failure(data)
        if failure is None:
            if pending is not None and (time.perf_counter_ns() - pending['first_ns']) / 1e9 >= self.fault_pending_seconds:
                self._finish_pending_fault(pending, request, 'time_limit_without_current_failure')
            return
        identity = json.dumps(failure, sort_keys=True, allow_nan=False, separators=(',', ':'))
        if pending is not None and pending['identity'] != identity:
            if not self._finish_pending_fault(pending, request, 'new_failure_before_final'):
                return
            pending = None
        with self._condition:
            if identity in self._seen_faults:
                self._seen_faults.move_to_end(identity)
                return
        now = time.perf_counter_ns()
        if pending is not None:
            age = (now - pending['first_ns']) / 1e9
            if pending['attempts'] >= self.fault_pending_attempts or age >= self.fault_pending_seconds:
                self._finish_pending_fault(pending, request,
                    'attempt_limit' if pending['attempts'] >= self.fault_pending_attempts else 'time_limit')
                return
        state = pending or dict(identity=identity, failure=failure, first_ns=now,
            attempts=0, reason=None, observed_snapshot_utc=data.get('utc'))
        path = self._fault_path(self.output_path.parent, identity)
        start = time.perf_counter_ns()
        try:
            if path.exists():
                with self._condition:
                    self._stats['existing_fault_files'] += 1
                self._mark_fault_seen(identity)
                return
            with self._condition:
                self._stats['fault_attempts'] += 1
            state['attempts'] += 1
            audit = self.fault_sampler()
            with self._condition:
                self._stats['fault_samples'] += 1
            if not isinstance(audit, dict):
                raise TypeError('Fault sampler must return a metadata dictionary')
            actual = _failure(audit)
            if actual is not None and actual != failure:
                raise ValueError('Full diagnostic failure identity changed before sampling')
            record = dict(observed_failure=failure, audit_identity_verified=actual == failure,
                observed_snapshot_utc=data.get('utc'),
                audit_sampled_utc=_utc(), audit=audit, final_audit_pending=False,
                pending_attempts=state['attempts'],
                pending_elapsed_s=max(0., (time.perf_counter_ns() - state['first_ns']) / 1e9))
            encoded = self._encode(record)
            # This instance is the sole writer. Recheck protects retained files
            # on restart; no old first-fault evidence is intentionally replaced.
            if path.exists():
                with self._condition:
                    self._stats['existing_fault_files'] += 1
                self._mark_fault_seen(identity)
                return
            self._write_atomic(path, encoded)
            with self._condition:
                self._stats['fault_writes'] += 1
            self._mark_fault_seen(identity)
        except DiagnosticAuditPending as pending_error:
            state['reason'] = str(pending_error)[:512]
            with self._condition:
                self._stats['fault_pending_checks'] += 1
                self._pending_fault = state
                while len(self._seen_faults) + 1 > 16:
                    self._seen_faults.popitem(last=False)
            age = (time.perf_counter_ns() - state['first_ns']) / 1e9
            if state['attempts'] >= self.fault_pending_attempts or age >= self.fault_pending_seconds:
                self._finish_pending_fault(state, request,
                    'attempt_limit' if state['attempts'] >= self.fault_pending_attempts else 'time_limit')
        except Exception as error:
            with self._condition:
                self._stats['fault_errors'] += 1
            self._mark_fault_seen(identity)
            self._error('fault', error, request)
        finally:
            elapsed = (time.perf_counter_ns() - start) / 1e6
            with self._condition:
                self._timings['fault_max_ms'] = max(self._timings['fault_max_ms'], elapsed)
                self._timings['fault_last_ms'] = elapsed
                self._timings['fault_total_ms'] += elapsed

    def _run(self):
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closing or self._pending)
                    if self._closing:
                        return
                    request = self._stats['requests']
                    self._pending = False
                    self._inflight = True
                    self._stats['samples_started'] += 1
                stage = 'sample'
                try:
                    data = self._timed('sample_max_ms', self.sampler)
                    if not isinstance(data, dict):
                        raise TypeError('Diagnostic sampler must return a metadata dictionary')
                    stage = 'validate'
                    self._timed('validate_max_ms', lambda: _validate_metadata(data))
                    stage = 'encode'
                    encoded = self._timed('encode_max_ms', lambda: self._encode(data, validated=True))
                    stage = 'write'
                    self._timed('write_max_ms', lambda: self._write_atomic(self.output_path, encoded))
                    with self._condition:
                        self._stats['samples_completed'] += 1
                        self._stats['writes'] += 1
                        self._stats['last_written_request'] = request
                        self._stats['last_success_utc'] = _utc()
                        self._stats['last_source_utc'] = data.get('utc')
                        self._stats['consecutive_errors'] = 0
                    self._fault(data, request)
                except Exception as error:
                    self._error(stage, error, request)
                finally:
                    with self._condition:
                        self._inflight = False
        finally:
            with self._condition:
                self._closed = True
                self._inflight = False
                self._condition.notify_all()
