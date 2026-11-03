"""Real bounded RawPreviewAdapter queue/owner with empty diagnostic backend."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import cProfile,io,json,pstats,struct,time
from pathlib import Path
from types import MappingProxyType
from legacy_adapter import RawPreviewAdapter,ValidatedDatagram
from monitor.raw128_receiver import decode_packet

class EmptyBackend:
    def __init__(self,*a,**kw):self.count=0;self.profile=None;self.prof=cProfile.Profile();self.prof.enable()
    def consume(self,packet):self.count+=1
    def idle(self):pass
    def fault(self,reason):raise RuntimeError('unexpected fault '+str(reason))
    def finish(self):
        self.prof.disable();o=io.StringIO();pstats.Stats(self.prof,stream=o).strip_dirs().sort_stats('tottime').print_stats(22);self.profile=o.getvalue();return self.snapshot()
    def close(self):pass
    def snapshot(self):return dict(count=self.count,profile=self.profile)

def run(profile=True):
    h=struct.Struct('>4sBBBBIHHQII');raw=h.pack(b'M128',1,32,1,128,48000,3,392,0,1,64)+b''.join(struct.pack('>Q',i)+bytes(384) for i in range(3));token=ValidatedDatagram(raw,MappingProxyType(decode_packet(raw)))
    a=RawPreviewAdapter({},lock_deg=0,capacity=1024,backend_factory=EmptyBackend,allow_test_factory=True);assert a.wait_ready(2)
    start=time.perf_counter();cpu=time.process_time();accepted=0
    for base in range(0,16000,16):
        left=start+base/16000.-time.perf_counter()
        if left>0:time.sleep(left)
        for _ in range(16):accepted+=a.consume_validated_datagram(token)
    until=time.perf_counter()+2
    while a.health()['processed_packets']<accepted and time.perf_counter()<until:time.sleep(.001)
    elapsed=time.perf_counter()-start;cpu=time.process_time()-cpu;a.close();assert a.wait_closed(2)
    s=a.snapshot();out=dict(scope='one second real queue/owner, prevalidated immutable repeated token, empty backend; cProfile overhead included, no DSP/GUI/network/device',accepted=accepted,elapsed_s=elapsed,cpu_s=cpu,snapshot=s);Path('MICRO_OWNER_BASELINE.yaml').write_text(store.dumps(out,indent=2),encoding='utf8');print(json.dumps({k:v for k,v in out.items() if k!='snapshot'}));print(s['backend']['profile'])
if __name__=='__main__':run()
