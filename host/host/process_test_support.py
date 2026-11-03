"""Opt-in process lifecycle test actor; never used by the production CLI."""
import hashlib,time

class TestBackend:
    def __init__(self,shared,spec,allowed,enable_playback,gain):
        self.shared=shared;self.spec=spec;self.allowed=allowed;self.count=0;self.hash=hashlib.sha256();self.closed=False
        time.sleep(spec.get('startup_ms',0)/1000.)
        self.native_open_ns=None if shared.stop.is_set() or not enable_playback else time.perf_counter_ns()
        self.native_close_ns=None;self.reason=None
    def consume(self,data):
        time.sleep(self.spec.get('consume_ms',0)/1000.)
        self.hash.update(data);self.count+=1
        if self.spec.get('raise_at')==self.count:raise RuntimeError('injected real backend failure')
    def idle(self):pass
    def request_output_stop(self,reason):
        self.reason=reason
        if self.native_open_ns is not None and self.native_close_ns is None:self.native_close_ns=time.perf_counter_ns()
    def quiesce_output(self,reason):self.request_output_stop(reason)
    def finish(self):time.sleep(self.spec.get('finish_ms',0)/1000.)
    def close_with_reason(self,reason):self.request_output_stop(reason);self.closed=True
    def snapshot(self):
        return dict(counts=dict(packets=self.count),digest=self.hash.hexdigest(),
                    output=dict(player=dict(native_open_ns=self.native_open_ns,native_close_ns=self.native_close_ns,error=None,closed=self.native_close_ns is not None),reason=self.reason),
                    dynamic=dict(active_frames=0,event_counts={}),test_backend=True)
