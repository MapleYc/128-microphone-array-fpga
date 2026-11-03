"""Bounded Windows waveOut PCM playback; no gain/filter/beamforming changes."""
import ctypes as C
import threading,time
class WaveOut:
    def __init__(self):
        from ctypes import wintypes as W
        class Format(C.Structure):
            _pack_=2
            _fields_=[('tag',W.WORD),('channels',W.WORD),('rate',W.DWORD),('bytes_sec',W.DWORD),('align',W.WORD),('bits',W.WORD),('extra',W.WORD)]
        class Header(C.Structure):
            _fields_=[('data',C.c_void_p),('length',W.DWORD),('recorded',W.DWORD),('user',C.c_size_t),('flags',W.DWORD),('loops',W.DWORD),('next',C.c_void_p),('reserved',C.c_size_t)]
        self.lib=C.WinDLL('winmm');self.handle=C.c_void_p();self.format=Format(1,1,16000,32000,2,16,0)
        self.lib.waveOutOpen.argtypes=[C.POINTER(C.c_void_p),W.UINT,C.POINTER(Format),C.c_size_t,C.c_size_t,W.DWORD]
        for name in ('waveOutPrepareHeader','waveOutUnprepareHeader','waveOutWrite'):
            getattr(self.lib,name).argtypes=[C.c_void_p,C.POINTER(Header),W.UINT]
        for name in ('waveOutReset','waveOutClose'):getattr(self.lib,name).argtypes=[C.c_void_p]
        self._check(self.lib.waveOutOpen(C.byref(self.handle),0xffffffff,C.byref(self.format),0,0,0))
        self.buffers=[C.create_string_buffer(1024) for _ in range(4)]
        self.headers=[Header(C.addressof(b),1024,0,0,0,0,None,0) for b in self.buffers]
        self.size=C.sizeof(Header);self.busy=[False]*4;self.prepared=[False]*4;self.data=bytearray();self.lock=threading.Lock();self.closed=False;self.dropped=0;self.error=None
        self.fed_samples=0;self.submitted_samples=0;self.completed_samples=0;self.last_completed=None
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
    @staticmethod
    def _check(code):
        if code:raise RuntimeError('waveOut error '+str(code))
    def feed(self,pcm):
        with self.lock:
            if self.closed or self.error:return
            if len(pcm)%2:raise ValueError('PCM16 byte count must be even')
            self.fed_samples+=len(pcm)//2
            if len(pcm)>16000:
                self.dropped+=(len(pcm)-16000)//2;pcm=pcm[-16000:]
            if len(self.data)+len(pcm)>16000:self.dropped+=len(self.data)//2;self.data.clear()
            self.data.extend(pcm)
    def snapshot(self):
        with self.lock:
            return dict(fed_samples=self.fed_samples,submitted_samples=self.submitted_samples,completed_samples=self.completed_samples,
                queued_samples=len(self.data)//2,dropped_samples=self.dropped,error=self.error,
                completion_age_seconds=None if self.last_completed is None else max(0,time.monotonic()-self.last_completed))
    def clear(self):
        with self.lock:self.data.clear()
    def _run(self):
        try:
            while not self.closed:
                for i,h in enumerate(self.headers):
                    if self.busy[i] and h.flags&1:
                        self._check(self.lib.waveOutUnprepareHeader(self.handle,C.byref(h),self.size));self.busy[i]=False;self.prepared[i]=False
                        with self.lock:self.completed_samples+=h.length//2;self.last_completed=time.monotonic()
                    if not self.busy[i]:
                        with self.lock:
                            chunk=bytes(self.data[:1024]) if len(self.data)>=1024 else None
                            if chunk is not None:del self.data[:1024]
                        if chunk is not None:
                            C.memmove(h.data,chunk,1024);h.flags=0
                            self._check(self.lib.waveOutPrepareHeader(self.handle,C.byref(h),self.size))
                            self.prepared[i]=True
                            self._check(self.lib.waveOutWrite(self.handle,C.byref(h),self.size));self.busy[i]=True
                            with self.lock:self.submitted_samples+=h.length//2
                time.sleep(.005)
        except Exception as e:self.error=str(e)
        finally:
            self.lib.waveOutReset(self.handle)
            for i,h in enumerate(self.headers):
                if self.prepared[i]:self.lib.waveOutUnprepareHeader(self.handle,C.byref(h),self.size)
            self.lib.waveOutClose(self.handle)
    def close(self):
        self.closed=True;self.thread.join(timeout=2)
