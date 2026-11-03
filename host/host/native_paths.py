"""Locate source-bound native libraries built outside the source checkout."""
from pathlib import Path
import hashlib,os,tempfile
import structured_store as store

ROOT=Path(__file__).resolve().parents[1]
ARTIFACTS=('bin/frame_kernel.dll','bin/libstream_post.dll')
_DLL_HANDLES=[]
_DLL_DIRS=set()
_VERIFIED_RUNTIME_DIRECTORY=None
_VERIFIED_RECORD=None
_VERIFIED_SELECTION=None
_CONFIGURED_RECORD=None

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def source_bindings():
    record=store.load_file(ROOT/'native_sources.yaml')
    if record.get('format')!='array-host-native-sources-v1':raise ValueError('Invalid native source manifest')
    expected=record['files']
    for name,digest in expected.items():
        if sha(ROOT/name)!=digest:raise ValueError('Native source changed: '+name)
    return expected

def build_directory():
    bindings=source_bindings()
    identity=hashlib.sha256('\n'.join(name+' '+bindings[name] for name in sorted(bindings)).encode('utf-8')).hexdigest()[:16]
    path=Path(os.environ.get('HOST_NATIVE_BUILD_DIR') or (Path(tempfile.gettempdir())/'array_host_native'/identity)).resolve()
    if path==ROOT or path.is_relative_to(ROOT):raise ValueError('Native build directory must be outside the source package')
    return path

def verify_native():
    global _VERIFIED_RUNTIME_DIRECTORY,_VERIFIED_RECORD,_VERIFIED_SELECTION
    _VERIFIED_RUNTIME_DIRECTORY=None
    _VERIFIED_RECORD=None
    _VERIFIED_SELECTION=None
    directory=build_directory();path=directory/'native_manifest.yaml'
    if not path.is_file():raise FileNotFoundError('Native libraries are not built. Run BuildNative.cmd; set HOST_NATIVE_BUILD_DIR when using a custom build directory.')
    record=store.load_file(path)
    if record.get('format')!='array-host-native-build-v1' or record.get('source_bindings')!=source_bindings():raise ValueError('Native build does not match this source package; rebuild it')
    if set(record.get('artifacts',{}))!=set(ARTIFACTS):raise ValueError('Incomplete native build manifest')
    for name in ARTIFACTS:
        if sha(directory/name)!=record['artifacts'][name]:raise ValueError('Native binary changed: '+name)
    _VERIFIED_RUNTIME_DIRECTORY=directory
    _VERIFIED_RECORD=record
    _VERIFIED_SELECTION=os.environ.get('HOST_NATIVE_BUILD_DIR')
    return directory,record

def _configure_verified(directory,record):
    global _CONFIGURED_RECORD
    candidates=[str(directory/'bin'),*record.get('runtime_directories',[]),*os.environ.get('HOST_DLL_DIRS','').split(os.pathsep)]
    paths=[]
    for raw in candidates:
        if not raw:continue
        p=str(Path(raw).resolve())
        if not Path(p).is_dir():continue
        if p not in paths:paths.append(p)
        if os.name=='nt' and p not in _DLL_DIRS:
            _DLL_HANDLES.append(os.add_dll_directory(p));_DLL_DIRS.add(p)
    os.environ['MICARRAY_DLL_DIRS']=os.pathsep.join(paths)
    os.environ['PATH']=os.pathsep.join(paths)+os.pathsep+os.environ.get('PATH','')
    _CONFIGURED_RECORD=record
    return paths

def configure_runtime():
    directory,record=verify_native()
    return _configure_verified(directory,record)

def _runtime_directory():
    if _VERIFIED_RUNTIME_DIRECTORY is None or _VERIFIED_SELECTION!=os.environ.get('HOST_NATIVE_BUILD_DIR'):
        configure_runtime()
    elif _CONFIGURED_RECORD is not _VERIFIED_RECORD:
        _configure_verified(_VERIFIED_RUNTIME_DIRECTORY,_VERIFIED_RECORD)
    return _VERIFIED_RUNTIME_DIRECTORY

def frame_library():
    return _runtime_directory()/'bin/frame_kernel.dll'

def post_library():
    return _runtime_directory()/'bin/libstream_post.dll'
