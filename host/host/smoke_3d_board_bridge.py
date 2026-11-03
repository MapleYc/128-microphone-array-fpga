"""No-device production BoardLockAdapter+DSP+original post paced validation.

Synthetic wire UART statuses are CRC parsed, RAW packets strict decoded once,
then immutable validated packets enter the bounded DSP owner. No socket/audio.
Cold model/DOA worker startup is separately measured before timed stable-lock
traffic; seq0 is an explicit transport establishment packet, not hidden loss.
"""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
from smoke_3d_paced import *
from board_follow import BoardLockAdapter
from test_board_follow import Parser,uart
import gc,threading,collections

def run(seconds):
    gc_rows=collections.deque(maxlen=128);gc_active={}
    def gc_audit(phase,info):
        k=threading.get_ident()
        if phase=='start':gc_active[k]=(time.perf_counter_ns(),info.get('generation'),threading.current_thread().name)
        elif k in gc_active:
            t,g,n=gc_active.pop(k);gc_rows.append(dict(ms=(time.perf_counter_ns()-t)/1e6,generation=g,thread=n))
    gc.callbacks.append(gc_audit)
    cfg=store.loads((HERE/'fixtures/P01.yaml').read_text(encoding='utf-8-sig'));pcm=numerical_source_pcm()
    bridge=BoardLockAdapter(cfg,capacity=1024,enable_playback=False);bridge.set_listen_mode(4)
    parser=Parser();status_seq=0;rows=[];child=None;error=None
    def status(frame):
        nonlocal status_seq
        wire=uart(az=-8,el=-14,frame=frame,epoch=1,seq=status_seq);status_seq+=1
        for message in parser.feed(wire):bridge.consume_uart_message(message)
        for d in parser.take_diagnostics():bridge.consume_uart_diagnostic(d)
    sent=0;accepted=0;start=None;before=None
    try:
        cold=time.perf_counter();bridge.consume_datagram(packet(0,pcm[0]));status(3)
        deadline=time.perf_counter()+20
        while time.perf_counter()<deadline:
            child=bridge.child
            if child is not None and child.wait_ready(0):break
            status(3);time.sleep(.01)
        else:raise RuntimeError('3D child startup '+str(bridge.health()))
        cold_s=time.perf_counter()-cold;start=time.perf_counter();last=start;next_status=start+.1
        packets=round(seconds*16000)
        while sent<packets:
            left=start+sent/16000.-time.perf_counter()
            if left>0:time.sleep(left)
            for _ in range(min(16,packets-sent)):
                seq=sent+1;accepted+=bool(bridge.consume_datagram(packet(seq,pcm[seq%16000])));sent+=1
            now=time.perf_counter()
            if now>=next_status:status((sent+1)*3);next_status=now+.1
            if now-last>=1.:
                h=child.health();rows.append(dict(elapsed_s=now-start,sent=sent,accepted=accepted,health=h));last=now
                if bridge.health()['sticky_failure']:raise RuntimeError('bridge sticky failure '+str(bridge.health()))
        send_s=time.perf_counter()-start;deadline=time.perf_counter()+5
        while time.perf_counter()<deadline:
            h=child.health()
            if h['processed_packets']==sent:break
            status((sent+1)*3);time.sleep(.01)
        before=child.snapshot();before_health=child.health();board=bridge.snapshot()
    except Exception as exc:
        error=type(exc).__name__+': '+str(exc);send_s=None;cold_s=locals().get('cold_s');before=None if child is None else child.snapshot();before_health=None if child is None else child.health();board=bridge.snapshot()
    finally:
        bridge.close();closed=bridge.wait_closed(20)
        gc.callbacks.remove(gc_audit)
    final=None if child is None else child.snapshot();backend={} if final is None else final.get('backend',{});counts={} if final is None else final['counts']
    passed=error is None and closed and sent==accepted==counts.get('processed_packets') and counts.get('inbox_overflow_packets',0)==0 and counts.get('queued_old_generation_discarded',0)==0 and backend.get('dynamic',{}).get('active_frames',0)>0 and backend.get('output',{}).get('nonzero_samples',0)>0
    result=dict(passed=passed,error=error,closed=closed,seconds=seconds,sent=sent,accepted=accepted,startup_s=cold_s,send_elapsed_s=send_s,scope='paced desktop synthetic 1s periodic multitones; real BoardLock adapter path; no GUI/network/soundcard/board',explicit_pre_timed_transport_packet=1,uart_status_packets=status_seq,uart_crc_errors=parser.crc_errors if hasattr(parser,'crc_errors') else None,capacity=1024,capacity_audio_ms=64,search_interval_hops=4,search_hz=23.4375,health=before_health,counts=counts,spatial_ms=backend.get('spatial_ms'),post_ms=backend.get('post_ms'),dynamic=backend.get('dynamic'),output=backend.get('output'),loaded_modules=backend.get('versions',{}).get('loaded_modules'),trace=rows,board_state=board.get('state'),board_counts=board.get('counts'))
    result['phase_max_ms']=backend.get('phase_max_ms');result['gc_events']=list(gc_rows)
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--seconds',type=float,default=30);ap.add_argument('--out',type=Path,default=HERE/'PACED_3D_BRIDGE_30S_V4_RESULT.yaml');a=ap.parse_args();r=run(a.seconds);a.out.write_text(store.dumps(r,indent=2,allow_nan=False),encoding='utf8');print(json.dumps({k:v for k,v in r.items() if k not in ('trace','loaded_modules','dynamic')},allow_nan=False));raise SystemExit(0 if r['passed'] else 1)
