"""V2 metadata only; does not relabel V1 runtime results as V2 tests."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import datetime,hashlib,json
from pathlib import Path
P=Path(__file__).resolve().parent;V1=P.with_name('host_dsp_process_candidate_20261101_a9172d')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
hashes={str(f.relative_to(P)).replace('\\','/'):sha(f) for f in P.rglob('*.py') if '__pycache__' not in f.parts}
hashes['MONITOR_HASHES.yaml']=sha(P/'MONITOR_HASHES.yaml')
(P/'V2_SOURCE_HASHES.yaml').write_text(store.dumps(hashes,indent=2),encoding='utf8')
changed=[n for n in hashes if n.endswith('.py') and (V1/n).is_file() and sha(P/n)!=sha(V1/n)]
r=dict(written_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),candidate=P.name,baseline=V1.name,
       purpose='diagnostic retired native final ledger refresh only; no DSP/queue/native-buffer changes',
       changed_files_vs_v1=changed,unit_tests=dict(passed=147,log='UNIT_PROCESS_V2_FINAL.log'),
       independent_original_process_contracts=dict(passed=8,log='PROCESS_CONTRACT_V2.log'),
       new_final_ledger_tests=dict(passed=4,log='PROCESS_FINAL_LEDGER_TESTS.log',scope='no-device real spawn actor + delayed final, identity/owner/newsource/idempotent/JSON/error retention'),
       preflight='PROCESS_V2_PREFLIGHT.log',source_hashes='V2_SOURCE_HASHES.yaml',
       previous_production_dsp_smoke=dict(path=str(V1/'PROCESS_REAL_NULL_3S.yaml'),scope='V1 evidence; NOT rerun or relabeled as V2'),
       fullmain_native_validation='PENDING root/app_run with corrected explicit finite-EOS control; no PASS claimed',
       runtime_source_writes_stopped=True,implementation_jobs_running=0)
(P/'V2_HANDOFF.yaml').write_text(store.dumps(r,indent=2),encoding='utf8')
print(json.dumps(dict(handoff='V2_HANDOFF.yaml',process_sha256=sha(P/'process_adapter.py'),router_sha256=sha(P/'audio_source_router.py'),hashes_sha256=sha(P/'V2_SOURCE_HASHES.yaml'))))
