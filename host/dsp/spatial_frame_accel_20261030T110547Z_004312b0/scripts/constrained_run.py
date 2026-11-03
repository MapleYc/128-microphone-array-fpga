"""Frozen one-profile constrained GSC experiment; existing inputs read only."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import os,sys
sys.dont_write_bytecode=True
for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[k]='1'
import argparse,copy,csv,hashlib,importlib.util,json,time,subprocess,shutil
from pathlib import Path
import numpy as np
from scipy import signal
import constrained_core as core
P=Path(str(__import__('pathlib').Path(__file__).resolve().parent / '../..'))
C=P/'constrained_20261030T041400Z_2df781a0';R=P/'phase2tuning_20261029T163647Z_84abcc1c'
L=P/'leakage_20261030T024130Z_b762ad91';G=P/'leakage_guard_20261030T030330Z_95ce71a4';B=P/'stage2_20261029T154710Z_63ffc32c'
def load(p):return store.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x',encoding='utf-8') as f:f.write(store.dumps(v, ensure_ascii=False, indent=2, allow_nan=False));f.write('\n')
def module(n,p):
 spec=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
geom=module('constrained_geometry',L/'scripts/leakage_coefficients.py')
quality=module('constrained_quality',Path(__file__).parent/'tuning_quality_audition.py')
aux=module('constrained_aux',Path(__file__).parent/'adaptive_solo_aux.py')
def locate(sid):
 q=C/'validation'/sid/'original/host';fi=C/'validation'/sid/'fixture.yaml'
 if q.exists() and fi.exists():return q,load(fi)
 for root,pid in [(R,'c10_L20'),(L,'original'),(G,'original')]:
  q=root/'runs'/sid/pid/'host';fi=root/'fixtures'/sid/'fixture.yaml'
  if q.exists() and fi.exists():return q,load(fi)
 raise FileNotFoundError(sid)
def spatial_metrics(y,unity,fi):
 b,e=[round(t*48000)+30 for t in fi['window']];x=unity[b:e].astype(float);z=y[b:e].astype(float)
 fit=aux.scalar(x,z);shape=quality.solo_shape(z,x)
 result=dict(scalar=fit,shape=shape,window=fi['window'],native_indices=[b,e])
 if fi['scene']['role']=='target_only':
  result['solo_gate']=bool(fit['correlation'] is not None and fit['correlation']>=.95 and shape['log_spectral_distance_db']<=3 and abs(fit['kappa_gain_db'])<=3)
 truth=dict(np.load(fi['truth']));a,z0=[round(t*48000) for t in fi['window']]
 result['clean_target']=quality.fidelity(z,truth['target_direct_center'][a:z0])
 return result
def one(sid):
 dest=C/'runs'/sid
 if (dest/'spatial_result.yaml').exists():return load(dest/'spatial_result.yaml')
 host,fi=locate(sid);md=load(host/'metadata.yaml');cfg=md['config'];assert cfg['wiener']['algorithm']=='fixed' and 'target_leakage' not in cfg['wiener']
 beams=[];hashes={}
 for name in ['beam_A','beam_B1','beam_B2']:
  path=host/(name+'.f32le');hashes[name]=sha(path);assert hashes[name]==md['stages'][name]['sha256'];beams.append(np.fromfile(path,dtype='<f4'))
 three=np.array(beams);dest.mkdir(exist_ok=False);dump(dest/'config.yaml',cfg);dump(dest/'fixture_pointer.yaml',dict(fixture=fi,original_host=str(host),beam_hashes=hashes))
 tic=time.perf_counter();model=core.transfers(cfg,geom);np.savez_compressed(dest/'model.npz',**{k:v for k,v in model.items() if k!='labels'});dump(dest/'model_labels.yaml',model['labels'])
 y,rows=core.run(three,model);elapsed=time.perf_counter()-tic
 unity=aux.unity(three[0]);assert len(y)==len(unity)==md['stages']['wiener']['sample_count'];assert np.isfinite(y).all()
 assert max(r['max_patch_response_change'] for r in rows)<=.250000001
 assert max(r['max_h_l1'] for r in rows)<=2.000000001
 assert sum(r['clamp_samples'] for r in rows)==0
 y.tofile(dest/'wiener.f32le')
 with (dest/'spatial_trace.csv').open('x',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 result=dict(scene=sid,old_host=str(host),fixture=fi,config=str(dest/'config.yaml'),input_beam_hashes=hashes,spatial_sha256=sha(dest/'wiener.f32le'),samples=len(y),wall_seconds=elapsed,valid_bins=int(model['valid'].sum()),
  max_patch_change=max(r['max_patch_response_change'] for r in rows),max_nominal_error=max(r['nominal_constraint_error'] for r in rows),max_h_l1=max(r['max_h_l1'] for r in rows),clamps=sum(r['clamp_samples'] for r in rows),spatial=spatial_metrics(y,unity,fi))
 dump(dest/'spatial_result.yaml',result)
 print('CONSTRAINED_SPATIAL',sid,'corr',result['spatial']['scalar']['correlation'],'LSD',result['spatial']['shape']['log_spectral_distance_db'],'kappaDB',result['spatial']['scalar']['kappa_gain_db'],'SI',result['spatial']['clean_target']['si_sdr_db'],'wall',round(elapsed,2),flush=True)
 return result
def solo():
 ids=['T1_solo_quiet','T2_solo_t0','V02_solo_t0_error','W01_soft_solo'];rows=[]
 for sid in ids:
  r=one(sid);rows.append(r)
  if not r['spatial']['solo_gate']:
   dump(C/'development/solo_gate.yaml',dict(status='FAIL_STOP_BEFORE_MIXED',rows=rows,unexecuted=ids[len(rows):]));return
 dump(C/'development/solo_gate.yaml',dict(status='PASS_SPATIAL_SOLO_GATE_NOT_FULLCHAIN_OR_ACOUSTIC_ACCEPTANCE',rows=rows))
def development():
 assert load(C/'development/solo_gate.yaml')['status'].startswith('PASS_')
 rows=[one(s) for s in load(C/'PROTOCOL.yaml')['development_after_solo']]
 dump(C/'development/mixed_result.yaml',dict(rows=rows))
def boundary():
 p=C/'runs/T1_solo_quiet';r=load(p/'spatial_result.yaml');host=Path(r['old_host']);three=np.array([np.fromfile(host/(n+'.f32le'),dtype='<f4') for n in ['beam_A','beam_B1','beam_B2']]);m=dict(np.load(p/'model.npz'));m['labels']=load(p/'model_labels.yaml')
 actual=np.fromfile(p/'wiener.f32le',dtype='<f4');alt,rows=core.run(three,m,packets=(1,511,7,1024,319));assert np.array_equal(actual,alt)
 repeat,_=core.run(three,m);assert np.array_equal(actual,repeat)
 z,trace=core.run(np.zeros((3,48000)),m);assert np.all(z==0)
 missing=copy.deepcopy(m);missing['valid'][:]=False
 by,_=core.run(three,missing);unity=aux.unity(three[0]);maxerr=float(np.max(abs(by-unity)));assert maxerr<=1e-7
 # Whole prefix must be identical even when unseen future data changes.
 past=three[:,:96000];prefix,_=core.run(past,m);future=three.copy();future[:,96000:]*=-.7;changed,_=core.run(future,m);assert np.array_equal(prefix,changed[:len(prefix)])
 dump(C/'development/boundary.yaml',dict(packet_byteexact=True,repeat_byteexact=True,silence_exactzero=True,invalid_model_max_unity_error=maxerr,future_independence_prefix_samples=len(prefix),future_independence_byteexact=True,reset_fresh_repeat=True,noflush=True))
 print('CONSTRAINED_BOUNDARY_PASS',flush=True)
if __name__=='__main__':
 if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
 ap=argparse.ArgumentParser();ap.add_argument('phase',choices=['solo','development','boundary']);args=ap.parse_args();globals()[args.phase]()
