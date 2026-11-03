"""Shared RAW metadata regressions; no socket, GUI, device or media output."""
import hashlib,importlib.util,struct,time,types,unittest
from pathlib import Path
from unittest.mock import patch

import board_follow as bridge_module
from board_follow import BoardLockAdapter
from monitor import raw128_receiver as wire

REFERENCE=Path(__file__).resolve().parent/'regression_baselines/legacy_raw_transport/raw128_receiver.py'
REFERENCE_SHA='76d0e8bb778c6f8f24cbd702139047f8982c7d10766c189952db3f5c07af9896'

def reference_module():
    if hashlib.sha256(REFERENCE.read_bytes()).hexdigest()!=REFERENCE_SHA:
        raise AssertionError('Independent frozen RAW reference changed')
    spec=importlib.util.spec_from_file_location('transport_frozen_raw_reference',REFERENCE)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def packet(seq=0,epoch=400,first=None,flags=0):
    first=seq*3 if first is None else first
    data=bytearray(struct.pack('>4sBBBBIHHQII',b'M128',1,32,1,128,48000,3,392,seq,epoch,flags))
    # Nonzero independent byte patterns expose frame-tag inclusion or row order.
    for row in range(3):data+=struct.pack('>Q',first+row)+bytes([17+row])*384
    return bytes(data)

def mutate(data,offset,value):
    b=bytearray(data);b[offset]=value;return bytes(b)

def wait_for(condition,timeout=2):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if condition():return
        time.sleep(.002)
    raise AssertionError('fake child did not reach expected state')

class Child:
    all=[]
    def __init__(self,config,**kwargs):self.closed=False;self.calls=[];Child.all.append(self)
    def wait_ready(self,timeout):return not self.closed
    def wait_closed(self,timeout):return self.closed
    def close(self):self.closed=True
    def consume_datagram(self,data):self.calls.append(data);return True
    def snapshot(self):return dict(state='closed' if self.closed else 'ready',calls=len(self.calls))

class TransportMetadata(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.old=reference_module()
    def bridge(self):
        Child.all=[]
        adapter=BoardLockAdapter({},child_factory=Child,allow_test_factory=True,status_timeout_s=2)
        def cleanup():adapter.close();self.assertTrue(adapter.wait_closed(3))
        self.addCleanup(cleanup);return adapter
    def test_handwritten_pcm_layout_and_public_decoder_match_frozen_reference(self):
        raw=packet(8,epoch=400,first=0x1234,flags=64)
        view=wire.decode_transport_packet(raw)
        expected=dict(sequence=8,epoch=400,flags=64,fatal_flags=0,clipped=True,
            frame_ids=(0x1234,0x1235,0x1236),sample_rate=48000,channels=128,acoustic_valid=True)
        self.assertEqual(dict(view.metadata),expected)
        self.assertNotIn('pcm24le',view.metadata);self.assertIs(view.raw,raw)
        joined=b''.join(bytes([17+i])*384 for i in range(3))
        self.assertEqual(wire.materialize_packet(view),dict(expected,pcm24le=joined))
        self.assertEqual(wire.decode_packet(raw),self.old.decode_packet(raw))
        self.assertEqual(len(joined),1152)
    def test_public_and_transport_decoders_reject_same_wire_damage(self):
        raw=packet()
        bad=[raw[:-1],raw+b'\x00',mutate(raw,0,0),mutate(raw,4,2),mutate(raw,5,31),
             mutate(raw,6,2),mutate(raw,7,127),mutate(raw,11,0),mutate(raw,13,2),
             mutate(raw,15,0),mutate(raw,31,128)]
        broken=bytearray(raw);struct.pack_into('>Q',broken,32+392,99);bad.append(bytes(broken))
        for payload in bad:
            with self.subTest(payload_len=len(payload),head=payload[:16]):
                for decoder in (self.old.decode_packet,wire.decode_packet,wire.decode_transport_packet):
                    with self.assertRaises(ValueError):decoder(payload)
    def test_public_feed_and_shared_feed_match_reference_counts_and_events(self):
        raw_bad=bytearray(packet(10));struct.pack_into('>Q',raw_bad,32+392,70)
        stream=[packet(0),packet(1),packet(1),packet(3),packet(2),packet(3),packet(4,flags=64),
                packet(5,flags=1),packet(6,flags=1),packet(7),packet(0,epoch=401,first=900),
                packet(1,epoch=401,first=903),packet(0,epoch=401,first=0),packet(2,epoch=401),
                b'M128',bytes(raw_bad),packet(11,flags=128)]
        old=self.old.Raw128Receiver();public=wire.Raw128Receiver();shared=wire.Raw128Receiver()
        for i,payload in enumerate(stream):
            expected=old.feed(payload,now=i*.01)
            self.assertEqual(public.feed(payload,now=i*.01),expected)
            try:view=wire.decode_transport_packet(payload)
            except ValueError as error:actual=shared.reject_malformed(error)
            else:actual=shared.feed_decoded(view,now=i*.01)
            self.assertEqual(actual,expected)
            self.assertEqual(public.snapshot(),old.snapshot());self.assertEqual(shared.snapshot(),old.snapshot())
            self.assertEqual(public.events,old.events);self.assertEqual(shared.events,old.events)
        self.assertEqual(shared.counts['recorded_bytes'],0);self.assertFalse(shared.snapshot()['recording'])
    def test_view_is_immutable_and_fake_views_are_rejected(self):
        raw=packet();view=wire.decode_transport_packet(raw)
        with self.assertRaises(TypeError):wire.RawPacketView(raw,dict(view.metadata))
        with self.assertRaises(TypeError):view.metadata['sequence']=99
        with self.assertRaises(AttributeError):view._raw=b'not a packet'
        with self.assertRaises(TypeError):view.metadata['frame_ids'][0]=99
        for fake in ({'raw':raw,'metadata':view.metadata},types.SimpleNamespace(raw=raw,metadata=view.metadata),object.__new__(wire.RawPacketView)):
            with self.subTest(fake_type=type(fake).__name__):
                with self.assertRaises(TypeError):wire.require_packet_view(fake)
                with self.assertRaises(TypeError):wire.Raw128Receiver().feed_decoded(fake)
        self.assertNotIn('pcm24le',repr(view));self.assertLess(len(repr(view)),100)
    def test_mutable_input_is_frozen_before_shared_consumers(self):
        source=bytearray(packet(7));original=bytes(source);view=wire.decode_transport_packet(source)
        source[40]=254;source[23]^=1
        self.assertIsInstance(view.raw,bytes);self.assertEqual(view.raw,original)
        self.assertEqual(view.metadata['sequence'],7)
        self.assertEqual(wire.materialize_packet(view)['pcm24le'][0],17)
    def test_nonrecording_diagnostic_and_bridge_do_not_materialize_pcm(self):
        class NoPayloadSlices(bytes):
            def __getitem__(self,key):
                if isinstance(key,slice) and key.start is not None and key.start>=40:
                    raise AssertionError('Unused PCM must not be joined on the transport path')
                return super().__getitem__(key)
        view=wire.decode_transport_packet(NoPayloadSlices(packet()))
        receiver=wire.Raw128Receiver();adapter=self.bridge();adapter.set_listen_mode(0)
        with patch.object(wire,'materialize_packet',side_effect=AssertionError('Unexpected PCM materialization')):
            self.assertTrue(receiver.feed_decoded(view));self.assertFalse(adapter.consume_decoded_datagram(view))
        self.assertEqual(receiver.counts['frames'],3);self.assertEqual(receiver.counts['recorded_bytes'],0)
        self.assertFalse(Child.all)
    def test_shared_consumers_reuse_one_header_and_three_frame_checks(self):
        receiver=wire.Raw128Receiver();adapter=self.bridge();adapter.set_listen_mode(0)
        original_header=wire.HEADER;frame_unpack=struct.unpack_from
        class HeaderSpy:
            calls=0
            def unpack_from(self,data):self.calls+=1;return original_header.unpack_from(data)
        spy=HeaderSpy()
        with patch.object(wire,'HEADER',spy),patch.object(wire.struct,'unpack_from',wraps=frame_unpack) as frame_checks:
            view=wire.decode_transport_packet(packet())
            with patch.object(bridge_module,'decode_transport_packet',side_effect=AssertionError('Bridge parsed twice')):
                self.assertTrue(receiver.feed_decoded(view));self.assertFalse(adapter.consume_decoded_datagram(view))
            self.assertEqual(spy.calls,1);self.assertEqual(frame_checks.call_count,3)
        self.assertEqual(receiver.counts['datagrams'],1);self.assertEqual(adapter.counts['raw_packets'],1)
    def test_shared_invalid_result_is_accounted_once_by_both_consumers(self):
        receiver=wire.Raw128Receiver();adapter=self.bridge();adapter.set_listen_mode(0)
        broken=bytearray(packet(4));struct.pack_into('>Q',broken,32+2*392,50)
        with self.assertRaises(wire.RawDecodeError) as caught:wire.decode_transport_packet(broken)
        self.assertFalse(receiver.reject_malformed(caught.exception))
        self.assertFalse(adapter.reject_malformed_raw(caught.exception))
        self.assertEqual(receiver.counts['datagrams'],1);self.assertEqual(receiver.counts['malformed'],1)
        self.assertEqual(receiver.events,[dict(type='noncontinuous_packet',sequence=4,frames=[12,13,50])])
        self.assertEqual(adapter.counts['malformed_raw_packets'],1)
    def test_fake_bridge_view_cannot_change_transport_counts(self):
        adapter=self.bridge();before=dict(adapter.counts)
        with self.assertRaises(TypeError):adapter.consume_decoded_datagram(types.SimpleNamespace(raw=packet(),metadata={}))
        self.assertEqual(dict(adapter.counts),before);self.assertIsNone(adapter.transport)
    def test_fatal_view_is_still_delivered_to_control_and_stops_fake_child(self):
        adapter=self.bridge();receiver=wire.Raw128Receiver()
        receiver.feed_decoded(wire.decode_transport_packet(packet()))
        adapter.consume_decoded_datagram(wire.decode_transport_packet(packet()))
        values=[0]*24;values[0]=1
        adapter.consume_uart_message((1,0,3,7,struct.pack('<24I',*values)))
        wait_for(lambda:adapter.child is not None and adapter.child.wait_ready(0))
        good=wire.decode_transport_packet(packet(1));self.assertTrue(receiver.feed_decoded(good));self.assertTrue(adapter.consume_decoded_datagram(good))
        child=Child.all[-1];fatal=wire.decode_transport_packet(packet(2,flags=1))
        self.assertFalse(receiver.feed_decoded(fatal));self.assertFalse(adapter.consume_decoded_datagram(fatal))
        self.assertTrue(child.closed);self.assertEqual(adapter.counts['raw_fatal_flags'],1)

if __name__=='__main__':unittest.main(verbosity=2)
