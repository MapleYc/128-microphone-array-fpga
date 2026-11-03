"""Mini200 monitor: display/record only. All inference and localization stay on FPGA.
Usage: python host/meeting_monitor.py --port COM5 --seconds 300
No waveforms, raw video archive or unbounded recording. Ctrl+C/window close flushes WAV.
"""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
from pathlib import Path
import argparse,binascii,json,struct,time,wave,sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'.deps'))
HEADER=struct.Struct('<4sBBHIQI')
def packet(kind,seq,frame,epoch,payload):
    b=HEADER.pack(b'M128',1,kind,len(payload),seq,frame,epoch)+payload
    return b+struct.pack('<H',binascii.crc_hqx(b,0xffff))
class Parser:
    def __init__(self):self.buffer=bytearray();self.bad_crc=0;self.bad_crc_opening=0;self.bad_crc_stream=0;self.lost=0;self.last=None;self.diagnostics=[]
    def take_diagnostics(self):
        items=self.diagnostics;self.diagnostics=[];return items
    def feed(self,data):
        self.buffer.extend(data);out=[]
        while True:
            k=self.buffer.find(b'M128')
            if k<0:
                self.buffer[:]=self.buffer[-3:];break
            if k:del self.buffer[:k]
            if len(self.buffer)<26:break
            _,version,kind,n,seq,frame,epoch=HEADER.unpack_from(self.buffer)
            if version!=1 or kind not in (1,2,3,4,5,6,7,8,9,10,11,12) or n>294:
                del self.buffer[0];continue
            if len(self.buffer)<26+n:break
            b=self.buffer[:24+n];crc=struct.unpack_from('<H',self.buffer,24+n)[0]
            if binascii.crc_hqx(b,0xffff)!=crc:
                opening=self.last is None
                self.bad_crc_opening+=int(opening);self.bad_crc_stream+=int(not opening)
                self.diagnostics.append(dict(type='packet_crc_error',kind=kind,sequence=seq,source_frame=frame,epoch=epoch,payload_bytes=n,before_first_valid_packet=opening))
                self.bad_crc+=1;del self.buffer[0];continue
            if self.last is None:self.diagnostics.append(dict(type='packet_stream_started',kind=kind,sequence=seq,source_frame=frame,epoch=epoch))
            if self.last is not None:
                delta=(seq-self.last-1)&0xffffffff
                if 0<delta<0x80000000:
                    self.lost+=delta
                    self.diagnostics.append(dict(type='packet_sequence_gap',previous_sequence=self.last,sequence=seq,missing=delta,next_kind=kind,source_frame=frame,epoch=epoch))
            self.last=seq;out.append((kind,seq,frame,epoch,bytes(b[24:])));del self.buffer[:26+n]
        return out

def camera_diagnostic(payload):
    if len(payload) not in (128,160):raise ValueError('Camera diagnostic payload length')
    v=struct.unpack('<%dI'%(len(payload)//4),payload)
    if v[0]!=0x31474443 or v[1] not in (1,2,3) or len(payload)!=(160 if v[1]==3 else 128) or v[3] not in (5,25) or v[7]>65535 or v[27]>3 or v[28]!=0 or v[31]>(1 if v[1]>=2 else 0):
        raise ValueError('Camera diagnostic schema')
    signed=lambda n:n if n<0x80000000 else n-0x100000000
    result=dict(version=v[1],ignore_ninth_bit=bool(v[31]),attempt=v[2],sccb_delay_us=v[3],init_result=signed(v[4]),id_high_result=signed(v[5]),id_low_result=signed(v[6]),
        sensor_id=v[7],mmio_id=v[8],gpio=v[9],sccb_inputs=v[10],capture_status=v[11],capture_error=v[12],
        capture_frame_id=v[13],completed_pixels=v[14],input_size=v[15],output_size=v[16],trace_stage=v[17],
        trace_register=v[18],trace_result=signed(v[19]),trace_value=v[20],table_writes=v[21],first_error_stage=v[22],
        first_error_byte=v[23],init_cycles=v[24],trigger_count=v[25],frames_sent=v[26],id_valid_mask=v[27],
        audio_and_ai_active=False,tx_drops=v[29],cycles=v[30],sensor_id_valid=v[27]==3,
        sensor_id_matches=v[27]==3 and v[7]==0x5640)
    if v[1]==3:
        if v[32] not in range(0x20,0x24) or v[33]>255 or v[36]>65535 or v[37]>65535 or v[38]>255 or v[39]>31:raise ValueError('DVP diagnostic schema')
        result.update(polarity_requested=v[32],polarity_readback=v[33],polarity_write_result=signed(v[34]),polarity_read_result=signed(v[35]),
            sensor_width=v[36],sensor_height=v[37],sensor_format=v[38],probe_valid_mask=v[39],
            polarity_verified=v[34]==0 and v[35]==0 and v[32]==v[33])
    return result

class Recorder:
    def __init__(self,out):
        self.out=out;out.mkdir(parents=True,exist_ok=False);self.last={};self.wav={};self.records=0
        self.events=(out/'events.log').open('w',encoding='utf-8');self.pixels=bytearray();self.image_id=None;self.offset=0;self.pair_capture=None
        self.image_key=None;self.image_width=0;self.image_height=0
    def event(self,**kw):store.append_stream(self.events,{'host_monotonic':time.monotonic(),**kw});self.events.flush()
    def consume(self,kind,seq,frame,epoch,payload,status_type='status'):
        self.records+=1
        if kind==12:
            if len(payload)==96:
                v=struct.unpack('<24I',payload)
                signed=lambda x:x if x<0x80000000 else x-0x100000000
                if (v[0]!=0x31465044 or v[1]!=3 or v[2]>1 or v[3]>1 or v[11]>1 or
                    not 1<=v[12]<=255 or v[13]>32767 or v[15]>1 or v[16]>4 or v[17]>32767 or
                    v[18]>1 or v[19]>2 or v[22]!=0x574e0100 or v[23]>7 or
                    abs(signed(v[8]))>80 or abs(signed(v[9]))>40):return None
                d=dict(version=3,algorithm='dualbeam_wiener256_pl',enabled=v[2],locked=v[3],processed=v[4],filtered=v[5],
                    unmatched=v[6],reference_clipped=v[7],faults=v[6],clipped=v[7],reference_azimuth=signed(v[8]),reference_elevation=signed(v[9]),
                    reference_generation=v[10],primed=v[11],no_votes_required=v[12],no_threshold_q15=v[13],build_id=v[14],
                    target_protected=v[15],strength_shift=v[16],gain_floor_q15=v[17],reference_valid=v[18],reference_pending=v[19],
                    max_cycles=v[20],last_cycles=v[21],hardware_id=v[22],control=v[23],active=v[23]==7,
                    source_frame=frame,epoch=epoch)
                self.event(type='directional_filter',**d);return {'directional_filter':d}
            if len(payload)!=64:return None
            v=struct.unpack('<16I',payload)
            if (v[0]!=0x31465044 or v[1]!=2 or v[2]>1 or v[3]>1 or
                v[8]>32512 or v[9]>8 or v[11]>1 or not 1<=v[12]<=255 or v[13]>32767 or v[15]>1):return None
            names=('enabled','locked','processed','filtered','unmatched','reference_clipped',
                   'reference_delay_q8','reference_shift','resets','primed','no_votes_required','no_threshold_q15','build_id','target_protected')
            d=dict(zip(names,v[2:]));d.update(source_frame=frame,epoch=epoch,version=v[1])
            self.event(type='directional_filter',**d);return {'directional_filter':d}
        elif kind==11:
            if len(payload)!=80:return None
            v=struct.unpack('<20I',payload)
            if (v[0]!=0x3144474b or v[1]!=1 or v[3]>3 or v[4]>32767 or v[5]>1 or
                v[6]>1000 or not 24576<=v[7]<=32767 or v[10]>2 or v[11]>5 or
                v[17]>65535*256 or v[18]>65535*256 or v[19]!=2048):return None
            names=('inferences','keyword_id','score_q15','voice_active','voice_ms','no_threshold_q15',
                   'rejected_no_voice','rejected_no_score','last_action','last_reason')
            d=dict(zip(names,v[2:12]))
            d.update(last_action_frame=v[12]|v[13]<<32,window_end=v[14]|v[15]<<32,actions=v[16],
                     level_q8=v[17],noise_q8=v[18],floor_q8=v[19],source_frame=frame,epoch=epoch,version=v[1])
            self.event(type='command_guard',**d);return {'command_guard':d}
        elif kind==10:
            if len(payload)!=64:return None
            v=struct.unpack('<16I',payload)
            if (v[0]!=0x31445541 or v[1]!=1 or v[2]>1 or v[3]>8 or v[4]>1 or
                not 0<v[5]<=32767 or max(v[10:12])>32768 or v[12] not in (1,2) or
                v[13] not in (1,2) or v[14]!=16000):return None
            names=('enabled','gain_shift','dc_enabled','limit','raw_clipped','beam_clipped',
                   'raw_samples','beam_samples','yes_threshold_q15','direction_threshold_q15',
                   'directions_required','yes_votes_required','sample_rate','build_id')
            d=dict(zip(names,v[2:]));d.update(source_frame=frame,epoch=epoch,version=v[1])
            self.event(type='board_audio_processing',**d);return {'board_audio_processing':d}
        elif kind==9:
            if len(payload)!=76:return None
            v=struct.unpack('<19I',payload)
            if v[0]!=0x31444956 or v[1]!=1 or v[2]>1:return None
            names=('stored','rejected','superseded','read_frames','memory_fault','sensor_frames','malformed_frames','overflow_frames',
                   'flags','sent_frames','payload_packets','aborted_frames','udp_packets','phy_state','phy_id','phy_failure')
            d=dict(zip(names,v[3:]));d.update(inference_enabled=bool(v[2]),source_frame=frame,epoch=epoch)
            d.update(pll_locked=bool(d['flags']&1),gpll_locked=bool(d['flags']&2),ddr_ready=bool(d['flags']&4),phy_ready=bool(d['flags']&8),link_up=bool(d['flags']&16),peer_ready=bool(d['flags']&32),phy_fault=bool(d['flags']&64),tx_error=bool(d['flags']&128))
            self.event(type='video_status',**d);return {'video_status':d}
        elif kind==7:
            if len(payload) not in (80,84,124):return None
            v=struct.unpack('<%dI'%(len(payload)//4),payload)
            if v[0]!=0x314d4143 or v[1] not in (1,2,3) or len(payload)!={1:80,2:84,3:124}[v[1]] or v[3]>1 or v[4]>65535 or v[5]>15 or v[6]>255 or v[7]>255:return None
            if v[1]==3 and (v[21]>3 or v[22]>9 or v[23]>65535 or v[26]>253 or v[27]>2):return None
            d=dict(init_result=v[2] if v[2]<0x80000000 else v[2]-0x100000000,enabled=bool(v[3]),sensor_id=v[4],probe_valid_mask=v[5],
                polarity=v[6],format=v[7],capture_status=v[8],capture_error=v[9],frame_id=v[10],pixels=v[11],
                service_calls=v[12],triggers=v[13],frames_sent=v[14],capture_failures=v[15],timeouts=v[16],image_offset=v[17],
                mono_queued=v[18],beam_queued=v[19],source_frame=frame,epoch=epoch)
            d.update(version=v[1],width=v[20]&65535 if v[1]>=2 else 160,height=v[20]>>16 if v[1]>=2 else 90)
            if v[1]==3:
                stages=('not_started','mmio_id','capture_idle','reset','sensor_id_high','sensor_id_low','sensor_id_match','table_write','configuration_readback','complete')
                d.update(init_attempts=v[21],init_stage=v[22],init_stage_name=stages[v[22]],init_register=v[23],
                    init_expected=v[24],init_observed=v[25],init_table_writes=v[26],last_fault_kind=v[27],
                    last_fault_error=v[28],last_fault_pixels=v[29],last_fault_frame=v[30])
            self.event(type='camera_application_status',**d);return {'camera_application_status':d}
        elif kind==6:
            try:d=camera_diagnostic(payload)
            except ValueError as exc:self.event(type='camera_diagnostic_invalid',reason=str(exc));return None
            self.event(type='camera_diagnostic',**d);return {'camera_diagnostic':d}
        elif kind in (2,3):
            if len(payload)%2:return None
            if kind not in self.wav:
                w=wave.open(str(self.out/('raw.wav' if kind==2 else 'beam.wav')),'wb');w.setparams((1,2,16000,0,'NONE','not compressed'));self.wav[kind]=w
            if kind in self.last:
                prior_epoch,expected=self.last[kind]
                if prior_epoch!=epoch or frame!=expected:
                    self.event(type='audio_discontinuity',stream=kind,epoch=epoch,prior_epoch=prior_epoch,frame=frame,expected=expected)
                    # Preserve short missing intervals. Long resets are logged,
                    # not allowed to create a large sparse/zero WAV file.
                    if prior_epoch==epoch and 0<frame-expected<=24000 and (frame-expected)%3==0:
                        self.wav[kind].writeframesraw(bytes(2*((frame-expected)//3)))
            self.wav[kind].writeframesraw(payload);self.last[kind]=(epoch,frame+3*(len(payload)//2))
        elif kind==1 and len(payload)==96:
            v=struct.unpack('<24I',payload);signed=lambda n:n if n<0x80000000 else n-0x100000000
            d={'mode':['SEARCH','LOCKED'][min(v[0],1)],'keyword':['silence','unknown','yes','no'][min(v[1],3)],
              'keyword_score':v[2]/32768,'azimuth':signed(v[3])/256,'elevation':signed(v[4])/256,'direction_quality':v[5]/32768,
              'mono_drops':v[6],'beam_drops':v[7],'tx_drops':v[8],'discontinuities':v[9],'inferences':v[10],
              'model_errors':v[11],'feature_max_ms':v[12]/63000,'model_slice_max_ms':v[13]/63000,'loop_max_ms':v[14]/63000,
              'queued':v[15],'protocol_errors':v[16],'pairs':v[17],'spatial_faults':v[18],'raw_enabled':v[19],
              'direction_age_ms':(frame-((v[21]<<32)|v[20]))/48,
              'locked_azimuth':signed(v[22])/256,'locked_elevation':signed(v[23])/256,'source_frame':frame,'epoch':epoch}
            self.event(type=status_type,**d);return d
        elif kind in (4,8):
            header=8 if kind==4 else 12
            if len(payload)<=header or (len(payload)-header)%2:return None
            fid,offset=struct.unpack_from('<II',payload)
            width,height=(160,90) if kind==4 else struct.unpack_from('<HH',payload,8)
            if not 1<=width<=1280 or not 1<=height<=720:return None
            count=width*height;key=(kind,fid,frame,width,height)
            if offset==0:
                self.image_id=fid;self.offset=0;self.image_key=key
                self.image_width=width;self.image_height=height;self.pixels=bytearray(count*2)
            if key!=self.image_key or offset!=self.offset or offset*2+len(payload)-header>count*2:return None
            self.pixels[offset*2:offset*2+len(payload)-header]=payload[header:];self.offset+=(len(payload)-header)//2
            if self.offset==count:
                # Retain only the latest original words for byte-order/format audits.
                (self.out/'latest_camera.rgb565').write_bytes(self.pixels)
                rgb=bytearray()
                for (p,) in struct.iter_unpack('<H',self.pixels):rgb.extend((((p>>11)&31)*255//31,((p>>5)&63)*255//63,(p&31)*255//31))
                ppm=f'P6\n{width} {height}\n255\n'.encode()+rgb;(self.out/'latest_camera.ppm').write_bytes(ppm)
                self.event(type='camera_frame',frame_id=fid,pixels=count,width=width,height=height,source_frame=frame)
                self.image_key=None
                return {'image':ppm,'frame_id':fid,'width':width,'height':height}
        elif kind==5:
            if len(payload)<32:
                self.event(type='pair_diagnostic_invalid',reason='short_header');return None
            version,floor,shift,first,count,fit,reason,reserved=struct.unpack_from('<8I',payload)
            signed=lambda n:n if n<0x80000000 else n-0x100000000
            if version!=1 or floor not in (64,16,4) or shift not in (8,6,4) or reserved or len(payload)!=32+16*count:
                self.event(type='pair_diagnostic_invalid',reason='schema');return None
            if count==0 and reason==1:
                d=dict(source_frame=frame,epoch=epoch,energy_floor=floor,raw_shift=shift)
                self.event(type='diagnostic_config',**d);return {'diagnostic_config':d}
            key=(frame,epoch,floor,shift,signed(fit))
            if not 1<=count<=16 or first+count>64 or reason!=2:
                self.event(type='pair_diagnostic_invalid',reason='range');self.pair_capture=None;return None
            if first==0:self.pair_capture={'key':key,'pairs':[]}
            if self.pair_capture is None or self.pair_capture['key']!=key or len(self.pair_capture['pairs'])!=first:
                self.event(type='pair_diagnostic_incomplete',source_frame=frame,first_pair=first);self.pair_capture=None;return None
            import math
            for i in range(count):
                lag,correlation,ea,eb=struct.unpack_from('<iIII',payload,32+16*i)
                den=math.isqrt(ea*eb);q=min(32767,correlation*32768//den) if den else 0
                self.pair_capture['pairs'].append(dict(index=first+i,lag=lag,correlation=correlation,energy_a=ea,energy_b=eb,coherence_q15=q))
            if len(self.pair_capture['pairs'])==64:
                rows=self.pair_capture['pairs'];self.pair_capture=None
                item=dict(source_frame=frame,epoch=epoch,energy_floor=floor,raw_shift=shift,fit_result=signed(fit),pairs=rows,
                    energy_rejected_pairs=sum(p['energy_a']<floor or p['energy_b']<floor for p in rows),
                    coherent_pairs=sum(p['coherence_q15']>=18022 for p in rows))
                self.event(type='pair_snapshot',**item);return {'pair_snapshot':item}
        return None
    def close(self):
        for w in self.wav.values():w.close()
        self.events.close()

def raw_enable_command(requested,status):
    # Firmware 'r' toggles rather than sets. A second recording must not
    # accidentally disable raw PCM that the board is already transmitting.
    return b'r' if requested and status.get('raw_enabled')==0 else b''

def self_test():
    p=Parser();good=packet(3,0xfffffffe,128,2,b'\x00\x00'*128)
    corrupt=bytearray(packet(3,0xffffffff,512,2,b'\x00\x01'));corrupt[-1]^=1
    data=b'junk'+good+corrupt+packet(1,0,900,2,bytes(96));got=[]
    for i in range(0,len(data),7):got+=p.feed(data[i:i+7])
    assert len(got)==2 and p.bad_crc==1 and p.lost==1 and len(p.buffer)<26
    diagnostics=p.take_diagnostics()
    assert [d['type'] for d in diagnostics]==['packet_stream_started','packet_crc_error','packet_sequence_gap']
    assert diagnostics[1]['sequence']==0xffffffff and diagnostics[2]['missing']==1
    assert not diagnostics[1]['before_first_valid_packet'] and p.bad_crc_stream==1 and p.bad_crc_opening==0
    assert p.take_diagnostics()==[]
    opening=Parser();opening.feed(corrupt+packet(1,0,900,2,bytes(96)))
    assert opening.bad_crc==1 and opening.bad_crc_opening==1 and opening.bad_crc_stream==0
    assert opening.take_diagnostics()[0]['before_first_valid_packet']
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        r=Recorder(Path(tmp)/'run')
        r.consume(*got[0]);status=r.consume(*got[1]);assert status['mode']=='SEARCH'
        for i in range(0,14400,128):
            n=min(128,14400-i);v=r.consume(4,i,1,0,struct.pack('<II',7,i)+b'\x00\xf8'*n)
        assert v['image'].endswith(b'\xff\x00\x00') and len(v['image'])<44000
        # A 320x180 frame is self-describing even when CAM status was lost.
        for i in range(0,57600,128):
            data=struct.pack('<IIHH',8,i,320,180)+b'\xe0\x07'*min(128,57600-i)
            decoded=Parser().feed(packet(8,i//128,48000,2,data))
            assert len(decoded)==1
            v=r.consume(*decoded[0])
        assert v['width']==320 and v['height']==180 and v['image']==b'P6\n320 180\n255\n'+b'\x00\xff\x00'*57600
        assert (r.out/'latest_camera.rgb565').stat().st_size==115200
        # Mixed geometry, missing chunks and oversized allocation are rejected.
        assert r.consume(8,0,2,0,struct.pack('<IIHH',9,0,320,180)+b'\x00\x00'*128) is None
        assert r.consume(8,1,2,0,struct.pack('<IIHH',9,128,160,90)+b'\x00\x00'*128) is None
        assert r.consume(8,2,2,0,struct.pack('<IIHH',9,256,320,180)+b'\x00\x00'*128) is None
        assert r.consume(8,3,2,0,struct.pack('<IIHH',9,0,65535,65535)+b'\x00\x00') is None
        diag_parser=Parser()
        cfg=struct.pack('<8I',1,4,8,0,0,0,1,0)
        assert r.consume(5,0,1233,7,cfg)['diagnostic_config']==dict(source_frame=1233,epoch=7,energy_floor=4,raw_shift=8)
        for first in range(0,64,16):
            data=struct.pack('<8I',1,64,8,first,16,1,2,0)+struct.pack('<iIII',-2,1000,1000,1000)*16
            decoded=diag_parser.feed(packet(5,first//16,1234,7,data))
            assert len(decoded)==1
            v=r.consume(*decoded[0])
        assert len(v['pair_snapshot']['pairs'])==64 and v['pair_snapshot']['energy_rejected_pairs']==0
        assert v['pair_snapshot']['coherent_pairs']==64 and v['pair_snapshot']['pairs'][0]['lag']==-2
        # A mixed frame or missing initial chunk must never become a snapshot.
        assert r.consume(5,8,1235,7,data) is None
        words=[0x314d4143,1,0xfffffffe,0,0x5640,3,0,0,0,0,9,0,12,1,0,0,1,0,0,0]
        data=struct.pack('<20I',*words);camera_parser=Parser();decoded=[]
        stream=packet(7,0,96000,2,data)
        for i in range(0,len(stream),3):decoded+=camera_parser.feed(stream[i:i+3])
        assert len(decoded)==1 and camera_parser.bad_crc==0 and camera_parser.lost==0
        v=r.consume(*decoded[0])['camera_application_status']
        assert v['init_result']==-2 and not v['enabled'] and v['sensor_id']==0x5640
        assert v['timeouts']==1 and v['service_calls']==12 and v['source_frame']==96000
        v2=words.copy();v2[1]=2;v2.append((180<<16)|320)
        v=r.consume(7,1,0,0,struct.pack('<21I',*v2))['camera_application_status']
        assert v['width']==320 and v['height']==180 and v['version']==2
        v3=v2.copy();v3[1]=3;v3.extend((3,8,0x3808,5,0,253,1,6,0,42))
        v=r.consume(7,2,0,0,struct.pack('<31I',*v3))['camera_application_status']
        assert v['init_stage_name']=='configuration_readback' and v['init_register']==0x3808
        assert v['init_expected']==5 and v['init_observed']==0 and v['init_attempts']==3
        assert v['last_fault_error']==6 and v['last_fault_frame']==42 and v['capture_error']==0
        for index,value in ((21,4),(22,10),(23,65536),(26,254),(27,3)):
            invalid=v3.copy();invalid[index]=value
            assert r.consume(7,3,0,0,struct.pack('<31I',*invalid)) is None
        assert r.consume(7,1,0,0,data[:-1]) is None
        for index,value in ((0,0),(1,2),(3,2),(5,16),(6,256),(7,256)):
            invalid=words.copy();invalid[index]=value
            assert r.consume(7,1,0,0,struct.pack('<20I',*invalid)) is None
        r.close()
        with wave.open(str(r.out/'beam.wav')) as w:assert w.getnframes()==128 and w.getframerate()==16000
    assert raw_enable_command(True,{'raw_enabled':0})==b'r'
    assert raw_enable_command(True,{'raw_enabled':1})==b''
    assert raw_enable_command(False,{'raw_enabled':0})==b''
    print('PASS: fragmentation, CRC resync, sequence wrap/loss, WAV, RGB565, raw toggle, pair assembly and application camera telemetry; temporary data deleted.')

def main():
    p=argparse.ArgumentParser();p.add_argument('--port');p.add_argument('--seconds',type=int,default=300)
    p.add_argument('--out',type=Path);p.add_argument('--no-gui',action='store_true');p.add_argument('--raw',action='store_true');p.add_argument('--self-test',action='store_true')
    p.add_argument('--request-pairs',action='store_true',help='Request one fresh pair snapshot from the opt-in diagnostic firmware')
    p.add_argument('--pair-period',type=float,default=0.,help='With --request-pairs: repeat p every 1..60 seconds; 0 requests only once')
    p.add_argument('--settle-seconds',type=float,default=1.,help='Log connection initialization before timed WAV capture; 0..5 seconds')
    a=p.parse_args()
    if a.self_test:self_test();return
    if not a.port:p.error('--port COMx required (no automatic programming/port selection)')
    if not 1<=a.seconds<=600:p.error('--seconds must be 1..600; bounded disk use')
    if not 0<=a.settle_seconds<=5:p.error('--settle-seconds must be 0..5')
    if a.pair_period and (not a.request_pairs or not 1<=a.pair_period<=60):p.error('--pair-period requires --request-pairs and 1..60 seconds')
    import shutil
    out=a.out or Path(__file__).resolve().parents[1]/'work/hardware_runs'/time.strftime('%Y%m%d_%H%M%S')
    if shutil.disk_usage(out.anchor).free<256*1024**2:raise SystemExit('Less than 256 MiB free')
    try:import serial
    except ImportError:raise SystemExit('Install pyserial in this Python: python -m pip install pyserial')
    port=serial.Serial(a.port,921600,timeout=0,write_timeout=0.5)
    buffer_hint=hasattr(port,'set_buffer_size')
    if buffer_hint:port.set_buffer_size(rx_size=65536,tx_size=4096)
    port.reset_input_buffer()
    parser=Parser();rec=Recorder(out);connected=time.monotonic();start=None;ui=None;running=True;raw_status_checked=False;pair_request_at=None
    last_camera_at=None;camera_health=None
    baseline=(0,0,0,0)
    print('Recording to',out,'; microphone geometry and camera projection require physical calibration.')
    rec.event(type='host_connect',port=a.port,baud=921600,settle_seconds=a.settle_seconds,
        rx_buffer_hint_bytes=65536 if buffer_hint else None,driver_may_ignore_buffer_hint=True)
    if not a.no_gui:
        import tkinter as tk
        ui=tk.Tk();ui.title('Mini200 / 128 microphone meeting monitor');ui.geometry('740x620')
        label=tk.Label(ui,text='Waiting for FPGA packets',font=('Microsoft YaHei',14),justify='left');label.pack(pady=12)
        picture=tk.Label(ui,text='Camera preview pending');picture.pack()
        camera_note=tk.Label(ui,text='Waiting for camera status');camera_note.pack()
        note=tk.Label(ui,text='Angles are in the microphone coordinate frame. Camera overlay is not calibrated.');note.pack(pady=10)
        tk.Button(ui,text='Release speaker lock',command=lambda:port.write(b'u')).pack()
        tk.Button(ui,text='Toggle raw PCM recording',command=lambda:port.write(b'r')).pack()
        def retry_camera():
            if camera_health is not None:
                port.write(b'c');rec.event(type='camera_retry_requested')
        tk.Button(ui,text='Retry camera capture',command=retry_camera).pack()
        def close():
            nonlocal running
            running=False
        ui.protocol('WM_DELETE_WINDOW',close)
    try:
        while running and (start is None or time.monotonic()-start<a.seconds):
            if start is None and time.monotonic()-connected>=a.settle_seconds:
                start=time.monotonic();baseline=(parser.bad_crc,parser.lost,parser.bad_crc_opening,parser.bad_crc_stream)
                rec.event(type='host_start',requested_seconds=a.seconds,port=a.port,baud=921600,raw_requested=a.raw,
                    connection_crc_errors=baseline[0],connection_sequence_lost=baseline[1])
            messages=parser.feed(port.read(8192))
            for diagnostic in parser.take_diagnostics():rec.event(phase='connection' if start is None else 'capture',**diagnostic)
            for msg in messages:
                if start is None and msg[0]!=1:continue
                d=rec.consume(*msg,status_type='connection_status' if start is None else 'status')
                if not d:continue
                if 'image' in d:
                    last_camera_at=time.monotonic()
                    if ui:
                        scale=max(1,min(640//d['width'],360//d['height']))
                        photo=tk.PhotoImage(data=d['image'],format='PPM').zoom(scale,scale);picture.configure(image=photo,text='');picture.image=photo
                elif 'camera_application_status' in d:
                    camera_health=d['camera_application_status']
                elif 'mode' in d:
                    if not raw_status_checked:
                        raw_status_checked=True
                        command=raw_enable_command(a.raw,d)
                        if command:port.write(command)
                        rec.event(type='raw_request',requested=a.raw,observed_enabled=d['raw_enabled'],toggle_sent=bool(command))
                    if a.request_pairs and start is not None and (pair_request_at is None or a.pair_period and time.monotonic()-pair_request_at>=a.pair_period):
                        port.write(b'p');pair_request_at=time.monotonic()
                        rec.event(type='pair_request',period_seconds=a.pair_period)
                    text=(f"{d['mode']}   Keyword: {d['keyword']} ({d['keyword_score']:.0%})\n"
                      f"DOA: {d['azimuth']:.1f} deg / {d['elevation']:.1f} deg   age {d['direction_age_ms']:.0f} ms\n"
                      f"Quality {d['direction_quality']:.0%}; pairs {d['pairs']}; inferences {d['inferences']}\n"
                      f"Drops mono/beam/TX: {d['mono_drops']}/{d['beam_drops']}/{d['tx_drops']}   CRC errors {parser.bad_crc}\n"
                      f"Feature {d['feature_max_ms']:.2f} ms; Inference slice {d['model_slice_max_ms']:.2f} ms")
                    if ui:label.config(text=text)
                    else:print(json.dumps(d),flush=True)
            if ui:
                age=None if last_camera_at is None else time.monotonic()-last_camera_at
                expected_period=2 if camera_health is None else max(2,camera_health['width']*camera_health['height']/7200)
                freshness='No complete image' if age is None else f'Last image {age:.1f} s ago'+(' (stale)' if age>expected_period*2.5 else '')
                health='' if camera_health is None else (f"; capture {'enabled' if camera_health['enabled'] else 'stopped'}"
                    f"; {camera_health['width']} x {camera_health['height']}; failures/timeouts {camera_health['capture_failures']}/{camera_health['timeouts']}")
                camera_note.config(text=freshness+health)
                ui.update()
            time.sleep(.005)
    except KeyboardInterrupt:pass
    finally:
        rec.event(type='host_end',crc_errors=parser.bad_crc-baseline[0],crc_errors_opening=parser.bad_crc_opening-baseline[2],
            crc_errors_after_first_valid=parser.bad_crc_stream-baseline[3],sequence_lost=parser.lost-baseline[1],
            connection_crc_errors=baseline[0],connection_sequence_lost=baseline[1],total_connection_and_capture_crc_errors=parser.bad_crc,
            packets=rec.records,elapsed_seconds=time.monotonic()-start if start is not None else 0)
        rec.close();port.close()
        if ui:ui.destroy()
        print('Saved WAV/status and only the latest camera image:',out)
if __name__=='__main__':main()
