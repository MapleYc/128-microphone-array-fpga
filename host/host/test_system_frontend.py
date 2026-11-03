"""Real Session methods, fake I/O only. Never opens native audio or a port."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import collections,io,json,queue,tempfile,threading,time,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import launcher
from meeting_monitor import packet
from test_board_follow import raw,uart

class Bridge:
    def __init__(self):self.views=[];self.malformed=[];self.messages=[]
    def consume_decoded_datagram(self,view):self.views.append(view)
    def reject_malformed_raw(self,e):self.malformed.append(str(e))
    def consume_uart_message(self,msg,received_at):self.messages.append(msg)
    def consume_uart_diagnostic(self,event):pass
    def health(self):return dict(state='test-control-only')
    def snapshot_compact(self):return self.health()

class Frontend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.m=launcher.load_monitor()
    def test_raw_peer_filter_and_one_strict_view_shared_by_both_consumers(self):
        m=self.m;s=m.Session.__new__(m.Session);s.stop=threading.Event();s.args=types.SimpleNamespace(bind='FAKE',udp_port=5001,board_ip='192.168.10.2')
        s.video_lock=threading.Lock();s.lock=threading.Lock();s.errors={};s.raw_preview=Bridge();s.assembler=Mock();s.assembler.feed.return_value=None
        class Diagnostic:
            def __init__(self):self.views=[];self.errors=[]
            def feed_decoded(self,view):self.views.append(view)
            def reject_malformed(self,e):self.errors.append(str(e))
            def close(self):pass
        s.raw128=Diagnostic()
        inputs=[(raw(),('192.168.10.2',5001)),(raw(1),('192.168.10.99',5001)),(b'M128broken',('192.168.10.2',5001)),(b'V128fake',('192.168.10.2',5001))]
        class Socket:
            def setsockopt(self,*a):pass
            def bind(self,*a):pass
            def settimeout(self,*a):pass
            def recvfrom(self,*a):
                if inputs:return inputs.pop(0)
                s.stop.set();raise m.socket.timeout()
            def close(self):pass
        with tempfile.TemporaryDirectory() as tmp:
            s.out=Path(tmp)
            with patch.object(m.socket,'socket',return_value=Socket()),patch.object(m,'decode_transport_packet',wraps=m.decode_transport_packet) as decode:s._video()
        self.assertEqual(decode.call_count,2) # one valid, one explicit malformed; foreign peer excluded
        self.assertEqual(len(s.raw128.views),1);self.assertIs(s.raw128.views[0],s.raw_preview.views[0])
        self.assertNotIn('pcm24le',s.raw128.views[0].metadata)
        self.assertEqual(len(s.raw128.errors),1);self.assertEqual(len(s.raw_preview.malformed),1)
        s.assembler.feed.assert_called_once_with(b'V128fake');self.assertFalse(s.errors)

    def test_native_feed_error_and_cleanup_error_cannot_kill_uart_heartbeat(self):
        for cleanup_error in (False,True):
          with self.subTest(cleanup_error=cleanup_error):
            m=self.m;s=m.Session.__new__(m.Session)
            s.args=types.SimpleNamespace(port='FAKE',mono_shift=None,energy_floor=None,pair_interval=0)
            s.stop=threading.Event();s.lock=threading.Lock();s.play_lock=threading.Lock();s.video_lock=threading.Lock();s.commands=queue.Queue()
            s.parser=m.Parser();s.raw_preview=Bridge();s.history=collections.deque(maxlen=80);s.errors={};s.latest=None;s.assembler=Mock()
            s.status={};s.video_status={};s.board_audio_processing={};s.directional_filter={};s.command_guard={};s.camera_status={}
            s.diagnostic_config={};s.pair_snapshot={};s.mono_profile_status={};s.started=time.monotonic();s.audio_mode=2
            s.listen_gain=m.ListenGain(0);s.audio_levels={2:m.PcmLevel(),3:m.PcmLevel()};s.output_level=m.PcmLevel();s.listen_last={};s.audio_log=io.StringIO()
            class Router:
                failures=[]
                def ticket(self):return 77
                def feed_board(self,*a):raise RuntimeError('injected native16 overflow')
                def fail_board(self,kind,reason,ticket=None):
                    self.failures.append((kind,reason,ticket))
                    if cleanup_error:raise RuntimeError('injected cleanup failure')
            s.audio_router=Router();s.audio_router.failures=[]
            data=bytearray(packet(2,0,0,1,b'\1\0'*128)+uart(seq=1,frame=384))
            class Serial:
                closed=False
                def __init__(self,*a,**kw):pass
                @property
                def in_waiting(self):return len(data)
                def set_buffer_size(self,**kw):pass
                def read(self,n):
                    if data:value=bytes(data[:n]);del data[:n];return value
                    s.stop.set();return b''
                def close(self):Serial.closed=True
            class Recorder:
                def __init__(self,*a,**kw):pass
                def consume(self,kind,seq,frame,epoch,payload):
                    return dict(mode='LOCKED',source_frame=frame,epoch=epoch) if kind==1 else None
                def event(self,**kw):pass
                def close(self):pass
            with tempfile.TemporaryDirectory() as tmp:
                s.out=Path(tmp)
                with patch.dict('sys.modules',{'serial':types.SimpleNamespace(Serial=Serial)}),patch.object(m,'Recorder',Recorder):s._audio()
            self.assertEqual(s.status['source_frame'],384);self.assertNotIn('uart',s.errors)
            self.assertIn('playback',s.errors);self.assertEqual(s.audio_mode,0);self.assertTrue(Serial.closed)
            self.assertEqual(s.audio_router.failures[0][::2],(2,77))
            self.assertEqual([msg[0] for msg in s.raw_preview.messages],[2,1])
            self.assertIn('playback_failed_transport_continues',s.audio_log.getvalue())
            self.assertEqual('playback_shutdown' in s.errors,cleanup_error)

    def test_display_toggle_does_not_change_audio_control_or_network(self):
        m=self.m;s=m.Session.__new__(m.Session);s.audio_mode=2;s.audio_log=io.StringIO();s.display_control_version=0
        s.raw128=object();s.commands=queue.Queue();s.status={'mode':'LOCKED'};raw_object=s.raw128;status=s.status.copy()
        s.set_display_enabled(False);self.assertFalse(s.display_enabled)
        s.set_display_enabled(True);self.assertTrue(s.display_enabled)
        self.assertEqual(s.display_control_version,2);self.assertEqual(s.audio_mode,2)
        self.assertIs(s.raw128,raw_object);self.assertEqual(s.status,status);self.assertTrue(s.commands.empty())
        rows=list(store.load_stream(s.audio_log.getvalue()))
        self.assertEqual([r['enabled'] for r in rows],[False,True])

    def test_stale_feed_fault_cannot_mute_a_new_same_kind_selection(self):
        m=self.m;s=m.Session.__new__(m.Session)
        s.lock=threading.Lock();s.play_lock=threading.Lock();s.errors={};s.audio_mode=2
        s.audio_log=io.StringIO();s.playback_last_failure=None
        class Router:
            def fail_board(self,kind,reason,ticket=None):
                self.ticket_seen=ticket
                return False # The real router rejects an older selection ticket.
        s.audio_router=Router()
        self.assertFalse(s._playback_fault(2,RuntimeError('old feed'),11))
        self.assertEqual(s.audio_mode,2);self.assertFalse(s.errors)
        self.assertIsNone(s.playback_last_failure)
        self.assertEqual(s.audio_router.ticket_seen,11)
        self.assertIn('stale_playback_fault_ignored',s.audio_log.getvalue())

    def test_actual_feed_ticket_is_attached_to_native_failure(self):
        m=self.m;s=m.Session.__new__(m.Session);s.audio_mode=2
        s.play_lock=threading.Lock();s.listen_last={};s.listen_gain=m.ListenGain(0)
        s.audio_levels={2:m.PcmLevel()};s.output_level=m.PcmLevel()
        class Router:
            def ticket(self):return 42
            def feed_board(self,kind,pcm,ticket):raise RuntimeError('native failed')
        s.audio_router=Router()
        with self.assertRaises(RuntimeError) as caught:s.play_packet((2,1,100,3,b'\1\0'*128))
        self.assertEqual(caught.exception.playback_ticket,42)

    def test_failed_selection_raises_and_cannot_get_an_applied_reply(self):
        m=self.m;s=m.Session.__new__(m.Session)
        s.audio_mode=2;s.lock=threading.Lock();s.play_lock=threading.Lock();s.errors={}
        s.audio_router=Mock();s.audio_router.select.side_effect=RuntimeError('device preparation failed')
        s.raw_preview=Mock()
        with self.assertRaisesRegex(RuntimeError,'device preparation failed'):s.listen(2)
        self.assertEqual(s.audio_mode,0);self.assertEqual(s.errors['audio_source'],'device preparation failed')
        s.raw_preview.set_listen_mode.assert_called_once_with(0)

if __name__=='__main__':unittest.main()
