"""Analyze existing frozen HOST outputs and create auditable listening WAVs.

No HOST execution, rendering, lag search, parameter selection, or audio playback.
Only a fresh quality_audition_20261030/run_* directory is written.
"""
from __future__ import annotations
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import uuid
import wave
import numpy as np
import scipy
from scipy import signal

ROOT = Path(str(__import__('pathlib').Path(__file__).resolve().parent / '../../phase2tuning_20261029T163647Z_84abcc1c'))
FS, DREF, LOG_DELAY = 48000, 30, 2048
BANDS = ((100, 300), (300, 800), (800, 2000), (2000, 4000), (4000, 8000))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return store.loads(Path(path).read_text(encoding="utf-8-sig"))


def dump(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        f.write(store.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        f.write("\n")


def finite(value):
    x = float(value)
    return x if math.isfinite(x) else None


def correlation(a, b):
    a, b = a - np.mean(a), b - np.mean(b)
    aa, bb = float(a @ a), float(b @ b)
    return finite(float(a @ b) / math.sqrt(aa * bb)) if aa > 0 and bb > 0 else None


def level(x):
    return dict(samples=len(x), finite=bool(np.all(np.isfinite(x))), rms=finite(np.sqrt(np.mean(x*x))),
                ac_rms=finite(np.std(x)), dc=finite(np.mean(x)), peak=finite(np.max(np.abs(x))),
                abs_ge_one_count=int(np.count_nonzero(np.abs(x) >= 1.0)))


def fidelity(y, target):
    yc, tc = y - np.mean(y), target - np.mean(target)
    tt, yy = float(tc @ tc), float(yc @ yc)
    result = dict(correlation=correlation(y, target), si_sdr_db=None,
                  alpha_diagnostic=None, status="computed", zero_mean=True)
    if tt <= 0 or yy <= 0:
        result["status"] = "zero_or_constant_signal"
        return result
    alpha = float(yc @ tc) / tt
    residual = yc - alpha * tc
    component = alpha * tc
    p, e = float(component @ component), float(residual @ residual)
    result["alpha_diagnostic"] = finite(alpha)
    if p > 0 and e > 0:
        result["si_sdr_db"] = finite(10.0 * (math.log10(p) - math.log10(e)))
    else:
        result["status"] = "zero_projection_or_perfect_projection_not_encoded_as_infinity"
    return result


def magnitude_stft(x):
    _, _, z = signal.stft(x, fs=FS, window=np.hanning(2048), nperseg=2048,
                         noverlap=1536, nfft=2048, boundary=None, padded=False)
    return np.abs(z)


def solo_shape(y, reference):
    """One positive AC-RMS factor per entire solo clip, never per frame/band."""
    y, t = y - np.mean(y), reference - np.mean(reference)
    yr, tr = float(np.std(y)), float(np.std(t))
    if yr <= 0 or tr <= 0:
        return dict(status="zero_or_constant_signal", global_ac_rms_gain=None,
                    log_spectral_distance_db=None, bands=[])
    gain = tr / yr
    ym, tm = magnitude_stft(y * gain), magnitude_stft(t)
    frequency = np.fft.rfftfreq(2048, 1.0 / FS)
    selected = (frequency >= 100) & (frequency <= 8000)
    # One shared floor from the complete reference; no per-output/per-frame floor.
    floor = max(float(np.max(tm)) * 1e-4, 1e-12)
    difference = 20 * np.log10(np.maximum(ym, floor)) - 20 * np.log10(np.maximum(tm, floor))
    values = difference[selected]
    weights = tm[selected] ** 2
    active_energy = np.sum(tm[selected] ** 2, axis=0)
    active = active_energy > max(float(np.max(active_energy)) * 1e-3, 1e-24)
    weighted = float(np.sqrt(np.sum(weights * values**2) / np.sum(weights))) if np.sum(weights) > 0 else None
    result = dict(status="computed_solo_waveform_shape_not_STOI", global_ac_rms_gain=gain,
                  analysis_gain_scope="One entire-clip AC-RMS factor; affects metrics only, not the listening WAVs.",
                  shared_magnitude_floor=floor, floor_db_relative_reference_peak=-80,
                  frequency_range_hz=[100,8000], fft_size=2048, hop_samples=512,
                  log_spectral_distance_db=finite(np.sqrt(np.mean(values**2))),
                  reference_energy_weighted_log_spectral_distance_db=weighted,
                  active_reference_frames=int(np.count_nonzero(active)), total_frames=len(active),
                  active_frame_definition="Reference-only100--8000Hz energy greater than -30dB of its maximum frame energy; not a speech classifier.",
                  active_frame_log_spectral_distance_db=finite(np.sqrt(np.mean(values[:,active]**2))) if np.any(active) else None,
                  bands=[])
    for low, high in BANDS:
        bins = (frequency >= low) & (frequency < high)
        ye = np.sqrt(np.mean(ym[bins]**2, axis=0))
        te = np.sqrt(np.mean(tm[bins]**2, axis=0))
        result["bands"].append(dict(hz=[low,high], linear_rms_envelope_correlation=correlation(ye,te),
            log_rms_envelope_correlation=correlation(np.log10(np.maximum(ye,floor)),np.log10(np.maximum(te,floor))),
            reference_envelope_rms=finite(np.sqrt(np.mean(te**2)))))
    return result


def load_stage(host, meta, name):
    p = host / (name + ".f32le")
    m = meta["stages"][name]
    raw = p.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if m["file"] != p.name or m["sha256"] != digest or m["byte_count"] != len(raw):
        raise ValueError(f"Stage hash/file/length mismatch: {p}")
    x = np.frombuffer(raw, dtype="<f4").astype(np.float64)
    if len(x) != m["sample_count"] or not np.all(np.isfinite(x)):
        raise ValueError(f"Stage sample-count/finite failure: {p}")
    return x, digest


def load_case(scene, profiles, sos):
    name = scene["id"]
    profile_data, pre_agc_data, inputs, beam_hashes = {}, {}, set(), set()
    bounds = None
    beam_full = None
    provenance = {}
    for profile in profiles:
        run = ROOT / "runs" / name / profile
        result, metrics, meta = read(run/"result.yaml"), read(run/"metrics.yaml"), read(run/"host/metadata.yaml")
        if int(metrics["alignment"]["beam_dref_samples"]) != DREF or metrics["alignment"]["extra_after_logmmse_samples"] != LOG_DELAY:
            raise ValueError("Prescribed30/+2048 alignment changed")
        this_bounds = tuple(metrics["alignment"]["truth_samples_half_open"])
        if this_bounds[1]-this_bounds[0] != 3*FS or (bounds is not None and bounds != this_bounds):
            raise ValueError("Profiles do not share the same exact three-second physical window")
        bounds = this_bounds
        inputs.add(meta["input_sha256"])
        if sha(run/"config.yaml") != meta["config_sha256"] or meta["config_sha256"] != result["config_sha256"]:
            raise ValueError("Config hash mismatch")
        actual_config=read(run/"config.yaml")
        template=read(ROOT/"profiles/selected"/(profile+".yaml"))
        documentary_or_scene_keys={"directions","profile_description","input_contract","profile_id"}
        if any(actual_config.get(k)!=template.get(k) for k in (set(actual_config)|set(template))-documentary_or_scene_keys):
            raise ValueError("Runtime DSP parameters differ from the frozen profile")
        f, fh = load_stage(run/"host",meta,"final")
        b, bh = load_stage(run/"host",meta,"beam_A")
        pre, pre_hash = load_stage(run/"host",meta,"post_logmmse")
        beam_hashes.add(bh)
        if beam_full is None: beam_full=b
        a,z = bounds
        if z+DREF+LOG_DELAY > len(f): raise ValueError("Insufficient final output; no window shortening")
        profile_data[profile]=f[a+DREF+LOG_DELAY:z+DREF+LOG_DELAY]
        pre_agc_data[profile]=pre[a+DREF+LOG_DELAY:z+DREF+LOG_DELAY]
        provenance[profile]=dict(result_sha256=sha(run/"result.yaml"),metadata_sha256=sha(run/"host/metadata.yaml"),
                                 config_sha256=meta["config_sha256"],input_sha256=meta["input_sha256"],
                                 final_sha256=fh,preAGC_sha256=pre_hash,beam_sha256=bh,full_final_level=level(f))
    if len(inputs)!=1 or len(beam_hashes)!=1:
        raise ValueError("Same-scene profiles differ in input or raw beam")
    fixture=ROOT/"fixtures"/name
    with np.load(fixture/"truth.npz",allow_pickle=False) as data:
        direct=data["target_direct_center"].astype(np.float64)
        wet=data["target_wet_center"].astype(np.float64)
    if not np.all(np.isfinite(direct)) or not np.all(np.isfinite(wet)):raise ValueError("Nonfinite truth")
    a,z=bounds
    bp_direct=signal.sosfilt(sos,direct)[a:z]
    bp_wet=signal.sosfilt(sos,wet)[a:z]
    beam=signal.sosfilt(sos,beam_full)[a+DREF:z+DREF]
    if any(len(x)!=3*FS for x in (bp_direct,bp_wet,beam,direct[a:z])):raise ValueError("Truth/beam window too short")
    return dict(id=name,scene=scene,role=scene["role"],bounds=list(bounds),signals={**profile_data,"beam_sameBP":beam},
                pre_agc_signals=pre_agc_data,direct=direct[a:z],bp_direct=bp_direct,bp_wet=bp_wet,
                provenance=provenance,truth_sha256=sha(fixture/"truth.npz"))


def write_wav(path, x):
    if path.exists():raise FileExistsError(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    rounded=np.rint(x*32768.0)
    clips=int(np.count_nonzero((rounded < -32768)|(rounded > 32767)))
    pcm=np.clip(rounded,-32768,32767).astype("<i2")
    with path.open("xb") as raw:
        with wave.open(raw,"wb") as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(FS);w.writeframes(pcm.tobytes())
    return dict(path=str(path),sha256=sha(path),sample_count=len(x),sample_rate_hz=FS,
                pcm16_encoder_clip_count=clips,float_level_after_all_gains=level(x))


def complete(scene,profiles):
    return all((ROOT/"runs"/scene["id"]/p/"result.yaml").is_file()
               and (ROOT/"runs"/scene["id"]/p/"host/metadata.yaml").is_file() for p in profiles)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-all-heldout",action="store_true")
    args=parser.parse_args()
    freeze=read(ROOT/"HOLDOUT_PROFILE_FREEZE.yaml")
    profiles=freeze["profiles"]
    train=read(ROOT/"train_design.yaml")
    held=read(ROOT/"holdout_design/scenes_frozen.yaml")
    pending=[s["id"] for s in train+held if not complete(s,profiles)]
    if args.require_all_heldout and pending:
        print(json.dumps(dict(status="WAITING_FOR_EXISTING_RESULTS_NOT_A_FAILURE",pending=pending)),flush=True)
        return 0
    for profile,expected in freeze["profile_template_sha256"].items():
        if sha(ROOT/"profiles/selected"/(profile+".yaml"))!=expected:raise ValueError("Frozen profile template changed")
    sos=np.vstack((signal.butter(2,100,btype="highpass",fs=FS,output="sos"),
                   signal.butter(2,8000,btype="lowpass",fs=FS,output="sos")))
    cases=[]
    for scene in train+held:
        if complete(scene,profiles):
            cases.append(load_case(scene,profiles,sos))
    anchors=[c for c in cases if c["id"]=="T1_solo_quiet"]
    if len(anchors)!=1:raise ValueError("T1 target-only loudness anchor is required")
    anchor=anchors[0]
    reference_rms=float(np.std(anchor["bp_direct"]))
    gains={"beam_sameBP":1.0}
    gain_records={}
    for profile in profiles:
        original_rms=float(np.std(anchor["signals"][profile]))
        if original_rms<=0 or reference_rms<=0:raise ValueError("Zero anchor signal")
        gains[profile]=reference_rms/original_rms
        gain_records[profile]=dict(anchor_case="T1_solo_quiet",anchor_final_ac_rms=original_rms,
                                   anchor_same_bp_clean_target_ac_rms=reference_rms,
                                   gain=gains[profile],gain_db=20*math.log10(gains[profile]))
    # Both groups and all signal/reference tracks share ONE headroom scalar.
    peak=0.0
    for c in cases:
        for name,x in c["signals"].items():
            peak=max(peak,float(np.max(np.abs(x))),float(np.max(np.abs(x*gains[name]))))
        peak=max(peak,float(np.max(np.abs(c["bp_direct"]))),float(np.max(np.abs(c["bp_wet"]))))
    headroom=min(1.0,0.95/peak) if peak>0 else 1.0
    output=ROOT/"quality_audition_20261030"/("run_"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+uuid.uuid4().hex[:8])
    output.mkdir(parents=True,exist_ok=False)
    with (output/"script_snapshot.py").open("xb") as f:f.write(Path(__file__).read_bytes())
    report=dict(schema_version=1,status="RUNNING",created_utc=datetime.now(timezone.utc).isoformat(),
        output_directory=str(output),generator_sha256=sha(__file__),numpy_version=np.__version__,scipy_version=scipy.__version__,
        frozen_profile_receipt_sha256=sha(ROOT/"HOLDOUT_PROFILE_FREEZE.yaml"),primary=freeze["primary"],secondary=freeze["secondary"],baseline=freeze["baseline"],
        pending_cases=pending,alignment=dict(dref_samples=DREF,extra_after_logmmse_samples=LOG_DELAY,
                                            sample_rate_hz=FS,window_samples=3*FS,lag_fitting=False),
        listening_policy=dict(common_gain_group_profile_gain=1.0,target_anchor_group_profile_gains=gain_records,
            beam_sameBP_gain=1.0,target_reference_gains=1.0,common_headroom=headroom,max_peak_before_common_headroom=peak,
            headroom_scope="One scalar shared across both groups, all cases, all profiles and references; maximum encoded float peak0.95.",
            interpretation="Target-only T1 loudness anchor, fixed across every case. Not an estimate or normalization of the true target loudness inside each mixture.",
            playback_performed=False),
        STOI=dict(computed=False,pystoi_available=importlib.util.find_spec("pystoi") is not None,
                  reason="No STOI is computed or imitated by these spectrum/envelope diagnostics; no package installation."),
        scope=["Existing f32/truth analysis only; no HOST or acoustic rendering.",
               "Mixed-output fidelity metrics include residual interference/distortion; not exact nonlinear target contribution.",
               "Solo shape metrics remove only one global clip AC-RMS scale; no framewise/bandwise gain or lag fitting.",
               "Band-envelope correlation and spectral distance are not intelligibility or subjective listening scores.",
               "No profile selection or switching is performed on heldout results."],cases={})
    for c in cases:
        solo=c["role"]=="target_only"
        row=dict(role=c["role"],is_heldout=c["id"].startswith("H"),truth_samples=c["bounds"],
                 truth_sha256=c["truth_sha256"],provenance=c["provenance"],tracks={},preAGC_tracks={},wav_groups={})
        for name,y in c["pre_agc_signals"].items():
            pre=dict(level_original=level(y),vs_direct=fidelity(y,c["direct"]),
                     vs_same_bp_target=fidelity(y,c["bp_direct"]))
            if solo:pre["solo_global_rms_removed_shape_vs_sameBP_target"]=solo_shape(y,c["bp_direct"])
            row["preAGC_tracks"][name]=pre
        for name,y in c["signals"].items():
            track=dict(level_original=level(y),vs_direct=fidelity(y,c["direct"]),
                       vs_same_bp_target=fidelity(y,c["bp_direct"]),vs_same_bp_wet_target_auxiliary=fidelity(y,c["bp_wet"]))
            if solo:
                track["solo_global_rms_removed_shape_vs_sameBP_target"]=solo_shape(y,c["bp_direct"])
                track["solo_global_rms_removed_shape_vs_direct"]=solo_shape(y,c["direct"])
            else:
                track["solo_shape_status"]="not_applicable_to_mixture_no_source_decomposition"
            row["tracks"][name]=track
        for group in ("common_gain","target_anchor_gain"):
            files={}
            for name,y in c["signals"].items():
                g=1.0 if group=="common_gain" else gains[name]
                record=write_wav(output/group/c["id"]/(name+".wav"),y*g*headroom)
                record.update(profile_gain=g,common_headroom=headroom,float_peak_before_common_headroom=float(np.max(np.abs(y*g))))
                files[name]=record
            for name,y in (("target_direct_sameBP",c["bp_direct"]),("target_wet_sameBP",c["bp_wet"])):
                record=write_wav(output/group/c["id"]/(name+".wav"),y*headroom)
                record.update(profile_gain=1.0,common_headroom=headroom,float_peak_before_common_headroom=float(np.max(np.abs(y))))
                files[name]=record
            row["wav_groups"][group]=files
        report["cases"][c["id"]]=row
        print(json.dumps(dict(analyzed=c["id"],solo=solo)),flush=True)
    report["case_count"]=len(cases)
    report["training_count"]=sum(not c["id"].startswith("H") for c in cases)
    report["heldout_count"]=sum(c["id"].startswith("H") for c in cases)
    report["wav_count"]=sum(len(files) for c in report["cases"].values() for files in c["wav_groups"].values())
    report["pcm16_encoder_clip_count_total"]=sum(f["pcm16_encoder_clip_count"] for c in report["cases"].values() for files in c["wav_groups"].values() for f in files.values())
    report["status"]="QUALITY_DIAGNOSTICS_AND_WAVS_COMPLETE_NOT_SUBJECTIVE_ACCEPTANCE" if not pending else "QUALITY_DIAGNOSTICS_COMPLETE_WITH_PENDING_CASES"
    report["completed_utc"]=datetime.now(timezone.utc).isoformat()
    dump(output/"result.yaml",report)
    dump(output/"result.sha256.yaml",dict(path=str(output/"result.yaml"),sha256=sha(output/"result.yaml")))
    print(json.dumps(dict(status=report["status"],result=str(output/"result.yaml"),cases=len(cases),pending=pending,wavs=report["wav_count"],headroom=headroom,pcm16_clips=report["pcm16_encoder_clip_count_total"])),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
