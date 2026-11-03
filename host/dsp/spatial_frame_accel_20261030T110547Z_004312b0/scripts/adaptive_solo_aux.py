"""Independent original adaptive SOLO evaluation; no HOST/DSP/render changes."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import os
for _k in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS"):os.environ[_k]="1"
import sys
sys.dont_write_bytecode=True
import copy,hashlib,importlib.util,json,math
from pathlib import Path
import numpy as np
from scipy import signal

A=Path(str(__import__('pathlib').Path(__file__).resolve().parent / '../../adaptive_gate_20261030T034530Z_6dcf920e'))
PROTOCOL_SHA="0fab6f2ec09204b9cd1ef59bbae3e1017ccb4ea749d9e1cbc16209deae5669c3"
QUALITY=Path(str(__import__('pathlib').Path(__file__).resolve().parent / 'tuning_quality_audition.py'))
QUALITY_SHA="45a7cb6d0aad8df25f340d50a6b33b7b48e07e77e0bbd12971bc5643cadac287"
FS,N,HOP=48000,1024,512
STAGES=["beam_A","beam_B1","beam_B2","wiener","post_bandpass","post_logmmse","post_agc","final"]

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(p):return store.loads(Path(p).read_text(encoding="utf-8-sig"))
def dump(p,data):
    with Path(p).open("x",encoding="utf-8",newline="\n") as f:f.write(store.dumps(data, indent=2, allow_nan=False));f.write("\n")
def db(a,b):return 20*math.log10(a/b) if min(a,b)>0 else None
def quantiles(x):
    return dict(zip(["min","q10","q50","q90","max"],np.quantile(x,[0,.1,.5,.9,1]).tolist())) if len(x) else None
def scalar(x,y):
    x=x-np.mean(x);y=y-np.mean(y)
    px=float(x@x);py=float(y@y)
    k=float(x@y)/px if px>0 else 0.
    error=y-k*x;pe=float(error@error);pc=k*k*px
    return dict(kappa=k,kappa_gain_db=db(abs(k),1),required_inverse_gain_db=db(1,abs(k)),
        inverse_gain_exceeds_AGC20dB=bool(k and db(1,abs(k))>20),
        correlation=float(x@y)/math.sqrt(px*py) if min(px,py)>0 else None,
        non_scalar_output_energy_fraction=pe/py if py>0 else None,
        SI_db=10*math.log10(pc/pe) if min(pc,pe)>0 else None,
        normalized_after_kappa_relative_l2=math.sqrt(pe/pc) if pc>0 else None,
        native_ac_rms_gain_db=db(float(np.std(y)),float(np.std(x))))
def unity(x):
    w=np.sqrt(np.maximum(0.,.5-.5*np.cos(2*np.pi*np.arange(N)/N)))
    tail=np.zeros(HOP);out=[]
    for start in range(0,len(x)-N+1,HOP):
        v=np.fft.ifft(np.fft.fft(x[start:start+N]*w)).real*w
        out.append(np.clip(v[:HOP]+tail,-1,1).astype(np.float32).astype(float));tail=v[HOP:]
    return np.concatenate(out)
def float_control(x,k):
    xc=x-x.mean();y=np.asarray(k*xc,dtype=np.float32).astype(float)
    err=y-k*xc
    return dict(kappa=k,relative_l2_error=float(np.linalg.norm(err)/max(np.linalg.norm(k*xc),1e-300)),
        inverse_scaled_relative_l2_error=float(np.linalg.norm(y/k-xc)/max(np.linalg.norm(xc),1e-300)) if k else None,
        actual_float_rounding_error_RMS=float(np.std(err)),
        explanation="Matched constant-kappa Float32 control only, not an acoustic/noise-floor model; no output compensation saved.")
def frames(x,y):
    step=4800;count=len(x)//step
    xs=x[:count*step].reshape(count,step);ys=y[:count*step].reshape(count,step)
    xr=np.sqrt(np.mean(xs*xs,axis=1));yr=np.sqrt(np.mean(ys*ys,axis=1))
    active=xr>=xr.max()*10**(-30/20)
    ratios=20*np.log10(np.maximum(yr,1e-300)/np.maximum(xr,1e-300))
    ks=[scalar(xx,yy)["kappa"] for xx,yy,act in zip(xs,ys,active) if act]
    return dict(frame_samples=step,active_definition="Input-only100ms raw RMS>=-30dB of maximum within window, no overlap",total=count,
        active=int(active.sum()),fraction_active_RMS_gain_le_minus30_db=float(np.mean(ratios[active]<=-30)),
        active_RMS_gain_db=quantiles(ratios[active]),active_time_kappa=quantiles(ks),
        negative_active_kappa_count=sum(k<0 for k in ks),
        frame_kappa_role="Diagnostics only, NEVER used for frame normalization or output compensation")
def spectral_transfer(x,y,k):
    args=dict(fs=FS,window="hann",nperseg=2048,noverlap=1024,detrend="constant")
    f,px=signal.welch(x,**args);_,py=signal.welch(y,**args);_,cross=signal.csd(x,y,**args)
    valid=(f>=100)&(f<=8000)&(px>=px.max()*1e-6)
    h=np.divide(cross,px,out=np.zeros_like(cross),where=px>0)
    coh=np.divide(abs(cross)**2,px*py,out=np.zeros_like(px),where=px*py>0)
    out=[]
    for low,high in [(100,300),(300,800),(800,2000),(2000,4000),(4000,8000)]:
        m=valid&(f>=low)&(f<high)
        if not np.any(m):out.append(dict(hz=[low,high],bins=0));continue
        gain=20*np.log10(np.maximum(abs(h[m]),1e-300)/max(abs(k),1e-300))
        out.append(dict(hz=[low,high],bins=int(m.sum()),coherence_inputPSD_weighted=float(px[m]@coh[m]/px[m].sum()),
            H_magnitude_relative_global_kappa_db=quantiles(gain),
            actual_output_to_input_band_RMS_db=db(float(np.sqrt(py[m].sum())),float(np.sqrt(px[m].sum())))))
    return dict(method="Welch/CSD Hann2048 hop1024, input-only PSD>=-60dBmax,100..8000Hz",bands=out)

def main():
    protocol_path=A/"PROTOCOL.yaml"
    if sha(protocol_path)!=PROTOCOL_SHA or sha(QUALITY)!=QUALITY_SHA:raise ValueError("Frozen protocol/helper SHA mismatch")
    out=A/"evaluation_aux";out.mkdir(exist_ok=False)
    import shutil
    shutil.copy2(__file__,out/"script_snapshot.py")
    spec=importlib.util.spec_from_file_location("quality_helper",QUALITY);q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
    tracked={}
    def track(p):
        p=Path(p);st=p.stat();tracked[str(p)]=dict(sha256=sha(p),bytes=st.st_size,mtime_ns=st.st_mtime_ns);return p
    track(protocol_path);track(QUALITY)
    def stages(folder):
        md=load(track(folder/"metadata.yaml"));values={}
        for key in STAGES:
            entry=md["stages"][key];path=track(folder/entry["file"])
            if sha(path)!=entry["sha256"]:raise ValueError("Stage SHA mismatch")
            y=np.fromfile(path,dtype="<f4").astype(float)
            if len(y)!=entry["sample_count"] or not np.isfinite(y).all():raise ValueError("Stage count/finite failure")
            values[key]=y
        return md,values
    reports=[]
    for sc in load(protocol_path)["first_gate_pair"]:
        name=sc["id"];md,v=stages(A/"runs"/name/"adaptive/host");fm,fixed=stages(Path(sc["fixed_host"]))
        cfg=load(track(sc["config"]))
        if sha(sc["config"])!=sc["config_sha256"] or cfg!=md["config"]:raise ValueError("Config differs")
        expected=copy.deepcopy(fm["config"]);expected["wiener"]["algorithm"]="adaptive"
        if cfg!=expected:raise ValueError("More than original fixed->adaptive config changed")
        if md["input_sha256"]!=sc["input_sha256"] or fm["input_sha256"]!=sc["input_sha256"]:raise ValueError("Input SHA differs")
        if any(not np.array_equal(v[k],fixed[k]) for k in ["beam_A","beam_B1","beam_B2"]):raise ValueError("Beam differs")
        x=unity(v["beam_A"])
        if len(x)!=len(v["wiener"]):raise ValueError("Unity WOLA length differs")
        shift=sc["dref"] if sc["channels"]==128 else 0
        def analyse(window):
            b,e=[round(t*FS)+shift for t in window]
            xx=x[b:e];y=v["wiener"][b:e];old=fixed["wiener"][b:e]
            fit=scalar(xx,y);shape=q.solo_shape(y,xx)
            ctl=float_control(xx,fit["kappa"])
            return dict(window_seconds=window,native_indices=[b,e],scalar_diagnostic=fit,
                matched_global_kappa_float32_control=ctl,gain_removed_shape=shape,
                fixed_original_vs_unity=scalar(xx,old),active100ms=frames(xx,y),
                spectrum=spectral_transfer(xx,y,fit["kappa"]),
                native_ac_RMS=dict(unityA=float(np.std(xx)),adaptive=float(np.std(y)),fixed=float(np.std(old))))
        primary=analyse(sc["module_steady_window_s"]);startup=analyse(sc["startup_window_s"])
        fit=primary["scalar_diagnostic"];shape=primary["gain_removed_shape"];fr=primary["active100ms"]
        rule_sustained=(fr["fraction_active_RMS_gain_le_minus30_db"]>=.7 and (fit["non_scalar_output_energy_fraction"]>=.1 or shape["log_spectral_distance_db"]>6))
        rule_corr=abs(fit["correlation"])<.8 and shape["log_spectral_distance_db"]>6
        report=dict(case=name,channels=sc["channels"],only_config_change_verified=True,beam_bitexact_to_fixed=True,
            window_interpretation="128 physicalwindow+dref; real48 nativebeam1..9s matches saved S1 window",unity_reference="Exact periodic sqrtHann1024/512 unity FFT WOLA fromstream0, float32 emit, noendflush",
            primary=primary,startup=startup,sustained_self_cancellation_rule=rule_sustained,
            additional_corr_and_shape_damage_rule=rule_corr,synthetic_clean=None,
            input_quantization=dict(decoded_per_mic_step=1/32768,physical_noise_floor_known=False,
                interpretation="Per-mic16bit grid after arithmetic>>8; spatialaverage/interpolation/f32 preserve subLSB. No independent sqrtN noise assumption."))
        rule_si=False
        if sc["synthetic_truth"]:
            with np.load(track(sc["synthetic_truth"]),allow_pickle=False) as z:t=np.asarray(z["target_direct_center"],dtype=float)
            b,e=[round(s*FS) for s in sc["module_steady_window_s"]];native_b,native_e=b+shift,e+shift
            clean=dict(unity=scalar(t[b:e],x[native_b:native_e]),
                beam=scalar(t[b:e],v["beam_A"][native_b:native_e]),
                fixed=scalar(t[b:e],fixed["wiener"][native_b:native_e]),
                adaptive=scalar(t[b:e],v["wiener"][native_b:native_e]))
            rule_si=clean["adaptive"]["SI_db"]<=clean["fixed"]["SI_db"]-3 and clean["adaptive"]["SI_db"]<=clean["unity"]["SI_db"]-10
            report["synthetic_clean"]=clean
            report["supplementary_late_window_not_primary"]=analyse([1.2,3.8])
            bp=signal.sosfilt(np.vstack([signal.butter(2,100,btype="highpass",fs=FS,output="sos"),signal.butter(2,8000,btype="lowpass",fs=FS,output="sos")]),t)
            report["fullchain_sameBP_clean_diagnostic"]={}
            for stage in ["post_bandpass","post_logmmse","post_agc","final"]:
                offset=shift+(2048 if stage in ["post_logmmse","post_agc","final"] else 0)
                report["fullchain_sameBP_clean_diagnostic"][stage]=dict(adaptive=scalar(bp[b:e],v[stage][b+offset:e+offset]),fixed=scalar(bp[b:e],fixed[stage][b+offset:e+offset]))
            report["digital_quiet_only"]={}
            for label,win in [("preroll",[.1,.5]),("tail",[4.5,5.5])]:
                a,z=[round(s*FS)+shift for s in win]
                report["digital_quiet_only"][label]=dict(window_s=win,unity_RMS=float(np.std(x[a:z])),adaptive_RMS=float(np.std(v["wiener"][a:z])),not_physical_noise_floor=True)
        report["additional_SI_damage_rule"]=rule_si
        report["frozen_firstgate_result"]="STOP_BEFORE_MIXED_SHAPE_DAMAGE" if rule_sustained or rule_corr or rule_si else "NO_RULE_TRIGGER_REVIEW_OTHER_EVIDENCE"
        reports.append(report);dump(out/(name+".yaml"),report)
        print(json.dumps(dict(case=name,decision=report["frozen_firstgate_result"],correlation=fit["correlation"],kappa=fit["kappa"],residual_fraction=fit["non_scalar_output_energy_fraction"],LSD=shape["log_spectral_distance_db"],float_control_error=primary["matched_global_kappa_float32_control"]["relative_l2_error"])),flush=True)
    changed=[]
    for p,entry in tracked.items():
        st=Path(p).stat()
        if dict(sha256=sha(p),bytes=st.st_size,mtime_ns=st.st_mtime_ns)!=entry:changed.append(p)
    if changed:raise RuntimeError("Tracked files changed")
    result=dict(status="MANDATORY_SOLO_FIRST_GATE_STOP_BEFORE_MIXED",protocol_sha256=PROTOCOL_SHA,
        script_sha256=sha(__file__),quality_helper_sha256=QUALITY_SHA,reports=reports,
        all_sources_and_existing_results_unchanged=True,HOST_or_render_or_DSP_change_performed=False,
        limitations=["Real48 lacks independent cleanGT: it shows severe non-scalar change relativetoA, not independently proven speechdamage.",
            "128knownsolo cleanGT confirms targetloss. Original frozen engineering safeguards are not approved user acoustic requirements.",
            "Shape helper removes one entirewindow AC-RMS factor; separate globalLSkappa diagnosis never used for perframe/perfrequency compensation.",
            "Residualfraction is scalar-model mismatch, not true acousticnoise. Digitalquiet0 is not measured room/ADCnoise-floor.",
            "Sourceagent independently reconstructs exactadaptiveequations; this receipt does not substitute for that execution check.",
            "No fullchain F(T)+F(I) source decomposition, mixed expansion, thresholdretuning, savedaudio or arraycache."])
    dump(out/"provenance.yaml",tracked);dump(out/"result.yaml",result)
    print(json.dumps(dict(output=str(out/"result.yaml"),sha256=sha(out/"result.yaml"),status=result["status"])),flush=True)

if __name__=="__main__":main()
