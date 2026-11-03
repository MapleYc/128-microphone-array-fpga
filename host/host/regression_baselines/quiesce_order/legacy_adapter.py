"""Optional HOST preview adapter. No GUI, socket, serial or device on import."""
from __future__ import annotations
import native_paths
import collections,copy,hashlib,json,math,queue,threading,time
from pathlib import Path
from dataclasses import dataclass
from types import MappingProxyType

HERE=Path(__file__).resolve().parent
class PlaybackPreparationCancelled(RuntimeError):
    """Normal owner/selection cancellation; not a hardware or DSP fault."""
@dataclass(frozen=True)
class ValidatedDatagram:
    """Immutable internal view of an already strictly decoded wire packet."""
    raw:bytes
    decoded:object
    def __post_init__(self):
        if not isinstance(self.raw,bytes) or len(self.raw)!=1208 or not isinstance(self.decoded,MappingProxyType):
            raise TypeError('validated packet must be immutable, strict M128 data')
BASE_SHA={
 'frozen.py':'e1edf7aaf6a2d8d596501c5c46af3cdb48f4636bdc9b350b5775c8497694c63f',
 'preview.py':'65bb9502b9c54247746acb553a2232ad1de2351f542f9dfb483265f00beb8aa1',
 'playback48.py':'e9ad2787637843b571de1ec5cb75dfb9a3d2f427e8bb78b6abdcda045704b13c'}

def load_base():
    import sys
    root=HERE/'base'
    for name,h in BASE_SHA.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=h:raise RuntimeError('pinned preview source changed: '+name)
    sys.path.insert(0,str(root))
    import frozen,preview
    if Path(frozen.__file__).resolve()!=root/'frozen.py' or Path(preview.__file__).resolve()!=root/'preview.py':raise RuntimeError('wrong preview module imported')
    versions=frozen.verify();return frozen,preview,versions

class _Output:
    def __init__(self,base,allowed,enable_playback,gain,playback_router=None):
        self.router=playback_router;self.router_owner=playback_router.register_pc_owner() if playback_router is not None else None;self.null=base.NullSink();self.allowed=allowed;self.enable_playback=enable_playback;self.gain=gain;self.player=None;self.suppressed=0;self.nonzero=0;self.peak_abs=0.
        # This constructor runs during backend startup, before the owner's ready
        # gate admits RAW packets. Native device open must not block first PCM.
        self.prepare_ms=0.
        if self.enable_playback:
            start=time.perf_counter_ns()
            if self.router is not None:
                if not self.router.prepare_pc(self.router_owner):raise PlaybackPreparationCancelled('PC playback selection changed during preparation')
            else:self._open()
            self.prepare_ms=(time.perf_counter_ns()-start)/1e6
            if not self.allowed():
                self.reset()
                raise PlaybackPreparationCancelled('DSP owner stopped during audio preparation')
    def _open(self):
        if self.enable_playback and self.player is None:
            from playback48 import WaveOut48
            self.player=WaveOut48(monitor_gain=self.gain)
    def feed(self,a):
        ticket=self.router.ticket() if self.router is not None else None
        if not self.allowed():self.suppressed+=1;return
        self.null.feed(a);self.nonzero+=int((a!=0).sum());self.peak_abs=max(self.peak_abs,float(abs(a).max()))
        if self.enable_playback:
            if self.router is not None:self.router.feed_pc(a,ticket,self.router_owner)
            else:
                if self.player is None:raise RuntimeError('Playback sink must be prepared before DSP readiness')
                self.player.feed(a)
    def snapshot(self):
        route=None if self.router is None else self.router.owner_diagnostics(self.router_owner)
        return dict(self.null.snapshot(),nonzero_samples=self.nonzero,peak_abs=self.peak_abs,suppressed_stale_or_shutdown_blocks=self.suppressed,player=(route['player'] if route is not None else None if self.player is None else self.player.snapshot()),router_owner_diagnostic=route,playback_enabled=self.enable_playback,prepare_ms=self.prepare_ms,prepared_before_dsp_ready=self.enable_playback)
    def reset(self,reason='output_reset'):
        if self.router is not None:self.router.flush_pc(self.router_owner,reason=reason)
        if self.player is not None:
            if hasattr(self.player,'close_with_reason'):self.player.close_with_reason(reason)
            else:self.player.close()
            self.player=None
    def close(self,reason='output_owner_close'):self.reset(reason)

class OriginalBackend:
    def __init__(self,config,lock_deg,allowed,enable_playback=False,monitor_gain=1.,playback_router=None,lock_elevation_deg=0.):
        frozen,base,self.versions=load_base();frozen.configure()
        self.pipeline=base.Pipeline(config,lock_deg=lock_deg,lock_elevation_deg=lock_elevation_deg)
        try:self.output=_Output(base,allowed,enable_playback,monitor_gain,playback_router)
        except BaseException:
            # Pipeline already owns the model worker and original post process.
            # Failed/cancelled native-open startup must release both even though
            # RawPreviewAdapter has not received a completed backend instance.
            self.pipeline.close()
            raise
        self.pipeline.sink=self.output;self.last_result=None
        import sys
        names=('runtime3d','worker3d','machine3d','doa3d','direction3d','calibrate3d','spatial_frame_accel','lowband_guarded_runner','sprint_blocked_power')
        self.versions['loaded_modules']={n:dict(path=str(Path(sys.modules[n].__file__).resolve()),sha256=hashlib.sha256(Path(sys.modules[n].__file__).read_bytes()).hexdigest()) for n in names}
        frame=native_paths.frame_library()
        self.versions['compiled_frame_kernel']=dict(path=str(frame),sha256=hashlib.sha256(frame.read_bytes()).hexdigest())
        machine=sys.modules['machine3d'];self.versions['atomic_policy']={k:getattr(machine,k,None) for k in ('MAX_DELTA_DEG','MAX_HOPS','MAX_WALL_NS','REFRESH_HOPS','LOG_CAPACITY')}
        self.frame_audits=collections.deque(maxlen=64)
        self.last_tracker_decision=None
        self.control_events=collections.deque(maxlen=2048);self.control_event_counts=collections.Counter()
        self.control_events_overflow=0;self.active_frames=0;self.tracker_valid_frames=0;self.first_accepted=None
        self.pipeline.observer=self._observe
    def _observe(self,event):
        audit=event['audit'];self.active_frames+=int(audit['application_active']);self.tracker_valid_frames+=int(audit['tracker_prior_valid'])
        self.frame_audits.append({k:audit.get(k) for k in ('frame','snapshot_status','application_active','old_valid_bins','snapshot_events','tracker_prior_physical','snapshot_seq','guard_pair_reason','next_search_performed','next_last_search_frame','next_evidence_frame','next_decision_fresh')})
        decision=event.get('next_doa_decision')
        if decision is not None:
            self.last_tracker_decision={k:copy.deepcopy(decision.get(k)) for k in ('frame','valid','azimuth_deg','elevation_deg','raw_candidate_azimuth_deg','raw_candidate_elevation_deg','evidence_valid','pending_confirmations','held_without_fresh_evidence','checks','peak_score','peak_margin','frequency_support_fraction','residual_power_fraction','search_performed','last_search_frame','compute_ms')}
        for item in audit.get('snapshot_events',[]):
            row=dict(observed_frame=audit['frame'],**item);kind=item.get('event','unknown');self.control_event_counts[kind]+=1
            if len(self.control_events)==self.control_events.maxlen:self.control_events_overflow+=1
            self.control_events.append(row)
            if kind=='accepted' and self.first_accepted is None:self.first_accepted=row
    def consume(self,data):
        if isinstance(data,ValidatedDatagram):self.pipeline.consume_packet(data.raw,decoded=data.decoded)
        else:self.pipeline.consume_packet(data)
    def idle(self):self.pipeline._collect()
    def fault(self,reason):
        # Reuse the pinned transport-discontinuity path, on this DSP owner only.
        self.pipeline.cont.invalid=True;self.pipeline.cont.events.append(dict(reason=reason,external_inbox_fault=True));self.pipeline._invalidate();self.output.reset(reason)
    def snapshot(self):
        import preview
        return dict(versions=self.versions,counts=dict(self.pipeline.counts),continuity=dict(self.pipeline.cont.counts),output=self.output.snapshot(),nnls=False,
            spatial_ms=preview.stats(self.pipeline.spatial_ms),post_ms=preview.stats(self.pipeline.post.post_ms),phase_max_ms=dict(self.pipeline.phase_max_ms),post_pending=self.pipeline.post.sent-self.pipeline.post.received,
            last_frame_audits=list(self.frame_audits),dynamic=dict(active_frames=self.active_frames,tracker_valid_frames=self.tracker_valid_frames,
                last_tracker_decision=self.last_tracker_decision,
                event_counts=dict(self.control_event_counts),event_overflow=self.control_events_overflow,first_accepted=self.first_accepted,
                latest_events=list(self.control_events)[-8:]))
    def finish(self):self.last_result=self.pipeline.finish();return self.last_result
    def close(self,reason='backend_owner_close'):
        try:self.pipeline.close()
        finally:self.output.close(reason)
    def close_with_reason(self,reason):self.close(reason)

class RawPreviewAdapter:
    """Three-method monitor hook: consume_datagram / snapshot / close.

    Producer does fixed-cost enqueue only. This object owns no socket. All DSP,
    original post IPC, model work and optional playback run on a separate owner.
    """
    def __init__(self,config,*,lock_deg,lock_elevation_deg=0.,capacity=512,enable_playback=False,monitor_gain=1.,backend_factory=None,allow_test_factory=False,playback_router=None):
        if not math.isfinite(float(lock_deg)+float(lock_elevation_deg)) or not -80<=float(lock_deg)<=80 or not -40<=float(lock_elevation_deg)<=40:raise ValueError('3D diagnostic lock range')
        if not 1<=capacity<=4096:raise ValueError('bounded inbox capacity')
        if not math.isfinite(float(monitor_gain)) or not 0<float(monitor_gain)<=8:raise ValueError('explicit monitor gain must be finite, 0..8')
        if backend_factory is not None and not allow_test_factory:raise ValueError('custom backend is test-only')
        self.config=copy.deepcopy(config);self.lock_deg=float(lock_deg);self.lock_elevation_deg=float(lock_elevation_deg);self.capacity=capacity;self.owns_playback=bool(enable_playback);self.enable_playback=bool(enable_playback);self.monitor_gain=float(monitor_gain)
        self.playback_router=playback_router;self._factory=backend_factory or OriginalBackend;self._test=backend_factory is not None;self._q=queue.Queue(capacity);self._lock=threading.Lock();self._stop=threading.Event();self._ready=threading.Event();self._done=threading.Event()
        self._generation=0;self._processing_generation=0;self._fault_pending=False;self._state='starting';self._error=None;self._backend=None;self._result=None;self._backend_snapshot={};self._counts=collections.Counter();self._events=collections.deque(maxlen=64);self._callback_us=collections.deque(maxlen=8192);self._queue_age_ms=collections.deque(maxlen=8192);self._consume_ms=collections.deque(maxlen=8192)
        self._busy=False;self._max_queue_age_ms=0.;self._max_consume_ms=0.;self._max_publish_ms=0.;self._close_reason=None
        self._thread=threading.Thread(target=self._run,name='raw128-preview-dsp-owner',daemon=True);self._thread.start()
    def _allow_output(self):
        with self._lock:return not self._stop.is_set() and self._processing_generation==self._generation and not self._fault_pending
    def consume_datagram(self,data):
        """Called by the EXISTING sole UDP receiver. Never runs or waits for DSP."""
        return self._enqueue(data,data)
    def consume_validated_datagram(self,packet):
        if not isinstance(packet,ValidatedDatagram):raise TypeError('internal validated token required')
        return self._enqueue(packet.raw,packet)
    def _enqueue(self,data,item):
        begin=time.perf_counter_ns()
        if not isinstance(data,bytes):raise TypeError('immutable datagram bytes required')
        if data[:4]!=b'M128':return False
        with self._lock:
            self._counts['raw_callback_packets']+=1
            if self._stop.is_set() or self._state in ('failed','closed'):
                self._counts['rejected_after_stop_or_error']+=1;return False
            if not self._ready.is_set():
                self._counts['startup_packets_not_queued']+=1;return False
            if len(data)!=1208:
                self._counts['bad_length_packets']+=1
                if not self._fault_pending:
                    self._generation+=1;self._fault_pending=True;self._counts['length_fault_generations']+=1;self._events.append(dict(type='invalid_packet_length',generation=self._generation))
                return False
            generation=self._generation
        try:self._q.put_nowait((generation,item,time.perf_counter_ns()));accepted=True
        except queue.Full:
            with self._lock:
                self._counts['inbox_overflow_packets']+=1
                if not self._fault_pending:
                    self._generation+=1;self._fault_pending=True;self._counts['overflow_fault_generations']+=1;self._events.append(dict(type='inbox_overflow',generation=self._generation))
            accepted=False
        with self._lock:
            self._counts['enqueued_packets']+=int(accepted);self._counts['inbox_peak_packets']=max(self._counts['inbox_peak_packets'],self._q.qsize());self._callback_us.append((time.perf_counter_ns()-begin)/1000.)
        return accepted
    def _publish(self,backend,state=None):
        publish_begin=time.perf_counter_ns()
        # Build an isolated audit before taking the data-plane lock. Published
        # caches are immutable and only replaced, never updated in place.
        snap=copy.deepcopy(backend.snapshot())
        with self._lock:
            self._backend_snapshot=snap
            self._backend_snapshot_at=time.monotonic()
            self._max_publish_ms=max(self._max_publish_ms,(time.perf_counter_ns()-publish_begin)/1e6)
            if state:self._state=state
    def _run(self):
        backend=None
        try:
            extra={} if self.playback_router is None else dict(playback_router=self.playback_router)
            extra['lock_elevation_deg']=self.lock_elevation_deg
            if self._test and self.lock_elevation_deg==0.:extra.pop('lock_elevation_deg')
            backend=self._factory(self.config,self.lock_deg,self._allow_output,enable_playback=self.enable_playback,monitor_gain=self.monitor_gain,**extra);self._backend=backend
            self._publish(backend,'ready');self._ready.set();last_publish=0.;processed=0
            while not self._stop.is_set():
                with self._lock:
                    fault=self._fault_pending;generation=self._generation
                    if fault:self._fault_pending=False;self._state='fault_recovering'
                if fault:
                    backend.fault('preview_inbox_overflow');self._processing_generation=generation
                    with self._lock:self._counts['backend_fault_resets']+=1
                try:gen,data,received_ns=self._q.get(timeout=.001)
                except queue.Empty:
                    backend.idle()
                else:
                    with self._lock:current=self._generation;self._busy=True
                    if gen!=current:
                        with self._lock:self._counts['queued_old_generation_discarded']+=1;self._busy=False
                        continue
                    age_ms=(time.perf_counter_ns()-received_ns)/1e6;self._queue_age_ms.append(age_ms)
                    self._processing_generation=gen;begin=time.perf_counter_ns();backend.consume(data);consume_ms=(time.perf_counter_ns()-begin)/1e6;self._consume_ms.append(consume_ms);processed+=1
                    with self._lock:
                        self._counts['processed_packets']+=1;self._busy=False
                        self._max_queue_age_ms=max(self._max_queue_age_ms,age_ms);self._max_consume_ms=max(self._max_consume_ms,consume_ms)
                now=time.monotonic()
                # GUI audits refresh at 5Hz. Real-time fault/readiness control
                # uses health(), not this diagnostic cache.
                if now-last_publish>=.2:self._publish(backend,'ready');last_publish=now
            # Stop does not process arbitrarily old inbox. Discard is counted.
            while True:
                try:self._q.get_nowait()
                except queue.Empty:break
                with self._lock:self._counts['queued_discarded_on_close']+=1
            self._result=backend.finish();self._publish(backend)
        except PlaybackPreparationCancelled as exc:
            with self._lock:
                self._state='cancelled';self._error=None;self._counts['playback_preparation_cancelled']+=1
                self._events.append(dict(type='playback_preparation_cancelled',reason=str(exc)))
            self._stop.set()
        except BaseException as exc:
            with self._lock:self._state='failed';self._error=type(exc).__name__+': '+str(exc);self._counts['backend_errors']+=1
            self._stop.set()
        finally:
            if backend is not None:
                try:
                    if hasattr(backend,'close_with_reason'):backend.close_with_reason(self._close_reason or ('backend_failed' if self._state=='failed' else 'backend_owner_ended'))
                    else:backend.close()
                except BaseException as exc:
                    with self._lock:self._error=self._error or 'close: '+str(exc);self._counts['close_errors']+=1
                try:self._publish(backend)
                except BaseException as exc:
                    with self._lock:self._counts['final_audit_errors']+=1;self._events.append(dict(type='final_audit_error',reason=type(exc).__name__+': '+str(exc)))
            with self._lock:
                self.owns_playback=False;self._busy=False
                if self._state not in ('failed','cancelled'):self._state='closed'
            self._done.set()
    def snapshot(self):
        with self._lock:
            times=list(self._callback_us);ages=list(self._queue_age_ms);consumes=list(self._consume_ms);cache=self._backend_snapshot
            result=dict(scope='HOST_PHYSICAL_AZIMUTH_ELEVATION_OWNER_CONTROLLED_NOT_BOARD_PL',state=self._state,error=self._error,close_reason=self._close_reason,lock_physical_deg=self.lock_deg,lock_source='explicit physical azimuth/elevation supplied by owner; outer adapter identifies UART or manual origin',automatic_board_lock_following=False,owns_playback=self.owns_playback,test_backend=self._test,inbox_capacity=self.capacity,inbox_current=self._q.qsize(),generation=self._generation,counts=dict(self._counts),recent_events=list(self._events),close_pending=self._stop.is_set() and not self._done.is_set(),audit_cache_age_s=None if not hasattr(self,'_backend_snapshot_at') else time.monotonic()-self._backend_snapshot_at)
        result.update(queue_age_ms=dict(count=len(ages),max=max(ages) if ages else None),consume_ms=dict(count=len(consumes),max=max(consumes) if consumes else None),callback_us=dict(count=len(times),max=max(times) if times else None),backend=copy.deepcopy(cache))
        return result
    def health(self):
        """O(1) control-plane state; never copy audits or scan timing history."""
        with self._lock:
            with self._q.mutex:
                queued=len(self._q.queue)
                oldest_ms=(time.perf_counter_ns()-self._q.queue[0][2])/1e6 if queued else 0.
            frames=self._backend_snapshot.get('counts',{})
            return dict(state=self._state,error=self._error,ready=self._ready.is_set(),
                done=self._done.is_set(),generation=self._generation,fault_pending=self._fault_pending,
                inbox_current=queued,inbox_capacity=self.capacity,inbox_peak_packets=self._counts['inbox_peak_packets'],
                processed_packets=self._counts['processed_packets'],enqueued_packets=self._counts['enqueued_packets'],
                inflight=int(self._busy),oldest_age_ms=oldest_ms,max_queue_age_ms=self._max_queue_age_ms,
                max_consume_ms=self._max_consume_ms,spatial_frames_published=frames.get('spatial_frames',0),
                max_publish_ms=self._max_publish_ms,
                post_frames_published=frames.get('post_frames',0),
                telemetry_scope='queue/count samples may straddle a queue transition; monitoring only, never DSP version control')
    def set_listen_mode(self,mode):
        # Fixed manual target may keep its continuous DSP state; final route/owner
        # gates decide audibility. It never borrows a board lock or changes angle.
        self.requested_listen_mode=mode
    def close(self):self.close_with_reason('owner_close_unspecified')
    def close_with_reason(self,reason):
        """Nonblocking monitor teardown; never waits for post IPC on GUI thread."""
        self._stop.set()
        with self._lock:
            if self._close_reason is None:
                self._close_reason=str(reason);self._events.append(dict(type='close_requested',at_ns=time.perf_counter_ns(),reason=str(reason),generation=self._generation))
            if self._state not in ('failed','closed'):self._state='stopping'
    def wait_closed(self,timeout=10):return self._done.wait(timeout)
    def wait_ready(self,timeout=10):return self._ready.wait(timeout)
