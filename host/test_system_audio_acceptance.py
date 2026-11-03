"""No real files/devices/GUI: injectable status, clock and control-file actor."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import copy,datetime as dt,io,json,unittest
from contextlib import redirect_stdout
import system_audio_acceptance as acceptance

PID=123;SHA='a'*64
class FakeClock:
    def __init__(self):self.t=0.;self.base=dt.datetime(2026,11,2,tzinfo=dt.timezone.utc).timestamp();self.hook=None
    def monotonic(self):return self.t
    def wall(self):return self.base+self.t
    def sleep(self,seconds):
        self.t+=seconds
        if self.hook:self.hook(seconds)

class FakeIO:
    def __init__(self,clock):
        self.clock=clock;self.files={'LAUNCH.yaml':dict(pid=PID,candidate_manifest_sha256=SHA)}
        self.claimed=False;self.sent=[];self.events=[];self.mode=0;self.display=True;self.locked=True
        self.epoch=0;self.generation=0;self.gain=12.;self.gain_serial=0;self.done=0;self.raw=0;self.video=0;self.presented=0
        self.raw_gaps=0;self.underflow=0;self.native_error=None;self.sticky=False;self.stale=0.;self.fail_after=None;self.loss_after=None
        self.clock.hook=self.advance
    def advance(self,seconds):
        self.raw+=int(16000*seconds);self.video+=int(10*seconds)
        if self.display:self.presented+=int(10*seconds)
        if self.mode:self.done+=int(({2:16000,3:16000,4:48000}[self.mode])*seconds)
        if self.fail_after is not None and self.clock.t>=self.fail_after:
            self.native_error='injected fake native underflow';self.underflow=1;self.sticky=True
        if self.loss_after is not None and self.clock.t>=self.loss_after:self.raw_gaps=3
    def live(self):
        timestamp=dt.datetime.fromtimestamp(self.clock.wall()-self.stale,dt.timezone.utc).isoformat()
        rate={0:None,2:16000,3:16000,4:48000}[self.mode]
        native=None if not self.mode else dict(rate=rate,fed_samples=self.done,submitted_samples=self.done,completed_samples=self.done,
            queued_samples=0,inflight_samples=0,dropped_samples=0,underflow=self.underflow,overflow=0,
            error=self.native_error,closed=False,device_closed=False,strict_no_drop=True,
            ledger_balanced=True,submitted_ledger_balanced=True,remote_generation=self.generation,monitor_gain=10**(self.gain/20))
        gain=dict(gain_db=self.gain,requested_gain_db=self.gain,applied_gain_db=self.gain,pending=False)
        backend=dict(generation=self.generation,worker_alive=self.mode==4,state='failed' if self.native_error else 'ready',
            playback_gain=dict(requested_serial=self.gain_serial,applied_serial=self.gain_serial,pending=False))
        return dict(pid=PID,utc=timestamp,media_recording=False,process_cpu_seconds=self.clock.t*.1,
            status=dict(mode='LOCKED' if self.locked else 'SEARCH',source_frame=self.raw*3,epoch=1),
            host_preview=dict(target_status=dict(mode='LOCKED' if self.locked else 'SEARCH'),status_age_s=.01,
                backend_owner='active' if self.mode==4 else 'retired_cache',diagnostic_stale=False,
                state='ready',error=self.native_error if self.mode==4 else None,sticky_failure=self.sticky,generation=self.generation,backend=backend),
            audio_source=dict(mode=self.mode,gain=gain,player=native,raw=dict(samples=self.raw,peak=100),beam={},
                output=dict(peak=100,ac_dbfs=-30),audio_route=dict(mode=self.mode,epoch=self.epoch,sink_open=bool(self.mode),
                  transition_pending=False,pc_preparing=False,board_preparing=False,error=self.native_error)),
            raw128=dict(datagrams=self.raw,packet_gaps=self.raw_gaps,frame_gaps=self.raw_gaps*3,recorded_bytes=0),
            video_counts=dict(complete=self.video),display_enabled=self.display,display=dict(presented_frames=self.presented),errors={})
    def read(self,name,optional=False):
        if name=='LIVE_STATUS.yaml':return self.live()
        if name in self.files:return copy.deepcopy(self.files[name])
        if optional:return None
        raise FileNotFoundError(name)
    def exists(self,name):return name in self.files
    def claim(self):
        if self.claimed:raise acceptance.AcceptanceError('OTHER_CONTROLLER_LOCK')
        self.claimed=True
    def release(self):self.claimed=False
    def request(self,value):
        self.sent.append(copy.deepcopy(value));self.files['CONTROL_REQUEST.yaml']=copy.deepcopy(value)
        command=value['command']
        if command=='select_audio':
            mode=value['mode']
            if mode!=self.mode:self.done=0;self.epoch+=1;self.generation+=1
            self.mode=mode;self.native_error=None;self.underflow=0;self.sticky=False
        elif command=='diagnostic_display':self.display=value['enabled']
        elif command=='set_gain':self.gain=value['gain_db'];self.gain_serial+=1
        self.files['CONTROL_REPLY.yaml']=dict(sequence=value['sequence'],pid=PID,command=command,status='APPLIED')
    def emit(self,kind,data):self.events.append((kind,copy.deepcopy(data)))

class ControllerTests(unittest.TestCase):
    def make(self):
        clock=FakeClock();io_=FakeIO(clock);c=acceptance.Controller(io_,PID,SHA,clock=clock,poll=.1,prepare_timeout=1,command_timeout=.5)
        c.acquire();self.addCleanup(lambda:c.cleanup() if c.claimed else None)
        return c,io_,clock
    def test_default_plan_never_constructs_fileio(self):
        with redirect_stdout(io.StringIO()) as out:self.assertEqual(acceptance.main([]),0)
        self.assertEqual(json.loads(out.getvalue())['status'],'PLAN_ONLY_NO_IO')
    def test_unlocked_board_never_gets_enhanced_select(self):
        c,f,t=self.make();f.locked=False
        with self.assertRaises(acceptance.AcceptanceError) as error:c.row('enhanced',4,False,.3)
        self.assertEqual(error.exception.code,'NEEDS_REAL_USER_YES_LOCK');self.assertFalse(f.sent)
        with self.assertRaises(acceptance.AcceptanceError):c.command('select_audio',mode=4)
        self.assertFalse(f.sent)
    def test_pending_control_is_not_overwritten_or_retried(self):
        c,f,t=self.make();f.files['CONTROL_REQUEST.yaml']={'sequence':9,'command':'select_audio','mode':2}
        f.files['CONTROL_REPLY.yaml']={'sequence':8,'pid':PID,'command':'select_audio','status':'APPLIED'}
        with self.assertRaises(acceptance.AcceptanceError) as error:c.command('diagnostic_display',enabled=False)
        self.assertEqual(error.exception.code,'CONTROL_ALREADY_PENDING');self.assertFalse(f.sent)
        cleanup=c.cleanup();self.assertEqual(cleanup[0]['status'],'NOT_CONFIRMED_NO_RETRY');self.assertFalse(f.sent)
    def test_pid_manifest_stale_or_closed_never_controls_other_app(self):
        for kind in ('pid','manifest','stale','closed'):
            with self.subTest(kind=kind):
                c,f,t=self.make()
                if kind=='pid':f.files['LAUNCH.yaml']['pid']=999
                elif kind=='manifest':f.files['LAUNCH.yaml']['candidate_manifest_sha256']='b'*64
                elif kind=='stale':f.stale=3
                else:f.files['CLOSED.yaml']={}
                with self.assertRaises(acceptance.AcceptanceError):c.command('select_audio',mode=2)
                self.assertFalse(f.sent);c.cleanup()
    def test_four_short_fake_rows_keep_network_while_display_off(self):
        c,f,t=self.make()
        for name,mode,display in acceptance.ROWS:
            result=c.row(name,mode,display,.4)
            self.assertTrue(result['overall_pass'],result)
            self.assertEqual(result['rate_hz'],16000 if mode==2 else 48000)
            self.assertTrue(result['completed_full_duration']);self.assertFalse(result['acoustic_quality_accepted'])
            self.assertGreater(result['end']['raw']['datagrams'],result['start']['raw']['datagrams'])
            if not display:self.assertEqual(result['start']['presented_frames'],result['end']['presented_frames'])
        self.assertTrue(all(command['command']!='close' for command in f.sent))
    def test_raw_loss_and_audio_failure_are_separate_dimensions(self):
        c,f,t=self.make();f.loss_after=.2
        result=c.row('mono',2,False,.4)
        self.assertTrue(result['audio_integrity_pass']);self.assertFalse(result['raw_integrity_pass']);self.assertFalse(result['overall_pass'])
        self.assertIn('RAW_PACKET_LOSS',result['warnings'])
        c2,f2,t2=self.make();f2.fail_after=.2
        failed=c2.row('enhanced',4,True,.4)
        self.assertFalse(failed['audio_integrity_pass']);self.assertIn('AUDIO_ERROR',failed['failures'])
        self.assertFalse(failed['completed_full_duration'])
    def test_retired_ledger_does_not_make_current_audio_ready(self):
        c,f,t=self.make();live=f.live();live['audio_source']['mode']=4
        live['audio_source']['audio_route'].update(mode=4,sink_open=False,last_player={'native':{'rate':48000,'completed_samples':99999}})
        self.assertFalse(c.is_ready(live,4))
    def test_real_generation_and_gain_application_are_required(self):
        c,f,t=self.make();c.prepare(4,True);good=f.live();self.assertTrue(c.is_ready(good,4))
        wrong=copy.deepcopy(good);wrong['audio_source']['player']['remote_generation']+=1;self.assertFalse(c.is_ready(wrong,4))
        pending=copy.deepcopy(good);pending['audio_source']['gain']['pending']=True;self.assertFalse(c.is_ready(pending,4))
    def test_normal_sequence_and_existing_error_recovery_never_inject_fault(self):
        c,f,t=self.make();result=c.sequence_test(.1)
        self.assertEqual(result['status'],'NORMAL_SEQUENCE_OBSERVED')
        self.assertEqual([v['mode'] for v in f.sent if v['command']=='select_audio'],[2,4,2,0])
        self.assertEqual([v['gain_db'] for v in f.sent if v['command']=='set_gain'],[6.,12.])
        before=len(f.sent);self.assertEqual(c.recover_existing_error()['status'],'NO_EXISTING_ERROR_NO_FAULT_INJECTED');self.assertEqual(len(f.sent),before)
        f.mode=4;f.sticky=True;f.native_error='actual simulated existing fault';old=f.generation
        # Simulate the real bridge's explicit retry creating a new generation.
        original=f.request
        def request(value):
            if value['command']=='select_audio' and value.get('mode')==4:f.mode=0
            original(value)
        f.request=request
        recovered=c.recover_existing_error();self.assertGreater(f.generation,old)
        self.assertIn('READY',recovered['status'])
    def test_unknown_conservation_is_not_invented_pass(self):
        c,f,t=self.make();c.prepare(4,True);a=f.live();t.sleep(.2);b=f.live()
        for x in (a,b):x['audio_source']['player'].pop('ledger_balanced')
        result=c.classify(a,b,[a,b],4,True)
        self.assertTrue(result['audio_integrity_pass']);self.assertFalse(result['overall_pass']);self.assertFalse(result['conservation_verified'])
    def test_unknown_raw_and_48k_drop_cannot_be_full_pass(self):
        c,f,t=self.make();c.prepare(4,True);a=f.live();t.sleep(.2);b=f.live()
        for x in (a,b):x['raw128'].pop('packet_gaps')
        result=c.classify(a,b,[a,b],4,True)
        self.assertFalse(result['raw_integrity_pass']);self.assertIsNone(result['raw_packet_gap_delta'])
        self.assertIn('RAW_GAP_COUNTER_UNKNOWN',result['warnings'])
        b['audio_source']['player']['dropped_samples']=64
        result=c.classify(a,b,[a,b],4,True)
        self.assertFalse(result['audio_integrity_pass']);self.assertIn('AUDIO_DROPPED_OR_UNKNOWN',result['failures'])

    def test_strict_48k_actual_credit_schema_can_prove_no_unaccounted_loss_without_faking_drop_counter(self):
        c,f,t=self.make();c.prepare(4,True);a=f.live();t.sleep(.2);b=f.live()
        for x in (a,b):
            p=x['audio_source']['player'];p['dropped_samples']=None
            p.update(held_samples=0,cancelled_unsubmitted_samples=0,cancelled_submitted_unconfirmed_samples=0)
        result=c.classify(a,b,[a,b],4,True)
        self.assertTrue(result['overall_pass']);self.assertIsNone(b['audio_source']['player']['dropped_samples'])
        self.assertIn('TWO_REBUILT_CREDIT_LEDGERS',result['native_drop_evidence'][0])
        b['audio_source']['player']['held_samples']=None
        self.assertFalse(c.classify(a,b,[a,b],4,True)['overall_pass'])

    def test_strict_flag_and_claimed_boolean_cannot_replace_actual_credit_accounting(self):
        c,f,t=self.make();c.prepare(4,True);a=f.live();t.sleep(.2);b=f.live()
        for x in (a,b):
            p=x['audio_source']['player'];p['dropped_samples']=None
            p.update(held_samples=1,cancelled_unsubmitted_samples=0,cancelled_submitted_unconfirmed_samples=0)
        result=c.classify(a,b,[a,b],4,True)
        self.assertFalse(result['overall_pass']);self.assertIn('AUDIO_DROPPED_OR_UNKNOWN',result['failures'])
    def test_accepted_format_and_cancel_are_current_owner_checks(self):
        c,f,t=self.make();c.prepare(4,True);a=f.live();t.sleep(.2);b=f.live()
        a['audio_source']['player']['accepted_format']={'rate':48000,'channels':1,'bits':16}
        b['audio_source']['player']['accepted_format']={'rate':16000,'channels':1,'bits':16}
        self.assertFalse(c.is_ready(b,4))
        a['audio_source']['player']['cancelled_unsubmitted_samples']=0
        b['audio_source']['player']['cancelled_unsubmitted_samples']=32
        result=c.classify(a,b,[a,b],4,True)
        self.assertIn('NATIVE_ACCEPTED_FORMAT_MISMATCH',result['failures'])
        self.assertIn('AUDIO_REJECTED_OR_CANCELLED_DURING_ROW',result['failures'])

class FileStorageTests(unittest.TestCase):
    def test_real_controller_files_are_yaml_and_stream_keeps_event_order(self):
        import tempfile
        from pathlib import Path
        from system_audio_acceptance import FileIO
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);run=root/'session';run.mkdir()
            (run/'LAUNCH.yaml').write_text(store.dumps(dict(pid=PID)),encoding='utf-8')
            io=FileIO(run,root/'results')
            self.assertEqual(io.read('LAUNCH.yaml')['pid'],PID)
            io.claim()
            self.assertEqual(store.load_file(io.lock)['token'],io.token)
            io.request(dict(sequence=1,command='set_gain',gain_db=3))
            self.assertEqual(io.read('CONTROL_REQUEST.yaml')['sequence'],1)
            self.assertFalse(list(run.glob('*.writing')))
            io.emit('command_issued',dict(sequence=1))
            io.emit('command_ack',dict(sequence=1))
            events=store.load_stream((io.out/'observations.log').read_text(encoding='utf-8'))
            self.assertEqual([item['kind'] for item in events],['command_issued','command_ack'])
            io.result(dict(status='OFFLINE_IO_PASS'))
            self.assertEqual(store.load_file(io.out/'RESULT.yaml')['status'],'OFFLINE_IO_PASS')
            with self.assertRaises(ValueError):io.emit('invalid',dict(value=float('nan')))
            io.release();self.assertFalse(io.lock.exists())
            self.assertFalse([p for p in root.rglob('*') if p.suffix in ('.json','.jsonl')])


if __name__=='__main__':unittest.main()
