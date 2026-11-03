"""Optional 48kHz PCM16 Windows sink. Import never opens an audio device.

Not wired to preview's default null output. Explicit construction is required.
Bounded queue failures are visible; it does not silently drop or synthesize PCM.
"""
import collections,ctypes as C,threading,time

class WaveOut48:
    def __init__(self,*,capacity_blocks=12,prefill_blocks=4,monitor_gain=1.0):
        self.created_ns=time.perf_counter_ns()
        if not 1<=prefill_blocks<=capacity_blocks<=64:raise ValueError('playback bounds')
        if not 0<monitor_gain<=8:raise ValueError('explicit monitor gain must be 0..8')
        from ctypes import wintypes as W
        class Format(C.Structure):
            _pack_=2
            _fields_=[('tag',W.WORD),('channels',W.WORD),('rate',W.DWORD),('bytes_sec',W.DWORD),('align',W.WORD),('bits',W.WORD),('extra',W.WORD)]
        class Header(C.Structure):
            _fields_=[('data',C.c_void_p),('length',W.DWORD),('recorded',W.DWORD),('user',C.c_size_t),('flags',W.DWORD),('loops',W.DWORD),('next',C.c_void_p),('reserved',C.c_size_t)]
        self.lib=C.WinDLL('winmm');self.handle=C.c_void_p();self.format=Format(1,1,48000,96000,2,16,0)
        self.lib.waveOutOpen.argtypes=[C.POINTER(C.c_void_p),W.UINT,C.POINTER(Format),C.c_size_t,C.c_size_t,W.DWORD]
        for name in ('waveOutPrepareHeader','waveOutUnprepareHeader','waveOutWrite'):getattr(self.lib,name).argtypes=[C.c_void_p,C.POINTER(Header),W.UINT]
        for name in ('waveOutReset','waveOutClose'):getattr(self.lib,name).argtypes=[C.c_void_p]
        self._check(self.lib.waveOutOpen(C.byref(self.handle),0xffffffff,C.byref(self.format),0,0,0))
        self.opened_ns=time.perf_counter_ns();self.device_closed=False;self.cleanup_errors=[]
        self.buffers=[C.create_string_buffer(1024) for _ in range(4)];self.headers=[Header(C.addressof(b),1024,0,0,0,0,None,0) for b in self.buffers]
        self.size=C.sizeof(Header);self.busy=[False]*4;self.prepared=[False]*4;self.q=collections.deque();self.held_samples=0;self.cleanup_finished=False;self.capacity=capacity_blocks;self.prefill=prefill_blocks;self.gain=monitor_gain;self.started=False;self.lock=threading.Lock();self.closed=False;self.error=None;self.counts=collections.Counter()
        self.events=collections.deque(maxlen=64);self.error_info=None;self.close_requested_ns=None;self.close_reason=None;self.worker_exit_ns=None;self.stage='created';self.first_feed_ns=None;self.last_feed_ns=None;self.max_feed_gap_ns=0;self.last_worker_ns=None;self.max_worker_gap_ns=0;self.native_api_max_ms={}
        self._record_locked('opened',open_ms=(self.opened_ns-self.created_ns)/1e6)
        try:
            self.thread=threading.Thread(target=self._run,name='preview-waveout48',daemon=True)
            self.thread.start()
        except BaseException as exc:
            # Open succeeded, but no worker owns this handle. No header has
            # been prepared or submitted yet, so close it exactly once here.
            # A cleanup failure must not replace the original start failure.
            self.closed=True;self.close_requested_ns=time.perf_counter_ns();self.close_reason='worker_start_failed'
            self.error=str(exc);self.error_info=dict(type=type(exc).__name__,message=str(exc),
                at_ns=self.close_requested_ns,stage='worker_start',close_requested_ns=self.close_requested_ns)
            self._record_locked('constructor_start_failed',error=str(exc))
            try:
                self._native('waveOutClose',self.handle);self.device_closed=True
            except BaseException as cleanup_exc:
                self.cleanup_errors.append(dict(api='waveOutClose',error=str(cleanup_exc)))
                self._record_locked('cleanup_error',api='waveOutClose',error=str(cleanup_exc))
            self.stage='startup_failed'
            self._record_locked('constructor_cleanup_returned',device_closed=self.device_closed)
            # The owner may use only this explicit proof when constructor
            # failure prevents publication of a sink object.
            try:
                exc.native_device_opened=True
                exc.native_device_closed=self.device_closed
            except BaseException:pass
            raise
    @staticmethod
    def _check(code):
        if code:raise RuntimeError('waveOut error '+str(code))
    def _record_locked(self,event,**fields):
        # Also supports constructor-free numerical tests; no device access.
        if not hasattr(self,'events'):self.events=collections.deque(maxlen=64)
        self.events.append(dict(event=event,at_ns=time.perf_counter_ns(),stage=getattr(self,'stage',None),**fields))
    def _native(self,name,*args):
        self.stage=name;begin=time.perf_counter_ns();code=getattr(self.lib,name)(*args)
        if not hasattr(self,'native_api_max_ms'):self.native_api_max_ms={}
        self.native_api_max_ms[name]=max(self.native_api_max_ms.get(name,0.),(time.perf_counter_ns()-begin)/1e6)
        self._check(code)
        return code
    def feed(self,a):
        arrived=time.perf_counter_ns()
        import numpy as np
        if a.shape!=(512,) or not np.isfinite(a).all():raise ValueError('512 finite mono samples required')
        x=np.asarray(a,dtype=float)*self.gain;clipped=int(np.count_nonzero(abs(x)>1));pcm=np.rint(np.clip(x,-1,1)*32767).astype('<i2').tobytes()
        with self.lock:
            if self.error:
                self._record_locked('feed_rejected_native_error',error=self.error)
                raise RuntimeError('playback error: '+str(self.error))
            if self.closed:
                self._record_locked('feed_rejected_closed',close_reason=getattr(self,'close_reason',None))
                raise RuntimeError('playback closed: '+str(getattr(self,'close_reason',None)))
            if len(self.q)*512+getattr(self,'held_samples',0)+512>self.capacity*512:self.counts['overflow']+=1;raise RuntimeError('playback queue full')
            previous=getattr(self,'last_feed_ns',None)
            if previous is not None:
                gap=arrived-previous;self.max_feed_gap_ns=max(getattr(self,'max_feed_gap_ns',0),gap)
                if gap>15_000_000:self._record_locked('feed_gap',gap_ms=gap/1e6,queued_blocks=len(self.q),inflight_blocks=sum(self.busy))
            if getattr(self,'first_feed_ns',None) is None:self.first_feed_ns=arrived;self._record_locked('first_feed')
            self.last_feed_ns=arrived
            self.q.append(pcm);self.counts['fed_samples']+=512;self.counts['clip_samples']+=clipped;self.counts['peak_blocks']=max(self.counts['peak_blocks'],len(self.q))
            # Bounded numeric evidence for cumulative supply deficit. Audio is
            # never copied here; healthy snapshots omit this per-block ring.
            if not hasattr(self,'feed_trace'):self.feed_trace=collections.deque(maxlen=64)
            self.feed_trace.append(dict(at_ns=arrived,fed_samples=self.counts['fed_samples'],
                submitted_samples=self.counts['submitted_samples'],completed_samples=self.counts['completed_samples'],
                queued_blocks=len(self.q),held_samples=getattr(self,'held_samples',0),inflight_blocks=sum(self.busy)))
    def _ledger_locked(self):
        c=self.counts;q=len(self.q)*512;held_raw=getattr(self,'held_samples',0);inflight=sum(self.busy)*512
        # Native counters are nonnegative integers. Missing Counter keys have
        # the established zero meaning, but an explicit bad value is unknown.
        # Do not mutate or sanitize the raw Counter used by normalize_player.
        raw={name:c.get(name,0) for name in ('fed_samples','submitted_samples','completed_samples',
            'cancelled_queued_samples','cancelled_held_samples','cancelled_submitted_unconfirmed_samples')}
        raw['held_samples']=held_raw
        values={name:value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None
                for name,value in raw.items()}
        invalid=[name for name,value in values.items() if value is None]
        def known_sum(*items):return sum(items) if all(value is not None for value in items) else None
        held=values['held_samples']
        cancelled_unsubmitted=known_sum(values['cancelled_queued_samples'],values['cancelled_held_samples'])
        cancelled_submitted=values['cancelled_submitted_unconfirmed_samples']
        ledger=None if invalid else values['fed_samples']==values['submitted_samples']+q+held+cancelled_unsubmitted
        submitted_ledger=None if invalid else values['submitted_samples']==values['completed_samples']+inflight+cancelled_submitted
        finished=getattr(self,'cleanup_finished',False);closed=getattr(self,'device_closed',False)
        return dict(queued_samples=q,held_samples=held,inflight_samples=inflight,
            cancelled_queued_samples=c.get('cancelled_queued_samples',0),cancelled_held_samples=c.get('cancelled_held_samples',0),
            cancelled_unsubmitted_samples=cancelled_unsubmitted,cancelled_submitted_unconfirmed_samples=c.get('cancelled_submitted_unconfirmed_samples',0),
            cancelled_submitted_samples=cancelled_submitted,cancelled_samples=known_sum(cancelled_unsubmitted,cancelled_submitted),
            ledger_balanced=ledger,submitted_ledger_balanced=submitted_ledger,
            ledger_inputs_valid=not invalid,ledger_invalid_fields=invalid,
            inflight_known=not(finished and not closed),
            inflight_state='UNKNOWN_AFTER_CLOSE_FAILURE' if finished and not closed else 'NATIVE_API_ACCEPTED_UNCONFIRMED',
            capacity_samples=None if getattr(self,'capacity',None) is None else self.capacity*512,hardware_capacity_samples=2048,
            ledger_scope='software admission and native API acknowledgements; not DAC audibility')
    def snapshot(self):
        with self.lock:
            headers=getattr(self,'headers',None);thread=getattr(self,'thread',None)
            return dict(self.counts,queued_blocks=len(self.q),inflight_blocks=sum(self.busy),
                inflight_not_done_blocks=None if headers is None else sum(bool(b and not(h.flags&1)) for b,h in zip(self.busy,headers)),
                error=self.error,error_info=getattr(self,'error_info',None),closed=getattr(self,'closed',None),started=getattr(self,'started',None),device_closed=getattr(self,'device_closed',None),cleanup_errors=list(getattr(self,'cleanup_errors',())),
                close_requested_ns=getattr(self,'close_requested_ns',None),close_reason=getattr(self,'close_reason',None),
                created_ns=getattr(self,'created_ns',None),opened_ns=getattr(self,'opened_ns',None),first_feed_ns=getattr(self,'first_feed_ns',None),last_feed_ns=getattr(self,'last_feed_ns',None),
                worker_exit_ns=getattr(self,'worker_exit_ns',None),worker_alive=None if thread is None else thread.is_alive(),
                max_feed_gap_ms=getattr(self,'max_feed_gap_ns',0)/1e6,max_worker_wake_gap_ms=getattr(self,'max_worker_gap_ns',0)/1e6,
                native_api_max_ms=dict(getattr(self,'native_api_max_ms',{})),events=list(getattr(self,'events',())),stage=getattr(self,'stage',None),rate=48000,monitor_gain=self.gain,**self._ledger_locked())
    def snapshot_compact(self):
        """Numeric control-plane ledger; full event/trace audit stays explicit."""
        with self.lock:
            thread=getattr(self,'thread',None);info=getattr(self,'error_info',None)
            return dict(self.counts,queued_blocks=len(self.q),inflight_blocks=sum(self.busy),
                error=self.error,error_info=None if not info else {k:info.get(k) for k in ('type','message','at_ns','stage','close_requested_ns')},
                closed=getattr(self,'closed',None),started=getattr(self,'started',None),device_closed=getattr(self,'device_closed',None),cleanup_errors=list(getattr(self,'cleanup_errors',())),
                close_requested_ns=getattr(self,'close_requested_ns',None),close_reason=getattr(self,'close_reason',None),
                created_ns=getattr(self,'created_ns',None),opened_ns=getattr(self,'opened_ns',None),
                first_feed_ns=getattr(self,'first_feed_ns',None),last_feed_ns=getattr(self,'last_feed_ns',None),
                worker_exit_ns=getattr(self,'worker_exit_ns',None),worker_alive=None if thread is None else thread.is_alive(),
                max_feed_gap_ms=getattr(self,'max_feed_gap_ns',0)/1e6,max_worker_wake_gap_ms=getattr(self,'max_worker_gap_ns',0)/1e6,
                native_api_max_ms=dict(getattr(self,'native_api_max_ms',{})),stage=getattr(self,'stage',None),
                rate=48000,channels=1,bits=16,requested_format='PCM16LE_MONO_48000_ACCEPTED_BY_WAVEOUTOPEN',
                monitor_gain=self.gain,strict_no_drop=True,drop_policy='strict-overflow-error-no-concealment',**self._ledger_locked())
    def _run(self):
        try:
            while not self.closed:
                with self.lock:
                    now=time.perf_counter_ns();previous=getattr(self,'last_worker_ns',None)
                    if previous is not None:self.max_worker_gap_ns=max(getattr(self,'max_worker_gap_ns',0),now-previous)
                    self.last_worker_ns=now
                    if self.closed:break
                    if not self.started and len(self.q)>=self.prefill:self.started=True;self._record_locked('playback_started',queued_blocks=len(self.q))
                for i,h in enumerate(self.headers):
                    with self.lock:
                        if self.closed:break
                        completed_before_reset=bool(self.busy[i] and h.flags&1)
                    if completed_before_reset:
                        self._native('waveOutUnprepareHeader',self.handle,C.byref(h),self.size)
                        with self.lock:
                            self.counts['completed_samples']+=512;self.busy[i]=False;self.prepared[i]=False
                    with self.lock:
                        if self.closed:break
                        chunk=self.q.popleft() if self.started and not self.busy[i] and self.q else None
                        if chunk is not None:self.held_samples=getattr(self,'held_samples',0)+512
                    if chunk is not None:
                        C.memmove(h.data,chunk,1024);h.flags=0
                        with self.lock:
                            if self.closed:break
                        self._native('waveOutPrepareHeader',self.handle,C.byref(h),self.size)
                        with self.lock:
                            self.prepared[i]=True
                            if self.closed:break
                        self._native('waveOutWrite',self.handle,C.byref(h),self.size)
                        with self.lock:
                            # A cancellation during Write cannot undo an API
                            # success; retain it as submitted until close ACK.
                            self.held_samples-=512;self.counts['submitted_samples']+=512;self.busy[i]=True
                with self.lock:
                    starving=self.started and not any(self.busy) and not self.closed
                    if starving:self.counts['underflow']+=1;self.stage='starvation';self._record_locked('starvation',queued_blocks=len(self.q),max_feed_gap_ms=getattr(self,'max_feed_gap_ns',0)/1e6,max_worker_wake_gap_ms=getattr(self,'max_worker_gap_ns',0)/1e6)
                if starving:
                    raise RuntimeError('playback underflow; no concealment')
                time.sleep(.001)
        except BaseException as exc:
            with self.lock:
                if self.error is None:
                    self.error=str(exc);self.error_info=dict(type=type(exc).__name__,message=str(exc),at_ns=time.perf_counter_ns(),stage=getattr(self,'stage',None),close_requested_ns=getattr(self,'close_requested_ns',None),queued_blocks=len(self.q),held_samples=getattr(self,'held_samples',0),inflight_blocks=sum(self.busy),feed_trace=list(getattr(self,'feed_trace',())))
                self._record_locked('native_error',error=self.error)
        finally:
            for name,args in [('waveOutReset',(self.handle,))]+[('waveOutUnprepareHeader',(self.handle,C.byref(h),self.size)) for i,h in enumerate(self.headers) if self.prepared[i]]+[('waveOutClose',(self.handle,))]:
                try:
                    self._native(name,*args)
                    if name=='waveOutClose':
                        with self.lock:self.device_closed=True
                except BaseException as exc:
                    with self.lock:
                        if not hasattr(self,'cleanup_errors'):self.cleanup_errors=[]
                        self.cleanup_errors.append(dict(api=name,error=str(exc)))
                        self._record_locked('cleanup_error',api=name,error=str(exc))
            with self.lock:
                # Nothing remaining in software can be submitted after this
                # worker exits. Reset-created DONE flags are never counted as
                # playback completion. Only a real Close success resolves the
                # outstanding submitted ledger as cancellation.
                self.counts['cancelled_queued_samples']+=len(self.q)*512
                self.counts['cancelled_held_samples']+=getattr(self,'held_samples',0)
                self.q.clear();self.held_samples=0
                if getattr(self,'device_closed',False):
                    self.counts['cancelled_submitted_unconfirmed_samples']+=sum(self.busy)*512
                    self.busy=[False]*len(self.busy)
                self.cleanup_finished=True
                self.worker_exit_ns=time.perf_counter_ns();self.stage='exited';self._record_locked('worker_exited')
    def request_close(self,reason='owner_close_unspecified'):
        """Only signal shutdown; safe from producer/control callbacks, no join."""
        with self.lock:
            if getattr(self,'close_requested_ns',None) is None:
                self.close_requested_ns=time.perf_counter_ns();self.close_reason=str(reason);self._record_locked('close_requested',reason=str(reason))
            self.closed=True
    def close(self,reason='owner_close_unspecified'):
        self.request_close(reason)
        if self.thread is not threading.current_thread():self.thread.join(2)
        with self.lock:self._record_locked('close_returned',worker_alive=self.thread.is_alive())
    def close_with_reason(self,reason):self.close(reason)
