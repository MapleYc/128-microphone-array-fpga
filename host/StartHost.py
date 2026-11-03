"""Explicit interactive GUI session; no media persistence; frozen source untouched."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
from pathlib import Path
import argparse, collections, ctypes, datetime, hashlib, importlib, json, os, subprocess, sys, time, uuid
HOST=Path(__file__).resolve().parent/'host'
USER_STATE=Path(__file__).resolve().parent/'config'
PYTHON=Path(sys.executable)
TITLE='Mini200 | 128阵列音频交互 · 系统验证'
INPUT_MANIFEST_EXPECTED='6e7bb85ecb4ddc22c20660d0f128862166c53d8ddcef17b94e744ea08973d2fa'
LOCAL_SUPPORT_EXPECTED='e4e00448c98cbb47e87ed918a0549bc75350db702175e4b4843f43a9ad5034b2'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
WRITE_STATS=dict(replace_permission_errors=0,replace_retry_exhausted=0)

def write(p,d,attempts=3,retry_delay=.01):
    if type(attempts) is not int or attempts<1:raise ValueError('attempts must be positive')
    t=p.with_suffix('.writing')
    t.write_text(store.dumps(d,indent=2,ensure_ascii=True),encoding='utf-8')
    for attempt in range(attempts):
        try:
            t.replace(p);return
        except PermissionError:
            WRITE_STATS['replace_permission_errors']+=1
            if attempt+1==attempts:
                WRITE_STATS['replace_retry_exhausted']+=1;raise
            time.sleep(retry_delay)


class TelemetryTicker:
    """Best-effort monitoring; telemetry errors cannot stop the next GUI tick."""
    def __init__(self,root,control,snapshot,report_error,interval_ms=500):
        self.root,self.control,self.snapshot,self.report_error=root,control,snapshot,report_error
        self.interval_ms=interval_ms
        self.state=dict(ticks=0,completed_ticks=0,control_errors=0,snapshot_errors=0,
                        error_report_errors=0,reschedule_errors=0,last_error=None)

    def _error(self,stage,error):
        self.state[stage+'_errors']+=1
        detail=dict(stage=stage,error=repr(error),tick=self.state['ticks'],
                    utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        self.state['last_error']=detail
        try:self.report_error(dict(**detail,counts=dict(self.state),atomic_write_counts=dict(WRITE_STATS)))
        except Exception as reporting_error:
            self.state['error_report_errors']+=1
            self.state['last_error_report_failure']=repr(reporting_error)
            # Console fallback is numeric/text diagnostics only, never audio/media.
            try:
                print('TELEMETRY_ERROR_REPORT_FAILED '+repr(detail)+' '+repr(reporting_error),file=sys.stderr,flush=True)
            except Exception:pass

    def tick(self):
        self.state['ticks']+=1
        complete=True
        try:
            for stage,operation in (('control',self.control),('snapshot',self.snapshot)):
                try:operation()
                except Exception as error:
                    complete=False;self._error(stage,error)
            if complete:self.state['completed_ticks']+=1
        finally:
            try:self.root.after(self.interval_ms,self.tick)
            except Exception as error:
                # A destroyed GUI cannot be rescheduled; preserve this distinct boundary.
                self._error('reschedule',error)

def child(out,settings):
    sys.path.insert(0,str(HOST))
    launcher=importlib.import_module('launcher');original_load=launcher.load_monitor
    # Import fixed dependencies before the sole UART/UDP receiver is opened.
    # No target, source direction, DSP stream or audio device is constructed.
    # This moves import work out of the first user-selected enhancement path.
    adapter_module=importlib.import_module('legacy_adapter')
    frozen_module,_,_=adapter_module.load_base();frozen_module.configure()
    for module_name in ('runtime3d','worker3d','machine3d','doa3d','direction3d','calibrate3d'):
        module=importlib.import_module(module_name)
        assert Path(module.__file__).resolve().parent==HOST, 'Wrong preloaded module: '+module_name
    from diagnostic_async import LatestJsonDiagnostics,DiagnosticAuditPending
    diag=dict(uart_bytes=0,uart_chunks=0,uart_magic_M128=0,uart_printable_bytes=0,uart_valid_messages=0,udp_datagrams_all_peers=0,udp_bytes_all_peers=0,udp_peers={})
    owned_support=('audio_playback','audio_gain','meeting_monitor','video_protocol','command_gate','mono_input_profile','interference_control')
    diag['local_support_modules']={}
    for name in owned_support:
        module=importlib.import_module(name);path=Path(module.__file__).resolve()
        assert path.parent==HOST,'Nonlocal first-party support module: '+name+' '+str(path)
        diag['local_support_modules'][name]=dict(path=str(path),sha256=sha(path))
    diag['raw_sample_audit']=dict(method='one in 512 RAW packets, numeric counts only; no sample storage',sampled_packets=0,sample_count=0,active_samples=0,bad_mic_samples=0,active_rail_samples=0,bad_mic_rail_samples=0,active_near_rail_samples=0,active_peak_abs=0,active_sum_squares=0,header_clip_sampled_packets=0)
    gui={};session_box={};control_last=[0];diagnostic_writer=[None]
    def load():
        m=original_load();original_parser=m.Parser;original_recorder=m.Recorder;original_session=m.Session;socket_module=m.socket
        class CountParser(original_parser):
            def feed(self,data):
                diag['uart_bytes']+=len(data);diag['uart_chunks']+=bool(data);diag['uart_magic_M128']+=data.count(b'M128')
                diag['uart_printable_bytes']+=sum(32<=x<127 or x in (9,10,13) for x in data)
                msgs=super().feed(data);diag['uart_valid_messages']+=len(msgs);return msgs
        class NoMediaRecorder(original_recorder):
            def consume(self,kind,seq,frame,epoch,payload,status_type='status'):
                if kind in (4,8):self.records+=1;return None
                if kind not in (2,3):return super().consume(kind,seq,frame,epoch,payload,status_type)
                self.records+=1
                if len(payload)%2:return None
                if kind in self.last:
                    pe,expected=self.last[kind]
                    if pe!=epoch or frame!=expected:self.event(type='audio_discontinuity',stream=kind,epoch=epoch,prior_epoch=pe,frame=frame,expected=expected)
                self.last[kind]=(epoch,frame+3*(len(payload)//2));return None
        class CountSocket:
            def __init__(self,s):self.s=s
            def __getattr__(self,k):return getattr(self.s,k)
            def recvfrom(self,n):
                data,peer=self.s.recvfrom(n);diag['udp_datagrams_all_peers']+=1;diag['udp_bytes_all_peers']+=len(data)
                key=str(peer[0]);peers=diag['udp_peers'];key=key if key in peers or len(peers)<8 else 'other';peers[key]=peers.get(key,0)+1
                if peer[0]==settings['board_ip'] and len(data)==1208 and data[:4]==b'M128':
                    diag['raw_seen_for_sampling']=diag.get('raw_seen_for_sampling',0)+1
                    if diag['raw_seen_for_sampling']%512==0:
                        q=diag['raw_sample_audit'];q['sampled_packets']+=1;q['header_clip_sampled_packets']+=int(bool(data[31]&64))
                        for row in range(3):
                            for ch in range(128):
                                i=40+row*392+ch*3;v=data[i]|data[i+1]<<8|data[i+2]<<16
                                if v&0x800000:v-=1<<24
                                q['sample_count']+=1;bad=ch in (2,3);q['bad_mic_samples' if bad else 'active_samples']+=1
                                if v in (-8388608,8388607):q['bad_mic_rail_samples' if bad else 'active_rail_samples']+=1
                                if not bad:
                                    q['active_peak_abs']=max(q['active_peak_abs'],abs(v));q['active_sum_squares']+=v*v
                                    q['active_near_rail_samples']+=int(abs(v)>=8220835)
                return data,peer
        class SocketProxy:
            def __getattr__(self,k):return getattr(socket_module,k)
            def socket(self,*a,**kw):return CountSocket(socket_module.socket(*a,**kw))
        class InteractiveSession(original_session):
            @property
            def latest(self):return None if getattr(self,'_closing_without_media',False) else getattr(self,'_latest',None)
            @latest.setter
            def latest(self,value):self._latest=value
            def __init__(self,args,*a,**kw):
                # Official CLI is 1..3600 seconds; the explicitly requested no-record
                # interactive variant changes only its lifecycle after validation.
                args.seconds=sys.maxsize
                super().__init__(args,*a,**kw);self.diagnostics_owned_async=True;session_box['session']=self
            def save_calibration_frame(self):raise ValueError('Image saving is disabled for this session')
            def close(self):
                self._closing_without_media=True # suppress persistence even if recv was in flight
                super().close()
        m.Parser=CountParser;m.Recorder=NoMediaRecorder;m.Session=InteractiveSession;m.socket=SocketProxy()
        dash=importlib.import_module('meeting_dashboard');original_dashboard=dash.Dashboard
        def sample_status():
            s=session_box.get('session')
            counters=dict(diag);counters['udp_peers']=dict(diag['udp_peers']);counters['atomic_write_counts']=dict(WRITE_STATS)
            d=dict(state='INTERACTIVE_GUI_RUNNING',pid=os.getpid(),utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),title=TITLE,gui=dict(gui),transport=counters,media_recording=False,automatic_close_timer=False,process_cpu_seconds=time.process_time())
            if s:
                with s.lock:d.update(errors=s.errors.copy(),status=s.status.copy(),video_status=s.video_status.copy(),uart_crc_errors=s.parser.bad_crc)
                with s.video_lock:d.update(video_counts=s.assembler.counts.copy())
                d.update(raw128=s.raw128.snapshot(),host_preview=s.preview_control_snapshot(),elapsed_seconds=time.monotonic()-s.started,
                    transport_timing=dict(s.transport_timing),display_enabled=s.display_enabled,display_control_version=s.display_control_version)
                d['audio_source']=s.audio_snapshot()
                if s.status_log_request.is_set():
                    s.status_log_request.clear()
                    s._audio_event(type='playback_status',host_preview=d['host_preview'],**d['audio_source'])
                display=getattr(s,'display_worker',None)
                d['display']=dict(worker=None if display is None else display.snapshot(),
                    presented_frames=s.displayed,last_display_id=s.last_display_id,
                    last_render=None if not s.render_profile else dict(s.render_profile[-1]))
            writer=diagnostic_writer[0]
            if writer is not None:d['diagnostic_writer']=writer.snapshot()
            return d
        def sample_fault():
            s=session_box.get('session')
            if s is None:raise DiagnosticAuditPending('Session not available')
            small=s.preview_control_snapshot() or {};backend=small.get('backend') or {}
            if backend.get('worker_alive') is True or backend.get('done') is False:
                raise DiagnosticAuditPending('Worker final audit not yet joined')
            full=s.preview_control_snapshot(full=True) or {};audit=full.get('backend') or {}
            if full.get('dsp_execution_mode')=='process' and not audit.get('full_audit_is_final',audit.get('final',False)):
                raise DiagnosticAuditPending('Final process audit not published yet')
            return full
        def snapshot():
            writer=diagnostic_writer[0]
            if writer is not None:writer.request()
        def control():
            p=out.parent/'CONTROL_REQUEST.yaml';s=session_box.get('session')
            if s is None or not p.is_file():return
            req=store.loads(p.read_text(encoding='utf-8'));seq=req.get('sequence')
            if type(seq) is not int or seq<=control_last[0]:return
            command=req.get('command');reply=dict(sequence=seq,command=command,pid=os.getpid(),utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
            try:
                if command=='select_audio':
                    mode=req.get('mode')
                    if type(mode) is not int or mode not in (0,2,3,4):raise ValueError('Invalid explicit source')
                    s.listen(mode)
                elif command=='set_gain':s.set_listen_gain(req.get('gain_db'))
                elif command=='diagnostic_display':s.set_display_enabled(req.get('enabled'))
                elif command=='close':s.stop.set()
                else:raise ValueError('Unsupported bounded application control')
                reply.update(status='APPLIED',audio_source=s.audio_snapshot(),display_enabled=s.display_enabled)
            except Exception as e:reply.update(status='FAILED',error=repr(e))
            control_last[0]=seq;write(out.parent/'CONTROL_REPLY.yaml',reply)
            with (out.parent/'CONTROL_EVENTS.log').open('a',encoding='utf-8') as f:store.append_stream(f,reply)
        class InteractiveDashboard(original_dashboard):
            def __init__(self,root,args,callbacks):
                callbacks=dict(callbacks);callbacks['save']=lambda:None
                super().__init__(root,args,callbacks)
                root.title(TITLE);root.deiconify();root.update_idletasks();root.lift()
                for key in ('save',):
                    if key in self.controls:self.controls[key].configure(state='disabled')
                self.note.configure(text='实时画面 · 声源定位 · 目标锁定 · 定向增强')
                gui.update(created=True,window_id=int(root.winfo_id()),mapped=bool(root.winfo_ismapped()),geometry=root.geometry())
                diagnostic_writer[0]=LatestJsonDiagnostics(out.parent/'LIVE_STATUS.yaml',sample_status,sample_fault)
                ticker=TelemetryTicker(root,control,snapshot,
                    lambda detail:write(out.parent/'DIAGNOSTIC_ERROR.yaml',detail))
                diag['telemetry']=ticker.state
                self._telemetry_ticker=ticker
                root.after(100,ticker.tick)
            def update(self,*a,**kw):
                super().update(*a,**kw);self.remaining.configure(text='实时运行 · 关闭窗口结束')
        dash.Dashboard=InteractiveDashboard
        return m
    launcher.load_monitor=load
    sys.argv=[str(HOST/'launcher.py'),'--host-preview','--dsp-process','--preview-config',str(HOST/'fixtures/P01.yaml'),'--port',settings['port'],'--bind',settings['bind'],'--board-ip',settings['board_ip'],'--udp-port',str(settings['udp_port']),'--seconds','3600','--raw128-record-seconds','0','--preview-inbox-packets','1024','--preview-monitor-gain','3.9810717055349722','--enable-playback','--alignment-file',str(settings['alignment_file']),'--out',str(out)]
    try:launcher.main()
    finally:
        if diagnostic_writer[0] is not None:
            diag['diagnostic_writer_closed']=diagnostic_writer[0].close(5)
            diag['diagnostic_writer_final']=diagnostic_writer[0].snapshot()
        final=dict(pid=os.getpid(),utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),transport=diag,gui=gui,
            media_files=[str(p) for p in out.rglob('*') if p.is_file() and p.suffix.lower() in ('.wav','.pcm','.pcm24','.rgb565','.png','.ppm','.bin')])
        current=session_box.get('session')
        if current is not None:
            try:
                final['audio_final']=current.audio_snapshot()
                final['host_preview_final']=None if current.raw_preview is None else current.preview_control_snapshot(full=True)
                worker=getattr(current,'display_worker',None)
                final['display_final']=None if worker is None else worker.snapshot()
            except Exception as error:final['final_snapshot_error']=repr(error)
        write(out.parent/'CLOSED.yaml',final)

def verify_inputs(alignment_file=None):
    expected=INPUT_MANIFEST_EXPECTED
    assert sha(HOST/'MANIFEST.yaml')==expected, 'Candidate manifest changed'
    deps=Path(__file__).resolve().parent/'LOCAL_SUPPORT_BINDING.yaml'
    assert sha(deps)==LOCAL_SUPPORT_EXPECTED, 'Local support receipt changed'
    for name,h in store.loads(deps.read_text(encoding='utf-8'))['files'].items():
        assert sha(Path(__file__).resolve().parent/name)==h, 'Local HOST support changed: '+name
    manifest=store.loads((HOST/'MANIFEST.yaml').read_text(encoding='utf-8'))
    for name,h in manifest['files'].items():assert sha(HOST/name)==(h['sha256'] if isinstance(h,dict) else h), 'Candidate input changed: '+name
    for name in ('launcher.py','board_follow.py','legacy_adapter.py','audio_source_router.py','meeting_dashboard.py'):
        assert (HOST/name).is_file(), 'Incomplete independent candidate'
    return dict(status='INPUTS_VERIFIED_NO_DEVICE',manifest_sha256=expected,local_support_sha256=LOCAL_SUPPORT_EXPECTED,
        wrapper_sha256=sha(__file__),calibration_configured=Path(alignment_file or USER_STATE/'local_alignment.yaml').is_file(),
        calibration_sha256=sha(Path(alignment_file or USER_STATE/'local_alignment.yaml')) if Path(alignment_file or USER_STATE/'local_alignment.yaml').is_file() else None,host=str(HOST))
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--child',type=Path);ap.add_argument('--validate-only',action='store_true')
    ap.add_argument('--port',default='COM8');ap.add_argument('--bind',default='192.168.10.1');ap.add_argument('--board-ip',default='192.168.10.2');ap.add_argument('--udp-port',type=int,default=5001)
    ap.add_argument('--alignment-file',type=Path,default=USER_STATE/'local_alignment.yaml');a=ap.parse_args()
    settings=dict(port=a.port,bind=a.bind,board_ip=a.board_ip,udp_port=a.udp_port,alignment_file=a.alignment_file.resolve())
    verified=verify_inputs(settings['alignment_file'])
    if a.validate_only:
        assert a.child is None,'validate-only cannot launch a child'
        print(json.dumps(verified),flush=True);return 0
    if a.child:return child(a.child,settings)
    if not verified['calibration_configured']:print('CALIBRATION_NOT_CONFIGURED: use现场声画校准 before relying on the direction overlay.',flush=True)
    expected=verified['manifest_sha256']
    out=Path(str(__import__('pathlib').Path(__file__).resolve().parent / 'runs'))/('host_system_validation_interactive_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:8]);out.mkdir(parents=True,exist_ok=False)
    cmd=[str(PYTHON),'-B',str(Path(__file__).resolve()),'--child',str(out/'session'),'--port',settings['port'],'--bind',settings['bind'],'--board-ip',settings['board_ip'],'--udp-port',str(settings['udp_port']),'--alignment-file',str(settings['alignment_file'])]
    with (out/'console.log').open('xb') as log:
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=1
        p=subprocess.Popen(cmd,cwd=HOST,stdout=log,stderr=subprocess.STDOUT,startupinfo=startup,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW)
    r=dict(pid=p.pid,command=cmd,cwd=str(HOST),title=TITLE,port=settings['port'],udp=settings['bind']+':'+str(settings['udp_port']),board=settings['board_ip'],wrapper_sha256=sha(__file__),candidate_manifest_sha256=expected,lifecycle='USER_CLOSE_GUI_ENDS_SESSION',original_cli_bound='1..3600 seconds; wrapper changes Session lifecycle only after validation',media_recording=False,manual_lock=False,playback_requested=True,started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    write(out/'LAUNCH.yaml',r);print(json.dumps(dict(out=str(out),**r)),flush=True)
if __name__=='__main__':main()
