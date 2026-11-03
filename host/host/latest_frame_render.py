"""Bounded display-only PIL worker; no Tk or audio/device imports.

One conversion may run while one latest request and one completed result wait.
Replacing either waiting slot discards display work only. Main-thread callers
must check viewport_size/geometry_version before displaying a completed image.
An image is never mutated by this worker after publication.
"""
import collections
import threading
import time


class LatestFrameRenderer:
    def __init__(self, convert):
        if not callable(convert):
            raise TypeError('convert must be callable')
        self._convert = convert
        self._condition = threading.Condition()
        self._pending = None
        self._inflight = None
        self._result = None
        self._closing = False
        self._sequence = 0
        self._counts = collections.Counter()
        self._error = None
        self._last_error = None
        self._worker = threading.Thread(target=self._run, name='latest-frame-PIL', daemon=True)
        self._worker.start()

    def submit(self, frame, size, version=0):
        try:
            width, height = size
        except (TypeError, ValueError):
            raise ValueError('viewport size must contain two positive integers') from None
        if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in (width, height)):
            raise ValueError('viewport size must contain two positive integers')
        source_size = (frame.width, frame.height)
        # Freeze metadata with the request; never infer the displayed frame from
        # a later session.latest value after asynchronous work completes.
        metadata = dict(frame=frame, frame_id=frame.frame_id, source_frame=frame.source_frame,
                        source_size=source_size, viewport_size=(width, height), geometry_version=version)
        with self._condition:
            if self._closing:
                raise RuntimeError('display renderer is closed')
            self._sequence += 1
            request = dict(metadata, seq=self._sequence)
            if self._pending is not None:
                self._counts['replaced_requests'] += 1
            self._pending = request
            self._counts['submitted'] += 1
            self._counts['max_pending'] = 1
            self._condition.notify()
            return request['seq']

    def poll_latest(self):
        with self._condition:
            result = self._result
            self._result = None
            if result is not None:
                self._counts['polled'] += 1
            return result

    def snapshot(self):
        with self._condition:
            return dict(counts=dict(self._counts), error=self._error,
                        last_error=None if self._last_error is None else dict(self._last_error),
                        worker_alive=self._worker.is_alive(), pending=int(self._pending is not None),
                        inflight=int(self._inflight is not None), result=int(self._result is not None),
                        closing=self._closing)

    def close(self, timeout=2):
        if timeout is not None and timeout < 0:
            raise ValueError('close timeout must be nonnegative or None')
        with self._condition:
            self._closing = True
            if self._pending is not None:
                self._counts['discarded_requests_on_close'] += 1
                self._pending = None
            if self._result is not None:
                self._counts['discarded_results_on_close'] += 1
                self._result = None
            self._condition.notify_all()
        if threading.current_thread() is self._worker:
            return False
        self._worker.join(timeout)
        return not self._worker.is_alive()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closing or self._pending is not None)
                if self._closing:
                    return
                request = self._pending
                self._pending = None
                self._inflight = request
                self._counts['started'] += 1
                self._counts['max_inflight'] = 1
            stage = 'convert'
            try:
                start = time.perf_counter_ns()
                image = self._convert(request['frame'])
                converted = time.perf_counter_ns()
                stage = 'resize'
                # Exactly PIL.Image.thumbnail(size)'s existing default options.
                image.thumbnail(request['viewport_size'])
                resized = time.perf_counter_ns()
                result = dict(request, image=image, convert_ms=(converted-start)/1e6,
                              resize_ms=(resized-converted)/1e6)
            except Exception as exc:
                with self._condition:
                    self._counts['errors'] += 1
                    self._counts[stage + '_errors'] += 1
                    self._error = type(exc).__name__ + ': ' + str(exc)
                    self._last_error = dict(seq=request['seq'], frame_id=request['frame_id'],
                                            source_frame=request['source_frame'], stage=stage,
                                            type=type(exc).__name__, message=str(exc))
                    self._inflight = None
                continue
            with self._condition:
                self._inflight = None
                self._counts['converted'] += 1
                if self._closing:
                    self._counts['discarded_results_on_close'] += 1
                else:
                    if self._result is not None:
                        self._counts['replaced_results'] += 1
                    self._result = result
                    self._counts['published'] += 1
                    self._counts['max_result'] = 1
