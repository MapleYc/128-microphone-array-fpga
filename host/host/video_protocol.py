"""Strict, bounded V128 UDP reassembly. No inference or acoustic DSP."""
from collections import OrderedDict,deque
from dataclasses import dataclass
import struct,time
HEADER=struct.Struct('<4sBBHIQIHHHH')
CHUNK=1024
@dataclass
class VideoFrame:
    frame_id:int
    source_frame:int
    width:int
    height:int
    pixels:bytes
    received_at:float
    assembly_seconds:float
class Reassembler:
    def __init__(self,width=1280,height=720,timeout=.7,max_pending=3):
        if not 0<width<=1280 or not 0<height<=720 or max_pending<1 or timeout<=0:raise ValueError('geometry/bounds')
        self.width=width;self.height=height;self.total=width*height*2
        self.timeout=timeout;self.max_pending=max_pending;self.pending=OrderedDict();self.recent=deque(maxlen=32)
        self.counts=dict(datagrams=0,bytes=0,complete=0,malformed=0,duplicate=0,conflicting=0,incomplete=0,late=0,resets=0)
        self.times=deque(maxlen=120);self.last_id=None
    def reset(self):
        self.counts['incomplete']+=len(self.pending);self.pending.clear();self.recent.clear();self.times.clear();self.last_id=None;self.counts['resets']+=1
    def expire(self,now=None):
        now=time.monotonic() if now is None else now
        for key,v in list(self.pending.items()):
            if now-v['start']>self.timeout:self.counts['incomplete']+=1;del self.pending[key]
    def feed(self,data,now=None):
        now=time.monotonic() if now is None else now;self.expire(now);self.counts['datagrams']+=1;self.counts['bytes']+=len(data)
        if len(data)<HEADER.size:self.counts['malformed']+=1;return None
        magic,version,fmt,hlen,fid,source,offset,w,h,n,flags=HEADER.unpack_from(data)
        if (magic!=b'V128' or version!=1 or fmt!=1 or hlen!=32 or flags!=0 or (w,h)!=(self.width,self.height) or
                offset>=self.total or offset%CHUNK or n!=min(CHUNK,self.total-offset) or len(data)!=32+n):
            self.counts['malformed']+=1;return None
        key=(fid,source)
        # Keep frame IDs, not only ID/timestamp pairs: all conflicting metadata
        # for a poisoned/completed frame must remain excluded until reset/wrap.
        if fid in self.recent:self.counts['duplicate']+=1;return None
        if self.last_id is not None and ((fid-self.last_id)&0xffffffff)>=0x80000000:
            self.counts['late']+=1;return None
        if key not in self.pending:
            # One frame ID must never have contradictory metadata.
            other=[k for k in self.pending if k[0]==fid]
            if other:
                for k in other:del self.pending[k]
                self.recent.append(fid);self.counts['conflicting']+=1;return None
            while len(self.pending)>=self.max_pending:self.pending.popitem(last=False);self.counts['incomplete']+=1
            self.pending[key]=dict(start=now,pixels=bytearray(self.total),seen=bytearray((self.total+CHUNK-1)//CHUNK),count=0)
        item=self.pending[key];index=offset//CHUNK;payload=data[32:]
        if item['seen'][index]:
            if item['pixels'][offset:offset+n]!=payload:
                del self.pending[key];self.recent.append(fid);self.counts['conflicting']+=1
            else:self.counts['duplicate']+=1
            return None
        item['pixels'][offset:offset+n]=payload;item['seen'][index]=1;item['count']+=1
        if item['count']!=len(item['seen']):return None
        del self.pending[key];self.recent.append(fid);self.last_id=fid;self.counts['complete']+=1;self.times.append(now)
        # Evict older incomplete frames; they cannot supersede this display.
        for k in list(self.pending):
            if ((fid-k[0])&0xffffffff)<0x80000000:del self.pending[k];self.counts['incomplete']+=1
        return VideoFrame(fid,source,w,h,bytes(item['pixels']),now,now-item['start'])
    def fps(self,now=None):
        now=time.monotonic() if now is None else now
        ts=[t for t in self.times if now-t<5]
        return (len(ts)-1)/(ts[-1]-ts[0]) if len(ts)>1 and ts[-1]>ts[0] and now-ts[-1]<1 else 0.
def rgb_image(frame):
    import numpy as np
    from PIL import Image
    p=np.frombuffer(frame.pixels,dtype='<u2').reshape(frame.height,frame.width)
    rgb=np.empty((frame.height,frame.width,3),dtype=np.uint8)
    rgb[:,:,0]=((p>>11)*255//31).astype(np.uint8)
    rgb[:,:,1]=(((p>>5)&63)*255//63).astype(np.uint8)
    rgb[:,:,2]=((p&31)*255//31).astype(np.uint8)
    return Image.fromarray(rgb)
