import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import datetime,hashlib,json
from pathlib import Path
P=Path(__file__).resolve().parent
B=P.with_name('host_packet_decode_perf_20261101_f593a1')
math_files=['legacy_adapter.py','direction3d.py','doa3d.py','calibrate3d.py','machine3d.py','runtime3d.py','worker3d.py','base/frozen.py','base/preview.py','base/playback48.py']
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
math_check={n:dict(sha256=sha(P/n),baseline_sha256=sha(B/n),identical=sha(P/n)==sha(B/n)) for n in math_files}
source_hashes={str(f.relative_to(P)).replace('\\','/'):sha(f) for f in P.rglob('*.py') if '__pycache__' not in f.parts}
source_hashes['MONITOR_HASHES.yaml']=sha(P/'MONITOR_HASHES.yaml')
(P/'PROCESS_SOURCE_HASHES.yaml').write_text(store.dumps(source_hashes,indent=2),encoding='utf8')
r=store.loads((P/'PROCESS_REAL_NULL_3S.yaml').read_text(encoding='utf-8-sig'))
f=r['final'];backend=f.get('backend',{})
handoff=dict(written_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source=P.name,baseline=B.name,
    production_entry='demo.py run --dsp-process --preview-inbox-packets 1024 [existing run options]',
    thread_path_remains_default=True,math_sources_unchanged=all(x['identical'] for x in math_check.values()),math_source_checks=math_check,
    new_modules=['process_adapter.py'],changed_modules=['audio_source_router.py','board_follow.py','launcher.py','demo.py','monitor/meeting_av_monitor.py'],
    production_worker='module-level spawn process; OriginalBackend direct owner; no test substitution',
    bounded_ipc=dict(capacity_packets=1024,batch_packets=32,raw_bytes_per_packet=1208,shared_payload_bytes=1024*1208,telemetry_queue=2,no_pcm_feeder=True),
    tests=dict(thread_regression=143,thread_passed=143,independent_process_contracts=8,process_passed=8,process_tests_path=str(P.parent/'process_contract_tests_20261101_c58df0/test_process_contract.py'),preflight='PROCESS_PREFLIGHT.log'),
    real_direct_null=dict(passed=r['passed'],packets_sent=r['packets_sent'],packets_accepted=r['packets_accepted'],counts=f['counts'],worker_pid=f['worker_pid'],native_closed_ack=f['native_closed_ack'],worker_done_ack=f['worker_done_ack'],process_joined=f['process_joined'],worker_exitcode=f['worker_exitcode'],dynamic=backend.get('dynamic',{}).get('event_counts'),active_frames=backend.get('dynamic',{}).get('active_frames'),output_nonzero=backend.get('output',{}).get('nonzero_samples'),scope=r['scope']),
    actual_native_fullmain_validation='PENDING root/app_run serial test; NOT claimed passed',
    schema=dict(child='BoardLock.snapshot.backend is ProcessRawAdapter.snapshot',raw_native='child.backend.output.router_owner_diagnostic.player or .last_player.native',global_credit='child.admitted_packets = child.inbox_current + child.consumer_batch_held',sample_time='child.source_sampled_ns = worker QPC sampling, NOT GUI poll time',native_closed='child.native_closed_ack; future open sealed and actual opened native quiesced',process_end='child.worker_done_ack, child.process_joined, child.worker_exitcode'),
    source_writes_stopped=True,running_implementation_jobs=0,no_device_actions=True)
(P/'PROCESS_HANDOFF.yaml').write_text(store.dumps(handoff,indent=2),encoding='utf8')
print(json.dumps(dict(handoff='PROCESS_HANDOFF.yaml',algorithm_source_identical=handoff['math_sources_unchanged'],process_adapter_sha256=sha(P/'process_adapter.py'),source_hash_manifest=sha(P/'PROCESS_SOURCE_HASHES.yaml'))))
