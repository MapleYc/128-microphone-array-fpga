"""Incremental 48 HOST post chain, fed already-produced mono spatial frames.

Source packet ownership stays with caller until the returned generator is
exhausted. Each yielded event is delivered before the next input portion is
read; a caller that abandons a generator must close it before continuing.
"""
from __future__ import annotations
import ctypes,json,os
from pathlib import Path
import numpy as np

BLOCK=512
_F=ctypes.POINTER(ctypes.c_float)

class PostStream:
    def __init__(self,dll_path,config):
        self._dirs=[]
        for path in os.environ.get('MICARRAY_DLL_DIRS','').split(os.pathsep):
            if os.path.isdir(path):self._dirs.append(os.add_dll_directory(path))
        self.lib=ctypes.CDLL(str(Path(dll_path).resolve()))
        self.lib.stream_post_error.restype=ctypes.c_char_p
        self.lib.stream_post_create.argtypes=[ctypes.c_char_p];self.lib.stream_post_create.restype=ctypes.c_void_p
        self.lib.stream_post_process.argtypes=[ctypes.c_void_p,_F,ctypes.c_int,_F,_F,_F,ctypes.c_int];self.lib.stream_post_process.restype=ctypes.c_int
        self.lib.stream_post_destroy.argtypes=[ctypes.c_void_p];self.lib.stream_post_destroy.restype=None
        self._config=json.loads(json.dumps(config,allow_nan=False));self._encoded=json.dumps(self._config,allow_nan=False,separators=(',',':')).encode('utf-8')
        self._partial=np.empty(BLOCK,dtype=np.float32);self._busy=False;self._closed=False;self._failed=False;self._handle=None
        self._new_state()
    def _new_state(self):
        self._handle=self.lib.stream_post_create(self._encoded)
        if not self._handle:raise ValueError(self.lib.stream_post_error().decode('utf-8',errors='replace'))
        self._n=0;self._consumed=0;self._output=0;self._frames=0;self._closed=False;self._failed=False
    @staticmethod
    def _ptr(x):return x.ctypes.data_as(_F)
    def _block(self,n,last):
        band=np.empty(n,dtype=np.float32);log=np.empty(n,dtype=np.float32);out=np.empty(n,dtype=np.float32)
        args=(self._handle,self._ptr(self._partial),n,self._ptr(band),self._ptr(log),self._ptr(out),int(last))
        if self.lib.stream_post_process(*args)!=0:raise RuntimeError(self.lib.stream_post_error().decode('utf-8',errors='replace'))
        start=self._output;self._output+=n;self._frames+=int(n>0)
        # This is the index delay of original LogMMSE's initialized output
        # queue, not its frequency-dependent bandpass phase or ADC latency.
        lag=int(self._config['logmmse']['fft_size']) if self._config['logmmse']['enabled'] else 0
        return dict(output_start=start,output_end=self._output,available_after_input=self._consumed,
                    alignment_input_start=start-lag,initial_zero_queue_samples=lag,
                    final_partial=bool(last),bandpass=band,logmmse=log,final=out)
    def feed(self,packet):
        if self._closed or self._failed:raise RuntimeError('feed after finish or failure; reset first')
        if self._busy:raise RuntimeError('exhaust or close prior feed generator')
        if not isinstance(packet,np.ndarray) or packet.ndim!=1 or packet.dtype!=np.float32 or not packet.flags.c_contiguous:
            raise TypeError('packet must be contiguous 1D float32 numpy array')
        def produce():
            if self._closed or self._failed:raise RuntimeError('feed after finish or failure; reset first')
            if self._busy:raise RuntimeError('exhaust or close prior feed generator')
            self._busy=True
            try:
                i=0
                while i<len(packet):
                    n=min(BLOCK-self._n,len(packet)-i);piece=packet[i:i+n]
                    # Inspect only data about to be consumed, not future packet.
                    if not np.isfinite(piece).all():raise ValueError('nonfinite input')
                    self._partial[self._n:self._n+n]=piece;self._n+=n;i+=n;self._consumed+=n
                    if self._n==BLOCK:
                        event=self._block(BLOCK,False);self._n=0
                        yield event
            except Exception:
                # A process error can occur after the native state advanced;
                # resuming would make continuity unknowable. Require reset.
                self._failed=True
                raise
            finally:self._busy=False
        return produce()
    def finish(self):
        if self._busy:raise RuntimeError('exhaust or close prior feed generator')
        if self._closed or self._failed:raise RuntimeError('finish after close or failure; reset first')
        try:event=self._block(self._n,True)
        except Exception:
            self._failed=True
            raise
        self._n=0;self._closed=True
        return event if event['output_end']>event['output_start'] else None
    def reset(self):
        if self._busy:raise RuntimeError('exhaust or close prior feed generator')
        if self._handle:self.lib.stream_post_destroy(self._handle)
        self._handle=None;self._new_state()
    def close(self):
        if self._busy:raise RuntimeError('exhaust or close prior feed generator')
        if self._handle:self.lib.stream_post_destroy(self._handle);self._handle=None
        self._closed=True
    def status(self):
        return dict(consumed_samples=self._consumed,emitted_samples=self._output,pending_samples=self._n,
                    delivered_blocks=self._frames,internal_packet_buffer_bytes=self._partial.nbytes,closed=self._closed,failed=self._failed)
