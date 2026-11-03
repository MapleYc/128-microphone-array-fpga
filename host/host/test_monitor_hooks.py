"""Exercise actual monitor methods with in-memory UART/socket replacements."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import collections,io,json,queue,struct,tempfile,threading,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import launcher
from test_board_follow import uart,raw

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.monitor=launcher.load_monitor()
    def test_real_audio_method_hooks_original_unclamped_messages(self):
        m=self.monitor;s=m.Session.__new__(m.Session)
        s.args=types.SimpleNamespace(port='FAKE_NO_DEVICE',mono_shift=None,energy_floor=None,pair_interval=0)
        s.stop=threading.Event();s.lock=threading.Lock();s.video_lock=threading.Lock();s.commands=queue.Queue()
        s.parser=m.Parser();s.raw_preview=Mock();s.history=collections.deque();s.errors={};s.latest=None;s.assembler=Mock()
        s.status={};s.video_status={};s.board_audio_processing={};s.directional_filter={};s.command_guard={};s.camera_status={}
        s.diagnostic_config={};s.pair_snapshot={};s.mono_profile_status={};s.started=m.time.monotonic();s.play_packet=Mock()
        payload=bytearray(uart(seq=0)+uart(mode=0,seq=1,frame=6))
        class Serial:
            writes=[]
            def __init__(self,*a,**k):self.closed=False
            @property
            def in_waiting(self):return len(payload)
            def set_buffer_size(self,**k):pass
            def write(self,b):self.writes.append(b);raise AssertionError('no board command allowed')
            def read(self,n):
                if payload:b=bytes(payload[:n]);del payload[:n];return b
                s.stop.set();return b''
            def close(self):self.closed=True
        with tempfile.TemporaryDirectory() as tmp:
            s.out=Path(tmp)
            with patch.dict('sys.modules',{'serial':types.SimpleNamespace(Serial=Serial)}),patch.object(m.socket,'socket',side_effect=AssertionError('no real socket')):
                s._audio()
        self.assertFalse(s.errors);self.assertFalse(Serial.writes)
        calls=s.raw_preview.consume_uart_message.call_args_list
        self.assertEqual(len(calls),2)
        self.assertEqual([struct.unpack_from('<I',c.args[0][4])[0] for c in calls],[1,0])
        self.assertEqual(s.status['mode'],'SEARCH')
    def test_video_uses_one_fake_socket_and_board_peer_filter(self):
        m=self.monitor;s=m.Session.__new__(m.Session);s.stop=threading.Event();s.args=types.SimpleNamespace(bind='192.168.10.1',udp_port=5001,board_ip='192.168.10.2')
        s.video_lock=threading.Lock();s.lock=threading.Lock();s.errors={};s.raw128=Mock();s.raw_preview=Mock();s.assembler=Mock();s.assembler.feed.return_value=None
        packets=[(raw(),('192.168.10.2',5001)),(b'V128fixture',('192.168.10.2',5001)),(raw(1),('192.168.10.99',5001))]
        class Socket:
            def __init__(self):self.binds=[];self.closed=False
            def setsockopt(self,*a):pass
            def bind(self,a):self.binds.append(a)
            def settimeout(self,*a):pass
            def recvfrom(self,*a):
                if packets:return packets.pop(0)
                s.stop.set();raise m.socket.timeout()
            def close(self):self.closed=True
        fake=Socket()
        with tempfile.TemporaryDirectory() as tmp:
            s.out=Path(tmp)
            with patch.object(m.socket,'socket',return_value=fake) as factory:s._video()
        self.assertEqual(factory.call_count,1);self.assertEqual(fake.binds,[('192.168.10.1',5001)]);self.assertTrue(fake.closed)
        s.raw_preview.consume_decoded_datagram.assert_called_once();s.raw128.feed_decoded.assert_called_once()
        view=s.raw_preview.consume_decoded_datagram.call_args.args[0]
        self.assertIs(s.raw128.feed_decoded.call_args.args[0],view);self.assertEqual(view.raw,raw())
        self.assertNotIn('pcm24le',view.metadata)
        s.assembler.feed.assert_called_once_with(b'V128fixture');self.assertFalse(s.errors)
    def test_status_log_contains_host_mode_and_unsupported_state(self):
        s=self.monitor.Session.__new__(self.monitor.Session);s.audio_log=io.StringIO();s.audio_snapshot=lambda:{}
        s.raw_preview=Mock();s.raw_preview.snapshot_compact.return_value={'state':'unsupported_nonzero_elevation','supports_nonzero_elevation':False}
        s.record_audio_status();d=list(store.load_stream(s.audio_log.getvalue()))[0]
        self.assertEqual(d['host_preview']['state'],'unsupported_nonzero_elevation')

if __name__=='__main__':unittest.main()
