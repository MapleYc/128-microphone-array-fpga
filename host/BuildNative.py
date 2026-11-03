"""Build both native libraries in an external directory and bind them to source."""
from pathlib import Path
import argparse,os,shutil,subprocess,sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'host'))
import native_paths
import structured_store as store

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path)
    parser.add_argument('--cmake',default='cmake')
    parser.add_argument('--generator',default=os.environ.get('CMAKE_GENERATOR') or 'Ninja')
    parser.add_argument('--cxx',default=os.environ.get('CXX') or 'g++')
    args=parser.parse_args()
    if args.build_dir:os.environ['HOST_NATIVE_BUILD_DIR']=str(args.build_dir.resolve())
    destination=native_paths.build_directory()
    destination.mkdir(parents=True,exist_ok=True)
    cmake=shutil.which(args.cmake) or (str(Path(args.cmake)) if Path(args.cmake).is_file() else None)
    cxx=shutil.which(args.cxx) or (str(Path(args.cxx)) if Path(args.cxx).is_file() else None)
    if not cmake or not cxx:parser.error('CMake and the Qt-compatible C++ compiler must be on PATH, or supplied with --cmake/--cxx')
    subprocess.run([cmake,'-S',str(ROOT/'native'),'-B',str(destination),'-G',args.generator,'-DCMAKE_BUILD_TYPE=Release','-DCMAKE_CXX_COMPILER='+str(cxx)],check=True)
    subprocess.run([cmake,'--build',str(destination),'--config','Release','--parallel','2'],check=True)
    runtime=[]
    roots=os.environ.get('CMAKE_PREFIX_PATH','').split(os.pathsep)
    for prefix in roots:
        if prefix and (Path(prefix)/'bin').is_dir():runtime.append(str((Path(prefix)/'bin').resolve()))
    qt_dir=os.environ.get('Qt6_DIR')
    if qt_dir:
        q=Path(qt_dir).resolve().parents[2]/'bin'
        if q.is_dir():runtime.append(str(q))
    runtime.append(str(Path(cxx).resolve().parent))
    for p in os.environ.get('PATH','').split(os.pathsep):
        if p and (Path(p)/'Qt6Core.dll').is_file():runtime.append(str(Path(p).resolve()))
    artifacts={name:native_paths.sha(destination/name) for name in native_paths.ARTIFACTS}
    record=dict(format='array-host-native-build-v1',source_bindings=native_paths.source_bindings(),artifacts=artifacts,runtime_directories=list(dict.fromkeys(runtime)))
    store.dump_file(destination/'native_manifest.yaml',record)
    native_paths.verify_native()
    print('NATIVE_BUILD_PASS '+str(destination))
    if args.build_dir:print('Use this build with HOST_NATIVE_BUILD_DIR='+str(destination))

if __name__=='__main__':main()
