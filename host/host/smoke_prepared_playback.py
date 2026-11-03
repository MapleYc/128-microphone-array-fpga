"""Full 3D/post chain plus actual PCM conversion, 200ms fake open, no devices."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import json,time,collections,threading
from pathlib import Path
import smoke_3d_board_bridge as bench
from audio_source_router import AudioSourceRouter
from base.playback48 import WaveOut48
from board_follow import BoardLockAdapter

class MemoryPlayer:
    def __init__(self):
        self.p=WaveOut48.__new__(WaveOut48);p=self.p
        p.counts=collections.Counter();p.q=collections.deque();p.busy=[False]*4;p.gain=1.;p.capacity=12;p.lock=threading.Lock();p.error=None;p.closed=False
    def feed(self,a):
        self.p.feed(a)
        # Immediate simulated completion. Exercises PCM conversion/queue API,
        # not the Windows driver or paced speaker hardware.
        with self.p.lock:
            self.p.q.clear();self.p.counts['submitted_samples']+=512;self.p.counts['completed_samples']+=512
    def snapshot(self):return self.p.snapshot()
    def close(self):self.p.closed=True

if __name__=='__main__':
    opens=[]
    def factory(mode):
        if mode!=4:raise AssertionError('unexpected source')
        start=time.perf_counter();time.sleep(.2);p=MemoryPlayer();opens.append((time.perf_counter()-start)*1000);return p
    router=AudioSourceRouter(sink_factory=factory);router.select(4)
    def bridge(cfg,**kw):
        kw.update(enable_playback=True,playback_router=router)
        adapter=BoardLockAdapter(cfg,**kw);adapter.set_listen_mode(4)
        return adapter
    bench.BoardLockAdapter=bridge
    result=bench.run(30.);result['fake_open_ms']=opens;result['router']=router.snapshot();result['scope']='full 3D production adapter/post/PCM conversion with one 200ms injected startup sink; no GUI/NIC/soundcard/board, immediate fake completion'
    path=Path(__file__).with_name('PREPARED_PLAYBACK_30S_NEW_RESULT.yaml');path.write_text(store.dumps(result,indent=2),encoding='utf8');router.close()
    print(json.dumps({k:result[k] for k in ('passed','error','sent','accepted','health','counts','fake_open_ms','spatial_ms','post_ms')},ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)
