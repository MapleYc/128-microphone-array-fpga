"""Causal UART-lock bridge to the 3D atomic HOST software candidate.

No socket, serial port, soundcard or backend is opened on import. Both physical
angles enter beam/model/tracker; SEARCH/stale/fault never become a fake lock.
"""
import collections, copy, math, struct, threading, time
from legacy_adapter import RawPreviewAdapter,ValidatedDatagram,PlaybackPreparationCancelled
from types import MappingProxyType
from monitor.raw128_receiver import decode_transport_packet,require_packet_view

# A restart scheduling bound, not an audio/DSP parameter. A real loss still
# invalidates the complete old stream. Repeated transport faults extend this
# quiet interval so the owner cannot repeatedly cold-open a replacement.
TRANSPORT_RECOVERY_HOLDOFF_S=.1
TRANSPORT_FAULT_REASONS=frozenset(('raw_malformed','raw_network_epoch_change',
                                 'raw_sequence_or_group_gap','raw_fatal_flags'))

def signed32(x): return x if x < 0x80000000 else x - 0x100000000

def status_message(msg):
    kind, seq, frame, epoch, payload = msg
    if kind != 1: return None
    if len(payload) != 96: raise ValueError('status must be UART type1/96 bytes')
    v = struct.unpack('<24I', payload)
    if v[0] not in (0,1): raise ValueError('invalid mode; no clamp to LOCKED')
    if v[1]>3 or v[2]>32767 or v[5]>32767: raise ValueError('status keyword/score/quality range')
    if (v[20] | v[21]<<32)>frame: raise ValueError('direction timestamp is in the future')
    az,el = signed32(v[22])/256.,signed32(v[23])/256.
    if not -80 <= az <= 80 or not -40 <= el <= 40: raise ValueError('locked direction range')
    if not 0 <= frame < 2**64 or not 0 <= epoch < 2**32: raise ValueError('UART timestamp range')
    return dict(mode='LOCKED' if v[0] else 'SEARCH',azimuth=az,elevation=el,
                source_frame=frame,controller_epoch=epoch,uart_sequence=seq)

class _CandidateRouter:
    """Gate slow candidate construction without exposing a stale audio owner.

    Registration/installation are short operations under the authority lock;
    process spawn and native prepare are never performed under that lock.
    The real router still enforces owner/ticket and native-close barriers.
    """
    def __init__(self,bridge,reservation):
        self.bridge=bridge;self.reservation=reservation;self.real=bridge.playback_router
        self.owner=None;self.sink=None
    def __getattr__(self,name):return getattr(self.real,name)
    def _valid_locked(self):return self.bridge._reservation_valid_locked(self.reservation)
    def register_pc_owner(self):
        with self.bridge.lock:
            if not self._valid_locked():raise PlaybackPreparationCancelled('Candidate superseded before owner registration')
            with self.real.lock:
                if self.real.player is not None or self.real.retiring is not None or self.real.preparing is not None:
                    raise PlaybackPreparationCancelled('Existing router owner must finish before candidate registration')
                self.owner=self.real.register_pc_owner();return self.owner
    def install_remote_pc(self,owner,sink):
        with self.bridge.lock:
            self.sink=sink
            if not self._valid_locked():
                sink.request_close('candidate_superseded_before_install');return False
            return self.real.install_remote_pc(owner,sink)
    def prepare_pc(self,owner=None):
        with self.bridge.lock:
            if not self._valid_locked():return False
        prepared=self.real.prepare_pc(owner)
        with self.bridge.lock:valid=self._valid_locked()
        if prepared and not valid:
            # _Output has not completed construction, so it cannot own cleanup.
            self.real.flush_pc(owner,reason='candidate_superseded_after_native_prepare',expected_ticket=self.reservation['router_ticket'])
            return False
        return prepared
    def feed_pc(self,samples,ticket,owner=None):
        with self.bridge.lock:
            if not self._valid_locked():return False
        return self.real.feed_pc(samples,ticket,owner)
    def cancel_locked(self,reason):
        self.reservation['cancelled']=True
        if self.sink is not None and hasattr(self.sink,'request_close'):
            self.sink.request_close('candidate_cancelled: '+str(reason))
        if self.owner is not None:self.real.request_pc_quiesce(self.owner,'candidate_cancelled: '+str(reason),expected_ticket=self.reservation['router_ticket'])
    def cleanup_unreturned(self):
        # A constructor can fail after registering a remote owner. Reclaim only
        # that exact owner; the router cannot close a different/new source.
        if self.owner is not None:self.real.flush_pc(self.owner,reason='candidate_constructor_failed',expected_ticket=self.reservation['router_ticket'])

class BoardLockAdapter:
    """One bounded DSP child, with explicit paused/unsupported/fault states.

    New target, NO/search, timeout, UART fault and transport gap close the old
    DSP generation. Replacement waits for the old child to finish. Already
    submitted soundcard buffers are not claimed to stop at the UART timestamp.
    """
    def __init__(self,config,*,enable_playback=False,monitor_gain=1.,capacity=512,
                 status_timeout_s=1.,child_factory=None,allow_test_factory=False,playback_router=None,dsp_process=False):
        if not .2 <= status_timeout_s <= 2.: raise ValueError('status timeout bounds')
        if child_factory is not None and not allow_test_factory: raise ValueError('test factory needs opt-in')
        self.config=copy.deepcopy(config);self.enable_playback=bool(enable_playback)
        self.monitor_gain=monitor_gain;self.capacity=capacity;self.timeout=status_timeout_s
        if dsp_process and child_factory is not None:raise ValueError('process production path cannot substitute test factory')
        if dsp_process:
            from process_adapter import ProcessRawAdapter
        self.dsp_process=bool(dsp_process)
        self.playback_router=playback_router;self.listen_pc_selected=playback_router is None;self.owns_playback=bool(enable_playback);self.factory=child_factory or (ProcessRawAdapter if dsp_process else RawPreviewAdapter)
        self.test_only=child_factory is not None;self.lock=threading.RLock();self.stop=threading.Event()
        self.done=threading.Event();self.child=None;self.retiring=None;self.desired=None;self.active_key=None
        self.status=None;self.status_at=None;self.need_status_after=-math.inf;self.transport=None
        self.state='awaiting_board_lock';self.error=None;self.sticky_failure=False;self.generation=0
        self.last_failure=None
        self.failed_owner=None;self.last_failed_backend_full=None
        self.counts=collections.Counter();self.events=collections.deque(maxlen=64);self.callback_us=collections.deque(maxlen=8192)
        self.last_backend={};self.last_raw_received_at=None
        self.last_backend_compact={};self.pending=None;self.active_reservation=None
        self.first_transport_failure=None;self.first_callback_pause=None
        self.transport_fault_history=collections.deque(maxlen=16)
        self.transport_failure_history=self.transport_fault_history # internal compatibility alias
        self.callback_pause_history=collections.deque(maxlen=8)
        self.recovery_not_before=-math.inf
        self.retry_status_floor=None
        self.generation_enqueued_packets=0;self.first_dsp_input=None
        self.thread=threading.Thread(target=self._run,name='board-lock-host-owner',daemon=True);self.thread.start()
    def _event(self,kind,**kw):self.events.append(dict(type=kind,at=time.monotonic(),generation=self.generation,**kw))
    def _invalidate(self,reason,now,*,new_status_required=True):
        if self.pending is not None:
            self.pending['cancelled']=True;self.pending['cancel_reason']=reason
            proxy=self.pending.get('router_proxy')
            if proxy is not None:proxy.cancel_locked(reason)
        if self.active_reservation is not None:
            self.active_reservation['cancelled']=True;self.active_reservation=None
        if reason in ('dsp_inbox_failure','dsp_backend_failed') and self.error:
            self.last_failure=dict(reason=reason,error=self.error,at=now,generation=self.generation,
                child_generation=getattr(self.child,'generation',None))
            self.failed_owner=self.child;self.last_failed_backend_full=None
        self.generation+=1;self.desired=None;self.active_key=None;self.state=reason
        self.generation_enqueued_packets=0;self.first_dsp_input=None
        if new_status_required:self.need_status_after=max(self.need_status_after,now)
        if reason in TRANSPORT_FAULT_REASONS:
            self.recovery_not_before=max(self.recovery_not_before,now+TRANSPORT_RECOVERY_HOLDOFF_S)
        if self.child is not None:
            # Set the child's stop/output gate before taking any diagnostic
            # copy. Already-submitted hardware audio buffers are separate.
            child=self.child
            if hasattr(child,'close_with_reason'):child.close_with_reason(f'{reason}; board_generation={self.generation}')
            else:child.close()
            self.retiring=child;self.child=None
            # Keep callback invalidation bounded: a process snapshot deep-copies
            # the complete DSP audit. The diagnostic reader or retirement owner
            # samples it later, after the nonblocking output seal above.
        self.counts['invalidations']+=1;self._event(reason)
    def set_listen_mode(self,mode):
        enabled=mode==4
        with self.lock:
            if self.stop.is_set():return
            retry=bool(enabled and self.sticky_failure)
            if enabled==self.listen_pc_selected and not retry:return
            if retry:
                previous_error=self.error
                self.sticky_failure=False;self.error=None
                self.retry_status_floor=copy.copy(self.status)
                self.first_transport_failure=None;self.first_callback_pause=None
                self.counts['operator_retries']+=1
                self._event('operator_retry_requested',previous_error=previous_error)
            self.listen_pc_selected=enabled
            self._invalidate('audio_pc_selected_waiting_fresh_status' if enabled else 'audio_other_source_selected',time.monotonic())
    def set_monitor_gain(self,gain):
        gain=float(gain)
        if not math.isfinite(gain) or not 0<gain<=8:raise ValueError('monitor gain must be finite in (0,8]')
        with self.lock:
            self.monitor_gain=gain;child=self.child;router=self.playback_router
        # Keep the future-generation configuration and the current playback
        # owner consistent, with one forwarding path (never two gain multiplies).
        if router is not None:return router.set_monitor_gain(gain)
        if child is not None and hasattr(child,'set_monitor_gain'):return child.set_monitor_gain(gain)
        return dict(requested=gain,pending=True,scope='cached for next child; no active playback owner')
    def consume_uart_message(self,msg,received_at=None):
        now=time.monotonic() if received_at is None else received_at
        try: st=status_message(msg)
        except (ValueError,TypeError,struct.error) as exc:
            with self.lock:
                self.counts['bad_uart_status']+=1;self.error=str(exc);self._invalidate('invalid_uart_status',now)
            return False
        if st is None:return False
        with self.lock:
            self.counts['uart_status_messages']+=1
            if self.stop.is_set():return False
            if self.status is not None and st['controller_epoch']==self.status['controller_epoch']:
                delta=(st['uart_sequence']-self.status['uart_sequence'])&0xffffffff
                if delta==0 or delta>=0x80000000:
                    self._invalidate('uart_status_sequence_regression',now);return False
            if self.status is not None and st['controller_epoch']==self.status['controller_epoch'] and st['source_frame']<self.status['source_frame']:
                self._invalidate('uart_source_frame_regression',now);self.status=None;return False
            changed_epoch=self.status is not None and st['controller_epoch']!=self.status['controller_epoch']
            if changed_epoch:self._invalidate('controller_epoch_change',now,new_status_required=False)
            self.status=st;self.status_at=now
            if self.sticky_failure:
                # A DSP/audio fault is not a UART outage. Continue validating
                # real heartbeat/epoch/sequence while holding the DSP disabled.
                # Only an explicit user mode4 request can arm a fresh attempt.
                self.counts['status_received_while_failed']+=1
                self.desired=None
                return False
            if self.retry_status_floor is not None:
                floor=self.retry_status_floor
                # GetTickCount64 may give the same timestamp to retry and an
                # already-buffered heartbeat. Require real source progress (or
                # a newly observed controller epoch), not timestamp rounding.
                if (st['controller_epoch']==floor['controller_epoch'] and
                        st['source_frame']<=floor['source_frame']):
                    self.counts['retry_status_without_source_progress']+=1
                    return False
                self.retry_status_floor=None
            route_selected=self.playback_router is None or self.playback_router.is_selected(4)
            if not self.listen_pc_selected or not route_selected:
                if self.child is not None:self._invalidate('audio_other_source_selected',now)
                self.desired=None;self.state='audio_pc_not_selected';return False
            if st['mode']!='LOCKED':self._invalidate('board_search_unlocked',now,new_status_required=False);return False
            if now<self.need_status_after:return False
            key=(st['controller_epoch'],st['azimuth'],st['elevation'])
            if self.desired is None or self.desired['key']!=key:
                self._invalidate('new_board_lock',now,new_status_required=False)
                self.desired=dict(key=key,source_frame=st['source_frame'],azimuth=st['azimuth'],elevation=st['elevation'],observed_at=now)
                self._event('lock_requested',physical_azimuth=st['azimuth'],cfg_azimuth=-st['azimuth'],physical_elevation=st['elevation'],cfg_elevation=-st['elevation'],source_frame=st['source_frame'])
            self.error=None
        return True
    def consume_uart_diagnostic(self,event):
        if event.get('type') not in ('packet_crc_error','packet_sequence_gap'):return
        with self.lock:
            self.counts['uart_integrity_faults']+=1;self._invalidate('uart_integrity_fault',time.monotonic())
    def consume_datagram(self,data):
        begin=time.perf_counter_ns();now=time.monotonic()
        if not isinstance(data,bytes):raise TypeError('immutable bytes required')
        if data[:4]!=b'M128':return False
        try:packet=decode_transport_packet(data)
        except ValueError as exc:return self.reject_malformed_raw(exc,now)
        return self.consume_decoded_datagram(packet,now,_begin_ns=begin)
    def reject_malformed_raw(self,error,received_at=None):
        now=time.monotonic() if received_at is None else received_at
        with self.lock:
            self.counts['malformed_raw_packets']+=1;self.error=str(error)
            self._latch_transport_failure_locked('raw_malformed',now,None,error)
            self._invalidate('raw_malformed',now)
        return False
    def _latch_transport_failure_locked(self,reason,now,p,raw_error=None):
        if self.first_transport_failure is not None:return
        old=self.transport
        row=dict(reason=reason,received_at=now,observed_perf_ns=time.perf_counter_ns(),generation=self.generation,
                 child_generation=getattr(self.child,'generation',None),
                 expected_epoch=None if old is None else old[0],expected_sequence=None if old is None else old[1]+1,
                 expected_first_frame=None if old is None else old[2]+1,
                 epoch=getattr(raw_error,'epoch',None) if p is None else p['epoch'],
                 sequence=getattr(raw_error,'sequence',None) if p is None else p['sequence'],
                 frame_ids=getattr(raw_error,'frames',None) if p is None else p['frame_ids'],metadata_valid=p is not None,
                 time_scope='host callback receipt; not NIC hardware timestamp')
        self.first_transport_failure=row;self.transport_fault_history.append(MappingProxyType(dict(row)))
    def consume_decoded_datagram(self,packet,received_at=None,*,_begin_ns=None):
        packet=require_packet_view(packet)
        begin=time.perf_counter_ns() if _begin_ns is None else _begin_ns
        now=time.monotonic() if received_at is None else received_at
        with self.lock:entry_generation=self.generation
        try:return self._consume_decoded(packet,now)
        finally:
            elapsed=time.perf_counter_ns()-begin
            with self.lock:
                self.callback_us.append(elapsed/1000.)
                if elapsed>10_000_000 and self.first_callback_pause is None:
                    row=dict(begin_perf_ns=begin,end_perf_ns=begin+elapsed,elapsed_ms=elapsed/1e6,
                        received_at=now,generation_at_entry=entry_generation,generation_at_end=self.generation,
                        sequence=packet.metadata['sequence'],epoch=packet.metadata['epoch'],
                        scope='callback wall time >10ms; includes scheduling, not proof of CPU root cause')
                    self.first_callback_pause=row;self.callback_pause_history.append(row)
    def _consume_decoded(self,packet,now):
        data=packet.raw;p=packet.metadata
        with self.lock:
            self.counts['raw_packets']+=1;self.last_raw_received_at=now
            key=(p['epoch'],p['sequence'],p['frame_ids'][-1]);previous=self.transport
            reason=None
            if previous is None:reason='raw_first_segment'
            elif key[0]!=previous[0]:reason='raw_network_epoch_change'
            elif key[1]!=previous[1]+1 or p['frame_ids'][0]!=previous[2]+1:reason='raw_sequence_or_group_gap'
            if p['fatal_flags']:reason='raw_fatal_flags'
            if reason in TRANSPORT_FAULT_REASONS:self._latch_transport_failure_locked(reason,now,p)
            if p['clipped']:self.counts['raw_clipping_warning_packets']+=1
            # Keep the newest accepted transport position, never splice a late
            # duplicate/reordered payload into a later DSP generation.
            if previous is None or key[0]!=previous[0] or key[1]>previous[1] or key[1]==0:self.transport=key
            if reason:
                self.counts[reason]+=1
                # Pre-ready acquisition is explicitly outside the DSP-covered
                # segment. A forward same-epoch transport gap cannot corrupt
                # DSP state which has never accepted an input in this child.
                # Preserve every loss count/event; do not enqueue the gap packet.
                ordinary_forward=(reason=='raw_sequence_or_group_gap' and previous is not None and key[0]==previous[0] and key[1]>previous[1] and p['frame_ids'][0]>previous[2])
                fresh_same_lock=(self.desired is not None and self.status is not None and self.status_at is not None and 0<=now-self.status_at<=self.timeout and self.status['mode']=='LOCKED' and self.desired['key']==(self.status['controller_epoch'],self.status['azimuth'],self.status['elevation']) and self.active_key==self.desired['key'])
                if ordinary_forward and fresh_same_lock and self.child is not None and self.generation_enqueued_packets==0 and not self.stop.is_set() and not self.sticky_failure:
                    self.recovery_not_before=max(self.recovery_not_before,now+TRANSPORT_RECOVERY_HOLDOFF_S)
                    self.counts['warmup_noncovered_gaps']+=1
                    self.counts['warmup_missing_packets']+=max(0,key[1]-previous[1]-1)
                    self.counts['warmup_missing_groups']+=max(0,p['frame_ids'][0]-previous[2]-1)
                    self._event('warmup_raw_gap_noncovered',previous_sequence=previous[1],sequence=key[1],first_frame=p['frame_ids'][0],previous_last_frame=previous[2],dsp_packets_accepted_this_generation=0)
                    return False
                self._invalidate(reason,now);return False
            if self.stop.is_set() or self.sticky_failure:return False
            if self.status_at is None or now-self.status_at>self.timeout:
                if self.child is not None:self._invalidate('board_status_timeout',now)
                self.counts['packets_without_fresh_lock']+=1;return False
            if self.desired is None or self.child is None:
                self.counts['packets_waiting_for_lock_or_dsp']+=1;return False
            if p['frame_ids'][0]<self.desired['source_frame']:
                self.counts['packets_before_lock_source_frame']+=1;return False
            child=self.child
            if not child.wait_ready(0):self.counts['packets_during_dsp_warmup']+=1;return False
            if self.generation_enqueued_packets==0 and now<self.recovery_not_before:
                self.counts['packets_during_transport_settle']+=1;return False
        # Reuse this strict decoded value on the DSP owner instead of parsing
        # and copying 16,000 identical packet payloads per second a second time.
        accepted=child.consume_validated_datagram(ValidatedDatagram(data,p)) if hasattr(child,'consume_validated_datagram') else child.consume_datagram(data)
        with self.lock:
            if self.child is child:
                if accepted:
                    if self.generation_enqueued_packets==0:
                        self.first_transport_failure=None;self.first_callback_pause=None
                    self.counts['enqueued_to_dsp']+=1;self.generation_enqueued_packets+=1
                    if self.first_dsp_input is None:
                        self.first_dsp_input=dict(sequence=p['sequence'],first_frame=p['frame_ids'][0],network_epoch=p['epoch'],accepted_at=now)
                        self._event('first_dsp_input',**self.first_dsp_input)
                else:
                    snap=child.health() if hasattr(child,'health') else child.snapshot();self.last_backend_compact=snap
                    if snap.get('state')=='cancelled':
                        self.counts['preparation_cancellations']+=1;self._invalidate('dsp_preparation_cancelled',now)
                    else:
                        self.sticky_failure=True
                        if snap.get('counts',{}).get('inbox_overflow_packets',0):self.error=f'DSP input queue overflow: {self.capacity} packets / {self.capacity*3/48:.3f}ms; explicit enhancement retry required'
                        else:self.error='DSP refused packet: '+str(snap.get('state'))+' / '+str(snap.get('error'))+'; explicit enhancement retry required'
                        self._invalidate('dsp_inbox_failure',now)
            else:self.counts['old_generation_callback_returns']+=1
        return accepted
    def _reservation_valid_locked(self,reservation):
        if reservation.get('cancelled') or self.stop.is_set() or self.sticky_failure:return False
        if self.pending is not reservation and self.active_reservation is not reservation:return False
        if reservation['generation']!=self.generation or self.desired is None or self.desired['key']!=reservation['key']:return False
        if not self.listen_pc_selected or self.status is None or self.status_at is None or time.monotonic()-self.status_at>self.timeout:return False
        if self.status['mode']!='LOCKED' or reservation['key']!=(self.status['controller_epoch'],self.status['azimuth'],self.status['elevation']):return False
        router=self.playback_router
        return router is None or router.is_selected(4) and router.ticket()==reservation['router_ticket']
    def _reserve_locked(self):
        d=copy.copy(self.desired)
        r=dict(generation=self.generation,key=d['key'],desired=d,cancelled=False,cancel_reason=None,
               router_ticket=None if self.playback_router is None else self.playback_router.ticket(),created_at=time.monotonic())
        if self.playback_router is not None:r['router_proxy']=_CandidateRouter(self,r)
        self.pending=r;self.state='dsp_constructing';self.counts['candidate_reservations']+=1;self._event('dsp_construct_reserved')
        return r
    def _construct_reserved(self,r):
        # Allocation, process spawn and native preparation can all be slow.
        # Only their short publication/control operations use the authority lock.
        child=None;error=None;proxy=r.get('router_proxy');d=r['desired']
        with self.lock:
            if not self._reservation_valid_locked(r):
                if self.pending is r:self.pending=None
                self.counts['candidate_construction_cancelled']+=1
                return
        extra={} if proxy is None else dict(playback_router=proxy)
        if not self.test_only or d['elevation']!=0.:extra['lock_elevation_deg']=d['elevation']
        try:
            child=self.factory(self.config,lock_deg=d['azimuth'],capacity=self.capacity,
                               enable_playback=self.enable_playback,monitor_gain=self.monitor_gain,**extra)
        except BaseException as exc:error=exc
        if child is None:
            cleanup_error=None
            if proxy is not None:
                try:proxy.cleanup_unreturned()
                except BaseException as exc:cleanup_error=exc
            with self.lock:
                cancelled=r.get('cancelled') or self.stop.is_set() or isinstance(error,PlaybackPreparationCancelled)
                if self.pending is r:self.pending=None
                self.counts['candidate_construction_cancelled' if cancelled else 'candidate_construction_errors']+=1
                if not cancelled or cleanup_error is not None:
                    self.error='DSP construction: '+repr(error)
                    if cleanup_error is not None:self.error+='; cleanup pending: '+repr(cleanup_error);self.pending=r
                    self.sticky_failure=True;self._invalidate('dsp_creation_failed',time.monotonic())
            return
        with self.lock:
            valid=self._reservation_valid_locked(r)
            if valid:
                self.pending=None;self.active_reservation=r;self.child=child;self.active_key=d['key']
                self.state='dsp_warming';self._event('dsp_created');self.counts['candidate_publications']+=1
                self.generation_enqueued_packets=0;self.first_dsp_input=None
            else:
                r['cancelled']=True
                if proxy is not None:proxy.cancel_locked(r.get('cancel_reason') or 'candidate_superseded_before_publication')
                if self.pending is r:self.pending=None
                if self.retiring is not None:raise RuntimeError('Candidate retirement would overwrite another owner')
                self.retiring=child;self.counts['candidate_superseded']+=1
        if not valid:
            reason='candidate_superseded_before_publication; board_generation='+str(r['generation'])
            if hasattr(child,'close_with_reason'):child.close_with_reason(reason)
            else:child.close()
    def _collect_retired(self,retired):
        # Called only after the complete owner/process close barrier. One full
        # cached audit is retained independently of future normal retirements.
        final_snapshot=retired.snapshot_full_cached() if hasattr(retired,'snapshot_full_cached') else retired.snapshot()
        compact=retired.snapshot_compact() if hasattr(retired,'snapshot_compact') else retired.health() if hasattr(retired,'health') else final_snapshot
        with self.lock:
            self.last_backend=final_snapshot;self.last_backend_compact=compact
            if retired is self.failed_owner and self.last_failure is not None:
                if self.last_failure.get('child_generation')==getattr(retired,'generation',None):
                    self.last_failed_backend_full=final_snapshot
    def _run(self):
        try:
            while not self.stop.is_set():
                retired=None;reservation=None
                with self.lock:
                    now=time.monotonic()
                    if self.desired is not None and (self.status_at is None or now-self.status_at>self.timeout):self._invalidate('board_status_timeout',now)
                    if self.retiring is not None:
                        if self.retiring.wait_closed(0):
                            retired=self.retiring;self.retiring=None
                    route_selected=self.playback_router is None or self.playback_router.is_selected(4)
                    can_create=(self.listen_pc_selected and route_selected and not self.sticky_failure and self.transport is not None and self.desired is not None and self.child is None and self.retiring is None and self.pending is None)
                    if can_create and now<self.recovery_not_before:self.state='transport_recovery_wait'
                    if can_create and now>=self.recovery_not_before:
                        reservation=self._reserve_locked()
                    if self.child is not None:
                        # Control needs current scalar fault/readiness only.
                        # Full audits remain available via snapshot(), but must
                        # not run under this 200Hz owner lock.
                        snap=self.child.health() if hasattr(self.child,'health') else self.child.snapshot()
                        if snap['state']=='failed':
                            self.sticky_failure=True;self.error=snap.get('error');self._invalidate('dsp_backend_failed',now)
                        elif snap['state']=='cancelled':
                            self.counts['preparation_cancellations']+=1
                            self._invalidate('dsp_preparation_cancelled',now)
                        elif self.child.wait_ready(0):
                            self.state='dsp_ready_transport_settling' if self.generation_enqueued_packets==0 and now<self.recovery_not_before else 'board_lock_3d_active'
                if retired is not None:
                    # Final deep audit is diagnostic work, never part of the
                    # UART/UDP authority lock or a native-close prerequisite.
                    self._collect_retired(retired)
                if reservation is not None:self._construct_reserved(reservation)
                self.stop.wait(.005)
        except BaseException as exc:
            with self.lock:
                self.error=type(exc).__name__+': '+str(exc);self.sticky_failure=True;self.state='bridge_failed'
                self.last_failure=dict(reason='bridge_failed',error=self.error,at=time.monotonic(),generation=self.generation,
                    child_generation=getattr(self.child,'generation',None))
                self.failed_owner=self.child;self.last_failed_backend_full=None
        finally:
            with self.lock:children=[c for c in (self.child,self.retiring) if c is not None];self.child=None;self.retiring=None
            for child in children:
                if hasattr(child,'close_with_reason'):child.close_with_reason(f'board_adapter_shutdown; board_generation={self.generation}')
                else:child.close()
            for child in children:
                if not child.wait_closed(15):
                    with self.lock:self.error='child shutdown pending';self.state='shutdown_pending'
                else:self._collect_retired(child)
            with self.lock:
                self.owns_playback=False
                if self.state!='shutdown_pending':self.state='closed'
            self.done.set()
    def _snapshot(self,full=False):
        with self.lock:
            child=self.child;retiring=self.retiring
            backend_cache=self.last_backend if full else self.last_backend_compact;sampled_generation=self.generation
            pending=self.pending
            failed_cache=self.last_failed_backend_full;failed_owner=self.failed_owner
            failure_pending=self.last_failure is not None and failed_cache is None
            result=dict(scope='HOST_3D_BOARD_UART_LOCK_FOLLOWING_SOFTWARE_CANDIDATE_NOT_FPGA_DSP',state=self.state,error=self.error,
                generation=self.generation,listen_pc_selected=self.listen_pc_selected,sticky_failure=self.sticky_failure,lock_source='CRC-validated actual UART type1 status',
                last_failure=copy.deepcopy(self.last_failure),
                fault_forensic_ready=self.last_failure is not None and failed_cache is not None,
                fault_forensic_pending=failure_pending,
                first_transport_failure=copy.deepcopy(self.first_transport_failure),first_callback_pause=copy.deepcopy(self.first_callback_pause),
                transport_fault_history=[dict(row) for row in self.transport_fault_history],callback_pause_history=list(self.callback_pause_history),
                construction_pending=None if pending is None else dict(generation=pending['generation'],key=pending['key'],cancelled=pending['cancelled'],router_ticket=pending['router_ticket']),
                target_status=copy.deepcopy(self.status),status_age_s=None if self.status_at is None else time.monotonic()-self.status_at,
                raw_network_epoch=None if self.transport is None else self.transport[0],controller_epoch=None if self.status is None else self.status['controller_epoch'],
                independent_epoch_semantics=True,desired=copy.deepcopy(self.desired),automatic_board_lock_following=True,
                supports_nonzero_elevation=True,owns_playback=self.owns_playback,test_factory=self.test_only,dsp_execution_mode='process' if self.dsp_process else 'thread',
                generation_enqueued_packets=self.generation_enqueued_packets,first_dsp_input=copy.deepcopy(self.first_dsp_input),
                transport_recovery_holdoff_s=TRANSPORT_RECOVERY_HOLDOFF_S,recovery_wait_s=max(0.,self.recovery_not_before-time.monotonic()),
                counts=dict(self.counts),recent_events=list(self.events),backend=None,
                callback_us=dict(count=len(self.callback_us),max=max(self.callback_us) if self.callback_us else None),
                transport_audio_hardware_tested=False,physical_end_to_end_latency_measured=False)
        diagnostic_child=child if child is not None else retiring
        if full and result['last_failure'] is not None:
            # A new/healthy active child must never impersonate the failed one.
            result['backend']=failed_cache if failed_cache is not None else failed_owner.health() if failed_owner is not None and hasattr(failed_owner,'health') else None
            result['backend_owner']='failed_cache' if failed_cache is not None else 'failed_pending'
            result['forensic_child_generation']=result['last_failure'].get('child_generation')
        elif diagnostic_child is None:result['backend']=backend_cache
        elif full:result['backend']=diagnostic_child.snapshot_full_cached() if hasattr(diagnostic_child,'snapshot_full_cached') else diagnostic_child.snapshot()
        elif hasattr(diagnostic_child,'snapshot_compact'):result['backend']=diagnostic_child.snapshot_compact()
        elif hasattr(diagnostic_child,'health'):result['backend']=diagnostic_child.health()
        else:result['backend']=diagnostic_child.snapshot() # explicit test factories only
        if 'backend_owner' not in result:result['backend_owner']='active' if child is not None else 'retiring' if retiring is not None else 'retired_cache'
        with self.lock:
            result['diagnostic_generation_at_end']=self.generation
            result['diagnostic_stale']=sampled_generation!=self.generation or child is not self.child or retiring is not self.retiring
        result['diagnostic_scope']='metadata sampled before backend copy; cross-field non-atomic diagnostic only; not DSP control'
        result['audit_scope']='explicit_full_cached' if full else 'compact_control_only'
        return result
    def snapshot_compact(self):return self._snapshot(False)
    def snapshot(self):return self.snapshot_compact()
    def snapshot_full_cached(self):return self._snapshot(True)
    def health(self):
        """Cheap polling for status/queue telemetry, no DSP audit copy."""
        with self.lock:
            child=self.child
            backend=(child.health() if hasattr(child,'health') else child.snapshot()) if child is not None else None
            return dict(state=self.state,error=self.error,generation=self.generation,
                sticky_failure=self.sticky_failure,backend=backend,
                status_age_s=None if self.status_at is None else time.monotonic()-self.status_at)
    def close(self):
        self.stop.set()
        with self.lock:
            if self.pending is not None:
                self.pending['cancelled']=True
                proxy=self.pending.get('router_proxy')
                if proxy is not None:proxy.cancel_locked('board_adapter_shutdown')
            if self.child is not None:self.child.close()
    def wait_closed(self,timeout=20):return self.done.wait(timeout)
