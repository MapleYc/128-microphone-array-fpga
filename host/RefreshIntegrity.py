"""Regenerate source checks after an intentional maintainer edit; never builds code."""
from pathlib import Path
import hashlib,os,re
import yaml
ROOT=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def text(p,s):p.write_text(s,encoding='utf-8',newline='\n')
def dump(p,v):text(p,yaml.safe_dump(v,allow_unicode=True,sort_keys=False))
def files(root):
    return [p for p in root.rglob('*') if p.is_file() and not any(x in p.parts for x in ('__pycache__','.git','runs','native_build','runtime')) and p.suffix.lower() not in ('.pyc','.pyo','.dll','.exe','.o','.obj') and p.name not in ('connection.yaml','camera_alignment_local.yaml','local_alignment.yaml')]
def records(paths,base):return {Path(os.path.relpath(p,base)).as_posix():sha(p) for p in sorted(paths)}

native=[ROOT/'native/CMakeLists.txt',ROOT/'BuildNative.py',ROOT/'dsp/spatial_frame_accel_20261030T110547Z_004312b0/scripts/frame.cpp',ROOT/'dsp/post_stream_20261030T110500Z_f6d9a225/CMakeLists.txt',ROOT/'dsp/post_stream_20261030T110500Z_f6d9a225/realtime_post_stream.cpp',*files(ROOT/'dsp/post_stream_20261030T110500Z_f6d9a225/src')]
dump(ROOT/'native_sources.yaml',dict(format='array-host-native-sources-v1',files=records(native,ROOT)))
geom=ROOT/'dsp/stage2_20261029T154710Z_63ffc32c/adapter/config/geometry_source.yaml'
p=ROOT/'dsp/leakage_20261030T024130Z_b762ad91/scripts/leakage_coefficients.py';s=p.read_text(encoding='utf-8');s=re.sub(r'GEOMETRY_SHA256\s*=\s*"[a-f0-9]{64}"','GEOMETRY_SHA256 = "'+sha(geom)+'"',s);text(p,s)
p=ROOT/'host/test_system_transport_metadata.py';s=p.read_text(encoding='utf-8');s=re.sub(r"REFERENCE_SHA='[a-f0-9]{64}'","REFERENCE_SHA='"+sha(ROOT/'host/regression_baselines/legacy_raw_transport/raw128_receiver.py')+"'",s);text(p,s)
snap=ROOT/'dsp/snapshot_atomic_20261030T120822Z_85680d12'
for directory in ['calibrate_reuse_20261030T114649Z_e151ee9d','spatial_frame_accel_20261030T110547Z_004312b0']:
    d=ROOT/'dsp'/directory;members=[p for p in files(d) if p.name!='FREEZE.yaml'];dump(d/'FREEZE.yaml',dict(format='source-integrity-v1',source_sha256=records(members,d)))
external=[p for p in files(ROOT/'dsp') if not p.is_relative_to(snap)]
dump(snap/'FREEZE.yaml',dict(format='source-integrity-v1',source_sha256=records(files(snap/'scripts'),snap),external_sha256=records(external,snap)))
post=ROOT/'dsp/post_stream_20261030T110500Z_f6d9a225'
p=ROOT/'host/base/frozen.py';s=p.read_text(encoding='utf-8')
for name,file in [('FREEZE_SHA',snap/'FREEZE.yaml'),('PARSER_SHA',ROOT/'dsp/protocol/raw128_receiver.py'),('POST_PY_SHA',post/'realtime_post_stream.py')]:s=re.sub(name+r"='[a-f0-9]{64}'",name+"='"+sha(file)+"'",s)
text(p,s)
for p in files(ROOT/'host'):
    if p.suffix!='.py' or 'vendor' in p.parts:continue
    s=p.read_text(encoding='utf-8');old=s
    for name in ['frozen.py','preview.py','playback48.py']:
        h=sha(ROOT/'host/base'/name);s=re.sub(r"('"+re.escape(name)+r"'\s*:\s*')[a-f0-9]{64}(')",lambda m:m[1]+h+m[2],s)
    if s!=old:text(p,s)
members=[p for p in files(ROOT/'host') if p.name not in ('MONITOR_HASHES.yaml','MANIFEST.yaml')]
dump(ROOT/'host/MONITOR_HASHES.yaml',records(members,ROOT/'host'))
members.append(ROOT/'host/MONITOR_HASHES.yaml')
dump(ROOT/'host/MANIFEST.yaml',dict(format='host-source-integrity-v1',files={p.relative_to(ROOT/'host').as_posix():dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted(members)}))
owned=['audio_playback.py','audio_gain.py','meeting_monitor.py','video_protocol.py','command_gate.py','mono_input_profile.py','interference_control.py','structured_store.py','native_paths.py']
dump(ROOT/'LOCAL_SUPPORT_BINDING.yaml',dict(format='host-local-source-bindings-v1',files=records([ROOT/'host'/n for n in owned],ROOT)))
p=ROOT/'StartHost.py';s=p.read_text(encoding='utf-8')
for name,file in [('INPUT_MANIFEST_EXPECTED',ROOT/'host/MANIFEST.yaml'),('LOCAL_SUPPORT_EXPECTED',ROOT/'LOCAL_SUPPORT_BINDING.yaml')]:s=re.sub(name+r"='[a-f0-9]{64}'",name+"='"+sha(file)+"'",s)
text(p,s)
members=[p for p in files(ROOT) if p.name!='SHA256SUMS'];text(ROOT/'SHA256SUMS','# Source package SHA256 file list\n'+''.join(sha(p)+'  '+p.relative_to(ROOT).as_posix()+'\n' for p in sorted(members)))
print('SOURCE_INTEGRITY_REFRESHED',len(members))
