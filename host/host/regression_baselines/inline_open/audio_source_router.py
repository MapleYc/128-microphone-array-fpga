"""One audible source: mute / board mono16 / board beam16 / PC enhancement48.

Import and construction never open sound devices. Real sinks are lazy. Selection
closes the previous sink (waveOutReset in its finally) before another may open.
"""
import collections,threading,time
from player_metrics import normalize_player

NAMES={0:'mute',2:'board_mono_16k',3:'board_beam_16k',4:'pc_enhancement_48k'}

class AudioSourceRouter:
    def __init__(self,*,monitor_gain=1.,sink_factory=None):
        self.lock=threading.RLock();self.mode=0;self.requested=0;self.epoch=0
        self.player=None;self.retiring=None;self.monitor_gain=monitor_gain
        self.factory=sink_factory or self._real_sink;self.error=None
        self.counts=collections.Counter();self.last_completed_seen=None;self.last_completion=None
        self.pc_owner=None;self.pc_owner_serial=0
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
        return not (t and t.is_alive())
    def ticket(self):
        with self.lock:return self.epoch
    def register_pc_owner(self):
        with self.lock:
            self.pc_owner_serial+=1;self.pc_owner=self.pc_owner_serial
            return self.pc_owner
    def select(self,mode):
        if mode not in NAMES:raise ValueError('Unknown audio source')
        with self.lock:
            if mode==self.mode and self.requested==mode and not self.error:return self.epoch
            self.epoch+=1;token=self.epoch;self.requested=mode;self.mode=0
            old=self.player or self.retiring;self.player=None;self.retiring=old
        # No router lock while waiting for hardware reset/close: DSP callbacks
        # observe muted mode and return immediately, rather than blocking inbox.
        if old is not None:old.close()
        with self.lock:
            if token!=self.epoch:return self.epoch
            if old is not None and not self._closed(old):
                self.error='Previous audio device shutdown is pending';raise RuntimeError(self.error)
            self.retiring=None;self.mode=mode;self.error=None;self.counts['source_changes']+=1
            from audio_status import PcmLevel
            self.level=PcmLevel();self.last_completed_seen=None;self.last_completion=None
            return token
    def _feed(self,mode,data,ticket,owner=None):
        with self.lock:
            if ticket!=self.epoch or self.mode!=mode or mode==4 and owner!=self.pc_owner:
                self.counts['stale_or_unselected_blocks']+=1;return False
            if self.error:raise RuntimeError(self.error)
            if self.player is None:self.player=self.factory(mode)
            try:self.player.feed(data)
            except Exception as e:self.error=str(e);raise
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
    def flush_pc(self,owner=None):
        with self.lock:
            if self.mode!=4 or owner!=self.pc_owner:return
            self.epoch+=1;token=self.epoch;self.mode=0;old=self.player;self.player=None;self.retiring=old
        if old is not None:old.close()
        with self.lock:
            if token!=self.epoch:return
            if old is not None and not self._closed(old):
                self.error='PC audio device shutdown is pending';return
            self.retiring=None;self.mode=4;self.counts['pc_flushes']+=1
            self.last_completed_seen=None;self.last_completion=None
    def snapshot(self):
        with self.lock:
            p=self.player.snapshot() if self.player else None
            if p is not None:
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
                        pc_owner=self.pc_owner,
                        player=p,counts=dict(self.counts),output_level=self.level.snapshot(),
                        sink_open=self.player is not None,transition_pending=self.retiring is not None)
    def close(self):self.select(0)
