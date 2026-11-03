"""Single geometry-noise-gain alternative; no original data modifications."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import os,sys
sys.dont_write_bytecode=True
from constrained_post_eval import *
import constrained_run as olddriver
import constrained_post_eval as oldpost
import noisegain_core as core
OLD=C;C=P/'noisegain_20261030T043400Z_c37124e9'
def locate(sid):
 new=C/'validation'/sid
 if (new/'fixture.yaml').exists():return new/'original/host',load(new/'fixture.yaml')
 prior=load(OLD/'runs'/sid/'spatial_result.yaml');return Path(prior['old_host']),prior['fixture']
def one(sid):
 dest=C/'runs'/sid
 if (dest/'spatial_result.yaml').exists():return load(dest/'spatial_result.yaml')
 host,fi=locate(sid);md=load(host/'metadata.yaml');cfg=md['config'];beams=[];hashes={}
 for name in ['beam_A','beam_B1','beam_B2']:
  p=host/(name+'.f32le');hashes[name]=sha(p);assert hashes[name]==md['stages'][name]['sha256'];beams.append(np.fromfile(p,dtype='<f4'))
 dest.mkdir(exist_ok=False);dump(dest/'config.yaml',cfg);dump(dest/'fixture_pointer.yaml',dict(fixture=fi,original_host=str(host),beam_hashes=hashes))
 tic=time.perf_counter();model=core.transfers(cfg,geom);np.savez_compressed(dest/'model.npz',**{k:v for k,v in model.items() if k!='labels'});dump(dest/'model_labels.yaml',model['labels'])
 y,rows=core.run(np.array(beams),model);assert len(y)==md['stages']['wiener']['sample_count'] and np.isfinite(y).all()
 assert max(r['max_noise_power_relative_A'] for r in rows)<=2+1e-8;assert max(r['max_patch_response_change'] for r in rows)<=.250000001
 assert sum(r['clamp_samples'] for r in rows)==0
 y.tofile(dest/'wiener.f32le')
 with (dest/'spatial_trace.csv').open('x',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 unity=aux.unity(beams[0]);result=dict(scene=sid,old_host=str(host),fixture=fi,config=str(dest/'config.yaml'),input_beam_hashes=hashes,spatial_sha256=sha(dest/'wiener.f32le'),samples=len(y),wall_seconds=time.perf_counter()-tic,
  max_noise_power_relative_A=max(r['max_noise_power_relative_A'] for r in rows),max_h_l1=max(r['max_h_l1'] for r in rows),max_patch_change=max(r['max_patch_response_change'] for r in rows),clamps=0,spatial=olddriver.spatial_metrics(y,unity,fi))
 dump(dest/'spatial_result.yaml',result)
 print('NOISEGAIN_SPATIAL',sid,'corr',result['spatial']['scalar']['correlation'],'LSD',result['spatial']['shape']['log_spectral_distance_db'],'kappaDB',result['spatial']['scalar']['kappa_gain_db'],'noiseMax',result['max_noise_power_relative_A'],'L1max',result['max_h_l1'],flush=True)
 return result
def solo():
 rows=[];ids=['T1_solo_quiet','T2_solo_t0','V02_solo_t0_error','W01_soft_solo']
 for sid in ids:
  r=one(sid);rows.append(r)
  if not r['spatial']['solo_gate']:
   dump(C/'development/solo_gate.yaml',dict(status='FAIL_STOP_BEFORE_MIXED',rows=rows));return
 dump(C/'development/solo_gate.yaml',dict(status='PASS_SPATIAL_SOLO_GATE_NOT_PRODUCT_ACCEPTANCE',rows=rows))
def post(sid,kind):
 dest=C/'runs'/sid/(kind+'_post')
 if (dest/'metadata.yaml').exists():return dest
 r=load(C/'runs'/sid/'spatial_result.yaml');host=Path(r['old_host']);src=C/'runs'/sid/'wiener.f32le'
 if kind=='beam':
  src=C/'runs'/sid/'unity_A.f32le';aux.unity(np.fromfile(host/'beam_A.f32le',dtype='<f4')).astype('<f4').tofile(src)
 if kind=='l1':
  # Only fresh validation needs a new original-L1 comparator replay.
  src=C/'runs'/sid/'l1_wiener.f32le';m=dict(np.load(C/'runs'/sid/'model.npz'));three=np.array([np.fromfile(host/(n+'.f32le'),dtype='<f4') for n in ['beam_A','beam_B1','beam_B2']]);y,rows=core.prior.run(three,m);y.tofile(src)
 env=os.environ.copy();env['PATH']=str(__import__('pathlib').Path(__file__).resolve().parent / '../../../runtime') + __import__('os').pathsep+env.get('PATH','');env['TEMP']=env['TMP']=str(C/'tmp')
 cmd=[str(OLD/'post_adapter/build/post_probe.exe'),'--input',str(src),'--config',str(C/'runs'/sid/'config.yaml'),'--output',str(dest),'--gain-summary-only']
 with (C/'runs'/sid/(kind+'_post.log')).open('x',encoding='utf-8') as f:f.write(store.dumps(cmd, explicit_start=True)+'\n');f.flush();p=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,env=env,timeout=90)
 assert p.returncode==0,(sid,kind);md=load(dest/'metadata.yaml')
 for s in STAGES:
  path=dest/(s+'.f32le');assert sha(path)==md['stages'][s]['sha256'];assert np.isfinite(np.fromfile(path,dtype='<f4')).all()
 return dest
def samples(sid,kind,stage):
 r=load(C/'runs'/sid/'spatial_result.yaml')
 if kind=='original':d=Path(r['old_host'])
 elif kind=='l1' and (OLD/'runs'/sid/'gsc_post').exists():d=OLD/'runs'/sid/'gsc_post'
 elif kind=='beam' and (OLD/'runs'/sid/'beam_post').exists():d=OLD/'runs'/sid/'beam_post'
 else:d=C/'runs'/sid/(kind+'_post')
 return np.fromfile(d/(stage+'.f32le'),dtype='<f4').astype(float)
def all_posts():
 for f in sorted((C/'runs').glob('*/spatial_result.yaml')):
  sid=f.parent.name;post(sid,'gsc')
  if not (OLD/'runs'/sid/'beam_post').exists():post(sid,'beam')
  print('NOISEGAIN_POST_COMPLETE',sid,flush=True)
def evaluate(sid,anchors):
 r=load(C/'runs'/sid/'spatial_result.yaml');fi=r['fixture'];truth=dict(np.load(fi['truth']));a,b=[round(t*48000) for t in fi['window']]
 sos=np.vstack([signal.butter(2,100,'highpass',fs=48000,output='sos'),signal.butter(2,8000,'lowpass',fs=48000,output='sos')]);t=signal.sosfilt(sos,truth['target_direct_center'])[a:b];i=signal.sosfilt(sos,truth['interferer_direct_center'])[a:b]
 te=np.mean(t.reshape(-1,960)**2,axis=1);ie=np.mean(i.reshape(-1,960)**2,axis=1)
 masks={'I_dominant':np.repeat((ie>=10*te)&(ie>=max(float(ie.max())*.001,1e-30)),960),'T_dominant':np.repeat((te>=10*ie)&(te>=max(float(te.max())*.001,1e-30)),960),'all':np.ones(b-a,bool)}
 rows=[]
 for kind in ['original','l1','gsc','beam']:
  stages={}
  for stage in STAGES:
   off=30 if stage=='post_bandpass' else 2078;y=samples(sid,kind,stage)[a+off:b+off]
   stages[stage]=dict(target=quality.fidelity(y,t),native_rms=rms(y),anchored_rms=rms(y*anchors[kind]),peak=float(abs(y).max()),solo_shape=quality.solo_shape(y,t) if fi['scene']['role']=='target_only' else None)
  y=samples(sid,kind,'final')[a+2078:b+2078]*anchors[kind];gates={k:dict(seconds=float(mask.sum()/48000),anchored_total_rms=rms(y[mask])) for k,mask in masks.items()}
  # Welch full3s band integration separately, not conflated with activity gates.
  freq,psd=signal.welch(y,fs=48000,window='hann',nperseg=48000,noverlap=24000,detrend=False,scaling='density');df=freq[1]-freq[0]
  bands={label:float(np.sum(psd[(freq>=lo)&(freq<hi)])*df) for label,lo,hi in [('below100',0,100),('100_150',100,150),('150_8000',150,8000),('100_8000',100,8000),('above8000',8000,24001)]}
  rows.append(dict(scene=sid,profile=kind,role=fi['scene']['role'],source_family=fi['scene'].get('source_family'),anchor_gain=anchors[kind],stages=stages,gates=gates,welch_anchored_power=bands))
 return rows
def summarize():
 r=load(C/'runs/T1_solo_quiet/spatial_result.yaml');a,b=[round(t*48000) for t in r['fixture']['window']];base=float(np.std(samples('T1_solo_quiet','beam','post_bandpass')[a+30:b+30]));anchors={kind:base/float(np.std(samples('T1_solo_quiet',kind,'final')[a+2078:b+2078])) for kind in ['original','l1','gsc','beam']}
 ids=sorted(p.parent.name for p in (C/'runs').glob('*/spatial_result.yaml'));rows=[]
 for sid in ids:rows.extend(evaluate(sid,anchors))
 suffix='final' if any(s.startswith('Y') for s in ids) else 'development';dump(C/('evaluation_'+suffix+'.yaml'),dict(anchors=anchors,rows=rows))
 if not (C/'ANCHOR_FREEZE.yaml').exists():dump(C/'ANCHOR_FREEZE.yaml',anchors)
 else:assert anchors==load(C/'ANCHOR_FREEZE.yaml')
 for sid in ids:
  group=[r for r in rows if r['scene']==sid];print('NOISEGAIN_METRICS',sid,[(r['profile'],r['stages']['final']['target']['si_sdr_db'],r['gates']['I_dominant']['anchored_total_rms']) for r in group],flush=True)
def development():
 assert load(C/'development/solo_gate.yaml')['status'].startswith('PASS')
 for sid in load(C/'PROTOCOL.yaml')['development'][4:]:one(sid)
 dump(C/'development/result.yaml',dict(scenes=load(C/'PROTOCOL.yaml')['development']))
def boundary():
 d=C/'runs/T1_solo_quiet';r=load(d/'spatial_result.yaml');host=Path(r['old_host']);three=np.array([np.fromfile(host/(n+'.f32le'),dtype='<f4') for n in ['beam_A','beam_B1','beam_B2']]);model=dict(np.load(d/'model.npz'));y=np.fromfile(d/'wiener.f32le',dtype='<f4')
 alt,_=core.run(three,model,packets=(1,17,511,1024,73));assert np.array_equal(y,alt)
 repeat,_=core.run(three,model);assert np.array_equal(y,repeat)
 silent,_=core.run(np.zeros((3,48000)),model);assert np.all(silent==0)
 prefix,_=core.run(three[:,:96000],model);other=three.copy();other[:,96000:]*=-.8;changed,_=core.run(other,model);assert np.array_equal(prefix,changed[:len(prefix)])
 invalid=copy.deepcopy(model);invalid['valid'][:]=False;by,_=core.run(three,invalid);assert np.max(abs(by-aux.unity(three[0])))<=1e-7
 dump(C/'development/boundary.yaml',dict(packet_byteexact=True,repeat_byteexact=True,silence_exactzero=True,future_independence_byteexact=True,invalid_model_unity=True,noendflush=True))
 print('NOISEGAIN_BOUNDARY_PASS',flush=True)
if __name__=='__main__':
 if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
 {'solo':solo,'development':development,'posts':all_posts,'evaluate':summarize,'boundary':boundary}[sys.argv[1]]()
