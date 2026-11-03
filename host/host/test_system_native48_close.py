"""Real WaveOut48 lifecycle against fake WinMM; no device or worker opened."""
import collections
import ctypes as C
import threading
import unittest
from unittest.mock import patch
import numpy as np
from base import playback48


class Call:
    def __init__(self,api,name):self.api,self.name=api,name;self.argtypes=None
    def __call__(self,*args):
        self.api.calls.append(self.name)
        callback=self.api.before.get(self.name)
        if callback:callback(*args)
        result=self.api.results.get(self.name,0)
        if isinstance(result,BaseException):raise result
        if self.name=='waveOutOpen' and result==0:args[0]._obj.value=123
        return result


class FakeWinMM:
    def __init__(self,close_result=0):
        self.calls=[];self.before={};self.results={'waveOutClose':close_result}
        for name in ('waveOutOpen','waveOutPrepareHeader','waveOutWrite',
                     'waveOutUnprepareHeader','waveOutReset','waveOutClose'):
            setattr(self,name,Call(self,name))


def unstarted_player(api):
    p=playback48.WaveOut48.__new__(playback48.WaveOut48)
    p.lib=api;p.handle=C.c_void_p(123);p.headers=[];p.size=0;p.busy=[];p.prepared=[]
    p.q=collections.deque();p.capacity=12;p.prefill=4;p.gain=1.;p.started=True
    p.lock=threading.Lock();p.closed=False;p.device_closed=False;p.cleanup_errors=[]
    p.error='original underflow';p.error_info=dict(type='RuntimeError',message=p.error,
        at_ns=101,stage='starvation',feed_trace=[{'at_ns':97,'fed_samples':512}])
    p.counts=collections.Counter();p.thread=threading.current_thread()
    return p


class Header(C.Structure):
    _fields_=[('data',C.c_void_p),('flags',C.c_uint)]


def ledger_player(*,queued=0,held=0,inflight=0,done=False,close_result=0):
    p=unstarted_player(FakeWinMM(close_result))
    p.buffers=[C.create_string_buffer(1024) for _ in range(4)]
    p.headers=[Header(C.addressof(b),1 if done else 0) for b in p.buffers]
    p.size=C.sizeof(Header);p.busy=[i<inflight for i in range(4)]
    p.prepared=[i<inflight for i in range(4)]
    p.q=collections.deque(bytes(1024) for _ in range(queued));p.held_samples=held*512
    p.counts=collections.Counter(fed_samples=(queued+held+inflight)*512,submitted_samples=inflight*512)
    p.error=None;p.error_info=None;p.cleanup_finished=False
    return p


class Native48Close(unittest.TestCase):
    def constructor_failure(self,close_result=0,*,thread_constructor=False):
        api=FakeWinMM(close_result);failure=RuntimeError('injected thread start failure');captured=[]
        class NoThread:
            def __init__(self,**kwargs):
                captured.append(kwargs['target'].__self__)
                if thread_constructor:raise failure
            def start(self):raise failure
            def is_alive(self):return False
        with patch.object(playback48.C,'WinDLL',return_value=api),patch.object(playback48.threading,'Thread',NoThread):
            with self.assertRaises(RuntimeError) as raised:playback48.WaveOut48()
        self.assertIs(raised.exception,failure)
        self.assertEqual(api.calls,['waveOutOpen','waveOutClose'])
        self.assertEqual(len(captured),1)
        return captured[0],failure

    def test_thread_start_failure_closes_once_and_keeps_original_exception(self):
        p,exc=self.constructor_failure()
        self.assertTrue(p.closed);self.assertTrue(p.device_closed)
        self.assertTrue(exc.native_device_opened);self.assertTrue(exc.native_device_closed)
        self.assertEqual(p.error,'injected thread start failure')
        self.assertEqual(p.error_info['stage'],'worker_start')
        self.assertEqual(p.cleanup_errors,[])
        self.assertEqual((p.prefill,p.capacity,p.gain),(4,12,1.))

    def test_thread_construction_failure_also_closes_open_handle_once(self):
        p,exc=self.constructor_failure(thread_constructor=True)
        self.assertTrue(p.device_closed);self.assertTrue(exc.native_device_closed)

    def test_start_failure_with_nonzero_close_is_not_a_close_ack(self):
        p,exc=self.constructor_failure(5)
        self.assertFalse(p.device_closed);self.assertFalse(exc.native_device_closed)
        self.assertEqual(p.error,'injected thread start failure')
        self.assertEqual(p.error_info['stage'],'worker_start')
        self.assertEqual(p.cleanup_errors,[dict(api='waveOutClose',error='waveOut error 5')])

    def test_start_failure_with_close_exception_is_not_a_close_ack(self):
        p,exc=self.constructor_failure(OSError('injected close exception'))
        self.assertFalse(p.device_closed);self.assertFalse(exc.native_device_closed)
        self.assertEqual(p.error_info['stage'],'worker_start')
        self.assertEqual(p.cleanup_errors,[dict(api='waveOutClose',error='injected close exception')])

    def test_normal_worker_close_success_is_a_real_ack_and_preserves_first_error(self):
        p=unstarted_player(FakeWinMM());original=p.error_info
        p._run()
        self.assertTrue(p.device_closed)
        self.assertEqual(p.error,'original underflow');self.assertIs(p.error_info,original)
        self.assertEqual(p.lib.calls.count('waveOutClose'),1)

    def test_worker_close_return_error_preserves_underflow_and_no_ack(self):
        p=unstarted_player(FakeWinMM(5));original=p.error_info
        p._run();s=p.snapshot()
        self.assertFalse(s['device_closed'])
        self.assertEqual(s['error'],'original underflow');self.assertIs(s['error_info'],original)
        self.assertEqual(s['cleanup_errors'],[dict(api='waveOutClose',error='waveOut error 5')])
        self.assertEqual(p.lib.calls.count('waveOutClose'),1)

    def test_worker_close_api_exception_preserves_underflow_and_no_ack(self):
        p=unstarted_player(FakeWinMM(OSError('close API failed')));original=p.error_info
        p._run()
        self.assertFalse(p.device_closed);self.assertIs(p.error_info,original)
        self.assertEqual(p.error,'original underflow')
        self.assertEqual(p.cleanup_errors,[dict(api='waveOutClose',error='close API failed')])

    def test_compact_omits_event_ring_and_feed_trace(self):
        p=unstarted_player(FakeWinMM(5));p._run();s=p.snapshot_compact()
        self.assertNotIn('events',s);self.assertNotIn('feed_trace',s)
        self.assertNotIn('feed_trace',s['error_info'])
        self.assertEqual(s['error_info']['at_ns'],101)
        self.assertFalse(s['device_closed']);self.assertEqual(s['monitor_gain'],1.)


class Native48Ledger(unittest.TestCase):
    def balanced(self,p):
        s=p.snapshot_compact()
        self.assertTrue(s['ledger_balanced'],s)
        self.assertTrue(s['submitted_ledger_balanced'],s)
        self.assertEqual(s.get('fed_samples',0),s.get('submitted_samples',0)+s['queued_samples']+s['held_samples']+s['cancelled_unsubmitted_samples'])
        self.assertEqual(s.get('submitted_samples',0),s.get('completed_samples',0)+s['inflight_samples']+s['cancelled_submitted_unconfirmed_samples'])
        return s

    def capture(self,p,seen,label):
        available=p.lock.acquire(False)
        seen.append((label,available))
        self.assertTrue(available,'WinMM call must not own the Python state lock')
        p.lock.release()
        return self.balanced(p)

    def test_pop_has_held_credit_and_prepare_cancel_never_writes(self):
        p=ledger_player(queued=2);seen=[];stages=[]
        def preparing(*args):
            stages.append(self.capture(p,seen,'prepare'));p.request_close('cancel_in_prepare')
        p.lib.before['waveOutPrepareHeader']=preparing
        p.lib.before['waveOutClose']=lambda *args:self.capture(p,seen,'close')
        p._run();s=self.balanced(p)
        self.assertEqual((stages[0]['queued_samples'],stages[0]['held_samples'],stages[0].get('submitted_samples',0)),(512,512,0))
        self.assertNotIn('waveOutWrite',p.lib.calls)
        self.assertEqual((s['cancelled_queued_samples'],s['cancelled_held_samples']),(512,512))
        self.assertEqual(s['held_samples'],0);self.assertTrue(s['device_closed'])
        self.assertTrue(all(available for _,available in seen))

    def test_write_success_moves_held_and_submission_together(self):
        p=ledger_player(queued=2);seen=[];before_write=[];after_commit=[]
        def prepare(*args):
            s=self.capture(p,seen,'prepare')
            if p.lib.calls.count('waveOutPrepareHeader')==2:
                after_commit.append(s);p.request_close('second_block_cancel')
        p.lib.before['waveOutPrepareHeader']=prepare
        p.lib.before['waveOutWrite']=lambda *args:before_write.append(self.capture(p,seen,'write'))
        p._run();s=self.balanced(p)
        self.assertEqual((before_write[0]['held_samples'],before_write[0].get('submitted_samples',0)),(512,0))
        self.assertEqual((after_commit[0]['held_samples'],after_commit[0].get('submitted_samples',0),after_commit[0]['inflight_samples']),(512,512,512))
        self.assertEqual((s['cancelled_unsubmitted_samples'],s['cancelled_submitted_unconfirmed_samples']),(512,512))
        self.assertEqual(p.lib.calls.count('waveOutWrite'),1)

    def test_true_done_unprepare_commits_completion_atomically(self):
        p=ledger_player(queued=1,inflight=1,done=True);seen=[];before=[];after=[]
        p.lib.before['waveOutUnprepareHeader']=lambda *args:before.append(self.capture(p,seen,'unprepare'))
        def preparing(*args):
            after.append(self.capture(p,seen,'prepare'));p.request_close('cancel_following_block')
        p.lib.before['waveOutPrepareHeader']=preparing
        p._run();s=self.balanced(p)
        self.assertEqual((before[0].get('completed_samples',0),before[0]['inflight_samples']),(0,512))
        self.assertEqual((after[0]['completed_samples'],after[0]['inflight_samples']),(512,0))
        self.assertEqual(s['cancelled_submitted_unconfirmed_samples'],0)
        self.assertEqual(s['cancelled_unsubmitted_samples'],512)

    def test_prepare_failure_cancels_held_without_claiming_submission(self):
        p=ledger_player(queued=1);p.lib.results['waveOutPrepareHeader']=7
        p._run();s=self.balanced(p)
        self.assertEqual(s.get('submitted_samples',0),0)
        self.assertEqual((s['cancelled_held_samples'],s['cancelled_unsubmitted_samples']),(512,512))
        self.assertEqual(s['error_info']['stage'],'waveOutPrepareHeader')
        self.assertNotIn('waveOutWrite',p.lib.calls)

    def test_cancel_after_pop_before_prepare_is_accounted(self):
        p=ledger_player(queued=1);original=playback48.C.memmove;held=[]
        def copy_then_cancel(*args):
            held.append(self.balanced(p)['held_samples']);original(*args);p.request_close('cancel_after_pop')
        with patch.object(playback48.C,'memmove',side_effect=copy_then_cancel):p._run()
        s=self.balanced(p)
        self.assertEqual(held,[512]);self.assertNotIn('waveOutPrepareHeader',p.lib.calls)
        self.assertNotIn('waveOutWrite',p.lib.calls);self.assertEqual(s['cancelled_held_samples'],512)

    def test_cancel_during_write_keeps_successfully_accepted_submission(self):
        p=ledger_player(queued=1);stages=[]
        def writing(*args):
            stages.append(self.balanced(p));p.request_close('cancel_in_write')
        p.lib.before['waveOutWrite']=writing
        p._run();s=self.balanced(p)
        self.assertEqual((stages[0]['held_samples'],stages[0].get('submitted_samples',0)),(512,0))
        self.assertEqual(s['submitted_samples'],512)
        self.assertEqual(s['cancelled_unsubmitted_samples'],0)
        self.assertEqual(s['cancelled_submitted_unconfirmed_samples'],512)
        self.assertEqual(s.get('completed_samples',0),0)

    def test_reset_done_is_cancellation_not_playback_completion(self):
        p=ledger_player(queued=1,held=1,inflight=1);p.closed=True
        p.lib.before['waveOutReset']=lambda *args:[setattr(h,'flags',1) for h in p.headers]
        p._run();s=self.balanced(p)
        self.assertEqual(s.get('completed_samples',0),0)
        self.assertEqual(s['cancelled_unsubmitted_samples'],1024)
        self.assertEqual(s['cancelled_submitted_unconfirmed_samples'],512)
        self.assertEqual(s['inflight_samples'],0);self.assertTrue(s['inflight_known'])

    def test_close_failure_cancels_software_but_keeps_unconfirmed_inflight(self):
        p=ledger_player(queued=1,held=1,inflight=1,close_result=5);p.closed=True
        p.lib.before['waveOutReset']=lambda *args:[setattr(h,'flags',1) for h in p.headers]
        p._run();s=self.balanced(p)
        self.assertEqual((s['queued_samples'],s['held_samples']),(0,0))
        self.assertEqual(s['cancelled_unsubmitted_samples'],1024)
        self.assertEqual((s['inflight_samples'],s['cancelled_submitted_unconfirmed_samples']),(512,0))
        self.assertFalse(s['inflight_known']);self.assertFalse(s['device_closed'])
        self.assertEqual(s['inflight_state'],'UNKNOWN_AFTER_CLOSE_FAILURE')
        self.assertEqual(s.get('completed_samples',0),0)

    def test_write_rejection_does_not_increment_submitted(self):
        p=ledger_player(queued=1);p.lib.results['waveOutWrite']=9
        p._run();s=self.balanced(p)
        self.assertEqual(s.get('submitted_samples',0),0)
        self.assertEqual(s['cancelled_held_samples'],512)
        self.assertEqual(s['error_info']['stage'],'waveOutWrite')

    def test_held_credit_cannot_expand_software_capacity(self):
        p=ledger_player(held=1);p.capacity=1
        with self.assertRaisesRegex(RuntimeError,'queue full'):p.feed(np.zeros(512))
        s=self.balanced(p)
        self.assertEqual(s['fed_samples'],512);self.assertEqual(s['held_samples'],512)
        self.assertEqual(s['capacity_samples'],512);self.assertEqual(s['overflow'],1)

    def test_damaged_counter_is_unknown_without_rewriting_raw_counter(self):
        for name in ('fed_samples','submitted_samples','completed_samples',
                     'cancelled_queued_samples','cancelled_held_samples','cancelled_submitted_unconfirmed_samples'):
            for bad in (None,True,-1,1.0,1.5,float('nan'),float('inf'),'512'):
                with self.subTest(name=name,value=bad):
                    p=ledger_player();p.counts[name]=bad;before=dict(p.counts)
                    full=p.snapshot();compact=p.snapshot_compact()
                    self.assertIs(p.counts[name],bad)
                    self.assertEqual(set(p.counts),set(before))
                    self.assertIs(full[name],bad);self.assertIs(compact[name],bad)
                    for snapshot in (full,compact):
                        self.assertIsNone(snapshot['ledger_balanced'])
                        self.assertIsNone(snapshot['submitted_ledger_balanced'])
                        self.assertFalse(snapshot['ledger_inputs_valid'])
                        self.assertIn(name,snapshot['ledger_invalid_fields'])
                    if name in ('cancelled_queued_samples','cancelled_held_samples'):
                        self.assertIsNone(compact['cancelled_unsubmitted_samples'])
                        self.assertIsNone(compact['cancelled_samples'])
                    if name=='cancelled_submitted_unconfirmed_samples':
                        self.assertIsNone(compact['cancelled_submitted_samples'])
                        self.assertIsNone(compact['cancelled_samples'])

    def test_bad_held_value_is_unknown_instead_of_fabricated_zero(self):
        for bad in (None,True,-1,1.5,float('nan')):
            with self.subTest(value=bad):
                p=ledger_player();p.held_samples=bad;s=p.snapshot_compact()
                self.assertIsNone(s['held_samples']);self.assertFalse(s['ledger_inputs_valid'])
                self.assertIsNone(s['ledger_balanced']);self.assertIsNone(s['submitted_ledger_balanced'])
                self.assertIn('held_samples',s['ledger_invalid_fields'])

    def test_missing_counters_remain_sparse_known_zero(self):
        p=ledger_player();p.counts=collections.Counter();s=p.snapshot()
        self.assertEqual(dict(p.counts),{})
        self.assertNotIn('fed_samples',s);self.assertNotIn('submitted_samples',s);self.assertNotIn('completed_samples',s)
        self.assertTrue(s['ledger_inputs_valid']);self.assertTrue(s['ledger_balanced']);self.assertTrue(s['submitted_ledger_balanced'])
        self.assertEqual(s['ledger_invalid_fields'],[])

    def test_large_integer_ledger_never_rounds_through_float(self):
        p=ledger_player();large=2**60+1
        p.counts=collections.Counter(fed_samples=large,submitted_samples=large,completed_samples=large)
        s=self.balanced(p)
        self.assertEqual(s['fed_samples'],large);self.assertEqual(s['completed_samples'],large)


if __name__=='__main__':unittest.main()
