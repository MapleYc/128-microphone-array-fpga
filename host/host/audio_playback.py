"""Strict bounded PCM16/16k waveOut owner; no hidden discard or concealment.

Default software credit is still 8000 samples. Four real 512-sample blocks
prefill before first submission (128ms; 96ms more than the former first-block
start). A selected-but-preparing source has not admitted a continuous stream.
Only numeric, bounded diagnostics are retained. Import never opens a device.
"""
import collections
import ctypes as C
import threading
import time


class WaveOut:
    RATE = 16000
    BLOCK_SAMPLES = 512
    BLOCK_BYTES = 1024
    HARDWARE_BLOCKS = 4

    def __init__(self, *, capacity_samples=8000, prefill_blocks=4,
                 _test_api=None, _allow_test_api=False, _start_worker=True):
        if not isinstance(capacity_samples,int) or not 512 <= capacity_samples <= 8000:
            raise ValueError('PCM16 software capacity must be 512..8000 samples')
        if not isinstance(prefill_blocks,int) or not 1 <= prefill_blocks <= 4 or prefill_blocks*512>capacity_samples:
            raise ValueError('PCM16 prefill must fit 1..4 real blocks')
        if _test_api is not None and not _allow_test_api:
            raise ValueError('Fake native API requires explicit test opt-in')
        from ctypes import wintypes as W
        class Format(C.Structure):
            _pack_=2
            _fields_=[('tag',W.WORD),('channels',W.WORD),('rate',W.DWORD),('bytes_sec',W.DWORD),('align',W.WORD),('bits',W.WORD),('extra',W.WORD)]
        class Header(C.Structure):
            _fields_=[('data',C.c_void_p),('length',W.DWORD),('recorded',W.DWORD),('user',C.c_size_t),('flags',W.DWORD),('loops',W.DWORD),('next',C.c_void_p),('reserved',C.c_size_t)]
        self.lock=threading.RLock();self.stop=threading.Event();self.done=threading.Event()
        self.created_ns=time.perf_counter_ns();self.opened_ns=None;self.worker_exit_ns=None
        self.capacity=capacity_samples;self.prefill=prefill_blocks;self.data=bytearray()
        self.busy=[False]*4;self.prepared=[False]*4;self.held_samples=0
        self.counts=collections.Counter();self.events=collections.deque(maxlen=64)
        self.feed_trace=collections.deque(maxlen=64);self.submit_trace=collections.deque(maxlen=64);self.complete_trace=collections.deque(maxlen=64)
        self.native_api_max_ms={};self.cleanup_errors=[];self.error=None;self.error_info=None
        self.closed=False;self.device_closed=False;self.device_open=False;self.started=False
        self.close_requested_ns=None;self.close_reason=None;self.first_feed_ns=None;self.last_feed_ns=None
        self.last_completed=None;self.last_worker_ns=None;self.max_worker_gap_ns=0;self.max_feed_gap_ns=0
        self.stage='created';self.thread=None;self.handle=C.c_void_p()
        self.format=Format(1,1,16000,32000,2,16,0)
        self.buffers=[C.create_string_buffer(1024) for _ in range(4)]
        self.headers=[Header(C.addressof(b),1024,0,0,0,0,None,0) for b in self.buffers]
        self.size=C.sizeof(Header)
        self.lib=_test_api if _test_api is not None else C.WinDLL('winmm')
        if _test_api is None:
            self.lib.waveOutOpen.argtypes=[C.POINTER(C.c_void_p),W.UINT,C.POINTER(Format),C.c_size_t,C.c_size_t,W.DWORD]
            self.lib.waveOutOpen.restype=W.UINT
            for name in ('waveOutPrepareHeader','waveOutUnprepareHeader','waveOutWrite'):
                getattr(self.lib,name).argtypes=[C.c_void_p,C.POINTER(Header),W.UINT]
                getattr(self.lib,name).restype=W.UINT
            for name in ('waveOutReset','waveOutClose'):
                getattr(self.lib,name).argtypes=[C.c_void_p];getattr(self.lib,name).restype=W.UINT
        try:
            self._api('waveOutOpen',C.byref(self.handle),0xffffffff,C.byref(self.format),0,0,0)
            self.device_open=True;self.opened_ns=time.perf_counter_ns();self._record('opened',open_ms=(self.opened_ns-self.created_ns)/1e6)
        except BaseException as exc:
            self._fail(exc,'waveOutOpen');self.closed=True;self.stop.set();self.done.set();raise
        if _start_worker:
            try:
                self.thread=threading.Thread(target=self._run,name='waveout16-owner',daemon=True);self.thread.start()
            except BaseException as exc:
                # Native open already succeeded. A failed thread construction
                # must not leak its device merely because no owner could run.
                self._fail(exc,'worker_start');self._cleanup();raise

    def _record(self,event,**values):
        with self.lock:self.events.append(dict(event=event,at_ns=time.perf_counter_ns(),stage=self.stage,**values))

    def _api(self,name,*args):
        with self.lock:self.stage=name
        begin=time.perf_counter_ns()
        try:
            code=getattr(self.lib,name)(*args)
            if code:raise RuntimeError('%s error %s'%(name,code))
        finally:
            elapsed=(time.perf_counter_ns()-begin)/1e6
            with self.lock:self.native_api_max_ms[name]=max(elapsed,self.native_api_max_ms.get(name,0.))

    def _fail(self,error,stage=None):
        with self.lock:
            if self.error is None:
                self.error=str(error)
                self.error_info=dict(type=type(error).__name__,message=str(error),at_ns=time.perf_counter_ns(),
                    stage=stage or self.stage,close_requested_ns=self.close_requested_ns,
                    queued_samples=len(self.data)//2,held_samples=self.held_samples,inflight_samples=sum(self.busy)*512,
                    feed_trace=list(self.feed_trace),submit_trace=list(self.submit_trace),complete_trace=list(self.complete_trace))
            self.closed=True;self.stop.set();self._record('native_error',error=self.error)

    def feed(self,pcm):
        if not isinstance(pcm,(bytes,bytearray,memoryview)):
            raise TypeError('PCM16 playback accepts bytes only')
        if len(pcm)%2:raise ValueError('PCM16 byte count must be even')
        samples=len(pcm)//2;now=time.perf_counter_ns()
        with self.lock:
            self.counts['input_samples_seen']+=samples
            if self.error or self.closed:
                self.counts['rejected_samples']+=samples
                self._record('feed_rejected',error=self.error,close_reason=self.close_reason)
                raise RuntimeError('PCM16 playback unavailable: '+str(self.error or self.close_reason or 'closed'))
            # Retain credit while a block is outside the queue awaiting Write.
            if len(self.data)//2+self.held_samples+samples>self.capacity:
                self.counts['rejected_samples']+=samples;self.counts['overflow']+=1
                error=RuntimeError('PCM16 playback overflow: 8000-sample maximum; no silent discard')
                self._fail(error,'input_capacity');raise error
            if self.first_feed_ns is None:self.first_feed_ns=now
            if self.last_feed_ns is not None:self.max_feed_gap_ns=max(self.max_feed_gap_ns,now-self.last_feed_ns)
            self.last_feed_ns=now;self.data.extend(pcm);self.counts['fed_samples']+=samples
            self.counts['peak_software_samples']=max(self.counts['peak_software_samples'],len(self.data)//2+self.held_samples)
            self.feed_trace.append(dict(at_ns=now,samples=samples,fed_samples=self.counts['fed_samples'],queued_samples=len(self.data)//2,
                held_samples=self.held_samples,inflight_samples=sum(self.busy)*512,completed_samples=self.counts['completed_samples']))

    def _observe_completed(self):
        # Observe WHDR_DONE before Reset. Reset-created DONE flags are cancelled,
        # never re-labelled as observed normal playback completions.
        for i,h in enumerate(self.headers):
            with self.lock:
                complete=self.busy[i] and bool(h.flags&1)
                if complete:
                    self.busy[i]=False;self.counts['completed_samples']+=512;self.last_completed=time.monotonic()
                    self.complete_trace.append(dict(at_ns=time.perf_counter_ns(),slot=i,completed_samples=self.counts['completed_samples']))
            if complete:
                self._api('waveOutUnprepareHeader',self.handle,C.byref(h),self.size)
                with self.lock:self.prepared[i]=False

    def _cycle(self):
        self._check_hardware_empty()
        self._observe_completed()
        self._check_hardware_empty()
        with self.lock:
            now=time.perf_counter_ns()
            if self.last_worker_ns is not None:self.max_worker_gap_ns=max(self.max_worker_gap_ns,now-self.last_worker_ns)
            self.last_worker_ns=now
            if self.closed:return
            if not self.started and len(self.data)//2>=self.prefill*512:
                self.started=True;self._record('playback_started',prefill_samples=self.prefill*512)
        for i,h in enumerate(self.headers):
            with self.lock:
                if self.closed:break
                if not self.started or self.busy[i] or len(self.data)<1024:continue
                chunk=bytes(self.data[:1024]);del self.data[:1024];self.held_samples+=512
            C.memmove(h.data,chunk,1024);h.flags=0
            self._api('waveOutPrepareHeader',self.handle,C.byref(h),self.size)
            with self.lock:
                self.prepared[i]=True
                if self.closed:break
                # Logical Write commitment precedes this native call. A stop
                # observed during Prepare cannot reach this commitment point.
                self.stage='write_committed'
            self._api('waveOutWrite',self.handle,C.byref(h),self.size)
            with self.lock:
                self.held_samples-=512;self.busy[i]=True;self.counts['submitted_samples']+=512
                self.submit_trace.append(dict(at_ns=time.perf_counter_ns(),slot=i,submitted_samples=self.counts['submitted_samples']))
        with self.lock:
            starving=self.started and not self.closed and not any(self.busy) and self.held_samples==0 and len(self.data)<1024
            if starving:
                self.counts['underflow']+=1;self.stage='starvation';self._record('starvation',queued_samples=len(self.data)//2)
        if starving:raise RuntimeError('PCM16 playback underflow; no concealment')

    def _check_hardware_empty(self):
        """Do not hide a delayed owner's empty native queue by refilling it.

        WHDR_DONE is a driver completion observation, not an absolute DAC-time
        certificate. A requested owner cancellation is not an underflow.
        """
        with self.lock:
            empty=(self.started and self.counts['submitted_samples']>0 and not self.closed
                   and not any(b and not(h.flags&1) for b,h in zip(self.busy,self.headers)))
            if empty:
                self.counts['underflow']+=1;self.stage='starvation'
                self._record('hardware_empty_observed',queued_samples=len(self.data)//2,held_samples=self.held_samples)
        if empty:raise RuntimeError('PCM16 playback underflow: submitted native queue empty; no concealment')

    def _cleanup(self):
        try:self._observe_completed()
        except BaseException as exc:
            self.cleanup_errors.append(dict(api='observe_before_reset',error=str(exc)));self._fail(exc)
        with self.lock:
            queued=len(self.data)//2;held=self.held_samples
            self.data.clear();self.held_samples=0
            self.counts['cancelled_queued_samples']+=queued;self.counts['cancelled_held_samples']+=held
        reset_ok=False
        try:self._api('waveOutReset',self.handle);reset_ok=True
        except BaseException as exc:self.cleanup_errors.append(dict(api='waveOutReset',error=str(exc)));self._fail(exc)
        if reset_ok:
            with self.lock:
                self.counts['cancelled_submitted_samples']+=sum(self.busy)*512;self.busy=[False]*4
        for i,h in enumerate(self.headers):
            if self.prepared[i]:
                try:
                    self._api('waveOutUnprepareHeader',self.handle,C.byref(h),self.size)
                    with self.lock:self.prepared[i]=False
                except BaseException as exc:self.cleanup_errors.append(dict(api='waveOutUnprepareHeader',slot=i,error=str(exc)));self._fail(exc)
        try:
            self._api('waveOutClose',self.handle)
            with self.lock:
                # Successful device close also retires unobserved submissions
                # if Reset failed; they are conservatively not called played.
                if any(self.busy):
                    self.counts['cancelled_submitted_samples']+=sum(self.busy)*512;self.busy=[False]*4
                self.device_closed=True;self.device_open=False
        except BaseException as exc:self.cleanup_errors.append(dict(api='waveOutClose',error=str(exc)));self._fail(exc)
        with self.lock:
            self.closed=True;self.worker_exit_ns=time.perf_counter_ns();self.stage='exited';self._record('worker_exited',device_closed=self.device_closed)
        self.done.set()

    def _run(self):
        try:
            while not self.stop.is_set():
                self._cycle();self.stop.wait(.001)
        except BaseException as exc:self._fail(exc)
        finally:self._cleanup()

    def _compact_locked(self):
        c=self.counts;q=len(self.data)//2;inflight=sum(self.busy)*512
        cancelled=c['cancelled_queued_samples']+c['cancelled_held_samples']+c['cancelled_submitted_samples']
        return dict(fed_samples=c['fed_samples'],submitted_samples=c['submitted_samples'],completed_samples=c['completed_samples'],
            queued_samples=q,held_samples=self.held_samples,inflight_samples=inflight,
            inflight_not_done_samples=sum(bool(b and not(h.flags&1)) for b,h in zip(self.busy,self.headers))*512,cancelled_samples=cancelled,
            cancelled_queued_samples=c['cancelled_queued_samples'],cancelled_held_samples=c['cancelled_held_samples'],cancelled_submitted_samples=c['cancelled_submitted_samples'],
            rejected_samples=c['rejected_samples'],input_samples_seen=c['input_samples_seen'],dropped_samples=0,
            overflow=c['overflow'],underflow=c['underflow'],peak_software_samples=c['peak_software_samples'],
            capacity_samples=self.capacity,hardware_capacity_samples=2048,prefill_samples=self.prefill*512,prefill_ms=self.prefill*32.,
            ledger_balanced=c['fed_samples']==c['completed_samples']+q+self.held_samples+inflight+cancelled,
            submitted_ledger_balanced=c['submitted_samples']==c['completed_samples']+inflight+c['cancelled_submitted_samples'],
            error=self.error,closed=self.closed,device_closed=self.device_closed,native_closed_ack=self.device_closed and self.worker_exit_ns is not None,
            worker_alive=bool(self.thread and self.thread.is_alive()),started=self.started,stage=self.stage,
            close_requested_ns=self.close_requested_ns,close_reason=self.close_reason,created_ns=self.created_ns,opened_ns=self.opened_ns,
            first_feed_ns=self.first_feed_ns,last_feed_ns=self.last_feed_ns,worker_exit_ns=self.worker_exit_ns,
            max_feed_gap_ms=self.max_feed_gap_ns/1e6,max_worker_wake_gap_ms=self.max_worker_gap_ns/1e6,
            native_api_max_ms=dict(self.native_api_max_ms),rate=16000,strict_no_drop=True,drop_policy='STRICT_FAIL_STOP_NO_DISCARD',
            completion_age_seconds=None if self.last_completed is None else max(0,time.monotonic()-self.last_completed))

    def snapshot_compact(self):
        with self.lock:return self._compact_locked()

    def snapshot(self):
        with self.lock:
            d=self._compact_locked();d.update(error_info=self.error_info,events=list(self.events),feed_trace=list(self.feed_trace),
                submit_trace=list(self.submit_trace),complete_trace=list(self.complete_trace),cleanup_errors=list(self.cleanup_errors))
            return d

    def request_close(self,reason='explicit_owner_close'):
        with self.lock:
            if self.close_requested_ns is None:
                self.close_requested_ns=time.perf_counter_ns();self.close_reason=str(reason);self._record('close_requested',reason=str(reason))
            self.closed=True;self.stop.set()

    def close(self,reason='explicit_owner_close'):
        self.request_close(reason)
        if self.thread is not None and self.thread is not threading.current_thread():self.thread.join(2)
        with self.lock:
            self._record('close_returned',device_closed=self.device_closed)
            return bool(self.device_closed and self.worker_exit_ns is not None and not (self.thread and self.thread.is_alive()))

    def close_with_reason(self,reason):return self.close(reason)

    def clear(self):
        """A legacy clear request invalidates this stream explicitly."""
        self.request_close('explicit_clear_invalidates_stream')
