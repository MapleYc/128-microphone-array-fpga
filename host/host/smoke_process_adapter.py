"""Bounded no-device smoke. --real uses unmodified full production DSP."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import argparse,json,time
from pathlib import Path
from process_adapter import ProcessRawAdapter

def fake():
    a=ProcessRawAdapter({},lock_deg=0,capacity=64,test_worker_spec=dict(finish_ms=80),allow_test_worker=True)
    try:
        if not a.wait_ready(10):raise RuntimeError('not ready '+str(a.health()))
        data=b'M128'+b'\0'*1204
        accepted=sum(a.consume_datagram(data) for _ in range(64))
        deadline=time.monotonic()+5
        while a.health()['processed_packets']<accepted and time.monotonic()<deadline:time.sleep(.01)
        before=a.snapshot()
    finally:a.close();closed=a.wait_closed(10)
    final=a.snapshot()
    return dict(passed=accepted==64 and before['processed_packets']==64 and closed and final['native_closed_ack'] and final['admitted_packets']==0,
                scope='spawn test actor, shared credit/lifecycle only; no production DSP/native',accepted=accepted,before=before,final=final)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--real',action='store_true');ap.add_argument('--seconds',type=float,default=3);ap.add_argument('--out',default='PROCESS_SMOKE_RESULT.yaml');args=ap.parse_args()
    if args.real:
        import smoke_3d_paced as bench
        bench.RawPreviewAdapter=ProcessRawAdapter
        result=bench.measure(args.seconds,True)
        result['scope']='real full3D/Wiener/BP/LogMMSE/AGC in spawned process, paced direct source, null sink; no NIC/UI/audio/board'
    else:result=fake()
    Path(args.out).write_text(store.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(dict(passed=result['passed'],scope=result['scope'],final=result.get('final',{}).get('counts'),worker_pid=result.get('final',{}).get('worker_pid'),error=result.get('error'))))
    raise SystemExit(0 if result['passed'] else 1)
