"""Optional HOST diagnostic. CLI defaults to offline/null output, never board control."""
from __future__ import annotations
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import argparse,collections,hashlib,json,multiprocessing as mp,queue,socket,struct,threading,time
from pathlib import Path
import frozen
frozen.configure()
import numpy as np

FS=48000;HOP=512

def decode_words(raw):
    """Canonical sample-major signed24 LE -> channel-major float32; no gain fit."""
    if len(raw)%384:raise ValueError('incomplete canonical sample group')
    if not raw:return np.empty((128,0),dtype=np.float32)
    # The original arithmetic signed24>>8 is exactly the signed upper16bits.
    # Stride over the discarded low byte without materializing several int32
    # arrays. This is an exact view/representation optimization, not rounding.
    w=np.ndarray((len(raw)//384,128),dtype='<i2',buffer=raw,offset=1,strides=(384,3))
    return (w.astype(np.float32)/np.float32(32768)).T

class Continuity:
    """No reorder concealment: forward gaps start a new contiguous segment."""
    def __init__(self,decoder=None):
        self.decoder=decoder or frozen.parser();self.last=None;self.segment=0;self.invalid=True
        self.counts=collections.Counter();self.events=collections.deque(maxlen=64)
    def accept(self,data,decoded=None):
        self.counts['datagrams']+=1
        if data[:4]==b'V128':self.counts['video_ignored']+=1;return None
        try:p=self.decoder(data) if decoded is None else decoded
        except ValueError as exc:
            self.counts['malformed']+=1;self.invalid=True;self.events.append(dict(reason='malformed',error=str(exc)));return None
        key=(p['epoch'],p['sequence'],p['frame_ids'][-1]);reason=None
        if self.last is not None:
            e,s,f=self.last
            if p['epoch']==e and p['sequence']==s:
                self.counts['duplicate']+=1;return None
            restart=p['epoch']==e and p['sequence']==0 and s>0 and p['frame_ids'][0]<=f
            if p['epoch']==e and p['sequence']<s and not restart:
                self.counts['late_reorder_dropped']+=1;return None
            if p['epoch']!=e:reason='epoch_change'
            elif restart:reason='possible_boot_reset_same_epoch'
            elif p['sequence']!=s+1 or p['frame_ids'][0]!=f+1:
                reason='sequence_or_frame_gap';self.counts['gaps']+=1
        else:reason='first_packet'
        self.last=key
        fatal=p.get('fatal_flags',p['flags']&63)
        if p.get('clipped',bool(p['flags']&64)):self.counts['clip_warning_packets']+=1
        if fatal:
            self.counts['flagged']+=1;self.invalid=True;self.events.append(dict(reason='invalid_flags',flags=p['flags']));return None
        if self.invalid:reason=reason or 'after_invalid_packet'
        reset=reason is not None
        if reset:
            self.segment+=1;self.events.append(dict(reason=reason,segment=self.segment,epoch=p['epoch'],sequence=p['sequence'],first_frame=p['frame_ids'][0]));self.counts['resets']+=1
        self.invalid=False;self.counts['accepted_packets']+=1;self.counts['accepted_samples']+=3
        return dict(packet=p,reset=reset,segment=self.segment)

def post_worker(inq,outq,ctl,cfg):
    try:
        frozen.configure()
        from realtime_post_stream import PostStream
        post=PostStream(frozen.post_library(),cfg);segment=None;expected=0
        ctl.put(('ready',None))
        while True:
            msg=inq.get();kind=msg[0]
            if kind=='stop':break
            if kind=='reset':
                post.reset();segment=msg[1];expected=0;outq.put(('reset',segment));continue
            _,seg,num,payload=msg
            if seg!=segment or num!=expected:raise RuntimeError('post segment/frame sequence')
            x=np.frombuffer(payload,dtype='<f4');start=time.perf_counter_ns();events=list(post.feed(x));end=time.perf_counter_ns()
            if len(events)!=1:raise RuntimeError('post block contract')
            outq.put(('audio',seg,num,events[0]['final'].astype('<f4').tobytes(),(end-start)/1e6));expected+=1
        post.close();ctl.put(('done',None))
    except BaseException as exc:ctl.put(('error',type(exc).__name__+': '+str(exc)))

class PostIPC:
    def __init__(self,cfg,capacity=4):
        ctx=mp.get_context('spawn');self.i=ctx.Queue(capacity);self.o=ctx.Queue(capacity);self.c=ctx.Queue(4)
        self.p=ctx.Process(target=post_worker,args=(self.i,self.o,self.c,cfg),name='preview-original-post');self.p.start()
        kind,msg=self.c.get(timeout=15)
        if kind!='ready':raise RuntimeError('post start: '+str(msg))
        self.sent=0;self.received=0;self.peak_pending=0;self.post_ms=collections.deque(maxlen=8192);self.discarded_on_reset=0
    def control(self):
        try:
            kind,msg=self.c.get_nowait()
            if kind=='error':raise RuntimeError('post child: '+msg)
        except queue.Empty:pass
        if not self.p.is_alive():raise RuntimeError('post worker stopped unexpectedly')
    def send(self,segment,num,arr):
        self.control()
        try:self.i.put_nowait(('audio',segment,num,arr.astype('<f4').tobytes()))
        except queue.Full:raise RuntimeError('post input queue full; no frame dropped silently')
        self.sent+=1;self.peak_pending=max(self.peak_pending,self.sent-self.received)
    def poll(self):
        self.control();out=[]
        while True:
            try:m=self.o.get_nowait()
            except queue.Empty:break
            if m[0]=='audio':self.received+=1;self.post_ms.append(m[4]);out.append(m)
        return out
    def reset(self,segment):
        # Drain all earlier segment output before changing state. No in-flight
        # old audio may be mistaken for the new epoch.
        deadline=time.monotonic()+5
        while self.received<self.sent:
            self.discarded_on_reset+=len(self.poll())
            if time.monotonic()>deadline:raise RuntimeError('post reset drain timeout')
            time.sleep(.001)
        self.i.put(('reset',segment),timeout=2)
        while True:
            m=self.o.get(timeout=max(.01,deadline-time.monotonic()))
            if m==('reset',segment):break
            raise RuntimeError('unexpected post reset reply')
    def close(self):
        if self.p.is_alive():
            try:self.i.put(('stop',),timeout=2)
            except queue.Full:pass
            self.p.join(3)
        if self.p.is_alive():self.p.terminate();self.p.join(2)
        for q in (self.i,self.o,self.c):q.close();q.cancel_join_thread()

class JitterBuffer:
    """Finite, fixed-prefill 512-sample blocks. Overflow is an explicit failure."""
    def __init__(self,capacity=12,prefill=4):
        if not 1<=prefill<=capacity:raise ValueError('jitter bounds')
        self.capacity=capacity;self.prefill=prefill;self.q=collections.deque();self.started=False;self.counts=collections.Counter()
    def put(self,item):
        if len(self.q)>=self.capacity:self.counts['overflow']+=1;raise RuntimeError('jitter queue full')
        self.q.append(item);self.counts['peak_blocks']=max(self.counts['peak_blocks'],len(self.q))
        if len(self.q)>=self.prefill:self.started=True
    def tick(self):
        if not self.started:return None
        if not self.q:self.counts['underflow']+=1;raise RuntimeError('jitter queue underflow; no zero concealment')
        self.counts['consumed_blocks']+=1;return self.q.popleft()
    def reset(self):self.counts['discarded_on_reset']+=len(self.q);self.q.clear();self.started=False

class NullSink:
    """Unpaced numerical sink, not a soundcard or real-time timing claim."""
    def __init__(self):self.h=hashlib.sha256();self.samples=0;self.clipped=0
    def feed(self,a):self.h.update(a.astype('<f4').tobytes());self.samples+=len(a);self.clipped+=int(np.count_nonzero(abs(a)>1))
    def snapshot(self):return dict(samples=self.samples,float_sha256=self.h.hexdigest(),outside_unit_range=self.clipped,scope='unpaced null sink; no playback deadline tested')

class Pipeline:
    def __init__(self,cfg,*,lock_deg,lock_elevation_deg=0.,decoder=None,observer=None):
        from runtime3d import AtomicSnapshotSpatialStream
        self.cfg=json.loads(json.dumps(cfg));self.cfg['directions']['A']['azimuth_deg']=-float(lock_deg)
        self.cfg['directions']['A']['elevation_deg']=-float(lock_elevation_deg);self.lock_deg=float(lock_deg);self.lock_elevation_deg=float(lock_elevation_deg)
        self.stream=AtomicSnapshotSpatialStream(self.cfg,include_gain=observer is not None);self.post=PostIPC(self.cfg);self.cont=Continuity(decoder);self.segment=0;self.frame=0
        self.phase_max_ms=collections.defaultdict(float)
        self.parts=[];self.part_samples=0;self.counts=collections.Counter();self.spatial_hash=hashlib.sha256();self.spatial_ms=collections.deque(maxlen=8192);self.sink=NullSink();self.jitter=JitterBuffer(12,4);self.active_frames=0;self.observer=observer;self.output_frame=0;self.segment_active=False
    def consume_packet(self,data,*,decoded=None):
        """Single DSP-owner API for a shared UDP receiver's M128 dispatch.

        May perform DSP; enqueue from the network/GUI thread into a finite inbox
        first. Never bind a second unicast listener alongside the video GUI.
        """
        return self.receive(data,decoded=decoded)
    def receive(self,data,decoded=None):
        # Internal trusted transport reuse only: BoardLock already applied
        # the same strict decoder, and the packet view is immutable. All
        # epoch/sequence/fatal-flag state checks still execute here.
        item=self.cont.accept(data,decoded=decoded)
        if item is None:
            if self.cont.invalid and self.segment:self._invalidate()
            return
        if item['reset']:
            self._invalidate();self.segment=item['segment'];self.post.reset(self.segment);self.frame=0;self.output_frame=0;self.segment_active=True
        # Aggregate only already-received samples, never future packets. 192
        # groups (=64 datagrams) bounds Python dispatch overhead and latency.
        self.parts.append(item['packet']['pcm24le']);self.part_samples+=3
        if self.part_samples>=192:
            self._consume()
            # Transport/IPC scheduling only: poll after the already-received
            # 192-group batch (4ms), rather than at every 3-group UDP packet.
            # Empty-inbox idle still polls. DSP state and samples are unchanged.
            self._collect()
    def _invalidate(self):
        if not self.segment_active:return
        # Earlier accepted groups are valid; commit any complete windows before
        # isolating the broken segment. The incomplete input/tail is counted.
        self._consume();status=self.stream.stats()
        self.counts['discarded_pending_window_history_samples']+=status['input_consumed_total_samples']-status['next_frame_start_sample']
        self.parts.clear();self.part_samples=0;self.stream.reset();self.jitter.reset();self.counts['dsp_resets']+=1;self.segment_active=False
    def _consume(self):
        if not self.parts:return
        phase_t=time.perf_counter_ns();raw=b''.join(self.parts);self.parts.clear();self.part_samples=0;x=decode_words(raw);self.phase_max_ms['decode']=max(self.phase_max_ms['decode'],(time.perf_counter_ns()-phase_t)/1e6)
        it=self.stream.push_iter(x)
        while True:
            start=time.perf_counter_ns()
            try:event=next(it)
            except StopIteration:break
            self.spatial_ms.append((time.perf_counter_ns()-start)/1e6)
            a=event['samples'];self.active_frames+=int(event['audit']['application_active']);self.spatial_hash.update(a.astype('<f4').tobytes())
            if self.observer is not None:self.observer(event)
            phase_t=time.perf_counter_ns();self.post.send(self.segment,self.frame,a);self.phase_max_ms['post_send']=max(self.phase_max_ms['post_send'],(time.perf_counter_ns()-phase_t)/1e6);self.frame+=1;self.counts['spatial_frames']+=1
            self._collect()
    def _collect(self):
        phase_t=time.perf_counter_ns();ready=self.post.poll();self.phase_max_ms['post_poll']=max(self.phase_max_ms['post_poll'],(time.perf_counter_ns()-phase_t)/1e6)
        for _,seg,num,payload,ms in ready:
            if seg!=self.segment:self.counts['old_segment_output_discarded']+=1;continue
            if num!=self.output_frame:raise RuntimeError('post output frame sequence')
            self.output_frame+=1;self.jitter.put(payload);self.counts['post_frames']+=1
            # Numerical null replay drains one block per arrival after prefill.
            # This exercises order/capacity, not soundcard scheduling.
            got=self.jitter.tick()
            if got is not None:self.sink.feed(np.frombuffer(got,dtype='<f4'))
    def finish(self):
        self._consume();self.stream.finish();deadline=time.monotonic()+10
        while self.post.received<self.post.sent:
            self._collect()
            if time.monotonic()>deadline:raise RuntimeError('post final drain timeout')
            time.sleep(.001)
        # Drain already-produced valid blocks only; no DSP/WOLA/LogMMSE flush.
        self.jitter.started=True
        while self.jitter.q:self.sink.feed(np.frombuffer(self.jitter.tick(),dtype='<f4'))
        return dict(lock_physical_deg=self.lock_deg,nnls=False,counts=dict(self.counts),continuity=dict(self.cont.counts),recent_events=list(self.cont.events),spatial_sha256=self.spatial_hash.hexdigest(),active_frames=self.active_frames,sink=self.sink.snapshot(),jitter=dict(self.jitter.counts,capacity_blocks=self.jitter.capacity,prefill_blocks=self.jitter.prefill,scope='unpaced numerical drain; no playback clock'),post_sent=self.post.sent,post_received=self.post.received,post_discarded_at_reset=self.post.discarded_on_reset,post_max_pending=self.post.peak_pending,spatial_ms=stats(self.spatial_ms),post_ms=stats(self.post.post_ms),limits='offline diagnostic null output; no network/audio hardware/full product yes-no integration')
    def close(self):self.stream.shutdown();self.stream.wait_shutdown(3);self.post.close()

def stats(values):
    if not values:return dict(count=0)
    return dict(count=len(values),p50=float(np.percentile(values,50)),p95=float(np.percentile(values,95)),max=max(values))

def datagrams_file(path):
    """Little-endian uint32 length + raw datagram; no full file read-ahead."""
    with Path(path).open('rb') as f:
        while True:
            h=f.read(4)
            if not h:return
            if len(h)!=4:raise ValueError('truncated packet record header')
            n=struct.unpack('<I',h)[0]
            if not 4<=n<=65507:raise ValueError('packet record length')
            b=f.read(n)
            if len(b)!=n:raise ValueError('truncated packet record')
            yield b

def udp_datagrams(bind,port,seconds,capacity=512):
    """Opt-in only. Reader queue finite; overflow fails, never silently skips."""
    q=queue.Queue(capacity);stop=threading.Event();failure=[]
    def read():
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                sock.bind((bind,port));sock.settimeout(.2)
                while not stop.is_set():
                    try:b=sock.recv(65535)
                    except socket.timeout:continue
                    try:q.put_nowait(b)
                    except queue.Full:raise RuntimeError('UDP receive queue overflow')
        except BaseException as exc:failure.append(str(exc));stop.set()
    t=threading.Thread(target=read,name='preview-udp',daemon=True);t.start();end=time.monotonic()+seconds
    try:
        while time.monotonic()<end:
            if failure:raise RuntimeError(failure[0])
            try:yield q.get(timeout=.2)
            except queue.Empty:continue
    finally:stop.set();t.join(1)

def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='mode',required=True)
    p=sub.add_parser('replay');p.add_argument('--packets',type=Path,required=True)
    p=sub.add_parser('udp');p.add_argument('--bind',default='0.0.0.0');p.add_argument('--port',type=int,default=5001);p.add_argument('--seconds',type=float,default=10);p.add_argument('--enable-network',action='store_true')
    for p in sub.choices.values():
        p.add_argument('--config',type=Path,required=True);p.add_argument('--lock-deg',type=float,required=True);p.add_argument('--out',type=Path,required=True)
    a=ap.parse_args()
    if a.out.exists():raise FileExistsError('output must be new')
    if not -80<=a.lock_deg<=80:raise ValueError('diagnostic lock must be -80..80 deg')
    if a.mode=='udp' and not a.enable_network:raise ValueError('network disabled; explicit --enable-network required')
    if a.mode=='udp' and not 0<a.seconds<=60:raise ValueError('UDP diagnostic duration must be 0..60 seconds')
    versions=frozen.verify();cfg=store.loads(a.config.read_text(encoding='utf-8-sig'));a.out.mkdir(parents=True,exist_ok=False);pipe=None
    try:
        pipe=Pipeline(cfg,lock_deg=a.lock_deg)
        source=datagrams_file(a.packets) if a.mode=='replay' else udp_datagrams(a.bind,a.port,a.seconds)
        for b in source:pipe.receive(b)
        result=pipe.finish();result['versions']=versions;result['mode']=a.mode;result['status']='ok'
        (a.out/'RESULT.yaml').write_text(store.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
        print(json.dumps(result,indent=2))
    except BaseException as exc:
        failed=dict(status='failed',error=type(exc).__name__+': '+str(exc),versions=versions,mode=a.mode,counts={} if pipe is None else dict(pipe.counts),continuity={} if pipe is None else dict(pipe.cont.counts))
        (a.out/'RESULT.yaml').write_text(store.dumps(failed,indent=2)+'\n',encoding='utf-8',newline='\n');raise
    finally:
        if pipe is not None:pipe.close()

if __name__=='__main__':mp.freeze_support();main()
