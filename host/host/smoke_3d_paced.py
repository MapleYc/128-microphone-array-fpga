"""No-device complete 3D HOST chain timing smoke; source is synthetic tones.

No sockets, serial, GUI or soundcard. Original BP/LogMMSE/AGC DLL+IPC runs.
M128 packets are numerically generated in RAM, 3 groups/packet, 48k groups/s.
The result is a paced desktop software test, not acoustic or FPGA acceptance.
"""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import sys,time,json,struct,math,argparse
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'base'))
import frozen
frozen.configure()
import numpy as np
from legacy_adapter import RawPreviewAdapter
import sprint_blocked_power as old

def unit(az,el):
    a=math.radians(az);e=math.radians(el)
    return np.array([math.sin(a)*math.cos(e),math.sin(e),math.cos(a)*math.cos(e)])

def numerical_source_pcm():
    p=old.geom.geometry();t=np.arange(48000)/48000.;mixed=np.zeros((128,48000),dtype=np.float64)
    # Integer-Hz components form a 1s periodic fixture; no discontinuity on wrap.
    # Source labels stay in this generator, never in the DUT constructor/gates.
    rng=np.random.default_rng(202611011301)
    for bearing,gain,freq in [((-8.,-14.),.004,np.array([823,1009,1279,1499,1777,2011,2281,2521,2791,3001,3299,3571])),((35.,20.),.010,np.array([887,1103,1327,1567,1831,2081,2347,2617,2879,3109,3371,3637]))]:
        incoming=-(p@unit(*bearing))/343.;phases=rng.uniform(-np.pi,np.pi,len(freq))
        for f,phase in zip(freq,phases):mixed+=gain*np.sin(2*np.pi*f*(t[None,:]-incoming[:,None])+phase)
    words=np.clip(np.rint(mixed.T*(1<<23)),-(1<<23),(1<<23)-1).astype(np.int32)
    # Known bad channels do not enter actual126ch beam/DOA.
    words[:,2]=(1<<23)-1;words[:,3]=-(1<<23)
    raw=np.empty((48000,128,3),dtype=np.uint8)
    raw[:,:,0]=words&255;raw[:,:,1]=(words>>8)&255;raw[:,:,2]=(words>>16)&255
    return raw.reshape(16000,3,384).copy()

HEADER=struct.Struct('>4sBBBBIHHQII')
def packet(seq,pcm):
    first=seq*3;data=bytearray(HEADER.pack(b'M128',1,32,1,128,48000,3,392,seq,1,64))
    for j in range(3):data+=struct.pack('>Q',first+j)+pcm[j].tobytes()
    return bytes(data)

def measure(seconds,paced):
    pcm=numerical_source_pcm();cfg=store.loads((HERE/'fixtures/P01.yaml').read_text(encoding='utf-8-sig'))
    adapter=RawPreviewAdapter(cfg,lock_deg=-8.,lock_elevation_deg=-14.,capacity=1024,enable_playback=False)
    begin_ready=time.perf_counter();trace=[];sent=0;accepted=0;failed=None
    try:
        if not adapter.wait_ready(20):raise RuntimeError('adapter initialization failed '+str(adapter.health()))
        start=time.perf_counter();last=start;packets=int(round(seconds*16000));burst=16
        while sent<packets:
            if paced:
                due=start+sent/16000.
                left=due-time.perf_counter()
                if left>0:time.sleep(left)
            for _ in range(min(burst,packets-sent)):
                accepted+=bool(adapter.consume_datagram(packet(sent,pcm[sent%16000])));sent+=1
            now=time.perf_counter()
            if now-last>=.5:
                snap=adapter.snapshot();trace.append(dict(elapsed_s=now-start,sent=sent,accepted=accepted,health=adapter.health(),counts=snap.get('counts'),inbox_current=snap.get('inbox_current'),queue_age_ms=snap.get('queue_age_ms'),backend=snap.get('backend')));last=now
                if snap.get('state')=='failed':raise RuntimeError('adapter failed '+str(snap.get('error')))
        send_elapsed=time.perf_counter()-start
        deadline=time.perf_counter()+10
        while time.perf_counter()<deadline:
            snap=adapter.snapshot();counts=snap.get('counts',{});processed=counts.get('processed_packets',0)+counts.get('queued_old_generation_discarded',0)
            if snap.get('state')=='failed':raise RuntimeError('adapter failed '+str(snap.get('error')))
            if processed>=accepted:break
            time.sleep(.02)
        before_close=adapter.snapshot();finish_elapsed=time.perf_counter()-start
    except Exception as exc:
        failed=type(exc).__name__+': '+str(exc);before_close=adapter.snapshot();send_elapsed=None;finish_elapsed=None
    finally:
        adapter.close();closed=adapter.wait_closed(20)
    final=adapter.snapshot()
    counters=final.get('counts',{});no_loss=counters.get('inbox_overflow_packets',0)==0 and counters.get('queued_old_generation_discarded',0)==0 and accepted==sent and counters.get('processed_packets',0)==sent
    active=final.get('backend',{}).get('dynamic',{}).get('active_frames',0);nonzero=final.get('backend',{}).get('output',{}).get('nonzero_samples',0)
    passed=failed is None and closed and no_loss and active>0 and nonzero>0
    return dict(kind='paced_no_device' if paced else 'unpaced_no_device',passed=passed,no_loss=no_loss,source='1s periodic independent physical 3D synthetic multitones; not speech',seconds_requested=seconds,packets_sent=sent,packets_accepted=accepted,capacity_packets=1024,capacity_audio_ms=64.,burst_packets=16,burst_audio_ms=1.,playback=False,socket=False,serial=False,initialization_s=start-begin_ready if 'start' in locals() else None,send_elapsed_s=send_elapsed,drain_elapsed_s=finish_elapsed,error=failed,closed=closed,before_close=before_close,final=final,trace=trace)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--seconds',type=float,default=8.);ap.add_argument('--unpaced',action='store_true');ap.add_argument('--out',type=Path,default=HERE/'PACED_3D_RESULT.yaml');a=ap.parse_args()
    result=measure(a.seconds,not a.unpaced);a.out.write_text(store.dumps(result,indent=2,allow_nan=False),encoding='utf8')
    final=result['final'];backend=final.get('backend',{})
    print(json.dumps(dict(result=a.out.name,passed=result['passed'],no_loss=result['no_loss'],error=result['error'],sent=result['packets_sent'],accepted=result['packets_accepted'],send_elapsed_s=result['send_elapsed_s'],drain_elapsed_s=result['drain_elapsed_s'],counts=final.get('counts'),spatial_ms=backend.get('spatial_ms'),post_ms=backend.get('post_ms'),dynamic=backend.get('dynamic',{}).get('event_counts'),output=backend.get('output')),allow_nan=False),flush=True)
    raise SystemExit(0 if result['passed'] else 1)
