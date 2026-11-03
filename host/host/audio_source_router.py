"""One audible source: mute / board mono16 / board beam16 / PC enhancement48.

Import and construction never open sound devices. Board sink preparation is
explicit on selection, outside the router lock and UART input callback.
PC sink preparation is explicit and precedes DSP readiness. Selection closes
the previous sink (waveOutReset in its finally) before another may open.
"""
import collections,math,threading,time
from player_metrics import normalize_player

NAMES={0:'mute',2:'board_mono_16k',3:'board_beam_16k',4:'pc_enhancement_48k'}

class AudioSourceRouter:
    def __init__(self,*,monitor_gain=1.,sink_factory=None):
        self.lock=threading.RLock();self.mode=0;self.requested=0;self.epoch=0
        self.player=None;self.retiring=None;self.monitor_gain=monitor_gain
        self.factory=sink_factory or self._real_sink;self.error=None
        self.counts=collections.Counter();self.last_completed_seen=None;self.last_completion=None
        self.pc_owner=None;self.pc_owner_serial=0
        self.preparing=None;self.prepare_ms_max=0.;self.board_prepare_ms_max=0.
        self.last_player=None;self._last_player_sink=None;self.lifecycle=collections.deque(maxlen=32)
        self.quiescing_owner=None
        from audio_status import PcmLevel
        self.level=PcmLevel()
    def _real_sink(self,mode):
        if mode==4:
            from playback48 import WaveOut48
            return WaveOut48(monitor_gain=self.monitor_gain)
        from audio_playback import WaveOut
        return WaveOut()
    @staticmethod
    def _closed(p):
        t=getattr(p,'thread',None)
        device_closed=getattr(p,'device_closed',None)
        if isinstance(device_closed,bool):return device_closed and not (t and t.is_alive())
        return not (t and t.is_alive())
    def ticket(self):
        with self.lock:return self.epoch
    def is_selected(self,mode):
        with self.lock:return self.mode==mode
    def set_monitor_gain(self,gain):
        """Playback-only gain; never changes beam, Wiener, post or source data.

        Local feeds and updates share this lock. Remote devices receive a
        bounded control value; a requested value is not an applied-device ACK.
        """
        gain=float(gain)
        if not math.isfinite(gain) or not 0<gain<=8:
            raise ValueError('PC playback gain must be finite, >0 and <=8')
        with self.lock:
            p=self.player if self.mode==4 else None
            if p is not None:
                if hasattr(p,'set_monitor_gain'):p.set_monitor_gain(gain)
                elif hasattr(p,'gain'):p.gain=gain
                else:raise RuntimeError('Selected audio sink has no actual gain setter')
            self.monitor_gain=gain;self.counts['monitor_gain_requests']+=1
            return gain
    def register_pc_owner(self):
        with self.lock:
            self.pc_owner_serial+=1;self.pc_owner=self.pc_owner_serial;self.quiescing_owner=None
            return self.pc_owner
    def install_remote_pc(self,owner,sink):
        """Reserve a worker-owned sink; selection awaits its native-close ACK."""
        with self.lock:
            if self.mode!=4 or self.pc_owner!=owner or self.player is not None or self.retiring is not None or self.preparing is not None or self.quiescing_owner is not None:return False
            if hasattr(sink,'set_monitor_gain'):sink.set_monitor_gain(self.monitor_gain)
            self.player=sink;self.counts['remote_pc_owner_installed']+=1
            self.lifecycle.append(dict(at_ns=time.perf_counter_ns(),reason='remote_owner_installed',owner=owner,epoch=self.epoch))
            return True
    def request_pc_quiesce(self,owner,reason,expected_ticket=None):
        """Owner/version-gated nonblocking native stop, never close/join here."""
        with self.lock:
            if expected_ticket is not None and expected_ticket!=self.epoch:
                self.counts['stale_quiesce_ticket_requests']+=1;return False
            if owner is None or self.mode!=4 or owner!=self.pc_owner:
                self.counts['stale_quiesce_requests']+=1;return False
            self.quiescing_owner=owner;p=self.player;epoch=self.epoch
            self.counts['pc_quiesce_requests']+=1
            self.lifecycle.append(dict(at_ns=time.perf_counter_ns(),reason='quiesce_requested',owner=owner,epoch=epoch,cause=str(reason)))
        if p is not None and hasattr(p,'request_close'):
            p.request_close(str(reason));return True
        # Fake/external sinks without a nonblocking API are closed by the DSP
        # owner's quiesce_output before any slow drain; never guess close cost.
        return False
    def release_remote_pc(self,owner,sink):
        """Release this retired reservation only after real native-close ACK.

        Source selection may already have detached it; an old worker can never
        clear a different/new owner or switch the user's selected source.
        """
        if not sink.adapter.shared.native_closed.is_set():return False
        with self.lock:
            if self.player is not sink or self.pc_owner!=owner or self.mode!=4:return False
            self._remember_player(sink,'remote_process_closed',owner=owner,epoch=self.epoch)
            self.player=None;self.epoch+=1;self.counts['remote_pc_owner_released']+=1
            self.last_completed_seen=None;self.last_completion=None
            return True
    def _remember_player(self,p,reason,*,owner=None,epoch=None,error=None,compact=False):
        try:native=None if p is None else p.snapshot_compact() if compact and hasattr(p,'snapshot_compact') else p.snapshot()
        except Exception as exc:native=dict(snapshot_error=type(exc).__name__+': '+str(exc))
        with self.lock:
            row=dict(at_ns=time.perf_counter_ns(),reason=reason,owner=owner,epoch=self.epoch if epoch is None else epoch,error=error)
            self.lifecycle.append(row)
            if p is not None:
                self.last_player=dict(row,native=native);self._last_player_sink=p
    def refresh_remote_final(self,owner,sink):
        """Replace only this same retired sink's diagnostic with worker final.

        No selection, epoch, current player, hardware flag or error is invented.
        An old worker may not overwrite a later owner's retirement record.
        """
        final=sink.snapshot()
        if not final.get('remote_worker_final') or not final.get('native_closed_ack'):return False
        if not final.get('remote_process_joined'):return False
        with self.lock:
            row=self.last_player
            if self.pc_owner!=owner or self._last_player_sink is not sink or not row or row.get('owner')!=owner:return False
            previous=row.get('native') or {}
            if previous.get('remote_generation')!=final.get('remote_generation'):return False
            if final.get('remote_generation')!=sink.adapter.generation:return False
            if row.get('worker_final_source_sampled_ns')==final.get('source_sampled_ns'):return True
            updated=dict(row)
            if 'native_before_worker_final' not in updated:updated['native_before_worker_final']=previous
            updated['native']=final;updated['worker_final_refreshed_ns']=time.perf_counter_ns()
            updated['worker_final_source_sampled_ns']=final.get('source_sampled_ns')
            updated['error']=row.get('error') or final.get('error')
            self.last_player=updated;self.counts['remote_final_ledger_refreshes']+=1
            return True
    def _close_sink(self,p,reason,*,owner=None,epoch=None):
        self._remember_player(p,reason+'_requested',owner=owner,epoch=epoch)
        if hasattr(p,'close_with_reason'):p.close_with_reason(reason)
        else:p.close()
        self._remember_player(p,reason+'_returned',owner=owner,epoch=epoch)
    def owner_diagnostics(self,owner):
        with self.lock:
            p=self.player if self.mode==4 and self.pc_owner==owner else None
            last=self.last_player if self.last_player is not None and self.last_player.get('owner')==owner else None
            epoch=self.epoch
        return dict(owner=owner,observed_epoch=epoch,player=None if p is None else p.snapshot(),last_player=last)
    @staticmethod
    def _compact_native(value):
        if not isinstance(value,dict):return None
        keys=('fed_samples','submitted_samples','completed_samples','queued_samples','held_samples','inflight_samples',
              'queued_blocks','inflight_blocks','cancelled_samples','cancelled_queued_samples','cancelled_held_samples',
              'cancelled_submitted_samples','dropped_samples','underflow','overflow','clip_samples','error','closed',
              'device_closed','native_closed_ack','native_device_open','worker_alive','rate','monitor_gain',
              'completion_age_seconds','remote_generation','remote_worker_pid','remote_process_joined','source_sampled_ns',
              'parent_cache_age_s','remote_transport_proxy','remote_device_state_unknown','strict_no_drop','drop_policy',
              'remote_output_level','native_api_max_ms','max_feed_gap_ms','max_worker_wake_gap_ms',
              'first_feed_ns','last_feed_ns','opened_ns','software_capacity_samples','hardware_capacity_samples',
              'prefill_samples','ledger_ok','capacity_samples','prefill_ms','ledger_balanced','submitted_ledger_balanced',
              'input_samples_seen','rejected_samples','peak_software_samples','inflight_not_done_samples',
              'started','stage','close_requested_ns','close_reason','created_ns','worker_exit_ns')
        return {k:value[k] for k in keys if k in value}
    @classmethod
    def _compact_retired(cls,row):
        if not isinstance(row,dict):return None
        result={k:row[k] for k in ('at_ns','reason','owner','epoch','error') if k in row}
        result['native']=cls._compact_native(row.get('native'));return result
    def owner_diagnostics_compact(self,owner):
        with self.lock:
            p=self.player if self.mode==4 and self.pc_owner==owner else None
            last=self.last_player if self.last_player is not None and self.last_player.get('owner')==owner else None
            native=None if p is None else p.snapshot_compact() if hasattr(p,'snapshot_compact') else self._compact_native(p.snapshot())
            return dict(owner=owner,observed_epoch=self.epoch,player=native,last_player=self._compact_retired(last),compact=True)
    def prepare_pc(self,owner=None):
        """Prepare only the selected current owner, outside the router lock.

        Call from backend initialization before RawPreviewAdapter.ready. Native
        waveOutOpen can block; it must never occur on the live DSP feed path.
        A selection/owner change during open prevents publication and closes
        the orphan device. No audio is discarded to hide startup latency.
        """
        with self.lock:
            if self.mode!=4 or owner!=self.pc_owner:return False
            if self.quiescing_owner is not None and owner==self.quiescing_owner:return False
            if self.error:raise RuntimeError(self.error)
            if self.player is not None:return True
            if self.preparing is not None:return False
            opening=dict(epoch=self.epoch,owner=owner,mode=4,done=threading.Event())
            self.preparing=opening;self.counts['pc_prepare_started']+=1
            self.lifecycle.append(dict(at_ns=time.perf_counter_ns(),reason='prepare_started',owner=owner,epoch=self.epoch))
        sink=None;begin=time.perf_counter_ns()
        try:
            sink=self.factory(4)
            with self.lock:
                current=(self.preparing is opening and opening['epoch']==self.epoch and self.mode==4 and owner==self.pc_owner and self.player is None)
                if current:
                    # A user may change gain while native open is in flight.
                    if hasattr(sink,'set_monitor_gain'):sink.set_monitor_gain(self.monitor_gain)
                    elif hasattr(sink,'gain'):sink.gain=self.monitor_gain
                    self.player=sink;sink=None;self.counts['pc_prepare_ready']+=1
                    self.lifecycle.append(dict(at_ns=time.perf_counter_ns(),reason='prepare_ready',owner=owner,epoch=self.epoch))
                else:self.counts['pc_prepare_superseded']+=1
            return current
        except Exception as exc:
            with self.lock:
                self.counts['pc_prepare_errors']+=1
                if opening['epoch']==self.epoch and owner==self.pc_owner:self.error='PC audio preparation failed: '+str(exc)
            raise
        finally:
            if sink is not None:self._close_sink(sink,'prepare_superseded',owner=owner,epoch=opening['epoch'])
            with self.lock:
                self.prepare_ms_max=max(self.prepare_ms_max,(time.perf_counter_ns()-begin)/1e6)
                if self.preparing is opening:self.preparing=None
            opening['done'].set()
    def select(self,mode):
        if mode not in NAMES:raise ValueError('Unknown audio source')
        with self.lock:
            if mode==self.mode and self.requested==mode and not self.error:
                current=self.player
                current_state=(current.snapshot_compact() if hasattr(current,'snapshot_compact') else current.snapshot()) if current is not None else {}
                fault=current_state.get('error') or ('Selected audio device is closed' if current_state.get('closed') else None)
                if not fault:return self.epoch
                self.error=str(fault)
            self.epoch+=1;token=self.epoch;self.requested=mode;self.mode=0
            old=self.player or self.retiring;old_owner=self.pc_owner;opening=self.preparing;self.player=None;self.retiring=old
        # No router lock while waiting for hardware reset/close: DSP callbacks
        # observe muted mode and return immediately, rather than blocking inbox.
        if old is not None:self._close_sink(old,'source_selection_'+str(mode),owner=old_owner,epoch=token)
        if opening is not None and not opening['done'].wait(2.):
            with self.lock:
                if token==self.epoch:self.error='Previous audio preparation shutdown is pending'
            raise RuntimeError('Previous audio preparation shutdown is pending')
        with self.lock:
            if token!=self.epoch:return self.epoch
            barrier=self.retiring or old
            if barrier is not None and not self._closed(barrier):
                self.error='Previous audio device shutdown is pending';raise RuntimeError(self.error)
            self.retiring=None;self.error=None
            from audio_status import PcmLevel
            self.level=PcmLevel();self.last_completed_seen=None;self.last_completion=None
            if mode not in (2,3):
                self.mode=mode;self.counts['source_changes']+=1;return token
            opening=dict(epoch=token,owner=None,mode=mode,done=threading.Event())
            self.preparing=opening;self.counts['board_prepare_started']+=1
            self.lifecycle.append(dict(at_ns=time.perf_counter_ns(),reason='board_prepare_started',epoch=token,mode=mode))
        # Opening a native device may block. UART can keep receiving while
        # mode remains0 and Session has not admitted this source yet.
        sink=None;begin=time.perf_counter_ns();current=False
        try:
            sink=self.factory(mode)
            with self.lock:
                current=(self.preparing is opening and self.epoch==token and self.requested==mode and self.mode==0 and self.player is None)
                if current:
                    self.player=sink;sink=None;self.mode=mode;self.error=None
                    self.counts['board_prepare_ready']+=1;self.counts['source_changes']+=1
                    self.lifecycle.append(dict(at_ns=time.perf_counter_ns(),reason='board_prepare_ready',epoch=token,mode=mode))
                else:self.counts['board_prepare_superseded']+=1
            return token if current else self.epoch
        except Exception as exc:
            with self.lock:
                self.counts['board_prepare_errors']+=1
                if token==self.epoch:self.error='Board audio preparation failed: '+str(exc)
            raise
        finally:
            if sink is not None:
                self._close_sink(sink,'board_prepare_superseded',epoch=token)
                if not self._closed(sink):
                    with self.lock:
                        self.retiring=sink;self.error='Superseded board audio device shutdown is pending'
            with self.lock:
                self.board_prepare_ms_max=max(self.board_prepare_ms_max,(time.perf_counter_ns()-begin)/1e6)
                if self.preparing is opening:self.preparing=None
            opening['done'].set()
    def fail_board(self,kind,reason,ticket=None):
        """Seal only this selected board source; never join on UART input.

        A later explicit selection closes/acknowledges the retiring device
        before another may open. A stale source cannot stop a new owner.
        """
        if kind not in (2,3):raise ValueError('Board source must be2 or3')
        with self.lock:
            if self.mode!=kind or ticket is not None and ticket!=self.epoch:
                self.counts['stale_board_faults']+=1;return False
            self.epoch+=1;self.mode=0;self.requested=kind;self.error=str(reason)
            old=self.player;self.player=None;self.retiring=old
            self.counts['board_playback_faults']+=1
            self.lifecycle.append(dict(at_ns=time.perf_counter_ns(),reason='board_fault_sealed',mode=kind,epoch=self.epoch,error=str(reason)))
        if old is not None and hasattr(old,'request_close'):old.request_close('board_playback_fault: '+str(reason))
        return True
    def _feed(self,mode,data,ticket,owner=None):
        with self.lock:
            if ticket!=self.epoch or self.mode!=mode or mode==4 and owner!=self.pc_owner:
                self.counts['stale_or_unselected_blocks']+=1;return False
            if mode==4 and self.quiescing_owner is not None and owner==self.quiescing_owner:
                self.counts['quiesced_pc_blocks']+=1;return False
            if self.error:raise RuntimeError(self.error)
            if self.player is None:
                self.counts['pc_feed_without_preparation' if mode==4 else 'board_feed_without_preparation']+=1
                raise RuntimeError('PC sink was not prepared before DSP readiness' if mode==4 else 'Board sink was not prepared before source admission')
            try:self.player.feed(data)
            except Exception as e:
                self.error=str(e);self._remember_player(self.player,'feed_error',owner=owner,epoch=ticket,error=str(e),compact=True);raise
            samples=512 if mode==4 else len(data)//2
            self.counts[NAMES[mode]+'_fed_samples']+=samples
            if mode==4:
                import numpy as np
                pcm=np.rint(np.clip(np.asarray(data,dtype=float)*self.monitor_gain,-1,1)*32767).astype('<i2').tobytes()
                self.level.feed(pcm)
            else:self.level.feed(data)
            return True
    def feed_board(self,kind,pcm,ticket):
        if kind not in (2,3):raise ValueError('Board PCM stream must be2 or3')
        return self._feed(kind,pcm,ticket)
    def feed_pc(self,samples,ticket,owner=None):return self._feed(4,samples,ticket,owner)
    def flush_pc(self,owner=None,reason='pc_owner_flush',expected_ticket=None):
        with self.lock:
            if expected_ticket is not None and expected_ticket!=self.epoch:
                self.counts['stale_flush_ticket_requests']+=1;return False
            if self.mode!=4 or owner!=self.pc_owner:return
            self.epoch+=1;token=self.epoch;self.mode=0;old=self.player;self.player=None;self.retiring=old
        if old is not None:self._close_sink(old,str(reason),owner=owner,epoch=token)
        with self.lock:
            if token!=self.epoch:return
            if old is not None and not self._closed(old):
                self.error='PC audio device shutdown is pending';return
            self.retiring=None;self.mode=4;self.counts['pc_flushes']+=1
            self.last_completed_seen=None;self.last_completion=None
    def _snapshot(self,compact=False):
        with self.lock:
            p=(self.player.snapshot_compact() if compact and hasattr(self.player,'snapshot_compact') else self.player.snapshot()) if self.player else None
            if compact and p is not None:p=self._compact_native(p)
            if p is not None and not p.get('remote_transport_proxy',False):
                completed=p.get('completed_samples',0) if isinstance(p,dict) else None
                if type(completed) is int and completed>=0:
                    if self.last_completed_seen is not None and completed>self.last_completed_seen:
                        self.last_completion=time.monotonic()
                    if self.last_completed_seen is not None and completed<self.last_completed_seen:
                        self.last_completion=None
                    self.last_completed_seen=completed
                observed_age=None if self.last_completion is None else max(0,time.monotonic()-self.last_completion)
                p=normalize_player(p,rate_hint=48000 if self.mode==4 else 16000,
                                   observed_completion_age=observed_age)
            return dict(mode=self.mode,requested_mode=self.requested,source=NAMES[self.mode],epoch=self.epoch,error=self.error,
                        monitor_gain_requested=self.monitor_gain,
                        pc_owner=self.pc_owner,
                        player=p,counts=dict(self.counts),output_level=(p.get('remote_output_level') if isinstance(p,dict) else None) or self.level.snapshot(),
                        sink_open=(p.get('native_device_open') if isinstance(p,dict) and p.get('remote_transport_proxy') else self.player is not None),transition_pending=self.retiring is not None or self.preparing is not None,
                        pc_preparing=bool(self.preparing is not None and self.preparing.get('mode',4)==4) or bool(isinstance(p,dict) and p.get('remote_transport_proxy') and p.get('remote_device_state_unknown') and not p.get('native_closed_ack')),pc_prepare_ms_max=self.prepare_ms_max,
                        board_preparing=bool(self.preparing is not None and self.preparing.get('mode') in (2,3)),board_prepare_ms_max=self.board_prepare_ms_max,
                        quiescing_owner=self.quiescing_owner,
                        last_player=self._compact_retired(self.last_player) if compact else self.last_player,player_lifecycle=list(self.lifecycle)[-4:] if compact else list(self.lifecycle),compact=compact)
    def snapshot(self):return self._snapshot(False)
    def snapshot_compact(self):return self._snapshot(True)
    def close(self):self.select(0)
