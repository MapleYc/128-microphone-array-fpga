"""Bounded parser/control microprofile. No DSP, network, GUI or devices."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import collections,cProfile,io,json,pstats,struct,sys,time,threading
from pathlib import Path
from types import MappingProxyType
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from monitor.raw128_receiver import Raw128Receiver,decode_packet
from board_follow import BoardLockAdapter
from legacy_adapter import ValidatedDatagram

H=struct.Struct('>4sBBBBIHHQII');PCM=bytes(384)
def packet(seq):return H.pack(b'M128',1,32,1,128,48000,3,392,seq,1,64)+b''.join(struct.pack('>Q',seq*3+j)+PCM for j in range(3))

class Child:
    def __init__(self,*a,**kw):self.closed=False;self.calls=0
    def wait_ready(self,t):return not self.closed
    def consume_validated_datagram(self,p):self.calls+=1;return True
    def snapshot(self):return dict(state='ready',calls=self.calls)
    def health(self):return self.snapshot()
    def close(self):self.closed=True
    def wait_closed(self,t):return self.closed

def setup():
    b=BoardLockAdapter({},child_factory=Child,allow_test_factory=True,status_timeout_s=2.)
    b.consume_datagram(packet(0));v=[0]*24;v[0]=1
    b.consume_uart_message((1,0,3,7,struct.pack('<24I',*v)))
    until=time.perf_counter()+2
    while b.child is None and time.perf_counter()<until:time.sleep(.001)
    if b.child is None:raise AssertionError('micro child startup')
    r=Raw128Receiver();r.feed(packet(0))
    return r,b

def run():
    packets=[packet(i) for i in range(1,16001)];rows=[]
    for mode in ('decode_only','receiver_only','board_only','combined'):
        timings=[];cpu=[]
        for rep in range(3):
            r,b=setup();start=time.perf_counter_ns();start_cpu=time.process_time_ns()
            for data in packets:
                if mode=='decode_only':decode_packet(data)
                elif mode=='receiver_only':r.feed(data)
                elif mode=='board_only':b.consume_datagram(data)
                else:r.feed(data);b.consume_datagram(data)
            cpu.append((time.process_time_ns()-start_cpu)/1e6);timings.append((time.perf_counter_ns()-start)/1e6);b.close();assert b.wait_closed(2)
        rows.append(dict(mode=mode,packets=len(packets),wall_ms=timings,cpu_ms=cpu,wall_us_per_packet=sum(timings)/3/len(packets)*1000))
    r,b=setup();pr=cProfile.Profile();pr.enable()
    for data in packets:r.feed(data);b.consume_datagram(data)
    pr.disable();b.close();assert b.wait_closed(2)
    out=io.StringIO();pstats.Stats(pr,stream=out).strip_dirs().sort_stats('tottime').print_stats(18)
    result=dict(scope='single-process parser/control only, mock child accepts token; no actual DSP/queue cost included',rows=rows,profile=out.getvalue())
    (HERE/'MICRO_BASELINE.yaml').write_text(store.dumps(result,indent=2),encoding='utf8');print(json.dumps(rows));print(out.getvalue())
if __name__=='__main__':run()
