"""Strict M128 raw128 diagnostic decoder; no board control or acoustic DSP.

Payload PCM stays 24-bit little endian sample-major, 128 channels per frame.
Discontinuities start new files. Missing data is never replaced by zeros.
"""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
from pathlib import Path
import json,struct,time
from types import MappingProxyType
HEADER=struct.Struct('>4sBBBBIHHQII')
_VALIDATED_TOKEN=object()

class RawDecodeError(ValueError):
    def __init__(self,message,*,sequence=None,epoch=None,frames=None):
        super().__init__(message);self.sequence=sequence;self.epoch=epoch;self.frames=frames

class RawPacketView:
    """Opaque immutable result of the canonical transport validation.

    It deliberately contains no joined PCM payload. Header/frame validation may
    be shared between diagnostic/control consumers without copying audio.
    """
    __slots__=('_raw','_metadata','_token')
    def __init__(self,raw,metadata,token=None):
        if token is not _VALIDATED_TOKEN:raise TypeError('Use decode_transport_packet')
        object.__setattr__(self,'_raw',raw);object.__setattr__(self,'_metadata',MappingProxyType(metadata));object.__setattr__(self,'_token',token)
    def __setattr__(self,name,value):raise AttributeError('Validated RAW view is immutable')
    @property
    def raw(self):return self._raw
    @property
    def metadata(self):return self._metadata
    def __repr__(self):return 'RawPacketView(sequence=%r, epoch=%r)'%(self._metadata['sequence'],self._metadata['epoch'])

def require_packet_view(packet):
    if not isinstance(packet,RawPacketView) or getattr(packet,'_token',None) is not _VALIDATED_TOKEN:
        raise TypeError('Canonical RawPacketView required')
    return packet

def decode_transport_packet(data):
    """Validate one RAW datagram exactly once; do not materialize PCM."""
    if not isinstance(data,bytes):
        try:data=memoryview(data).tobytes()
        except TypeError:raise TypeError('RAW binary buffer required') from None
    if len(data)!=1208:raise RawDecodeError('M128 packet length')
    magic,ver,hlen,fmt,ch,sr,n,rlen,seq,epoch,flags=HEADER.unpack_from(data)
    if (magic,ver,hlen,fmt,ch,sr,n,rlen)!=(b'M128',1,32,1,128,48000,3,392) or flags&~127:
        raise RawDecodeError('M128 format/header/flags',sequence=seq,epoch=epoch)
    frames=tuple(struct.unpack_from('>Q',data,32+r*392)[0] for r in range(3))
    if frames[1]!=frames[0]+1 or frames[2]!=frames[1]+1:
        raise RawDecodeError('M128 frame IDs not continuous',sequence=seq,epoch=epoch,frames=frames)
    metadata=dict(sequence=seq,epoch=epoch,flags=flags,fatal_flags=flags&63,clipped=bool(flags&64),
                  frame_ids=frames,sample_rate=sr,channels=ch,acoustic_valid=(flags&63)==0)
    return RawPacketView(data,metadata,_VALIDATED_TOKEN)

def materialize_packet(packet):
    packet=require_packet_view(packet);data=packet.raw
    result=dict(packet.metadata);acoustic_valid=result.pop('acoustic_valid')
    result['pcm24le']=b''.join(data[40+r*392:424+r*392] for r in range(3))
    result['acoustic_valid']=acoustic_valid
    return result

def decode_packet(data):
    """Stateless strict decoder. Source labels/DOA never enter this interface.

    Raises ValueError for a damaged packet. Fatal flags0..5 disqualify acoustic
    use; diagnostic extension flag6 is a distinct amplitude clipping warning.
    Caller must additionally enforce inter-packet epoch/sequence/frame continuity.
    """
    return materialize_packet(decode_transport_packet(data))

class Raw128Receiver:
    def __init__(self,out=None,max_seconds=0,max_bytes=1024**3):
        if not 0<=max_seconds<=60 or max_bytes<1208:raise ValueError('record bounds')
        self.out=Path(out) if out else None;self.max_seconds=max_seconds;self.max_bytes=max_bytes
        self.counts=dict(datagrams=0,malformed=0,duplicates=0,out_of_order=0,packet_gaps=0,
                         frame_gaps=0,sessions=0,invalid_flags=0,clipping_warnings=0,frames=0,recorded_frames=0,recorded_bytes=0)
        self.epoch=None;self.sequence=None;self.last_frame=None;self.first_at=None
        self.segment=0;self.pcm=None;self.journal=None;self.events=[];self.closed=False
    def _event(self,kind,**kw):
        event=dict(type=kind,**kw);self.events.append(event)
        # Keep in-memory state bounded; the final summary includes latest events.
        if len(self.events)>64:self.events.pop(0)
        if self.out and self.max_seconds:
            self.out.mkdir(parents=True,exist_ok=True)
            with (self.out/'events.log').open('a',encoding='utf8') as f:f.write(store.dumps(event, explicit_start=True)+'\n')
    def _close_segment(self):
        if self.pcm:self.pcm.close();self.pcm=None
        if self.journal:self.journal.close();self.journal=None
    def _new_segment(self,epoch,sequence,first_frame,reason,flags=0):
        self._close_segment();self.segment+=1
        if self.out and self.max_seconds and not self.closed:
            self.out.mkdir(parents=True,exist_ok=True)
            stem=f'segment_{self.segment:04d}_epoch{epoch:08x}_frame{first_frame}'
            self.pcm=(self.out/(stem+'.pcm24le')).open('xb',buffering=1024*1024)
            self.journal=(self.out/(stem+'.packets.bin')).open('xb',buffering=1024*1024)
            info=dict(sample_rate=48000,channels=128,pcm_format='signed24_little_endian_samplemajor',
                      first_frame=first_frame,epoch=epoch,first_sequence=sequence,reason=reason,initial_flags=flags,
                      geometry_and_channel_mask='external; canonical channels2/3 known bad, raw retained',
                      packet_journal_format='<QIIQQQ: sequence,epoch,flags,three frame IDs',
                      acceptance='diagnostic only; gaps or flags0..5 invalidate continuity; flag6 is amplitude clipping warning')
            (self.out/(stem+'.yaml')).write_text(store.dumps(info,indent=2)+'\n',encoding='utf-8',newline='\n')
        self._event('segment',segment=self.segment,epoch=epoch,sequence=sequence,first_frame=first_frame,reason=reason)
    def feed(self,data,now=None):
        try:packet=decode_transport_packet(data)
        except ValueError as error:return self.reject_malformed(error)
        return self.feed_decoded(packet,now)
    def reject_malformed(self,error):
        """Account for a failed shared decode without parsing its bytes again."""
        self.counts['datagrams']+=1;self.counts['malformed']+=1
        frames=getattr(error,'frames',None)
        if frames is not None:self._event('noncontinuous_packet',sequence=getattr(error,'sequence',None),frames=list(frames))
        return False
    def feed_decoded(self,packet,now=None):
        packet=require_packet_view(packet);p=packet.metadata;data=packet.raw
        self.counts['datagrams']+=1;now=time.monotonic() if now is None else now
        seq,epoch,flags=p['sequence'],p['epoch'],p['flags'];frames=p['frame_ids']
        reason=None
        if self.epoch is None or epoch!=self.epoch:
            reason='network_session';self.counts['sessions']+=1
        elif seq==0 and self.sequence is not None and self.sequence>0 and frames[0]<=self.last_frame:
            reason='possible_global_reset_same_epoch';self.counts['sessions']+=1
        elif seq==self.sequence:
            self.counts['duplicates']+=1;return False
        elif seq<self.sequence:
            self.counts['out_of_order']+=1;return False
        elif seq!=self.sequence+1:
            self.counts['packet_gaps']+=seq-self.sequence-1;reason='packet_gap'
        if self.last_frame is not None and reason!='network_session' and frames[0]!=self.last_frame+1:
            self.counts['frame_gaps']+=max(0,frames[0]-self.last_frame-1);reason=reason or 'source_frame_gap'
        if (flags&63)!=(getattr(self,'last_flags',0)&63):reason=reason or ('fatal_flags_onset' if flags&63 else 'fatal_flags_cleared')
        if flags&63:
            self.counts['invalid_flags']+=1
            if not getattr(self,'last_flags',0)&63:self._event('invalid_flags',sequence=seq,flags=flags)
        if flags&64:self.counts['clipping_warnings']+=1
        self.last_flags=flags
        if reason:self._new_segment(epoch,seq,frames[0],reason,flags)
        self.epoch=epoch;self.sequence=seq;self.last_frame=frames[-1];self.counts['frames']+=3
        if self.first_at is None:self.first_at=now
        if self.max_seconds and (now-self.first_at>=self.max_seconds or self.counts['recorded_bytes']+1152>self.max_bytes):
            self._close_segment();self.closed=True
        if self.pcm:
            self.pcm.write(b''.join(data[40+r*392:424+r*392] for r in range(3)))
            self.journal.write(struct.pack('<QIIQQQ',seq,epoch,flags,*frames))
            self.counts['recorded_frames']+=3;self.counts['recorded_bytes']+=1152
        return (flags&63)==0
    def snapshot(self):return dict(**self.counts,epoch=self.epoch,last_sequence=self.sequence,last_frame=self.last_frame,segments=self.segment,recording=bool(self.pcm))
    def close(self):
        self._close_segment();self.closed=True
        if self.out and self.max_seconds:
            self.out.mkdir(parents=True,exist_ok=True)
            (self.out/'summary.yaml').write_text(store.dumps(dict(**self.snapshot(),recent_events=self.events),indent=2)+'\n',encoding='utf-8',newline='\n')
