"""Mini200 display/recorder/player; explicit playback gain, no host keyword recognition or DOA."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
from pathlib import Path
from collections import deque
import argparse,hashlib,json,math,queue,socket,struct,sys,threading,time
from datetime import datetime
# Explicit, read-only official UI dependencies; this file and raw receiver are local.
sys.path.append(str(__import__('pathlib').Path(__file__).resolve().parent / '..'))
if str(__import__('pathlib').Path(__file__).resolve().parent / '../vendor') not in sys.path:sys.path.append(str(__import__('pathlib').Path(__file__).resolve().parent / '../vendor'))
from monitor.raw128_receiver import Raw128Receiver,decode_transport_packet
# Optional explicit launcher hook; no additional socket or default host DSP.
RAW_PREVIEW_ADAPTER=None
from meeting_monitor import Parser,Recorder
from video_protocol import Reassembler,rgb_image
from audio_status import PcmLevel,playback_text
from audio_gain import ListenGain
from command_gate import guard_caption,release_caption,matching_guard
from mono_input_profile import MonoInputProfile,caption as mono_profile_caption
from interference_control import reference_supported
from video_direction_overlay import (MODEL as QUICK_ALIGNMENT,validate_alignment,project_quick,
                                     select_markers,draw_overlay)

def validate_calibration(cal):
    if isinstance(cal,dict) and cal.get('model')==QUICK_ALIGNMENT:return validate_alignment(cal)
    if not isinstance(cal,dict) or cal.get('verified') is not True or cal.get('size')!=[1280,720]:
        raise ValueError('Calibration must be explicitly verified for 1280x720')
    if cal.get('ray_basis')!='array_right_up_forward_to_right_down_forward':
        raise ValueError('Calibration ray basis missing or incompatible; refit using the current calibration tool')
    scalars=[cal.get(n) for n in ('fx','fy','cx','cy')]
    if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in scalars) or min(scalars[:2])<=0:
        raise ValueError('Invalid calibration intrinsics')
    r=cal.get('rotation_array_to_camera')
    if not isinstance(r,list) or len(r)!=3 or any(not isinstance(row,list) or len(row)!=3 for row in r):
        raise ValueError('rotation_array_to_camera must be a 3x3 matrix')
    if not all(isinstance(x,(int,float)) and math.isfinite(x) for row in r for x in row):raise ValueError('Nonfinite rotation')
    if any(abs(sum(r[i][k]*r[j][k] for k in range(3))-(1 if i==j else 0))>1e-3 for i in range(3) for j in range(3)):
        raise ValueError('Rotation must be orthonormal')
    determinant=sum(r[0][j]*(r[1][(j+1)%3]*r[2][(j+2)%3]-r[1][(j+2)%3]*r[2][(j+1)%3]) for j in range(3))
    if abs(determinant-1)>1e-3:raise ValueError('Rotation must have determinant +1')
    distortion=cal.get('distortion',[0]*5)
    if not isinstance(distortion,list) or len(distortion)!=5 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in distortion):
        raise ValueError('Expected finite OpenCV k1,k2,p1,p2,k3 distortion')
    return cal

def project_direction(d,calibration,width,height):
    """Only project with an explicitly verified, matching-resolution calibration."""
    if not calibration or not calibration.get('verified') or calibration.get('size')!=[width,height]:return None
    if d.get('mode')!='LOCKED' and (d.get('direction_quality',0)<.5 or d.get('direction_age_ms',10000)>200):return None
    az=math.radians(d['locked_azimuth'] if d['mode']=='LOCKED' else d['azimuth'])
    el=math.radians(d['locked_elevation'] if d['mode']=='LOCKED' else d['elevation'])
    if not math.isfinite(az) or not math.isfinite(el):return None
    if calibration.get('model')==QUICK_ALIGNMENT:
        return project_quick(calibration,math.degrees(az),math.degrees(el),width,height)
    # Firmware array axes are +X right, +Y up, +Z forward. OpenCV image rays
    # have +Y down. Convert basis BEFORE applying a proper det=+1 rotation.
    vector=[math.sin(az)*math.cos(el),-math.sin(el),math.cos(az)*math.cos(el)]
    r=calibration['rotation_array_to_camera'];v=[sum(r[i][j]*vector[j] for j in range(3)) for i in range(3)]
    if v[2]<=0:return None
    # Rectified-camera model. Raw distorted frames require calibration supplying
    # the matching distortion model; do not claim calibration from a guessed FOV.
    x=v[0]/v[2];y=v[1]/v[2];k1,k2,p1,p2,k3=calibration.get('distortion',[0,0,0,0,0])
    r2=x*x+y*y;f=1+k1*r2+k2*r2*r2+k3*r2*r2*r2
    xd=x*f+2*p1*x*y+p2*(r2+2*x*x);yd=y*f+p1*(r2+2*y*y)+2*p2*x*y
    u=calibration['fx']*xd+calibration['cx'];v=calibration['fy']*yd+calibration['cy']
    return (u,v) if 0<=u<width and 0<=v<height else None

class Session:
    def __init__(self,args,raw_preview=None):
        self.args=args;self.out=args.out;self.out.mkdir(parents=True,exist_ok=False)
        self.stop=threading.Event();self.commands=queue.Queue();self.lock=threading.Lock();self.video_lock=threading.Lock()
        self.status={};self.video_status={};self.camera_status={};self.history=deque(maxlen=80)
        self.diagnostic_config={};self.pair_snapshot={};self.pair_received_at=0
        self.mono_profile_status={}
        self.command_guard={};self.guard_received_at=0
        self.directional_filter={};self.filter_received_at=0
        self.errors={};self.latest=None;self.last_status_at=0;self.audio_mode=0;self.player=None
        self.audio_levels={2:PcmLevel(),3:PcmLevel()}
        self.listen_gain=ListenGain(getattr(args,'listen_gain_db',12));self.output_level=PcmLevel()
        self.board_audio_processing={};self.listen_last={};self.play_lock=threading.Lock()
        self.audio_log=(self.out/'audio_events.log').open('w',encoding='utf-8');self.audio_log_lock=threading.Lock()
        self.playback_last_failure=None;self.playback_failure_count=0
        self.status_log_request=threading.Event();self.diagnostics_owned_async=False
        self.display_enabled=True;self.display_control_version=0
        self.transport_timing=dict(raw_callbacks=0,raw_callback_max_ms=0.,raw_decode_sample_max_ms=0.,
            raw_diagnostic_sample_max_ms=0.,raw_bridge_sample_max_ms=0.,udp_receive_observed_gap_max_ms=0.,
            raw_receive_observed_gap_max_ms=0.,slow_raw_callbacks=0,first_slow_raw_callback=None)
        self._last_udp_receipt_ns=None;self._last_raw_receipt_ns=None
        self.raw_preview=raw_preview if raw_preview is not None else RAW_PREVIEW_ADAPTER
        self.audio_router=getattr(self.raw_preview,'playback_router',None)
        # A separate, explicit playback preference for the enhanced source.
        # Legacy mono gain is not multiplied again on this path.
        requested_pc_db=20*math.log10(getattr(self.audio_router,'monitor_gain',1.))
        self.pc_monitor_gain_db=min(18.,max(0.,requested_pc_db))
        if self.audio_router is not None:
            self._set_pc_monitor_gain(self.pc_monitor_gain_db)
        store.append_stream(self.audio_log,dict(type='initial_playback_gain',host_monotonic=time.monotonic(),
            legacy_gain_db=float(getattr(args,'listen_gain_db',12)),pc_requested_gain_db=self.pc_monitor_gain_db,
            pc_initial_range_limited=requested_pc_db!=self.pc_monitor_gain_db,scope='PLAYBACK_ONLY_NOT_ENHANCEMENT_ALGORITHM'));self.audio_log.flush()
        self.raw128=Raw128Receiver(self.out/'raw128',getattr(args,'raw128_record_seconds',0));self.assembler=Reassembler();self.parser=Parser();self.displayed=0;self.last_display_id=None;self.started=time.monotonic();self.render_profile=deque(maxlen=512);self.display_times=deque(maxlen=120)
        self.threads=[threading.Thread(target=self._audio,daemon=True),threading.Thread(target=self._video,daemon=True)]
        for t in self.threads:t.start()
    def set_error(self,name,e):
        with self.lock:self.errors[name]=str(e)
    def _audio_event(self,**event):
        line=store.dumps(dict(host_monotonic=time.monotonic(),**event),explicit_start=True)
        guard=getattr(self,'audio_log_lock',None)
        try:
            if guard is None:self.audio_log.write(line);self.audio_log.flush();return True
            with guard:self.audio_log.write(line);self.audio_log.flush()
            return True
        except (OSError,ValueError) as exc:
            self.set_error('audio_log',exc)
            return False
    def preview_control_snapshot(self,full=False):
        if not self.raw_preview:return None
        method=getattr(self.raw_preview,'snapshot_full_cached' if full else 'snapshot_compact',None)
        return method() if callable(method) else self.raw_preview.snapshot() if full else self.raw_preview.health()
    def set_display_enabled(self,enabled):
        if type(enabled) is not bool:raise ValueError('Boolean display control required')
        self.display_enabled=enabled;self.display_control_version+=1
        self._audio_event(type='diagnostic_display',enabled=enabled,version=self.display_control_version,
            scope='DISPLAY_ONLY; RAW/VIDEO_NETWORK_AND_ALL_AUDIO_UNCHANGED')
    def listen(self,mode):
        if mode not in (0,2,3,4):raise ValueError('Invalid listen source')
        if self.audio_router is None:
            if mode==4:
                self.set_error('audio_source','PC enhancement is not enabled')
                raise RuntimeError('PC enhancement is not enabled')
            with self.play_lock:
                if self.player:self.player.close();self.player=None
                if mode:
                    from audio_playback import WaveOut
                    self.player=WaveOut()
                self.audio_mode=mode;self.listen_gain.reset();self.output_level=PcmLevel();self.listen_last={}
        else:
            with self.play_lock:self.audio_mode=0
            try:
                self.audio_router.select(mode)
                self.raw_preview.set_listen_mode(mode)
            except Exception as e:
                # A failed selection is not an applied control request. Preserve
                # the cause even if stopping the failed preparation also fails.
                try:self.raw_preview.set_listen_mode(0)
                except Exception as cleanup:self.set_error('audio_source_cleanup',cleanup)
                self.set_error('audio_source',e)
                raise
            with self.play_lock:
                self.audio_mode=mode;self.listen_gain.reset();self.output_level=PcmLevel();self.listen_last={}
            with self.lock:
                self.errors.pop('host_preview',None);self.errors.pop('audio_source',None);self.errors.pop('playback',None)
        self._audio_event(type='listen_selection',mode=mode,
            source={0:'mute',2:'board_mono_16k',3:'board_beam_16k',4:'pc_enhancement_48k'}[mode])
    def audio_snapshot(self):
        route=(self.audio_router.snapshot_compact() if hasattr(self.audio_router,'snapshot_compact') else self.audio_router.snapshot()) if self.audio_router is not None else None
        gain=self.listen_gain.snapshot()
        if route and route['mode']==4:
            requested=getattr(self,'pc_monitor_gain_db',20*math.log10(self.audio_router.monitor_gain))
            native=route.get('player') or {};actual=native.get('monitor_gain')
            applied=20*math.log10(actual) if isinstance(actual,(int,float)) and math.isfinite(actual) and actual>0 and not native.get('closed',False) else None
            gain=dict(gain,gain_db=requested,requested_gain_db=requested,applied_gain_db=applied,
                min_db=0.,max_db=18.,controllable=hasattr(self.audio_router,'set_monitor_gain'),
                pending=applied is None or not math.isclose(requested,applied,abs_tol=.001),
                limited_samples=native.get('clip_samples'),limiter_reduction_db=0.,
                scope='ENHANCED_OUTPUT_PLAYBACK_ONLY_NOT_SPATIAL_SUPPRESSION')
        else:
            gain=dict(gain,min_db=0.,max_db=48.,controllable=True,requested_gain_db=gain['gain_db'],
                applied_gain_db=gain['gain_db'],pending=False)
        return dict(mode=self.audio_mode,raw=self.audio_levels[2].snapshot(),beam=self.audio_levels[3].snapshot(),
            player=route['player'] if route else self.player.snapshot() if self.player else None,
            output=route['output_level'] if route else self.output_level.snapshot(),gain=gain,
            board_processing=self.board_audio_processing.copy(),audio_route=route,
            pc_state=self.raw_preview.health() if self.raw_preview and hasattr(self.raw_preview,'health') else None,
            playback_last_failure=getattr(self,'playback_last_failure',None))
    def set_listen_gain(self,value):
        value=float(value)
        if not math.isfinite(value):raise ValueError('Finite playback gain required')
        if self.audio_mode==4:
            if not 0<=value<=18:raise ValueError('Enhanced playback gain must be 0..18 dB')
            self._set_pc_monitor_gain(value);self.pc_monitor_gain_db=value
            scope='ENHANCED_OUTPUT_PLAYBACK_ONLY_REQUESTED_NOT_NATIVE_ACK'
        else:
            self.listen_gain.set_gain(value);scope='LEGACY_OUTPUT_PLAYBACK_ONLY'
        self._audio_event(type='listen_gain',gain_db=float(value),scope=scope)
    def _set_pc_monitor_gain(self,value):
        multiplier=10**(float(value)/20.)
        if self.raw_preview is not None and hasattr(self.raw_preview,'set_monitor_gain'):
            return self.raw_preview.set_monitor_gain(multiplier)
        if self.audio_router is not None:return self.audio_router.set_monitor_gain(multiplier)
        raise ValueError('Enhanced playback gain backend is unavailable')
    def play_packet(self,msg):
        kind,_,frame,epoch,pcm=msg
        self.audio_levels[kind].feed(pcm)
        with self.play_lock:
            if self.audio_mode!=kind:return
            if self.audio_router is not None:
                ticket=self.audio_router.ticket()
                try:
                    if self.listen_last.get(kind)!=(epoch,frame):self.listen_gain.reset()
                    self.listen_last[kind]=(epoch,frame+3*(len(pcm)//2))
                    audible=self.listen_gain.process(pcm);self.output_level.feed(audible)
                    self.audio_router.feed_board(kind,audible,ticket)
                except Exception as error:
                    # The UART's pre-call ticket may belong to another selection.
                    # Seal the owner that actually attempted this feed only.
                    error.playback_ticket=ticket
                    raise
                return
            if not self.player:return
            if self.listen_last.get(kind)!=(epoch,frame):self.listen_gain.reset()
            self.listen_last[kind]=(epoch,frame+3*(len(pcm)//2))
            audible=self.listen_gain.process(pcm);self.output_level.feed(audible);self.player.feed(audible)
    def _playback_fault(self,kind,error,ticket=None):
        # A playback failure must not kill the UART transport or KWS heartbeat.
        reason=type(error).__name__+': '+str(error)
        route=self.audio_router;accepted=True
        with self.play_lock:
            if route is not None and hasattr(route,'fail_board'):
                try:accepted=route.fail_board(kind,reason,ticket=ticket) is not False
                except Exception as exc:self.set_error('playback_shutdown',exc)
            if accepted:
                if self.audio_mode==kind:self.audio_mode=0
                self.set_error('playback',reason)
                self.playback_failure_count=getattr(self,'playback_failure_count',0)+1
                self.playback_last_failure=dict(kind=kind,reason=reason,ticket=ticket,at=time.monotonic(),count=self.playback_failure_count)
                detail=dict(self.playback_last_failure)
        if not accepted:
            self._audio_event(type='stale_playback_fault_ignored',kind=kind,reason=reason,ticket=ticket,at=time.monotonic())
            return False
        self._audio_event(type='playback_failed_transport_continues',**detail)
        return True
    def record_audio_status(self):
        self._audio_event(type='playback_status',host_preview=self.preview_control_snapshot(),**self.audio_snapshot())
    def request_audio_status_log(self):
        self.status_log_request.set()
    def record_keyword_display_threshold(self,value):
        self._audio_event(type='keyword_display_threshold',percent=value,
            scope='DISPLAY_ONLY_BOARD_ACTIONS_AND_RAW_RECORDS_UNCHANGED')
    def save_calibration_frame(self):
        with self.lock:frame=self.latest
        if frame is None or time.monotonic()-frame.received_at>1:
            raise ValueError('没有新鲜完整图像，不能保存标定帧')
        folder=self.out/'calibration_frames';folder.mkdir(exist_ok=True)
        existing=list(folder.glob('view_*.png'))
        if len(existing)>=40:raise ValueError('本轮已保存40张标定图；请先完成标定')
        path=folder/('view_%02d.png'%(len(existing)+1))
        rgb_image(frame).save(path)
        with (folder/'frames.log').open('a',encoding='utf-8') as log:
            store.append_stream(log,dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                frame_id=frame.frame_id,source_frame=frame.source_frame,width=frame.width,height=frame.height))
        return path
    def _audio(self):
        rec=Recorder(self.out/'uart');port=None;previous_frame=None
        profile=MonoInputProfile(getattr(self.args,'mono_shift',None));profile_logged=None
        floor_command={64:b'H',16:b'M',4:b'L'}.get(getattr(self.args,'energy_floor',None))
        floor_sent=False;pair_interval=getattr(self.args,'pair_interval',0);next_pair=time.monotonic()+1
        try:
            import serial
            port=serial.Serial(self.args.port,921600,timeout=.02,write_timeout=.5)
            if hasattr(port,'set_buffer_size'):port.set_buffer_size(rx_size=65536,tx_size=4096)
            rec.event(type='host_connect',port=self.args.port,baud=921600)
            while not self.stop.is_set():
                profile_command=profile.command(time.monotonic())
                if profile_command:
                    if port.write(profile_command)!=len(profile_command):raise IOError('Incomplete mono profile command')
                    profile.sent(profile_command,time.monotonic())
                    rec.event(type='mono_profile_command',command=profile_command.decode('ascii'))
                profile_state=profile.snapshot()
                if profile.shift is not None and profile_state!=profile_logged:
                    rec.event(type='mono_input_profile',**profile_state);profile_logged=profile_state
                    with self.lock:
                        self.mono_profile_status=profile_state
                        if profile_state['error']:self.errors['mono_profile']=profile_state['error']
                        else:self.errors.pop('mono_profile',None)
                if pair_interval and time.monotonic()>=next_pair:
                    self.commands.put(b'p');next_pair=time.monotonic()+pair_interval
                while not self.commands.empty():
                    cmd=self.commands.get_nowait()
                    if cmd.startswith(b'@B'):
                        with self.lock:
                            allowed=(time.monotonic()-self.last_status_at<=1 and reference_supported(
                                self.directional_filter,time.monotonic()-self.filter_received_at,self.status.get('epoch')))
                        if not allowed:
                            rec.event(type='host_command_rejected',command=cmd.decode('ascii'),
                                      reason='Current dualbeam hardware/version acknowledgement required')
                            continue
                    written=port.write(cmd)
                    if cmd.startswith(b'@B') and written!=len(cmd):raise IOError('Incomplete interference direction command')
                    rec.event(type='operator_command',command=cmd.decode('ascii'))
                for msg in self.parser.feed(port.read(max(1,min(8192,port.in_waiting)))):
                    if self.raw_preview and hasattr(self.raw_preview,'consume_uart_message'):
                        self.raw_preview.consume_uart_message(msg,time.monotonic())
                    d=rec.consume(*msg)
                    if msg[0] in (2,3):
                        ticket=self.audio_router.ticket() if self.audio_router is not None else None
                        try:self.play_packet(msg)
                        except Exception as exc:self._playback_fault(msg[0],exc,getattr(exc,'playback_ticket',ticket))
                    if not d:continue
                    with self.lock:
                        if 'mode' in d:
                            profile.status(d['source_frame'],time.monotonic())
                            self.status=d;self.history.append(d);self.last_status_at=time.monotonic()
                            if previous_frame is not None and d['source_frame']<previous_frame:
                                with self.video_lock:self.assembler.reset()
                                self.history.clear();self.latest=None
                                self.diagnostic_config={};self.pair_snapshot={};self.pair_received_at=0;floor_sent=False
                                self.command_guard={};self.guard_received_at=0
                                self.directional_filter={};self.filter_received_at=0
                            previous_frame=d['source_frame']
                            # Apply only an explicitly requested run setting, once per
                            # observed boot. Display board replies, never assume ACK.
                            if floor_command and not floor_sent:
                                self.commands.put(floor_command);floor_sent=True
                        if 'video_status' in d:self.video_status=d['video_status']
                        if 'board_audio_processing' in d:self.board_audio_processing=d['board_audio_processing']
                        if 'directional_filter' in d:self.directional_filter=d['directional_filter'];self.filter_received_at=time.monotonic()
                        if 'command_guard' in d:self.command_guard=d['command_guard'];self.guard_received_at=time.monotonic()
                        if 'camera_application_status' in d:self.camera_status=d['camera_application_status']
                        if 'diagnostic_config' in d:
                            profile.report(d['diagnostic_config'],time.monotonic(),acknowledgement=True)
                            self.diagnostic_config=d['diagnostic_config'];self.pair_snapshot={};self.pair_received_at=0
                        if 'pair_snapshot' in d:
                            profile.report(d['pair_snapshot'],time.monotonic())
                            self.pair_snapshot=d['pair_snapshot'];self.pair_received_at=time.monotonic()
                            self.diagnostic_config={k:d['pair_snapshot'][k] for k in ('source_frame','epoch','energy_floor','raw_shift')}
                for event in self.parser.take_diagnostics():
                    rec.event(**event)
                    if self.raw_preview and hasattr(self.raw_preview,'consume_uart_diagnostic'):
                        self.raw_preview.consume_uart_diagnostic(event)
        except Exception as e:self.set_error('uart',e)
        finally:
            rec.event(type='host_end',crc_errors=self.parser.bad_crc,sequence_lost=self.parser.lost,elapsed_seconds=time.monotonic()-self.started)
            rec.close()
            if port:port.close()
    def _video(self):
        sock=None
        try:
            sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4*1024*1024)
            sock.bind((self.args.bind,self.args.udp_port));sock.settimeout(.2)
            with (self.out/'video_events.log').open('w',encoding='utf-8') as f:
                while not self.stop.is_set():
                    try:data,peer=sock.recvfrom(2048)
                    except socket.timeout:
                        with self.video_lock:self.assembler.expire()
                        continue
                    if peer[0]!=self.args.board_ip:continue
                    receipt=time.perf_counter_ns();timing=getattr(self,'transport_timing',None)
                    if timing is not None:
                        previous=self._last_udp_receipt_ns;self._last_udp_receipt_ns=receipt
                        if previous is not None:timing['udp_receive_observed_gap_max_ms']=max(timing['udp_receive_observed_gap_max_ms'],(receipt-previous)/1e6)
                    if data[:4]==b'M128':
                        if timing is not None:
                            previous=self._last_raw_receipt_ns;self._last_raw_receipt_ns=receipt
                            if previous is not None:timing['raw_receive_observed_gap_max_ms']=max(timing['raw_receive_observed_gap_max_ms'],(receipt-previous)/1e6)
                            timing['raw_callbacks']+=1
                        try:view_packet=decode_transport_packet(data)
                        except (ValueError,TypeError) as exc:
                            self.raw128.reject_malformed(exc)
                            if self.raw_preview:self.raw_preview.reject_malformed_raw(exc)
                            continue
                        decode_done=time.perf_counter_ns() if timing is not None and timing['raw_callbacks']%256==0 else None
                        self.raw128.feed_decoded(view_packet)
                        diagnostic_done=time.perf_counter_ns() if decode_done is not None else None
                        if self.raw_preview:
                            try:self.raw_preview.consume_decoded_datagram(view_packet)
                            except Exception as exc:self.set_error('host_preview',exc)
                        if timing is not None:
                            end=time.perf_counter_ns();duration=(end-receipt)/1e6
                            timing['raw_callback_max_ms']=max(timing['raw_callback_max_ms'],duration)
                            if duration>1:
                                timing['slow_raw_callbacks']+=1
                                if timing['first_slow_raw_callback'] is None:timing['first_slow_raw_callback']=dict(at_ns=receipt,duration_ms=duration,sequence=view_packet.metadata['sequence'])
                            if decode_done is not None:
                                timing['raw_decode_sample_max_ms']=max(timing['raw_decode_sample_max_ms'],(decode_done-receipt)/1e6)
                                timing['raw_diagnostic_sample_max_ms']=max(timing['raw_diagnostic_sample_max_ms'],(diagnostic_done-decode_done)/1e6)
                                timing['raw_bridge_sample_max_ms']=max(timing['raw_bridge_sample_max_ms'],(end-diagnostic_done)/1e6)
                        continue
                    with self.video_lock:frame=self.assembler.feed(data)
                    if frame:
                        with self.lock:self.latest=frame
                        store.append_stream(f,dict(type='complete_video_frame',frame_id=frame.frame_id,source_frame=frame.source_frame,
                            width=frame.width,height=frame.height,received_at=frame.received_at,assembly_seconds=frame.assembly_seconds));f.flush()
        except Exception as e:self.set_error('network',e)
        finally:
            if sock:sock.close()
            self.raw128.close()
    def close(self):
        self.stop.set()
        # Seal output immediately when shutdown is requested, before UDP timeout
        # and I/O joins. The old order left native playback starved for .2s+.
        preclose_audio_status=self.audio_snapshot()
        if self.raw_preview:self.raw_preview.close()
        if self.audio_router:self.audio_router.close()
        for t in self.threads:t.join(timeout=2)
        final_audio_status=self.audio_snapshot()
        final_player=final_audio_status.get('player') or {}
        final_route=final_audio_status.get('audio_route') or {}
        if self.audio_router:self.audio_router.close()
        if self.raw_preview:self.raw_preview.close()
        if self.player:self.player.close()
        self.record_audio_status();self.audio_log.close()
        elapsed=time.monotonic()-self.started
        with self.video_lock:
            report=dict(raw128=self.raw128.snapshot(),host_preview=self.raw_preview.snapshot() if self.raw_preview else None,elapsed_seconds=elapsed,received=self.assembler.counts.copy(),displayed_frames=self.displayed,
                complete_fps_including_startup=self.assembler.counts['complete']/elapsed,displayed_fps_including_startup=self.displayed/elapsed,crc_errors=self.parser.bad_crc,uart_sequence_lost=self.parser.lost,
                errors=self.errors,physical_latency_measured=False,hardware_acceptance='PENDING_REVIEW',
                playback_status_before_shutdown=preclose_audio_status,
                mono_input_profile=self.mono_profile_status.copy(),
                playback_samples_dropped=final_player.get('dropped_samples'),
                playback_error=final_route.get('error') or final_player.get('error'),playback_status=final_audio_status,playback_status_after_close=self.audio_snapshot())
        (self.out/'summary.yaml').write_text(store.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (self.out/'render_profile.yaml').write_text(store.dumps(list(self.render_profile)),encoding='utf-8')
        if self.latest:
            (self.out/'latest_camera.rgb565').write_bytes(self.latest.pixels)
            rgb_image(self.latest).save(self.out/'latest_camera.png')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='COM8');p.add_argument('--bind',default='192.168.10.1');p.add_argument('--board-ip',default='192.168.10.2');p.add_argument('--udp-port',type=int,default=5001)
    p.add_argument('--seconds',type=int,default=300);p.add_argument('--no-gui',action='store_true');p.add_argument('--calibration',type=Path)
    p.add_argument('--alignment-file',type=Path,default=Path(str(__import__('pathlib').Path(__file__).resolve().parent / '../../config/local_alignment.yaml')))
    p.add_argument('--energy-floor',type=int,choices=(4,16,64),help='Explicit diagnostic firmware setting; omitted preserves board state')
    p.add_argument('--mono-shift',type=int,choices=(4,6,8),help='Opt-in mono/keyword input scaling with board acknowledgement; beam and DOA unchanged')
    p.add_argument('--pair-interval',type=float,default=0,help='Read-only pair snapshot interval in seconds; 0 disables')
    p.add_argument('--listen-gain-db',type=float,default=12,help='Playback-only gain 0..48 dB; WAV remains board PCM')
    p.add_argument('--keyword-display-min',type=float,default=85,help='Display-only minimum keyword score in percent; board actions unchanged')
    p.add_argument('--out',type=Path,default=Path(str(__import__('pathlib').Path(__file__).resolve().parent / '../../runs'))/datetime.now().strftime('av720_%Y%m%d_%H%M%S'))
    p.add_argument('--raw128-record-seconds',type=float,default=0,help='0 disables disk capture; bounded 0..60 s raw diagnostic PCM24, no host DSP')
    a=p.parse_args()
    if not 0<=a.raw128_record_seconds<=60:p.error('--raw128-record-seconds must be 0..60')
    if not 1<=a.seconds<=3600:p.error('--seconds must be 1..3600; bounded recordings')
    if not 1<=a.udp_port<=65535:p.error('Invalid UDP port')
    if not math.isfinite(a.pair_interval) or (a.pair_interval!=0 and not 1<=a.pair_interval<=60):p.error('--pair-interval must be 0 or 1..60')
    try:
        import ipaddress,shutil
        ListenGain.validate(a.listen_gain_db)
        from keyword_display import threshold_percent
        threshold_percent(a.keyword_display_min)
        ipaddress.IPv4Address(a.bind);ipaddress.IPv4Address(a.board_ip)
        selected_cal=a.calibration or (a.alignment_file if a.alignment_file.is_file() else None)
        cal=validate_calibration(store.loads(selected_cal.read_text(encoding='utf-8-sig'))) if selected_cal else None
        if shutil.disk_usage(a.out.resolve().anchor).free<512*1024**2:raise ValueError('Less than 512 MiB free on recording drive')
    except (ValueError,OSError) as e:p.error(str(e))
    # Finish GUI and image-library startup before receiving timed live data.
    # The recording still includes every frame from Session creation onward.
    session=None;ui=None;display_worker=None
    try:
        if not a.no_gui:
            import tkinter as tk
            import numpy as np
            from PIL import Image,ImageTk,ImageDraw
            from meeting_dashboard import Dashboard
            ui=tk.Tk()
            def save_frame():
                try:view.show_message('已保存原始标定图：'+str(session.save_calibration_frame().name),time.monotonic())
                except (ValueError,OSError) as e:session.set_error('calibration_capture',e)
            alignment_window=None
            def open_alignment():
                nonlocal alignment_window
                from video_alignment_ui import AlignmentDialog
                if alignment_window and alignment_window.window.winfo_exists():
                    alignment_window.window.lift();return
                def capture():
                    from calibration_capture import capture_metadata
                    now=time.monotonic()
                    with session.lock:
                        f=session.latest;hist=list(session.history)
                        last_status_at=session.last_status_at;epoch=session.status.get('epoch')
                    metadata=capture_metadata(f,hist,epoch,now,last_status_at)
                    return rgb_image(f),metadata
                def use_alignment(value):
                    nonlocal cal
                    from calibration_store import save_and_readback
                    reloaded=save_and_readback(a.alignment_file,value,validate_calibration,
                                               session.out/'previous_alignment.yaml')
                    payload=store.dumps(reloaded,indent=2,ensure_ascii=False)
                    (session.out/'active_alignment.yaml').write_text(payload,encoding='utf-8')
                    cal=reloaded
                    view.show_message('声画对齐已启用；镜头相对阵列移动后需重做',time.monotonic())
                    session.last_display_id=None
                alignment_window=AlignmentDialog(ui,capture,use_alignment,session.out/'camera_alignment',save_images=False)
            view=Dashboard(ui,a,dict(
                listen=lambda mode:session.listen(mode) if session else None,
                gain=lambda value:session.set_listen_gain(value) if session else None,
                keyword_threshold=lambda value:session.record_keyword_display_threshold(value) if session else None,
                command=lambda cmd:session.commands.put(cmd) if session else None,
                save=save_frame,alignment=open_alignment,close=lambda:session.stop.set() if session else None))
            canvas=view.canvas
            from latest_frame_render import LatestFrameRenderer
            from video_direction_overlay import font as display_font
            display_font(15);display_font(16) # GUI-library warmup before live Session creation.
            display_worker=LatestFrameRenderer(rgb_image)
            display_request_key=None;display_geometry_key=None;display_geometry_version=0
            display_rejected=0;view.rendered_geometry=None;last_display_enabled=True
            from video_reference_drag import VideoReferenceDrag
            def video_reference_geometry():
                photo=getattr(canvas,'image',None)
                if photo is None:return None
                return ((canvas.winfo_width(),canvas.winfo_height()),(photo.width(),photo.height()))
            video_reference=VideoReferenceDrag(lambda:(view.rendered_geometry or {}).get('calibration'),video_reference_geometry,
                lambda:(view.reference_ready,view.reference_feedback['message']),view.request_reference,
                lambda text:view.show_message(text,time.monotonic()))
            canvas.bind('<ButtonPress-3>',video_reference.begin)
            canvas.bind('<B3-Motion>',video_reference.move)
            canvas.bind('<ButtonRelease-3>',video_reference.release)
            ui.bind('<Escape>',video_reference.cancel,add='+')
            warmup=ImageTk.PhotoImage(Image.new('RGB',(1,1)))
            ui.update();ui.update_idletasks()
        session=Session(a)
        session.display_worker=display_worker # diagnostic ownership; no Tk in worker
        last_print=0;last_status_update=0;last_audio_log=0
        while not session.stop.is_set() and time.monotonic()-session.started<a.seconds:
            profile_start=time.perf_counter();profile_stage=None
            now=time.monotonic()
            if now-last_audio_log>=1:
                if getattr(session,'diagnostics_owned_async',False):session.request_audio_status_log()
                else:session.record_audio_status()
                last_audio_log=now
            if session.player and session.player.error:session.set_error('playback',session.player.error)
            with session.lock:
                d=session.status.copy();v=session.video_status.copy();c=session.camera_status.copy();frame=session.latest;history=list(session.history);errors=session.errors.copy();status_at=session.last_status_at
                diag=session.diagnostic_config.copy();pair=session.pair_snapshot.copy();pair_at=session.pair_received_at
                mono_profile=session.mono_profile_status.copy()
                guard=matching_guard(session.command_guard.copy(),d,now-session.guard_received_at)
                d['command_guard']=guard
                df=session.directional_filter.copy()
                d['directional_filter_age']=(max(0,now-session.filter_received_at)
                    if df and df.get('epoch')==d.get('epoch') else None)
                if now-session.filter_received_at>1.5 or df.get('epoch')!=d.get('epoch'):df={}
                d['directional_filter']=df
            with session.video_lock:fps=session.assembler.fps();counts=session.assembler.counts.copy()
            age=None if frame is None else now-frame.received_at
            if ui:
                if now-last_status_update>=.2:
                    display_ts=[t for t in session.display_times if now-t<5]
                    display_fps=(len(display_ts)-1)/(display_ts[-1]-display_ts[0]) if len(display_ts)>1 and now-display_ts[-1]<1 else 0.
                    doa_age=d.get('direction_age_ms')
                    direction_time='尚无有效方向' if not d.get('direction_quality') else ('未收到方向时间' if doa_age is None else f'距今 {doa_age/1000:.2f}s'+('（历史结果）' if doa_age>1000 else ''))
                    locked_direction=(f"{d.get('locked_azimuth',0):.1f}° / {d.get('locked_elevation',0):.1f}°" if d.get('mode')=='LOCKED' else '未锁定')
                    text=(f"板端：{d.get('mode','等待')}   关键词：{d.get('keyword','—')} ({d.get('keyword_score',0):.0%})   推理次数：{d.get('inferences',0)}   "
                      f"AI使能：{v.get('inference_enabled','—')}\n"
                      f"声源：{d.get('azimuth',0):.1f}° / {d.get('elevation',0):.1f}°   "
                      f"方向质量：{d.get('direction_quality',0):.0%}   {direction_time}   "
                      f"锁定方向：{locked_direction}\n"
                      f"1280×720 接收 {fps:.2f} fps / 显示 {display_fps:.2f} fps   丢弃不完整帧 {counts['incomplete']}   "
                      f"DDR：{v.get('ddr_ready','—')}   千兆链路：{v.get('link_up','—')}   "
                      f"音频丢包 {d.get('mono_drops',0)}/{d.get('beam_drops',0)}   UART CRC {session.parser.bad_crc}")
                    if a.pair_interval or a.energy_floor is not None:
                        eligible=sum(x['energy_a']>=pair['energy_floor'] and x['energy_b']>=pair['energy_floor'] and x['coherence_q15']>=18022 and -56<=x['lag']<=56 for x in pair.get('pairs',[]))
                        detail=(f"抽样初筛 {eligible}/64（至少32） · 此样本拟合{'通过' if pair['fit_result']==1 else '未通过'} · 诊断距今 {now-pair_at:.1f}s" if pair else '等待配对诊断')
                        text+=f"\n板端能量门槛：{diag.get('energy_floor','待确认')} · {detail}"
                        if a.energy_floor is not None and diag.get('energy_floor')!=a.energy_floor:text+=f' · 请求门槛 {a.energy_floor} 待确认'
                    if now-status_at>1:text+='\n板端状态已过期，不能用旧状态判断当前运行。'
                    policy=session.board_audio_processing
                    if policy:
                        text+=f"\n板端锁定规则：yes ≥{policy['yes_threshold_q15']/32768:.0%}、{policy['yes_votes_required']}次识别 + 同一语音窗内{policy['directions_required']}次方向 ≥{policy['direction_threshold_q15']/32768:.0%}"
                    audio=session.audio_snapshot()
                    if session.raw_preview:
                        hs=session.raw_preview.health()
                        text+='\nEnhancement: '+str(hs.get('state'))+'; physical azimuth/elevation; DSP/control/native-playback counters are separate'
                        if hs.get('error'):text+='; '+str(hs['error'])
                    detail=text+'\n\n'+playback_text(audio)+'\n\n'+('；'.join(f'{k}: {val}' for k,val in errors.items()) if errors else '未观察到连接错误')
                    if policy:detail+=f"\n板端版本：{policy.get('build_id','—')}"
                    if mono_profile:detail+='\n'+mono_profile_caption(mono_profile)
                    if guard:
                        detail+=f"\n口令防误触：{guard_caption(guard)}；同窗有效声音 {guard['voice_ms']} ms；NO 门槛 {guard['no_threshold_q15']/32768:.0%}"
                        detail+=f"\nNO 拦截累计：低能量 {guard['rejected_no_voice']} / 得分不足 {guard['rejected_no_score']}；最近解锁：{release_caption(guard) or '无'}"
                    if df:
                        if df.get('version')==3:
                            detail+=f"\n板端双波束频域抑制：{'开' if df['enabled'] else '关'}；已处理 {df['processed']} / 已滤波 {df['filtered']}；故障位 0x{df['faults']:x} / 饱和次数 {df['clipped']}；固件 {df['build_id']}"
                            detail+=f"\n干扰 B {df['reference_azimuth']:+.0f}° / {df['reference_elevation']:+.0f}°；提交代次 {df['reference_generation']}；有效 {df['reference_valid']} / 等待提交 {df['reference_pending']}；硬件使能 0x{df['control']:x}；最长处理 {df['max_cycles']} 周期"
                        else:
                            detail+=f"\n板端自适应抵消：{'开' if df['enabled'] else '关'}；已处理 {df['processed']} / 已滤波 {df['filtered']}；参考未匹配 {df['unmatched']} / 参考削顶 {df['reference_clipped']}；固件 {df['build_id']}"
                        detail+=f"\nNO 解锁：{df['no_votes_required']}次 ≥{df['no_threshold_q15']/32768:.0%} + 有效语音检查；界面关键词门槛仅影响显示。"
                    view.update(status=d,video=v,status_age=now-status_at,frame_age=age,fps=fps,display_fps=display_fps,
                                audio=audio,errors=errors,diagnostic_text=detail,elapsed=now-session.started,now=now,
                                calibrated=cal.get('model','intrinsics') if cal else False)
                    last_status_update=now
                display_enabled=getattr(session,'display_enabled',True)
                if display_enabled!=last_display_enabled:
                    view.rendered_geometry=None;video_reference.cancel()
                    if not display_enabled:
                        canvas.configure(image='',text='显示诊断暂停；音频与网络持续');canvas.image=None
                    else:view.show_message('显示诊断已恢复',now)
                    last_display_enabled=display_enabled
                viewport=tuple(view.video_size());geometry_key=(viewport,id(cal),getattr(session,'display_control_version',0))
                if geometry_key!=display_geometry_key:
                    display_geometry_version+=1;display_geometry_key=geometry_key
                if frame and display_enabled:
                    request_key=(frame.frame_id,frame.source_frame,display_geometry_version)
                    if request_key!=display_request_key:
                        display_worker.submit(frame,viewport,version=display_geometry_version)
                        display_request_key=request_key
                prepared=display_worker.poll_latest()
                if prepared is not None:
                    # Never combine a newer frame's angles with an older image.
                    paint_frame=prepared['frame']
                    matching=(display_enabled and frame is not None and prepared['viewport_size']==viewport and
                              prepared['geometry_version']==display_geometry_version and
                              (paint_frame.frame_id,paint_frame.source_frame)==(frame.frame_id,frame.source_frame) and
                              prepared['source_size']==(paint_frame.width,paint_frame.height))
                    if not matching:display_rejected+=1
                    else:
                        profile_rgb=time.perf_counter();img=prepared['image']
                        aligned=min(history,key=lambda x:abs(x['source_frame']-paint_frame.source_frame)) if history else None
                        displayed_status=aligned if aligned and abs(aligned['source_frame']-paint_frame.source_frame)<9600 else {}
                        markers=select_markers(displayed_status,max(now-status_at,now-paint_frame.received_at))
                        reference=view.reference_report
                        if view.reference_ready and reference.get('reference_valid')==1 and reference.get('reference_pending')==0:
                            markers.append(dict(kind='reference',azimuth=reference['reference_azimuth'],elevation=reference['reference_elevation']))
                        def project(az,el):
                            return project_direction(dict(mode='LOCKED',locked_azimuth=az,locked_elevation=el),cal,paint_frame.width,paint_frame.height)
                        draw_overlay(img,markers,project if cal else None,cal.get('model','intrinsics') if cal else None)
                        profile_photo=time.perf_counter()
                        photo=ImageTk.PhotoImage(img);canvas.configure(image=photo,text='',width=0,height=0);canvas.image=photo
                        # Same main-thread publication as the actual displayed PhotoImage.
                        view.rendered_geometry=dict(frame_id=paint_frame.frame_id,source_frame=paint_frame.source_frame,
                            source_size=prepared['source_size'],viewport_size=viewport,geometry_version=display_geometry_version,
                            image_size=img.size,calibration=cal)
                        profile_stage=dict(frame_id=paint_frame.frame_id,source_frame=paint_frame.source_frame,
                            display_age_ms=max(0,time.monotonic()-paint_frame.received_at)*1000,
                            before_rgb_ms=(profile_rgb-profile_start)*1000,worker_rgb_ms=prepared['convert_ms'],
                            worker_resize_ms=prepared['resize_ms'],rgb_ms=(profile_photo-profile_rgb)*1000,
                            photo_ms=(time.perf_counter()-profile_photo)*1000,display_rejected=display_rejected)
                        session.displayed+=1;session.last_display_id=(paint_frame.frame_id,paint_frame.source_frame);session.display_times.append(time.monotonic())
                display_state=display_worker.snapshot()
                if display_state.get('error'):session.set_error('display_worker',display_state['error'])
                if age is not None and age>1:
                    view.video_interrupted()
                profile_update=time.perf_counter();ui.update()
                if profile_stage is not None:
                    profile_stage.update(update_ms=(time.perf_counter()-profile_update)*1000,total_ms=(time.perf_counter()-profile_start)*1000)
                    session.render_profile.append(profile_stage)
            elif now-last_print>=1:
                print(json.dumps(dict(status=d,video=v,diagnostic_config=diag,pair_snapshot=pair,mono_input_profile=mono_profile,fps=fps,errors=errors),ensure_ascii=False),flush=True);last_print=now
            time.sleep(.01)
    except KeyboardInterrupt:pass
    finally:
        if session:session.close()
        if display_worker and not display_worker.close(2):print('display worker shutdown pending')
        if ui:ui.destroy()
        print('记录已保存：'+str(a.out))
if __name__=='__main__':main()
