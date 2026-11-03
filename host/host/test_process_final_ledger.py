"""No devices: delayed final publication and retired-owner diagnostic gates."""
import copy,json,threading,time,unittest
from process_adapter import ProcessRawAdapter,RemotePCSink
from audio_source_router import AudioSourceRouter

class Mono:
    def __init__(self):self.closed=False
    def feed(self,data):pass
    def snapshot(self):return dict(completed_samples=0,error=None)
    def close(self):self.closed=True

class FinalLedger(unittest.TestCase):
    def setUp(self):self.actors=[];self.router=AudioSourceRouter(sink_factory=lambda mode:Mono());self.router.select(4)
    def tearDown(self):
        for a in self.actors:a.close()
        for a in self.actors:self.assertTrue(a.wait_closed(8))
        self.router.close()
    def actor(self,finish_ms=350):
        a=ProcessRawAdapter({},lock_deg=0,capacity=1024,enable_playback=True,playback_router=self.router,
            test_worker_spec=dict(finish_ms=finish_ms),allow_test_worker=True)
        self.actors.append(a);self.assertTrue(a.wait_ready(5));return a
    def test_delayed_final_refreshes_retired_ledger_not_current_mono_or_epoch(self):
        a=self.actor();r=self.router;r.select(2);r.feed_board(2,b'\0'*32,r.ticket())
        old=copy.deepcopy(r.last_player);self.assertFalse(old['native']['closed'])
        self.assertTrue(old['native']['native_closed_ack']);self.assertFalse(old['native']['remote_worker_final'])
        p,mode,epoch=r.player,r.mode,r.epoch
        self.assertTrue(a.wait_closed(5));row=r.last_player
        self.assertTrue(row['native']['closed']);self.assertTrue(row['native']['remote_worker_final'])
        self.assertTrue(row['native']['remote_process_joined'])
        self.assertFalse(row['native_before_worker_final']['closed'])
        self.assertEqual((r.player,r.mode,r.epoch),(p,mode,epoch))
        self.assertEqual(row['native']['source_sampled_ns'],a._cached()['source_sampled_ns'])
        self.assertLessEqual(row['native']['source_sampled_ns'],row['worker_final_refreshed_ns'])
        json.dumps(r.snapshot(),allow_nan=False)
        count=r.counts['remote_final_ledger_refreshes'];saved=copy.deepcopy(row)
        self.assertTrue(r.refresh_remote_final(a.router_owner,a._remote))
        self.assertEqual(r.last_player,saved);self.assertEqual(r.counts['remote_final_ledger_refreshes'],count)
    def test_new_owner_rejects_old_final_and_does_not_replace_its_history(self):
        a=self.actor(800);r=self.router;r.select(0);r.select(4);b=self.actor(0)
        p,epoch=r.player,r.epoch;saved=copy.deepcopy(r.last_player)
        self.assertTrue(a.wait_closed(5));self.assertIs(r.player,p);self.assertEqual(r.epoch,epoch)
        self.assertEqual(r.last_player,saved)
        self.assertFalse(r.refresh_remote_final(a.router_owner,a._remote))
        self.assertFalse(b.shared.stop.is_set());self.assertTrue(b.wait_ready(0))
    def test_same_generation_different_remote_object_is_rejected(self):
        a=self.actor(0);a.close();self.assertTrue(a.wait_closed(5))
        saved=copy.deepcopy(self.router.last_player);other=RemotePCSink(a)
        self.assertFalse(self.router.refresh_remote_final(a.router_owner,other))
        self.assertEqual(self.router.last_player,saved)
    def test_previous_error_is_preserved_when_final_native_has_no_error(self):
        a=self.actor();r=self.router;r.select(0)
        with r.lock:r.last_player['error']='original feed error must remain'
        self.assertTrue(a.wait_closed(5));self.assertEqual(r.last_player['error'],'original feed error must remain')
        self.assertTrue(r.last_player['native']['closed'])

if __name__=='__main__':unittest.main()
