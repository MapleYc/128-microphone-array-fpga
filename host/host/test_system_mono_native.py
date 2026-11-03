"""Actual native16 methods with fake WinMM; no WinDLL or physical audio."""
import json,threading,time,unittest
from unittest.mock import patch
from audio_playback import WaveOut

class FakeWinMM:
    def __init__(self):
        self.headers=[];self.writes=0;self.prepare_hook=None;self.unprepare_hook=None
        self.write_code=0;self.reset_code=0;self.close_code=0;self.unprepare_code=0
    def waveOutOpen(self,handle,*args):handle._obj.value=99;return 0
    def waveOutPrepareHeader(self,handle,p,size):
        h=p._obj
        if h not in self.headers:self.headers.append(h)
        if self.prepare_hook:
            hook=self.prepare_hook;self.prepare_hook=None;hook()
        return 0
    def waveOutWrite(self,*args):self.writes+=1;return self.write_code
    def waveOutUnprepareHeader(self,*args):
        if self.unprepare_hook:
            hook=self.unprepare_hook;self.unprepare_hook=None;hook()
        return self.unprepare_code
    def waveOutReset(self,*args):
        if not self.reset_code:
            for h in self.headers:h.flags|=1
        return self.reset_code
    def waveOutClose(self,*args):return self.close_code

def player(**kw):
    api=FakeWinMM();p=WaveOut(_test_api=api,_allow_test_api=True,_start_worker=False,**kw)
    return p,api

class ClockedFakeWinMM(FakeWinMM):
    """A deterministic-rate fake driver, never a physical device."""
    def __init__(self,open_delay=0):
        super().__init__();self.open_delay=open_delay;self.queue=[];self.mutex=threading.Lock()
        self.stop=threading.Event();self.driver=None;self.last_end=0.
    def waveOutOpen(self,handle,*args):
        time.sleep(self.open_delay);code=super().waveOutOpen(handle,*args)
        self.driver=threading.Thread(target=self._clock,name='fake16-driver',daemon=True);self.driver.start();return code
    def _clock(self):
        while not self.stop.wait(.0005):
            now=time.perf_counter()
            with self.mutex:
                while self.queue and self.queue[0][0]<=now:
                    _,h=self.queue.pop(0);h.flags|=1
    def waveOutWrite(self,handle,p,size):
        self.writes+=1
        with self.mutex:
            self.last_end=max(time.perf_counter(),self.last_end)+.032
            self.queue.append((self.last_end,p._obj))
        return 0
    def waveOutReset(self,*args):
        with self.mutex:
            self.queue.clear()
            for h in self.headers:h.flags|=1
        return 0
    def waveOutClose(self,*args):
        self.stop.set()
        if self.driver:self.driver.join(.5)
        return 0

class Native16(unittest.TestCase):
    def finish(self,p):
        if p.worker_exit_ns is None:p.request_close('test_end');p._run()
        self.assertTrue(p.snapshot()['ledger_balanced']);self.assertTrue(p.snapshot()['submitted_ledger_balanced'])
    def test_prefill_is_four_real_blocks_and_capacity_unchanged(self):
        p,api=player()
        try:
            p.feed(bytes(1536*2));p._cycle();self.assertFalse(p.started);self.assertEqual(api.writes,0)
            p.feed(bytes(512*2));p._cycle();s=p.snapshot_compact()
            self.assertEqual((s['submitted_samples'],s['inflight_samples'],s['queued_samples']),(2048,2048,0))
            self.assertEqual((s['capacity_samples'],s['prefill_ms']),(8000,128.))
        finally:self.finish(p)
    def test_thread_start_failure_closes_already_opened_device_and_preserves_first_error(self):
        api=FakeWinMM();calls=[];instances=[];original_cleanup=WaveOut._cleanup
        original_open=api.waveOutOpen;original_reset=api.waveOutReset;original_close=api.waveOutClose
        def open_call(*a):calls.append('open');return original_open(*a)
        def reset_call(*a):calls.append('reset');return original_reset(*a)
        def close_call(*a):calls.append('close');return original_close(*a)
        api.waveOutOpen=open_call;api.waveOutReset=reset_call;api.waveOutClose=close_call
        def cleanup(instance):instances.append(instance);return original_cleanup(instance)
        with patch.object(WaveOut,'_cleanup',new=cleanup),patch('audio_playback.threading.Thread.start',side_effect=RuntimeError('injected thread start failed')):
            with self.assertRaisesRegex(RuntimeError,'injected thread start failed'):
                WaveOut(_test_api=api,_allow_test_api=True)
        self.assertEqual(calls,['open','reset','close']);self.assertEqual(len(instances),1)
        s=instances[0].snapshot_compact();self.assertTrue(s['device_closed']);self.assertTrue(s['native_closed_ack'])
        self.assertFalse(s['worker_alive']);self.assertEqual(s['error'],'injected thread start failed')
        self.assertEqual(instances[0].error_info['stage'],'worker_start');self.assertTrue(s['ledger_balanced'])
    def test_overflow_is_explicit_and_rejects_without_discarding_old_audio(self):
        p,_=player()
        try:
            p.feed(bytes(16000))
            with self.assertRaisesRegex(RuntimeError,'overflow'):p.feed(bytes(2))
            s=p.snapshot_compact();self.assertEqual((s['fed_samples'],s['queued_samples'],s['rejected_samples']),(8000,8000,1))
            self.assertEqual(s['dropped_samples'],0);self.assertEqual(s['overflow'],1)
        finally:self.finish(p)
        self.assertEqual(p.snapshot()['cancelled_queued_samples'],8000)
    def test_held_block_does_not_free_software_credit(self):
        p,api=player();p.feed(bytes(16000))
        def during_prepare():
            with self.assertRaisesRegex(RuntimeError,'overflow'):p.feed(bytes(2))
        api.prepare_hook=during_prepare
        try:
            p._cycle();s=p.snapshot_compact();self.assertEqual(s['queued_samples']+s['held_samples'],8000)
            self.assertEqual(api.writes,0)
        finally:self.finish(p)
    def test_closed_and_faulted_feeds_raise_and_never_increase_fed(self):
        p,_=player();p.request_close('test_operator_stop')
        try:
            with self.assertRaisesRegex(RuntimeError,'test_operator_stop'):p.feed(bytes(256))
            self.assertEqual(p.snapshot_compact()['fed_samples'],0)
            p._fail(RuntimeError('first native failure'),'test_stage')
            with self.assertRaisesRegex(RuntimeError,'first native failure'):p.feed(bytes(256))
            self.assertEqual(p.snapshot_compact()['rejected_samples'],256)
        finally:self.finish(p)
    def test_stop_during_prepare_never_writes_and_control_does_not_block(self):
        p,api=player();p.feed(bytes(4096))
        def prepare():
            t=threading.Thread(target=lambda:p.request_close('prepare_cancel'));t.start();t.join(.5)
            self.assertFalse(t.is_alive(),'native Prepare must not hold the state lock')
        api.prepare_hook=prepare;p._run();s=p.snapshot()
        self.assertEqual(api.writes,0);self.assertEqual(s['cancelled_held_samples'],512)
        self.assertEqual(s['cancelled_samples'],2048);self.assertEqual(s['underflow'],0);self.assertTrue(s['native_closed_ack'])
    def test_stop_during_unprepare_prevents_later_writes(self):
        p,api=player();p.feed(bytes(8192));p._cycle();p.headers[0].flags|=1
        api.unprepare_hook=lambda:p.request_close('during_unprepare');p._run();s=p.snapshot()
        self.assertEqual(api.writes,4);self.assertEqual(s['completed_samples'],512)
        self.assertEqual(s['cancelled_samples'],3584);self.assertEqual(s['underflow'],0)
        self.assertTrue(s['ledger_balanced'])
    def test_reset_done_flags_are_cancelled_not_normal_completions(self):
        p,api=player();p.feed(bytes(4096));p._cycle();p.request_close('finite_cancel');p._run();s=p.snapshot()
        self.assertEqual(s['completed_samples'],0);self.assertEqual(s['cancelled_submitted_samples'],2048)
        self.assertTrue(s['submitted_ledger_balanced']);self.assertTrue(s['native_closed_ack'])
    def test_real_completion_before_reset_is_counted_once(self):
        p,api=player();p.feed(bytes(4096));p._cycle();p.headers[0].flags|=1;p.request_close('finite_cancel');p._run();s=p.snapshot()
        self.assertEqual(s['completed_samples'],512);self.assertEqual(s['cancelled_submitted_samples'],1536)
        self.assertTrue(s['ledger_balanced'])
    def test_actual_underflow_records_evidence_and_seals_the_stream(self):
        p,api=player();p.feed(bytes(4096));p._cycle()
        for h in p.headers:h.flags|=1
        p._run();s=p.snapshot()
        self.assertEqual(s['underflow'],1);self.assertIn('underflow',s['error'])
        self.assertEqual(s['completed_samples'],2048);self.assertEqual(s['error_info']['stage'],'starvation')
        self.assertTrue(s['error_info']['feed_trace']);self.assertTrue(s['native_closed_ack'])
        with self.assertRaisesRegex(RuntimeError,'underflow'):p.feed(bytes(1024))
    def test_hardware_empty_is_not_hidden_by_available_software_audio(self):
        p,api=player();p.feed(bytes(8192));p._cycle()
        for h in p.headers:h.flags|=1
        p._run();s=p.snapshot()
        self.assertEqual(api.writes,4,'must not refill first and hide the observed service gap')
        self.assertEqual(s['underflow'],1);self.assertEqual(s['completed_samples'],2048)
        self.assertEqual(s['cancelled_queued_samples'],2048)
    def test_native_write_error_retains_primary_error_and_cancellation_ledger(self):
        p,api=player();p.feed(bytes(4096));api.write_code=7;api.unprepare_code=9;p._run();s=p.snapshot()
        self.assertEqual(s['error'],'waveOutWrite error 7');self.assertEqual(s['error_info']['stage'],'waveOutWrite')
        self.assertEqual(s['cancelled_samples'],2048);self.assertTrue(s['cleanup_errors'])
        self.assertTrue(s['ledger_balanced'])
    def test_close_failure_is_not_a_false_hardware_ack(self):
        p,api=player();api.close_code=7;p.request_close('test_close_error');p._run();s=p.snapshot_compact()
        self.assertFalse(s['device_closed']);self.assertFalse(s['native_closed_ack']);self.assertFalse(s['worker_alive'])
        self.assertIn('waveOutClose',s['error']);self.assertFalse(p.close())
    def test_compact_poll_omits_large_audits_and_empty_pcm_is_not_fabricated(self):
        p,_=player()
        try:
            p.feed(b'');s=p.snapshot_compact();json.dumps(s,allow_nan=False)
            self.assertIs(s['strict_no_drop'],True)
            self.assertFalse(set(('events','feed_trace','error_info','submit_trace','complete_trace'))&set(s))
            self.assertEqual(s['fed_samples'],0);self.assertFalse(s['started'])
            for bad in (b'x',):
                with self.assertRaises(ValueError):p.feed(bad)
            with self.assertRaises(TypeError):p.feed([0,1])
        finally:self.finish(p)
    def test_clear_invalidates_instead_of_silently_mutating_a_live_queue(self):
        p,_=player();p.feed(bytes(256));p.clear()
        try:
            self.assertEqual(p.snapshot_compact()['queued_samples'],128)
            self.assertEqual(p.close_reason,'explicit_clear_invalidates_stream')
            with self.assertRaises(RuntimeError):p.feed(bytes(256))
        finally:self.finish(p)
        self.assertEqual(p.snapshot()['cancelled_samples'],128)

class Native16Threaded(unittest.TestCase):
    def test_real_owner_loop_with_paced_synthetic_input_and_cold_open(self):
        api=ClockedFakeWinMM(open_delay=.2)
        p=WaveOut(_test_api=api,_allow_test_api=True)
        try:
            self.assertGreaterEqual(p.snapshot_compact()['native_api_max_ms']['waveOutOpen'],190)
            start=time.perf_counter()
            for i in range(96):
                left=start+i*.008-time.perf_counter()
                if left>0:time.sleep(left)
                p.feed(b'\x01\0'*128)
            s=p.snapshot_compact()
            self.assertIsNone(s['error']);self.assertEqual(s['fed_samples'],12288)
            self.assertEqual(s['underflow'],0);self.assertEqual(s['overflow'],0)
            self.assertGreater(s['completed_samples'],4096)
            self.assertLessEqual(s['peak_software_samples'],8000)
        finally:self.assertTrue(p.close('finite_fake_paced_test_end'))
        s=p.snapshot_compact();self.assertTrue(s['ledger_balanced']);self.assertTrue(s['submitted_ledger_balanced'])
        self.assertFalse(s['worker_alive']);self.assertFalse(api.driver.is_alive());self.assertTrue(s['native_closed_ack'])

if __name__=='__main__':unittest.main()
