"""Optional process-isolated DSP owner; no device/network on import.

The shared ring is the ONLY packet inbox. A packet credit is retained while
queued, copied into a <=32 packet worker batch, or being consumed. No Queue
feeder contains PCM. Frontend transport/BoardLock remains the sole authority.
"""
from __future__ import annotations
import collections,copy,ctypes,hashlib,json,math,multiprocessing as mp,os,queue,threading,time
from pathlib import Path
from legacy_adapter import (ValidatedDatagram,PlaybackPreparationCancelled,
                            compact_native_state,compact_router_state,compact_backend_state)

NAMES=('raw_callback_packets','enqueued_packets','processed_packets','inbox_overflow_packets',
 'queued_discarded_on_close','inflight_aborted_on_error','rejected_after_stop_or_error',
 'startup_packets_not_queued','bad_length_packets','inbox_peak_packets','backend_errors',
 'telemetry_dropped','batches_processed','max_batch_packets')
CI={n:i for i,n in enumerate(NAMES)}
PUB_NAMES=('light_calls','full_calls','light_snapshot_max_ns','full_snapshot_max_ns',
           'router_snapshot_max_ns','enqueue_max_ns','light_total_max_ns','full_total_max_ns')
PI={n:i for i,n in enumerate(PUB_NAMES)}
STATES=('starting','ready','stopping','closed','failed','cancelled')
STARTING,READY,STOPPING,CLOSED,FAILED,CANCELLED=range(6)

def _put_text(array,text):
    raw=str(text).encode('utf8')[:len(array)-1];array[:]=raw+b'\0'*(len(array)-len(raw))
def _get_text(array):return bytes(array).split(b'\0',1)[0].decode('utf8','replace') or None

class SharedInbox:
    """One producer / one worker, explicitly global packet-credit accounting."""
    def __init__(self,ctx,capacity):
        self.capacity=capacity;self.lock=ctx.RLock()
        self.data=ctx.RawArray(ctypes.c_ubyte,capacity*1208)
        self.receipts=ctx.RawArray(ctypes.c_longlong,capacity)
        self.read=ctx.Value('Q',0,lock=False);self.write=ctx.Value('Q',0,lock=False)
        self.admitted=ctx.Value('I',0,lock=False);self.held=ctx.Value('I',0,lock=False)
        self.processing=ctx.Value('I',0,lock=False);self.oldest=ctx.Value('q',0,lock=False)
        self.counts=ctx.RawArray(ctypes.c_ulonglong,len(NAMES))
        self.max_age_ns=ctx.Value('Q',0,lock=False);self.max_consume_ns=ctx.Value('Q',0,lock=False)
        self.max_batch_ns=ctx.Value('Q',0,lock=False)
        self.state=ctx.Value('i',STARTING,lock=False);self.pid=ctx.Value('I',0,lock=False)
        self.error=ctx.RawArray(ctypes.c_ubyte,2048);self.reason=ctx.RawArray(ctypes.c_ubyte,512)
        self.native_close_error=ctx.RawArray(ctypes.c_ubyte,2048);self.finalization_error=ctx.RawArray(ctypes.c_ubyte,2048)
        self.close_requested_ns=ctx.Value('q',0,lock=False)
        # Metadata control only: no additional PCM queue. The requested value
        # is applied by the audio owner at a batch boundary, never by the GUI.
        self.monitor_gain=ctx.Value('d',1.,lock=False)
        self.gain_serial=ctx.Value('Q',0,lock=False)
        self.gain_applied_serial=ctx.Value('Q',0,lock=False)
        self.gain_applied_value=ctx.Value('d',1.,lock=False)
        self.publication_timing=ctx.RawArray(ctypes.c_ulonglong,len(PUB_NAMES))
        self.stop=ctx.Event();self.wake=ctx.Event();self.ready=ctx.Event();self.done=ctx.Event();self.native_closed=ctx.Event()
    def enqueue(self,data):
        now=time.perf_counter_ns()
        with self.lock:
            self.counts[CI['raw_callback_packets']]+=1
            state=self.state.value
            if state!=READY and state!=STARTING:
                self.counts[CI['rejected_after_stop_or_error']]+=1;return False
            if state==STARTING:self.counts[CI['startup_packets_not_queued']]+=1;return False
            if len(data)!=1208:
                self.counts[CI['bad_length_packets']]+=1;self._fail_locked('Invalid M128 packet length');return False
            if self.admitted.value>=self.capacity:
                self.counts[CI['inbox_overflow_packets']]+=1;self._fail_locked('DSP process inbox overflow: '+str(self.capacity)+' total packet credits');return False
            slot=self.write.value%self.capacity
            ctypes.memmove(ctypes.addressof(self.data)+slot*1208,data,1208)
            self.receipts[slot]=now;empty=self.write.value==self.read.value
            self.write.value+=1;self.admitted.value+=1
            if not self.oldest.value:self.oldest.value=now
            self.counts[CI['enqueued_packets']]+=1
            self.counts[CI['inbox_peak_packets']]=max(self.counts[CI['inbox_peak_packets']],self.admitted.value)
            if empty:self.wake.set()
            return True
    def _fail_locked(self,reason):
        _put_text(self.error,reason);self.state.value=FAILED;self.stop.set();self.wake.set()
    def request_stop(self,reason):
        with self.lock:
            if not self.close_requested_ns.value:
                self.close_requested_ns.value=time.perf_counter_ns();_put_text(self.reason,reason)
            if self.state.value not in (FAILED,CLOSED,CANCELLED):self.state.value=STOPPING
            self.stop.set();self.wake.set()
    def take(self,maximum=32):
        with self.lock:
            count=min(maximum,self.write.value-self.read.value)
            batch=[]
            for j in range(count):
                slot=(self.read.value+j)%self.capacity
                batch.append((ctypes.string_at(ctypes.addressof(self.data)+slot*1208,1208),self.receipts[slot]))
            self.read.value+=count;self.held.value+=count
            if self.read.value==self.write.value:self.wake.clear()
            self.counts[CI['max_batch_packets']]=max(self.counts[CI['max_batch_packets']],count)
            return batch
    def retire(self,batch,processed,aborted=0):
        with self.lock:
            n=len(batch);self.admitted.value-=n;self.held.value-=n;self.processing.value=0
            self.counts[CI['processed_packets']]+=processed
            self.counts[CI['inflight_aborted_on_error']]+=aborted
            self.counts[CI['queued_discarded_on_close']]+=n-processed-aborted
            self.counts[CI['batches_processed']]+=1
            self.oldest.value=self.receipts[self.read.value%self.capacity] if self.write.value>self.read.value else 0
            if self.admitted.value!=self.write.value-self.read.value+self.held.value:raise RuntimeError('process packet credit mismatch')
    def discard(self):
        with self.lock:
            n=self.write.value-self.read.value;self.read.value=self.write.value
            self.admitted.value-=n;self.counts[CI['queued_discarded_on_close']]+=n
            if not self.admitted.value:self.oldest.value=0
    def abandon_after_worker_exit(self):
        """Called only once process death is confirmed, never on a timeout."""
        with self.lock:
            held=self.held.value;self.held.value=0;self.processing.value=0
            self.admitted.value-=held;self.counts[CI['inflight_aborted_on_error']]+=held
            self.discard()
    def health(self):
        with self.lock:
            queued=self.write.value-self.read.value;now=time.perf_counter_ns()
            return dict(state=STATES[self.state.value],error=_get_text(self.error),ready=self.ready.is_set(),done=self.done.is_set(),
                worker_pid=self.pid.value,counts={n:int(self.counts[i]) for n,i in CI.items()},
                inbox_capacity=self.capacity,inbox_current=queued,admitted_packets=self.admitted.value,
                consumer_batch_held=self.held.value,inflight=self.held.value,processing_batch_size=self.processing.value,
                inbox_peak_packets=int(self.counts[CI['inbox_peak_packets']]),enqueued_packets=int(self.counts[CI['enqueued_packets']]),processed_packets=int(self.counts[CI['processed_packets']]),
                oldest_age_ms=(now-self.oldest.value)/1e6 if self.oldest.value else 0.,
                max_queue_age_ms=self.max_age_ns.value/1e6,max_consume_ms=self.max_consume_ns.value/1e6,max_batch_ms=self.max_batch_ns.value/1e6,
                native_closed_ack=self.native_closed.is_set(),native_close_error=_get_text(self.native_close_error),
                finalization_error=_get_text(self.finalization_error),close_requested_ns=self.close_requested_ns.value or None,
                playback_gain=dict(requested=self.monitor_gain.value,requested_serial=self.gain_serial.value,
                    applied=None if not self.gain_applied_serial.value else self.gain_applied_value.value,
                    applied_serial=self.gain_applied_serial.value,pending=self.gain_serial.value!=self.gain_applied_serial.value,
                    scope='worker playback-router scalar before audio batch; not a playback-completion claim'),
                publication_timing=dict({n:int(self.publication_timing[i]) for i,n in enumerate(PUB_NAMES)},clock='perf_counter_ns',
                    enqueue_scope='put API only; background Queue feeder serialization and pipe not individually timed',nested_metrics_not_additive=True),
                packet_capacity_scope='ALL shared-ring queued + IPC/worker-held + processing credits <= capacity; no PCM feeder queue')

def _apply_monitor_gain(shared,router,backend=None):
    """One worker owns application; caller invokes only before a data batch."""
    with shared.lock:
        serial=shared.gain_serial.value
        if serial==shared.gain_applied_serial.value:return False
        if shared.stop.is_set():return False
        gain=shared.monitor_gain.value
    target=router if router is not None else backend
    if target is None or not hasattr(target,'set_monitor_gain'):return False
    # No authority lock while taking the playback router's own lock. A newer
    # request may arrive; acknowledge only this exact serial/value and let the
    # following batch apply the newer value.
    target.set_monitor_gain(gain)
    with shared.lock:
        shared.gain_applied_value.value=gain;shared.gain_applied_serial.value=serial
    return True

def _constructor_close_proven(error):
    opened=getattr(error,'native_device_opened',None);closed=getattr(error,'native_device_closed',None)
    return opened is False or opened is True and closed is True

def _native_close_proven(router,backend,*,attempted,testing=False,construction_closed=False):
    """A stopped worker is not proof that WinMM accepted device closure."""
    if testing:
        opened=getattr(backend,'native_open_ns',None);closed=getattr(backend,'native_close_ns',None)
        return opened is None or type(closed) is int and closed>=opened
    if router is None:return not attempted or construction_closed
    with router.lock:
        if getattr(router,'preparing',None) is not None:return False
        devices=[getattr(router,k,None) for k in ('player','retiring','_last_player_sink')]
    devices=[p for p in devices if p is not None]
    if devices:return all(getattr(p,'device_closed',None) is True for p in devices)
    return not attempted or construction_closed

def _finalization_failure(shared,kind,error):
    """Keep the triggering failure; cleanup evidence has a separate field."""
    reason=kind+': '+str(error)
    with shared.lock:
        _put_text(shared.native_close_error if kind=='native close' else shared.finalization_error,reason)
        if not _get_text(shared.error):_put_text(shared.error,reason)
        shared.state.value=FAILED;shared.stop.set();shared.wake.set()

class _RemoteThread:
    def __init__(self,adapter):self.adapter=adapter
    def is_alive(self):return not self.adapter.shared.native_closed.is_set()

class RemotePCSink:
    """Parent router barrier, never receives PCM or opens a local PC device."""
    def __init__(self,adapter):self.adapter=adapter;self.thread=_RemoteThread(adapter)
    def request_close(self,reason='remote_sink_requested_close'):self.adapter.close_with_reason(reason)
    def close_with_reason(self,reason):
        self.request_close(reason)
        if not self.adapter.shared.native_closed.wait(10):raise RuntimeError('Remote native shutdown is pending; local source remains disabled')
    def close(self):self.close_with_reason('remote_sink_close')
    def set_monitor_gain(self,gain):return self.adapter.set_monitor_gain(gain)
    def snapshot(self):
        if hasattr(self.adapter,'_native_cached'):
            snap=self.adapter._native_cached();p=snap.get('native')
        else:
            snap=self.adapter._cached();backend=snap.get('backend',{});output=backend.get('output',{})
            route=output.get('router_owner_diagnostic') or {};p=snap.get('router',{}).get('player') or route.get('player') or output.get('player')
            if p is None:p=(route.get('last_player') or {}).get('native')
        result=compact_native_state(p) if isinstance(p,dict) else dict(completed_samples=None,submitted_samples=None,error=None)
        # A GUI poll must not make delayed worker counters look newly completed.
        age=result.get('completion_age_seconds');sample=snap.get('source_sampled_ns')
        if age is not None and sample is not None:
            result['completion_age_seconds']=age+max(0.,(time.perf_counter_ns()-sample)/1e9)
            result['completion_recent']=result.get('completed_samples',0)>0 and result['completion_age_seconds']<1.
        process=getattr(self.adapter,'process',None)
        result.update(remote_worker_pid=self.adapter.shared.pid.value,remote_generation=self.adapter.generation,
            remote_worker_final=bool(snap.get('final')),remote_process_joined=bool(process is not None and process.exitcode is not None and not process.is_alive()),
            source_sampled_ns=snap.get('source_sampled_ns'),parent_cache_age_s=None if not snap.get('parent_received_at') else time.monotonic()-snap['parent_received_at'],
            native_closed_ack=self.adapter.shared.native_closed.is_set(),remote_device_state_unknown=p is None,remote_transport_proxy=True,
            native_device_open=False if self.adapter.shared.native_closed.is_set() else None if p is None else bool(p.get('opened_ns') and not p.get('closed')),
            remote_output_level=snap.get('output_level',snap.get('router',{}).get('output_level')),scope='actual worker native ledger; parent proxy never opens a PC sound device')
        return result
    def snapshot_full_cached(self):
        """Explicit historical forensic view; never used for routine UI polls."""
        full=self.adapter.snapshot_full_cached();backend=full.get('backend',{});output=backend.get('output',{})
        route=output.get('router_owner_diagnostic') or {};p=full.get('router',{}).get('player') or route.get('player') or output.get('player')
        if p is None:p=(route.get('last_player') or {}).get('native')
        return dict(native=p,full_audit_sampled_ns=full.get('full_audit_sampled_ns'),
                    full_audit_generation=full.get('full_audit_generation'),full_audit_is_final=full.get('full_audit_is_final'),
                    scope='explicit cached full audit; not current native or completion ACK')

def _worker_main(shared,telemetry,config,lock_deg,lock_el,enable_playback,gain,generation,test_spec):
    """Module-level spawn target. No frontend socket/serial/UI imported here."""
    backend=None;router=None;last_publish=0.;stopper_done=threading.Event();backend_slot=[None];native_attempted=[False];construction_closed=[False]
    with shared.lock:shared.pid.value=os.getpid()
    def allow():return not shared.stop.is_set()
    def publish(final=False,initial=False):
        nonlocal last_publish
        start=time.perf_counter_ns();full=bool(initial or final);kind='full' if full else 'light'
        if backend is None:snap={}
        elif full:snap=backend.snapshot()
        elif hasattr(backend,'snapshot_light'):snap=backend.snapshot_light()
        elif test_spec is not None:snap=backend.snapshot()
        else:raise RuntimeError('Production backend has no bounded publication API')
        snapshot_end=time.perf_counter_ns()
        if router is None:route={}
        elif full:route=router.snapshot()
        elif hasattr(router,'snapshot_compact'):route=router.snapshot_compact()
        else:route=compact_router_state(router.snapshot())
        router_end=time.perf_counter_ns()
        row=dict(source_sampled_ns=time.perf_counter_ns(),worker_pid=os.getpid(),generation=generation,
                 backend=snap,router=route,final=final,publication_kind=kind)
        enqueue_begin=time.perf_counter_ns()
        try:telemetry.put_nowait(row)
        except queue.Full:
            if final:
                try:telemetry.get_nowait()
                except queue.Empty:pass
                try:telemetry.put(row,timeout=.1)
                except queue.Full:
                    with shared.lock:shared.counts[CI['telemetry_dropped']]+=1
            else:
                with shared.lock:shared.counts[CI['telemetry_dropped']]+=1
        end=time.perf_counter_ns()
        with shared.lock:
            metrics=shared.publication_timing;metrics[PI[kind+'_calls']]+=1
            for name,value in ((kind+'_snapshot_max_ns',snapshot_end-start),('router_snapshot_max_ns',router_end-snapshot_end),
                               ('enqueue_max_ns',end-enqueue_begin),(kind+'_total_max_ns',end-start)):
                metrics[PI[name]]=max(metrics[PI[name]],value)
        last_publish=time.monotonic()
    def request_output_stop():
        parent=mp.parent_process()
        while not stopper_done.is_set():
            if parent is not None and not parent.is_alive():shared.request_stop('frontend_parent_process_exited')
            if shared.stop.wait(.005):
                b=backend_slot[0]
                if b is not None and hasattr(b,'request_output_stop'):
                    b.request_output_stop(_get_text(shared.reason) or 'process_stop')
                return
    stop_thread=threading.Thread(target=request_output_stop,name='process-output-stop-gate',daemon=True);stop_thread.start()
    try:
        if test_spec is None:
            from legacy_adapter import OriginalBackend
            from audio_source_router import AudioSourceRouter
            if enable_playback:
                router=AudioSourceRouter(monitor_gain=gain)
                original_factory=router.factory
                def sealed_factory(mode):
                    if shared.stop.is_set():raise PlaybackPreparationCancelled('Process owner stopped before native open')
                    native_attempted[0]=True
                    try:return original_factory(mode)
                    except BaseException as exc:
                        construction_closed[0]=_constructor_close_proven(exc)
                        raise
                router.factory=sealed_factory;router.select(4)
            backend=OriginalBackend(config,lock_deg,allow,enable_playback=enable_playback,monitor_gain=gain,
                                    playback_router=router,lock_elevation_deg=lock_el)
        else:
            # Only reachable through explicit allow_test_worker opt-in. The
            # production CLI never accepts this option or substitutes a factory.
            from process_test_support import TestBackend
            backend=TestBackend(shared,test_spec,allow,enable_playback,gain)
        backend_slot[0]=backend
        if shared.stop.is_set():raise PlaybackPreparationCancelled('Process owner stopped during backend preparation')
        publish(initial=True)
        with shared.lock:
            if shared.stop.is_set():raise PlaybackPreparationCancelled('Process owner stopped at ready boundary')
            shared.state.value=READY;shared.ready.set()
        while not shared.stop.is_set():
            batch=shared.take(32)
            if not batch:
                backend.idle();shared.wake.wait(.001)
            else:
                batch_start=time.perf_counter_ns();processed=0;aborted=0
                try:
                    _apply_monitor_gain(shared,router,backend)
                    for data,received_ns in batch:
                        if shared.stop.is_set():break
                        begin=time.perf_counter_ns()
                        with shared.lock:
                            shared.processing.value=1;shared.max_age_ns.value=max(shared.max_age_ns.value,begin-received_ns)
                        backend.consume(data);processed+=1
                        elapsed=time.perf_counter_ns()-begin
                        with shared.lock:shared.max_consume_ns.value=max(shared.max_consume_ns.value,elapsed)
                except BaseException:
                    aborted=len(batch)-processed;raise
                finally:
                    shared.retire(batch,processed,aborted)
                    with shared.lock:shared.max_batch_ns.value=max(shared.max_batch_ns.value,time.perf_counter_ns()-batch_start)
            if time.monotonic()-last_publish>=.2:publish()
    except PlaybackPreparationCancelled:
        with shared.lock:
            if shared.state.value!=FAILED:shared.state.value=CANCELLED
    except BaseException as exc:
        with shared.lock:
            shared._fail_locked(type(exc).__name__+': '+str(exc));shared.counts[CI['backend_errors']]+=1
    finally:
        # State and Event are published in that order under the SAME authority
        # lock used by enqueue / timeout0 readiness. No READY-but-exiting gap.
        with shared.lock:
            if shared.state.value not in (FAILED,CANCELLED,CLOSED):shared.state.value=STOPPING
            shared.stop.set()
        stopper_done.set();stop_thread.join(.1)
        # Seal all future factory opens, then actually close the existing device
        # before slow post finish/worker joins. Native ACK is NOT process-done.
        try:
            if backend is not None and hasattr(backend,'quiesce_output'):backend.quiesce_output(_get_text(shared.reason) or 'process_owner_stopping')
            if router is not None:router.close()
            if not _native_close_proven(router,backend,attempted=native_attempted[0],testing=test_spec is not None,
                                        construction_closed=construction_closed[0]):
                raise RuntimeError('native device closure not positively confirmed; no replacement sink allowed')
            shared.native_closed.set()
        except BaseException as exc:
            _finalization_failure(shared,'native close',exc)
            # Deliberately no native_closed ACK: parent must not open 16k sink.
        shared.discard()
        if backend is not None:
            try:backend.finish()
            except BaseException as exc:
                _finalization_failure(shared,'finish',exc)
            try:
                if hasattr(backend,'close_with_reason'):backend.close_with_reason(_get_text(shared.reason) or 'process_owner_ended')
                else:backend.close()
            except BaseException as exc:
                _finalization_failure(shared,'backend close',exc)
        with shared.lock:
            if shared.state.value not in (FAILED,CANCELLED):shared.state.value=CLOSED
        try:publish(final=True)
        except BaseException:pass
        shared.done.set()
        parent=mp.parent_process()
        if parent is not None and not parent.is_alive():telemetry.cancel_join_thread()

class ProcessRawAdapter:
    def __init__(self,config,*,lock_deg,lock_elevation_deg=0.,capacity=1024,enable_playback=False,monitor_gain=1.,playback_router=None,
                 test_worker_spec=None,allow_test_worker=False):
        if not math.isfinite(float(lock_deg)+float(lock_elevation_deg)) or not -80<=float(lock_deg)<=80 or not -40<=float(lock_elevation_deg)<=40:raise ValueError('3D lock range')
        if capacity!=1024 and not allow_test_worker:raise ValueError('Process production packet capacity is exactly1024')
        if not 1<=capacity<=4096:raise ValueError('bounded process capacity')
        if not math.isfinite(float(monitor_gain)) or not 0<float(monitor_gain)<=8:raise ValueError('monitor gain')
        if test_worker_spec is not None and not allow_test_worker:raise ValueError('test worker must opt in')
        self.config=json.loads(json.dumps(config));self.capacity=capacity;self.lock_deg=float(lock_deg);self.lock_elevation_deg=float(lock_elevation_deg);self._test=test_worker_spec is not None
        self.playback_router=playback_router;self.owns_playback=bool(enable_playback);self.enable_playback=enable_playback
        self.generation=time.perf_counter_ns();self._cache_lock=threading.Lock();self._cache={};self._thread_done=threading.Event()
        self._full_audit={};self._versions=None;self._versions_sampled_ns=None;self._versions_digest=None;self._publication_rejections=collections.Counter()
        ctx=mp.get_context('spawn');self.shared=SharedInbox(ctx,capacity);self.telemetry=ctx.Queue(maxsize=2)
        with self.shared.lock:
            self.shared.monitor_gain.value=float(monitor_gain);self.shared.gain_serial.value=1
        self._remote=RemotePCSink(self);self.router_owner=None
        if playback_router is not None:
            self.router_owner=playback_router.register_pc_owner()
            if not playback_router.install_remote_pc(self.router_owner,self._remote):
                self.shared.request_stop('parent source superseded before process start')
        executable=Path(os.sys.executable).with_name('pythonw.exe')
        if executable.is_file():mp.set_executable(str(executable))
        self.process=ctx.Process(target=_worker_main,args=(self.shared,self.telemetry,self.config,self.lock_deg,self.lock_elevation_deg,
                                  enable_playback,monitor_gain,self.generation,test_worker_spec),name='raw128-isolated-dsp',daemon=False)
        try:self.process.start()
        except BaseException as exc:
            # A start failure can be partially completed. Without a child-side
            # close proof, never turn parent uncertainty into a hardware ACK.
            _finalization_failure(self.shared,'spawn',exc)
            if not enable_playback:self.shared.native_closed.set() # configured to create no native device
            self.shared.done.set();raise
        self._thread=threading.Thread(target=self._observe,name='dsp-process-telemetry',daemon=True);self._thread.start()
    def _cached(self):
        with self._cache_lock:return copy.deepcopy(self._cache)
    def _native_cached(self):
        """Copy only the native/control projection needed by the parent UI."""
        with self._cache_lock:
            row=self._cache;output=row.get('backend',{}).get('output',{})
            route=output.get('router_owner_diagnostic') or {}
            native=row.get('router',{}).get('player') or route.get('player') or output.get('player')
            if native is None:native=(route.get('last_player') or {}).get('native')
            level=row.get('router',{}).get('output_level')
            return dict(native=compact_native_state(native),output_level=None if level is None else dict(level),source_sampled_ns=row.get('source_sampled_ns'),
                parent_received_at=row.get('parent_received_at'),final=bool(row.get('final')))
    def _accept_publication(self,item):
        """Same-process generation and source-time gates precede cache swaps."""
        if not isinstance(item,dict) or item.get('generation')!=self.generation:
            with self._cache_lock:self._publication_rejections['wrong_generation']+=1
            return False
        sampled=item.get('source_sampled_ns');kind=item.get('publication_kind')
        if type(sampled) is not int or sampled<0 or kind not in ('light','full'):
            with self._cache_lock:self._publication_rejections['invalid_envelope']+=1
            return False
        backend=compact_backend_state(item.get('backend'));route=compact_router_state(item.get('router'))
        light={k:v for k,v in item.items() if k not in ('backend','router')}
        light.update(backend=backend,router=route)
        with self._cache_lock:
            full_ns=self._full_audit.get('source_sampled_ns',-1)
            if kind=='full' and sampled>full_ns:
                # The receive thread owns this unpickled object. Routine
                # publication never gets a reference to its histories.
                self._full_audit=item
                versions=(item.get('backend') or {}).get('versions')
                if versions is not None:
                    self._versions=versions;self._versions_sampled_ns=sampled
                    self._versions_digest=hashlib.sha256(json.dumps(versions,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
            if sampled<=self._cache.get('source_sampled_ns',-1):
                self._publication_rejections['older_control_sample']+=1
                self._update_provenance_locked();return False
            self._cache=light;self._update_provenance_locked()
        return True
    def _update_provenance_locked(self):
        if self._versions is not None:
            self._cache=dict(self._cache,versions_source_sampled_ns=self._versions_sampled_ns,
                             versions_digest_sha256=self._versions_digest)
        full=self._full_audit
        self._cache=dict(self._cache,full_audit_sampled_ns=full.get('source_sampled_ns'),
            full_audit_generation=full.get('generation'),full_audit_is_final=bool(full.get('final')),
            full_audit_available=bool(full),publication_rejections=dict(self._publication_rejections))
    def _observe(self):
        while True:
            try:
                item=self.telemetry.get(timeout=.05);item['parent_received_at']=time.monotonic()
                self._accept_publication(item)
            except queue.Empty:pass
            except (EOFError,OSError) as exc:
                self.shared.request_stop('telemetry control channel failed: '+str(exc));time.sleep(.01)
            if self.shared.done.is_set() and not self.process.is_alive():
                # Queue feeder may finish after DONE; drain final evidence once
                # the worker has exited, without inventing a new sample time.
                while True:
                    try:
                        item=self.telemetry.get_nowait();item['parent_received_at']=time.monotonic()
                        self._accept_publication(item)
                    except queue.Empty:break
                break
            if not self.process.is_alive() and not self.shared.done.is_set():
                with self.shared.lock:self.shared._fail_locked('DSP worker exited without shutdown ACK: '+str(self.process.exitcode))
                self.shared.abandon_after_worker_exit();self.shared.done.set();break
        self.process.join()
        if self.playback_router is not None:
            self.playback_router.release_remote_pc(self.router_owner,self._remote)
            self.playback_router.refresh_remote_final(self.router_owner,self._remote)
        self._thread_done.set()
    def consume_datagram(self,data):
        if not isinstance(data,bytes):raise TypeError('immutable raw bytes')
        if data[:4]!=b'M128':return False
        return self.shared.enqueue(data)
    def consume_validated_datagram(self,packet):
        if not isinstance(packet,ValidatedDatagram):raise TypeError('canonical immutable validated packet required')
        # MappingProxy is deliberately NOT pickled. Worker uses the pinned
        # strict decoder on the unchanged original wire bytes.
        return self.consume_datagram(packet.raw)
    def health(self):
        r=self.shared.health();cache=self._cache
        with self._cache_lock:rejections=dict(self._publication_rejections)
        counts=cache.get('backend',{}).get('counts',{})
        r.update(owns_playback=self.owns_playback,execution_mode='process',generation=self.generation,
                 worker_alive=self.process.is_alive(),worker_exitcode=self.process.exitcode,
                 spatial_frames_published=counts.get('spatial_frames',0),post_frames_published=counts.get('post_frames',0),
                 source_sampled_ns=cache.get('source_sampled_ns'),audit_cache_age_s=None if not cache.get('parent_received_at') else time.monotonic()-cache['parent_received_at'])
        r['publication_rejections']=rejections
        return r
    def snapshot(self):
        r=self.health();cache=self._cached()
        r.update(scope='PROCESS_ISOLATED_ORIGINAL_3D_FULL_DSP',backend=cache.get('backend',{}),
                 source_sampled_ns=cache.get('source_sampled_ns'),parent_cache_received_at=cache.get('parent_received_at'),
                 native_closed_ack=self.shared.native_closed.is_set(),worker_done_ack=self.shared.done.is_set(),
                 process_joined=self._thread_done.is_set(),test_backend=self._test,
                 ipc_scope='fixed shared-memory RAW ring, <=32 held packet batch, total global credits <=1024; telemetry queue2 contains NO PCM')
        r.update(full_audit_sampled_ns=cache.get('full_audit_sampled_ns'),full_audit_generation=cache.get('full_audit_generation'),
                 full_audit_is_final=cache.get('full_audit_is_final'),full_audit_available=cache.get('full_audit_available',False),
                 versions_source_sampled_ns=cache.get('versions_source_sampled_ns'),
                 versions_digest_sha256=cache.get('versions_digest_sha256'),
                 audit_scope='compact current control plus version provenance; full historical audit is explicit')
        return r
    def snapshot_compact(self):return self.snapshot()
    def snapshot_full_cached(self):
        with self._cache_lock:
            full=copy.deepcopy(self._full_audit);light_ns=self._cache.get('source_sampled_ns')
        sampled=full.get('source_sampled_ns')
        full.update(full_audit_sampled_ns=sampled,full_audit_generation=full.get('generation'),
            full_audit_age_s=None if sampled is None else max(0.,(time.perf_counter_ns()-sampled)/1e9),
            full_audit_is_final=bool(full.get('final')),full_audit_available=bool(full),process_generation=self.generation,
            current_control_sampled_ns=light_ns,scope='explicit historical full audit; preserve source timestamp/generation, not live control')
        return full
    def set_listen_mode(self,mode):self.requested_listen_mode=mode
    def set_monitor_gain(self,gain):
        gain=float(gain)
        if not math.isfinite(gain) or not 0<gain<=8:raise ValueError('monitor gain must be finite in (0,8]')
        with self.shared.lock:
            if gain!=self.shared.monitor_gain.value:
                self.shared.monitor_gain.value=gain;self.shared.gain_serial.value+=1
            return dict(requested=gain,requested_serial=self.shared.gain_serial.value,
                applied_serial=self.shared.gain_applied_serial.value,
                pending=self.shared.gain_serial.value!=self.shared.gain_applied_serial.value)
    def close(self):self.close_with_reason('process_owner_close')
    def close_with_reason(self,reason):self.shared.request_stop(reason)
    def wait_ready(self,timeout=10):
        if timeout==0:
            with self.shared.lock:return self.shared.state.value==READY
        deadline=None if timeout is None else time.monotonic()+timeout
        while not self.shared.stop.is_set():
            if self.shared.ready.wait(.01):return not self.shared.stop.is_set()
            if deadline is not None and time.monotonic()>=deadline:return False
        return False
    def wait_closed(self,timeout=15):return self._thread_done.wait(timeout)
