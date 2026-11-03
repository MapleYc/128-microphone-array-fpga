import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import os,sys
sys.dont_write_bytecode=True
from constrained_run import *
STAGES=['post_bandpass','post_logmmse','post_agc','final']
def post(sid,kind):
 dest=C/'runs'/sid/(kind+'_post');ready=dest/'metadata.yaml'
 if ready.exists():return ready
 result=load(C/'runs'/sid/'spatial_result.yaml');host=Path(result['old_host']);src=C/'runs'/sid/'wiener.f32le'
 if kind=='beam':
  src=C/'runs'/sid/'unity_A.f32le';a=np.fromfile(host/'beam_A.f32le',dtype='<f4');u=aux.unity(a)
  if not src.exists():u.astype('<f4').tofile(src)
  else:assert sha(src)==hashlib.sha256(u.astype('<f4').tobytes()).hexdigest()
 assert kind in ['beam','gsc']
 env=os.environ.copy();env['PATH']=str(__import__('pathlib').Path(__file__).resolve().parent / '../../../runtime') + __import__('os').pathsep+env.get('PATH','');env['TEMP']=env['TMP']=str(C/'tmp')
 cmd=[str(C/'post_adapter/build/post_probe.exe'),'--input',str(src),'--config',str(C/'runs'/sid/'config.yaml'),'--output',str(dest),'--gain-summary-only']
 with (C/'runs'/sid/(kind+'_post.log')).open('x',encoding='utf-8') as f:
  f.write(store.dumps(cmd, explicit_start=True)+'\n');f.flush();p=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,env=env,timeout=90)
 assert p.returncode==0,(sid,kind,p.returncode);md=load(ready)
 for s in STAGES:
  path=dest/(s+'.f32le');assert sha(path)==md['stages'][s]['sha256'];x=np.fromfile(path,dtype='<f4');assert np.isfinite(x).all()
 print('CONSTRAINED_FULLCHAIN',sid,kind,flush=True);return ready
def all_posts():
 ids=[p.parent.name for p in (C/'runs').glob('*/spatial_result.yaml')]
 for sid in sorted(ids):
  for kind in ['gsc','beam']:post(sid,kind)
def samples(sid,kind,stage):
 r=load(C/'runs'/sid/'spatial_result.yaml');d=Path(r['old_host']) if kind=='original' else C/'runs'/sid/(kind+'_post')
 return np.fromfile(d/(stage+'.f32le'),dtype='<f4').astype(float)
def anchor():
 r=load(C/'runs/T1_solo_quiet/spatial_result.yaml');a,b=[round(t*48000) for t in r['fixture']['window']]
 base=float(np.std(samples('T1_solo_quiet','beam','post_bandpass')[a+30:b+30]));return {kind:base/float(np.std(samples('T1_solo_quiet',kind,'final')[a+2078:b+2078])) for kind in ['original','gsc','beam']}
def rms(x):return float(np.sqrt(np.mean(x*x))) if len(x) else None
def db(a,b):return float(20*np.log10(a/b)) if a and b and min(a,b)>0 else None
def evaluate(sid,anchors):
 r=load(C/'runs'/sid/'spatial_result.yaml');fi=r['fixture'];truth=dict(np.load(fi['truth']));a,b=[round(t*48000) for t in fi['window']]
 sos=np.vstack([signal.butter(2,100,'highpass',fs=48000,output='sos'),signal.butter(2,8000,'lowpass',fs=48000,output='sos')])
 t=signal.sosfilt(sos,truth['target_direct_center'])[a:b];i=signal.sosfilt(sos,truth['interferer_direct_center'])[a:b]
 te=np.mean(t.reshape(-1,960)**2,axis=1);ie=np.mean(i.reshape(-1,960)**2,axis=1)
 masks={'I_dominant':np.repeat((ie>=10*te)&(ie>=max(float(ie.max())*.001,1e-30)),960),'T_dominant':np.repeat((te>=10*ie)&(te>=max(float(te.max())*.001,1e-30)),960),'all':np.ones(b-a,bool)}
 rows=[]
 for kind in ['original','gsc','beam']:
  stages={}
  for stage in STAGES:
   offset=30 if stage=='post_bandpass' else 2078;y=samples(sid,kind,stage)[a+offset:b+offset];assert len(y)==len(t)
   stages[stage]=dict(target=quality.fidelity(y,t),native_rms=rms(y),anchored_rms=rms(y*anchors[kind]),peak=float(abs(y).max()),nonfinite=int(np.sum(~np.isfinite(y))),
    limiter_rail_samples=int(np.sum(abs(y.astype('<f4'))==np.float32(10**(-1/20)))) if stage=='final' else None,
    floor_clip_fullscale=int(np.sum(abs(y)>=1)),solo_shape=quality.solo_shape(y,t) if fi['scene']['role']=='target_only' else None)
  y=samples(sid,kind,'final')[a+2078:b+2078]*anchors[kind]
  gates={k:dict(seconds=float(mask.sum()/48000),anchored_total_rms=rms(y[mask])) for k,mask in masks.items()}
  rows.append(dict(scene=sid,profile=kind,role=fi['scene']['role'],anchor_gain=anchors[kind],stages=stages,gates=gates))
 return rows
def summarize():
 anchors=anchor();ids=sorted(p.parent.name for p in (C/'runs').glob('*/spatial_result.yaml'));out=[]
 for sid in ids:out.extend(evaluate(sid,anchors))
 suffix='final' if len(ids)>11 else 'development';dump(C/('evaluation_'+suffix+'.yaml'),dict(anchors=anchors,rows=out,scope='FixedT1solo targetanchor acrossallcases;sourceenergygates evaluate totaloutput ONLY,not trueSIR/sourcecontributions. Never used byDSP.'))
 for sid in ids:
  rows=[r for r in out if r['scene']==sid];print('FULLCHAIN_METRICS',sid,[(r['profile'],r['stages']['final']['target']['si_sdr_db'],r['gates']['I_dominant']['anchored_total_rms']) for r in rows],flush=True)
 if suffix=='development' and not (C/'ANCHOR_FREEZE.yaml').exists():dump(C/'ANCHOR_FREEZE.yaml',anchors)
 else:assert anchors==load(C/'ANCHOR_FREEZE.yaml')
if __name__=='__main__':
 if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
 {'posts':all_posts,'evaluate':summarize}[sys.argv[1]]()
