"""Pure/fake telemetry and immutable data-flow tests; no device or network."""
import collections,copy,multiprocessing as mp,threading,time,types,unittest
from types import MappingProxyType
from unittest.mock import patch
from legacy_adapter import (OriginalBackend,ValidatedDatagram,_Output,
                            compact_backend_state,compact_native_state)
from process_adapter import (ProcessRawAdapter,RemotePCSink,SharedInbox,_native_close_proven,
                             _constructor_close_proven,_finalization_failure,_get_text)

class NoCopy:
    def __deepcopy__(self,memo):raise AssertionError('heavy history was copied on control path')

def adapter():
    a=ProcessRawAdapter.__new__(ProcessRawAdapter);a.generation=700
    a._cache_lock=threading.Lock();a._cache={};a._full_audit={};a._versions=None
    a._versions_sampled_ns=None;a._versions_digest=None;a._publication_rejections=collections.Counter()
    a._thread_done=threading.Event();a.shared=types.SimpleNamespace(native_closed=threading.Event(),done=threading.Event(),pid=types.SimpleNamespace(value=12))
    a.health=lambda:dict(state='ready');a._test=True
    a.process=types.SimpleNamespace(exitcode=None,is_alive=lambda:True)
    return a

def row(at,kind='light',*,generation=700,completed=20,closed=False,final=False):
    native=dict(fed_samples=completed+2,submitted_samples=completed+1,completed_samples=completed,
                closed=closed,opened_ns=1,error=None,monitor_gain=2.,events=[dict(event='opened')],
                error_info=dict(message='example',feed_trace=[dict(at_ns=10)]))
    return dict(generation=generation,source_sampled_ns=at,publication_kind=kind,parent_received_at=time.monotonic(),
                final=final,backend=dict(counts=dict(spatial_frames=completed),output=dict(samples=completed,player=native),
                dynamic=dict(active_frames=1,event_counts=dict(accepted=1),latest_events=[NoCopy()]),
                last_frame_audits=[dict(raw_history='preserved full')],versions=dict(build='real-build')),
                router=dict(mode=4,player=native,player_lifecycle=[NoCopy()]))

class Compact(unittest.TestCase):
    def test_full_projection_does_not_copy_histories(self):
        v=row(10,'full')['backend'];v['last_frame_audits']=[NoCopy()];v['versions']=NoCopy()
        out=compact_backend_state(v)
        self.assertNotIn('versions',out);self.assertNotIn('last_frame_audits',out)
        self.assertNotIn('latest_events',out['dynamic']);self.assertNotIn('events',out['output']['player'])
        self.assertNotIn('feed_trace',out['output']['player']['error_info'])
        self.assertEqual(out['dynamic']['event_counts']['accepted'],1)
    def test_backend_light_does_not_scan_percentiles_or_audit(self):
        b=OriginalBackend.__new__(OriginalBackend)
        b.pipeline=types.SimpleNamespace(counts=dict(spatial_frames=3),cont=types.SimpleNamespace(counts=dict(accepted_samples=1536)),
                                        post=types.SimpleNamespace(sent=3,received=2))
        b.output=types.SimpleNamespace(snapshot_light=lambda:dict(samples=512))
        b.active_frames=2;b.tracker_valid_frames=2;b.control_event_counts=collections.Counter(accepted=1)
        b.control_events_overflow=0;b.first_accepted=dict(frame=1);b.last_tracker_decision=dict(valid=True)
        b.frame_audits=NoCopy();b.versions=NoCopy();b._telemetry_timing=collections.Counter()
        out=b.snapshot_light();self.assertEqual(out['post_pending'],1)
        self.assertNotIn('versions',out);self.assertNotIn('spatial_ms',out);self.assertNotIn('last_frame_audits',out)
    def test_output_prefers_compact_owner_api(self):
        o=_Output.__new__(_Output);o.router_owner=9
        o.router=types.SimpleNamespace(owner_diagnostics=lambda _:self.fail('heavy owner called'),
            owner_diagnostics_compact=lambda owner:dict(owner=owner,player=dict(completed_samples=512)))
        o.null=types.SimpleNamespace(samples=512,clipped=0);o.nonzero=500;o.peak_abs=.1;o.suppressed=0
        o.enable_playback=True;o.prepare_ms=1;o.stop_requested_ns=None;o.stop_reason=None
        self.assertEqual(o.snapshot_light()['player']['completed_samples'],512)
    def test_late_initial_full_never_replaces_new_control(self):
        a=adapter();self.assertTrue(a._accept_publication(row(20,completed=200,closed=True)))
        self.assertFalse(a._accept_publication(row(10,'full',completed=1)))
        snap=a.snapshot_compact();self.assertEqual(snap['source_sampled_ns'],20)
        self.assertEqual(snap['backend']['output']['player']['completed_samples'],200)
        self.assertTrue(snap['backend']['output']['player']['closed'])
        self.assertEqual(snap['full_audit_sampled_ns'],10);self.assertEqual(snap['versions_source_sampled_ns'],10)
        self.assertNotIn('versions',snap['backend']);self.assertIsNotNone(snap['versions_digest_sha256'])
    def test_wrong_generation_or_bad_envelope_does_not_change_cache(self):
        a=adapter();a._accept_publication(row(20));before=a._cached()
        self.assertFalse(a._accept_publication(row(30,generation=701)))
        bad=row(30);bad['source_sampled_ns']='not a timestamp';self.assertFalse(a._accept_publication(bad))
        self.assertEqual(a._cached(),before)
    def test_final_full_keeps_forensic_data_only_in_explicit_api(self):
        a=adapter();a._accept_publication(row(10,'full'));a._accept_publication(row(20))
        full=row(30,'full',completed=333,closed=True,final=True)
        # The synthetic poison fields model histories but explicit deepcopy is
        # expected to work with real numeric JSON, so use a numeric full audit.
        full['backend']['dynamic']['latest_events']=[dict(event='accepted')];full['router']['player_lifecycle']=[]
        self.assertTrue(a._accept_publication(full));compact=a.snapshot_compact()
        self.assertTrue(compact['full_audit_is_final']);self.assertNotIn('last_frame_audits',compact['backend'])
        forensic=a.snapshot_full_cached();self.assertTrue(forensic['full_audit_is_final'])
        self.assertEqual(forensic['full_audit_generation'],700);self.assertEqual(forensic['full_audit_sampled_ns'],30)
        self.assertEqual(forensic['backend']['versions']['build'],'real-build')
        self.assertEqual(forensic['backend']['output']['player']['error_info']['feed_trace'][0]['at_ns'],10)
        self.assertFalse(a._accept_publication(row(15,'full')))
        self.assertEqual(a._cache['source_sampled_ns'],30);self.assertEqual(a._full_audit['source_sampled_ns'],30)
    def test_remote_ui_copies_only_native_not_unrelated_backend(self):
        a=adapter();a._accept_publication(row(20));a._cache['backend']['unrelated_heavy']=NoCopy()
        a._cache['router']['output_level']=dict(nonzero_samples=100,ac_dbfs=-20.)
        a._cached=lambda:self.fail('remote UI must not copy whole backend')
        p=RemotePCSink(a).snapshot();self.assertEqual(p['completed_samples'],20)
        self.assertNotIn('events',p);self.assertNotIn('feed_trace',p['error_info'])
        self.assertEqual(p['monitor_gain'],2.)
        self.assertEqual(p['remote_output_level']['nonzero_samples'],100)
    def test_native_held_cancelled_and_close_ledger_survives_remote_projection(self):
        a=adapter();message=row(20)
        ledger=dict(held_samples=512,cancelled_unsubmitted_samples=1024,
            cancelled_submitted_unconfirmed_samples=512,ledger_balanced=True,
            submitted_ledger_balanced=True,device_closed=False,
            cleanup_errors=[dict(api='waveOutClose',error='explicit close failed',code=1)])
        message['router']['player'].update(ledger)
        a._accept_publication(message)
        projected=RemotePCSink(a).snapshot()
        for key,value in ledger.items():self.assertEqual(projected[key],value,key)
        self.assertFalse(projected['native_closed_ack'])
        self.assertNotIn('events',projected);self.assertNotIn('feed_trace',projected['error_info'])

class NativeCloseProof(unittest.TestCase):
    def router(self,*devices,preparing=None):
        values=list(devices)+[None]*3
        return types.SimpleNamespace(lock=threading.RLock(),player=values[0],retiring=values[1],
                                     _last_player_sink=values[2],preparing=preparing)
    def test_no_attempt_is_proven_no_native_device(self):
        self.assertTrue(_native_close_proven(None,None,attempted=False))
    def test_dead_thread_does_not_replace_device_close_ack(self):
        p=types.SimpleNamespace(device_closed=False,thread=types.SimpleNamespace(is_alive=lambda:False))
        r=self.router(None,None,p)
        self.assertFalse(_native_close_proven(r,None,attempted=True))
        p.device_closed=True;self.assertTrue(_native_close_proven(r,None,attempted=True))
    def test_unconfirmed_retiring_or_preparing_device_blocks_ack(self):
        closed=types.SimpleNamespace(device_closed=True);unknown=types.SimpleNamespace(device_closed=None)
        self.assertFalse(_native_close_proven(self.router(closed,unknown,closed),None,attempted=True))
        self.assertFalse(_native_close_proven(self.router(closed,preparing={}),None,attempted=True))
        self.assertFalse(_native_close_proven(self.router(),None,attempted=True))
    def test_fake_actor_requires_its_explicit_close_timestamp(self):
        b=types.SimpleNamespace(native_open_ns=10,native_close_ns=None)
        self.assertFalse(_native_close_proven(None,b,attempted=False,testing=True))
        b.native_close_ns=9;self.assertFalse(_native_close_proven(None,b,attempted=False,testing=True))
        b.native_close_ns=10;self.assertTrue(_native_close_proven(None,b,attempted=False,testing=True))
    def test_cleanup_failure_preserves_primary_underflow_and_never_acks(self):
        s=SharedInbox(mp.get_context('spawn'),4)
        with s.lock:s._fail_locked('original underflow')
        _finalization_failure(s,'native close',RuntimeError('waveOutClose returned nonzero'))
        self.assertEqual(_get_text(s.error),'original underflow')
        self.assertIn('waveOutClose',s.health()['native_close_error'])
        self.assertFalse(s.native_closed.is_set())
    def test_constructor_uses_boolean_evidence_not_error_words(self):
        unknown=RuntimeError('device closed successfully is only text')
        self.assertFalse(_constructor_close_proven(unknown))
        known=RuntimeError('thread start failed');known.native_device_opened=True;known.native_device_closed=True
        self.assertTrue(_constructor_close_proven(known))
        self.assertTrue(_native_close_proven(self.router(),None,attempted=True,construction_closed=True))
        still_open=types.SimpleNamespace(device_closed=False)
        self.assertFalse(_native_close_proven(self.router(still_open),None,attempted=True,construction_closed=True))
        known.native_device_closed=False;self.assertFalse(_constructor_close_proven(known))
    def test_spawn_failure_has_no_native_ack_when_audio_may_have_started(self):
        real=mp.get_context('spawn');seen=[]
        class FakeProcess:
            def start(self):raise RuntimeError('partial spawn outcome')
        class Context:
            def __getattr__(self,name):return getattr(real,name)
            def Process(self,**kw):seen.append(kw['args'][0]);return FakeProcess()
        with patch('process_adapter.mp.get_context',return_value=Context()):
            with self.assertRaisesRegex(RuntimeError,'partial spawn outcome'):
                ProcessRawAdapter({},lock_deg=0,enable_playback=True)
        self.assertEqual(len(seen),1);self.assertFalse(seen[0].native_closed.is_set())
        self.assertTrue(seen[0].done.is_set());self.assertIn('partial spawn',_get_text(seen[0].error))

class TimingAndData(unittest.TestCase):
    def backend(self):
        b=OriginalBackend.__new__(OriginalBackend);b._telemetry_timing=collections.Counter();b._post_sent_at=collections.OrderedDict()
        return b
    def test_post_timing_keeps_exact_objects_and_reply_order(self):
        b=self.backend();arr=object();calls=[]
        b._original_post_send=lambda s,n,x:calls.append((s,n,x))
        messages=[('audio',2,7,b'exact payload',0.),('reset',2)]
        b._original_post_poll=lambda:messages
        b._post_send_timed(2,7,arr);self.assertIs(calls[0][2],arr)
        self.assertIs(b._post_poll_timed(),messages);self.assertFalse(b._post_sent_at)
        t=b._timing_snapshot();self.assertEqual(t['post_roundtrip_calls'],1)
        self.assertEqual(t['post_poll_calls'],1);self.assertGreaterEqual(t['post_noncompute_roundtrip_total_ns'],0)
    def test_collect_exception_is_not_hidden(self):
        b=self.backend();b._original_collect=lambda:(_ for _ in ()).throw(ValueError('real collect fault'))
        with self.assertRaisesRegex(ValueError,'real collect fault'):b._collect_timed()
        self.assertEqual(b._telemetry_timing['collect_calls'],1)
    def test_metadata_only_token_materializes_pcm_without_reparse(self):
        pcm=[bytes([i])*384 for i in (1,2,3)]
        raw=bytes(32)+b''.join(bytes(8)+p for p in pcm)
        meta=MappingProxyType(dict(epoch=4,sequence=10,frame_ids=(30,31,32),flags=0))
        b=self.backend();calls=[];b.pipeline=types.SimpleNamespace(consume_packet=lambda data,**kw:calls.append((data,kw)))
        b.consume(ValidatedDatagram(raw,meta))
        self.assertIs(calls[0][0],raw);self.assertEqual(calls[0][1]['decoded']['pcm24le'],b''.join(pcm))
        self.assertNotIn('pcm24le',meta);self.assertEqual(calls[0][1]['decoded']['sequence'],10)
    def test_existing_pcm_token_is_not_materialized_again(self):
        raw=bytes(1208);meta=MappingProxyType(dict(pcm24le=b'existing'))
        b=self.backend();calls=[];b.pipeline=types.SimpleNamespace(consume_packet=lambda data,**kw:calls.append(kw['decoded']))
        b.consume(ValidatedDatagram(raw,meta));self.assertIs(calls[0],meta)

class FakeProcess(unittest.TestCase):
    def test_periodic_publication_is_light_and_final_audit_preserved(self):
        a=ProcessRawAdapter({},lock_deg=0,capacity=1024,test_worker_spec={},allow_test_worker=True)
        try:
            self.assertTrue(a.wait_ready(5))
            limit=time.monotonic()+2
            while time.monotonic()<limit and a.health()['publication_timing']['light_calls']<1:time.sleep(.01)
            h=a.health();self.assertGreaterEqual(h['publication_timing']['light_calls'],1)
            self.assertEqual(h['publication_timing']['full_calls'],1)
        finally:a.close();self.assertTrue(a.wait_closed(8))
        final=a.snapshot_full_cached();self.assertTrue(final['full_audit_is_final'])
        self.assertEqual(final['full_audit_generation'],a.generation)
        self.assertTrue(a.shared.native_closed.is_set());self.assertGreaterEqual(a.health()['publication_timing']['full_calls'],2)

if __name__=='__main__':unittest.main()
