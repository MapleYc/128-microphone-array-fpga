"""Experimental compiled frame helper; frozen guarded DSP otherwise unchanged.

Import after adding the frozen v2 scripts directory to sys.path. C++ library
must be built from the adjacent source; no lookahead or prerecorded DOA data.
"""
import ctypes
import native_paths
from pathlib import Path
import types
import numpy as np
from spatial_frame_stream import SpatialFrameStream, N

_lib=ctypes.CDLL(str(native_paths.frame_library()))
_call=_lib.frame_kernel
_call.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,
                ctypes.c_void_p,ctypes.c_int,ctypes.c_int64,ctypes.c_void_p]
_call.restype=None

def candidate_frame(engine,start,physical):
    ii,cc=engine.beam.taps_for(physical)
    use=np.ascontiguousarray(engine.beam.use,dtype=np.int64)
    delay=np.ascontiguousarray(ii[use],dtype=np.int64)
    coeff=np.ascontiguousarray(cc[use],dtype=np.float64)
    # Each mic addresses a contiguous logical interval. Mirror old per-tap
    # future and overwritten-history checks, including negative startup indices.
    for tap in range(4):
        first=start-delay-tap+1
        last=first+N-1
        valid=last>=0
        if np.any(valid & (last>=engine.received)):
            raise RuntimeError('future sample requested')
        if np.any(valid & (np.maximum(first,0)<engine.received-engine.capacity)):
            raise RuntimeError('ring history overwritten')
    out=np.empty(N,dtype='<f4')
    _call(engine.ring.ctypes.data,engine.capacity,use.ctypes.data,delay.ctypes.data,
          coeff.ctypes.data,len(use),int(start),out.ctypes.data)
    while len(engine.beam.taps)>engine.taps_capacity:
        engine.beam.taps.pop(next(iter(engine.beam.taps)))
    return out

class AcceleratedSpatialFrameStream(SpatialFrameStream):
    def _make_engine(self):
        super()._make_engine()
        if self._nnls:
            raise ValueError('this experimental acceleration is NNLS-disabled only')
        self._engine._frame=types.MethodType(candidate_frame,self._engine)
