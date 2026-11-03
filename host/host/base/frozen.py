"""Pinned original DSP imports. No input generation, network or audio on import."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import hashlib,json,os,sys
import native_paths
from pathlib import Path

ROOT=Path(str(__import__('pathlib').Path(__file__).resolve().parent / '../../dsp'))
SNAP=ROOT/'snapshot_atomic_20261030T120822Z_85680d12'
ACCEL=ROOT/'spatial_frame_accel_20261030T110547Z_004312b0'
REUSE=ROOT/'calibrate_reuse_20261030T114649Z_e151ee9d'
POST=ROOT/'post_stream_20261030T110500Z_f6d9a225'
PARSER=Path(str(__import__('pathlib').Path(__file__).resolve().parent / '../../dsp/protocol/raw128_receiver.py'))
PARSER_SHA='76d0e8bb778c6f8f24cbd702139047f8982c7d10766c189952db3f5c07af9896'
FREEZE_SHA='d69a64b2e517714b49eeed4d7c12358671630464abd18749d278bd211918f6b6'
POST_PY_SHA='95ba0e265dd074c3c75df86fee2a5f1a9813501c1d53d862611d57fc14f0773e'
DLL_DIRS=[]

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def configure():
    global DLL_DIRS
    DLL_DIRS=native_paths.configure_runtime()
    for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[k]='1'
    os.environ['MICARRAY_DLL_DIRS']=os.pathsep.join(DLL_DIRS)
    os.environ['PATH']=os.pathsep.join(DLL_DIRS)+os.pathsep+os.environ.get('PATH','')
    sys.path[:0]=[str(SNAP/'scripts'),str(REUSE/'scripts'),str(ACCEL/'scripts'),str(POST)]

def verify():
    if sha(SNAP/'FREEZE.yaml')!=FREEZE_SHA:raise RuntimeError('snapshot FREEZE mismatch')
    x=store.loads((SNAP/'FREEZE.yaml').read_text(encoding='utf-8-sig'))
    for key in ('source_sha256','external_sha256'):
        for p,h in x[key].items():
            p=Path(p);p=p if p.is_absolute() else SNAP/p
            if sha(p)!=h:raise RuntimeError('frozen source changed: '+str(p))
    if sha(POST/'realtime_post_stream.py')!=POST_PY_SHA:raise RuntimeError('post interface source changed')
    _,native_record=native_paths.verify_native()
    if not PARSER.is_file() or sha(PARSER)!=PARSER_SHA:raise RuntimeError('M128 parser frozen dependency missing/changed')
    return dict(snapshot_freeze=FREEZE_SHA,post_dll=native_record['artifacts']['bin/libstream_post.dll'],parser_path=str(PARSER),parser_sha256=sha(PARSER))

def parser():
    import importlib.util
    spec=importlib.util.spec_from_file_location('preview_pinned_m128',PARSER)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod.decode_packet

post_library=native_paths.post_library
