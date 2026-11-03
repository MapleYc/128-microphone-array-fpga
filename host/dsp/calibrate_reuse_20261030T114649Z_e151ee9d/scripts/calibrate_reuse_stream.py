"""Instance-local synchronous model replacement; never monkeypatch a module."""
import types
from spatial_frame_accel import AcceleratedSpatialFrameStream
from calibrate_reuse import calibrate

def _model(engine,refs):
 key=tuple(round(float(x),4) for x in refs)
 if key not in engine.cache:
  engine.cache[key]=calibrate(list(key),engine.cfg,engine.beam)
  if len(engine.cache)>engine.cache_capacity:engine.cache.popitem(last=False)
 else:engine.cache.move_to_end(key)
 return engine.cache[key]

class ReuseSpatialFrameStream(AcceleratedSpatialFrameStream):
 def _make_engine(self):
  super()._make_engine();self._engine._model=types.MethodType(_model,self._engine)
