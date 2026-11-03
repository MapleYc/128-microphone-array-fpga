"""Read-only package, import and offline test checks. No device is opened."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
from pathlib import Path
import argparse, ast, hashlib, json, os, subprocess, sys

ROOT=Path(__file__).resolve().parent

def run(args,cwd=ROOT):
    result=subprocess.run([sys.executable,'-B',*map(str,args)],cwd=cwd)
    if result.returncode:raise SystemExit(result.returncode)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tests',action='store_true')
    parser.add_argument('--source-only',action='store_true',help='Check source package without importing unbuilt native libraries')
    args=parser.parse_args()
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    manifest=ROOT/'SHA256SUMS'
    if not manifest.is_file():raise SystemExit('Missing SHA256SUMS')
    checked=0
    for line in manifest.read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):continue
        expected,name=line.split('  ',1)
        actual=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        if actual!=expected:raise SystemExit('Package integrity mismatch: '+name)
        checked+=1
    print('PACKAGE_INTEGRITY_PASS',checked,flush=True)
    files=list(ROOT.rglob('*.py'))
    for path in files:ast.parse(path.read_text(encoding='utf-8-sig'),filename=str(path.relative_to(ROOT)))
    print('PYTHON_SYNTAX_PASS',len(files),flush=True)
    run(['StartHost.py','--validate-only'])
    if args.source_only:
        if args.tests:parser.error('--tests requires built native libraries; omit --source-only')
        print('SOURCE_ONLY_CHECKS_PASS_NO_NATIVE_IMPORT',flush=True);return
    run(['launcher.py','--preflight'],ROOT/'host')
    if args.tests:
        run(['-m','unittest','discover','-s','.','-p','test_*.py'],ROOT/'host')
        run(['-m','unittest','test_system_audio_acceptance.py'])
        run(['tests_3d_independent.py'],ROOT/'host')
    print('OFFLINE_CHECKS_PASS_NO_DEVICES',flush=True)

if __name__=='__main__':main()
