"""Bounded control-file acceptance for ONE already-running owned HOST.

Default invocation prints a plan and performs no filesystem/device operation.
There are no serial, socket, audio, GUI, subprocess or bit-programming imports.
Real runs read numeric metadata and use the HOST's bounded normal controls only.
"""
from __future__ import annotations
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import argparse,datetime as dt,json,math,os,re,time,uuid
from pathlib import Path

ROWS=(('mono_display_off',2,False),('mono_display_on',2,True),
      ('enhanced_display_off',4,False),('enhanced_display_on',4,True))
NATIVE_FIELDS=('rate','channels','bits','requested_format','accepted_format','strict_no_drop','drop_policy','fed_samples',
 'submitted_samples','completed_samples','queued_samples','held_samples','inflight_samples',
 'queued_blocks','inflight_blocks','dropped_samples','rejected_samples','overflow','underflow',
 'cancelled_samples','cancelled_queued_samples','cancelled_held_samples','cancelled_submitted_samples',
 'cancelled_unsubmitted_samples','cancelled_submitted_unconfirmed_samples',
 'ledger_balanced','submitted_ledger_balanced','error','device_closed','closed','started',
 'native_closed_ack','native_device_open','monitor_gain','remote_generation','source_sampled_ns',
 'parent_cache_age_s','completion_age_seconds','max_feed_gap_ms','max_worker_wake_gap_ms','native_api_max_ms')

class AcceptanceError(RuntimeError):
    def __init__(self,code,detail=''):
        super().__init__(code+(': '+str(detail) if detail else ''));self.code=code;self.detail=str(detail)

def number(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
def pick(d,keys):return {key:d.get(key) for key in keys}

def native_drop_evidence(p):
    """Do not invent a legacy drop counter for a newer strict-credit sink.

    Both independently rebuilt equalities account for every admitted sample.
    Rejected input, cancellation and native errors remain separate failure gates.
    Missing or invalid credit fields cannot establish this alternate evidence.
    """
    dropped=p.get('dropped_samples')
    if dropped is not None:
        return 'EXPLICIT_ZERO_COUNTER' if type(dropped) is int and dropped==0 else None
    names=('fed_samples','submitted_samples','completed_samples','queued_samples','held_samples','inflight_samples',
           'cancelled_unsubmitted_samples','cancelled_submitted_unconfirmed_samples')
    if p.get('strict_no_drop') is not True or any(type(p.get(k)) is not int or p[k]<0 for k in names):return None
    if p.get('ledger_balanced') is not True or p.get('submitted_ledger_balanced') is not True:return None
    if any(p.get(k)!=0 for k in ('underflow','overflow')):return None
    if p['fed_samples']!=p['submitted_samples']+p['queued_samples']+p['held_samples']+p['cancelled_unsubmitted_samples']:return None
    if p['submitted_samples']!=p['completed_samples']+p['inflight_samples']+p['cancelled_submitted_unconfirmed_samples']:return None
    return 'STRICT_ADMISSION_AND_TWO_REBUILT_CREDIT_LEDGERS_NO_SYNTHESIZED_DROP_COUNT'
def utc_seconds(value):
    try:
        value=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        if value.tzinfo is None:raise ValueError('timezone required')
        return value.timestamp()
    except (AttributeError,TypeError,ValueError) as error:raise AcceptanceError('LIVE_TIME_INVALID',error)

def compact(live):
    """Whitelist numeric/text status. Never copy media, RAW, full audits or retired sinks."""
    a=live.get('audio_source') or {};route=a.get('audio_route') or {};p=a.get('player') or route.get('player') or {}
    hp=live.get('host_preview') or {};b=hp.get('backend') or {};engine=b.get('backend') or {}
    return dict(utc=live.get('utc'),pid=live.get('pid'),mode=a.get('mode'),board_mode=(live.get('status') or {}).get('mode'),
      board_frame=(live.get('status') or {}).get('source_frame'),board_epoch=(live.get('status') or {}).get('epoch'),
      display_enabled=live.get('display_enabled'),display_control_version=live.get('display_control_version'),
      presented_frames=(live.get('display') or {}).get('presented_frames'),video_complete=(live.get('video_counts') or {}).get('complete'),
      gain=pick(a.get('gain') or {},('requested_gain_db','applied_gain_db','gain_db','pending','scope')),
      native=pick(p,NATIVE_FIELDS),route=pick(route,('mode','requested_mode','source','epoch','pc_owner','sink_open','transition_pending','pc_preparing','board_preparing','error')),
      preview=pick(hp,('state','error','sticky_failure','generation','backend_owner','diagnostic_stale','status_age_s','last_failure','first_transport_failure','transport_fault_history')),
      worker=pick(b,('state','worker_pid','generation','worker_alive','ready','done','error','admitted_packets','inbox_peak_packets','oldest_age_ms','max_queue_age_ms','max_consume_ms','playback_gain','publication_timing')),
      counts=b.get('counts'),dynamic=pick(engine.get('dynamic') or {},('active_frames','tracker_valid_frames','event_counts','first_accepted')),
      output=pick(engine.get('output') or {},('samples','nonzero_samples','peak_abs')),output_level=a.get('output'),
      source_levels={key:pick(a.get(key) or {},('packets','samples','peak','ac_dbfs','age_seconds')) for key in ('raw','beam')},
      raw=pick(live.get('raw128') or {},('datagrams','packet_gaps','frame_gaps','duplicates','out_of_order','recorded_bytes')),
      uart_messages=(live.get('transport') or {}).get('uart_valid_messages'),uart_crc=live.get('uart_crc_errors'),
      process_cpu_seconds=live.get('process_cpu_seconds'),transport_timing=live.get('transport_timing'),diagnostic_writer=live.get('diagnostic_writer'),errors=live.get('errors'))

def current_audio_errors(live,mode):
    s=compact(live);p=s['native'];route=s['route'];errors=[]
    if route.get('error'):errors.append('route: '+str(route['error']))
    if p.get('error'):errors.append('native: '+str(p['error']))
    if mode==4 and (s['preview'].get('error') or s['preview'].get('sticky_failure')):
        errors.append('enhancement: '+str(s['preview'].get('error')))
    return errors

class Clock:
    monotonic=staticmethod(time.monotonic)
    wall=staticmethod(time.time)
    sleep=staticmethod(time.sleep)

class FileIO:
    """Read status/control metadata, write the control request and an owned lock."""
    def __init__(self,run_dir,out):
        self.run=Path(run_dir).resolve();self.out=Path(out).resolve();self.token=uuid.uuid4().hex
        self.out.mkdir(parents=True,exist_ok=False);self.lock=self.run/'SYSTEM_AUDIO_ACCEPTANCE.lock'
    def read(self,name,optional=False):
        try:return store.loads((self.run/name).read_text(encoding='utf-8-sig'))
        except FileNotFoundError:
            if optional:return None
            raise
    def exists(self,name):return (self.run/name).is_file()
    def claim(self):
        try:
            with self.lock.open('x',encoding='utf-8') as f:f.write(store.dumps(dict(token=self.token,pid=os.getpid(),scope='CONTROL_FILES_ONLY')))
        except FileExistsError:raise AcceptanceError('OTHER_CONTROLLER_LOCK',str(self.lock))
    def release(self):
        if self.lock.is_file():
            data=store.loads(self.lock.read_text(encoding='utf-8'))
            if data.get('token')==self.token:self.lock.unlink() # Only this controller's transient lock.
    def request(self,data):
        target=self.run/'CONTROL_REQUEST.yaml';temporary=target.with_name('CONTROL_REQUEST.'+self.token+'.writing')
        temporary.write_text(store.dumps(data,ensure_ascii=True),encoding='utf-8');temporary.replace(target)
    def emit(self,kind,data):
        with (self.out/'observations.log').open('a',encoding='utf-8') as f:store.append_stream(f,dict(kind=kind,**data),allow_nan=False)
    def result(self,data):(self.out/'RESULT.yaml').write_text(store.dumps(data,ensure_ascii=True,indent=2,allow_nan=False),encoding='utf-8')

class Controller:
    def __init__(self,io,pid,manifest,*,clock=None,poll=.2,prepare_timeout=30.,command_timeout=5.):
        if type(pid) is not int or pid<=0 or not re.fullmatch(r'[0-9a-fA-F]{64}',manifest):raise ValueError('Exact PID and SHA256 required')
        self.io=io;self.pid=pid;self.manifest=manifest.lower();self.clock=clock or Clock();self.poll=poll
        self.prepare_timeout=prepare_timeout;self.command_timeout=command_timeout;self.sequence=0;self.claimed=False
        self.last_sample_utc=None;self.records=[]
    def status(self):
        if self.io.exists('CLOSED.yaml'):raise AcceptanceError('GUI_CLOSED')
        launch=self.io.read('LAUNCH.yaml')
        if launch.get('pid')!=self.pid or str(launch.get('candidate_manifest_sha256','')).lower()!=self.manifest:
            raise AcceptanceError('LAUNCH_IDENTITY_MISMATCH')
        live=self.io.read('LIVE_STATUS.yaml')
        if live.get('pid')!=self.pid:raise AcceptanceError('LIVE_PID_MISMATCH')
        age=self.clock.wall()-utc_seconds(live.get('utc'))
        if not -2<=age<=2:raise AcceptanceError('LIVE_STALE_OR_FUTURE',age)
        if live.get('media_recording') is not False or (live.get('raw128') or {}).get('recorded_bytes',0)!=0:
            raise AcceptanceError('NO_MEDIA_GUARD_FAILED')
        return live
    def acquire(self):
        self.status();self.io.claim();self.claimed=True
    def ensure_lock(self,live=None):
        live=live or self.status();hp=live.get('host_preview') or {};target=hp.get('target_status') or {}
        if (live.get('status') or {}).get('mode')!='LOCKED' or target.get('mode')!='LOCKED' or not number(hp.get('status_age_s')) or hp['status_age_s']>1:
            raise AcceptanceError('NEEDS_REAL_USER_YES_LOCK','No synthetic lock or KWS command will be sent')
    def command(self,name,**fields):
        current=self.status()
        if name not in ('select_audio','diagnostic_display','set_gain'):raise AcceptanceError('CONTROL_NOT_ALLOWED',name)
        if name=='select_audio':
            if type(fields.get('mode')) is not int or fields['mode'] not in (0,2,3,4):raise AcceptanceError('SOURCE_MODE_INVALID')
            if fields['mode']==4:self.ensure_lock(current)
        if name=='diagnostic_display' and type(fields.get('enabled')) is not bool:raise AcceptanceError('DISPLAY_VALUE_INVALID')
        if name=='set_gain':
            ceiling=18 if (current.get('audio_source') or {}).get('mode')==4 else 48
            if not number(fields.get('gain_db')) or not 0<=fields['gain_db']<=ceiling:raise AcceptanceError('GAIN_VALUE_INVALID')
        request=self.io.read('CONTROL_REQUEST.yaml',True) or {};reply=self.io.read('CONTROL_REPLY.yaml',True) or {}
        rq=request.get('sequence',0);rp=reply.get('sequence',0)
        if type(rq) is not int or type(rp) is not int or min(rq,rp)<0:raise AcceptanceError('CONTROL_SEQUENCE_INVALID')
        if rq>rp:raise AcceptanceError('CONTROL_ALREADY_PENDING',rq)
        if reply and reply.get('pid')!=self.pid:raise AcceptanceError('CONTROL_REPLY_PID_MISMATCH')
        self.sequence=max(self.sequence,rq,rp)+1;sequence=self.sequence
        value=dict(sequence=sequence,command=name,**fields)
        self.io.request(value);self.io.emit('command_issued',dict(controller_elapsed=self.clock.monotonic(),**value))
        end=self.clock.monotonic()+self.command_timeout
        while self.clock.monotonic()<end:
            self.status();answer=self.io.read('CONTROL_REPLY.yaml',True) or {};got=answer.get('sequence',0)
            if type(got) is not int or got<0:raise AcceptanceError('CONTROL_REPLY_SEQUENCE_INVALID')
            if got>sequence:raise AcceptanceError('CONTROL_REPLACED_BY_OTHER_CONTROLLER',got)
            if got==sequence:
                if answer.get('pid')!=self.pid or answer.get('command')!=name:raise AcceptanceError('CONTROL_REPLY_IDENTITY_MISMATCH')
                if answer.get('status')!='APPLIED':raise AcceptanceError('CONTROL_REJECTED',answer.get('error'))
                self.io.emit('command_ack',dict(sequence=sequence,command=name));return answer
            self.clock.sleep(self.poll)
        raise AcceptanceError('CONTROL_TIMEOUT_UNCERTAIN_NO_RETRY',sequence)
    def is_ready(self,live,mode):
        s=compact(live);p=s['native'];r=s['route'];a=live.get('audio_source') or {}
        if a.get('mode')!=mode or r.get('mode')!=mode or r.get('sink_open') is not True or r.get('transition_pending') or r.get('pc_preparing') or r.get('board_preparing'):return False
        if current_audio_errors(live,mode):return False
        if p.get('rate')!={2:16000,4:48000}[mode] or not number(p.get('completed_samples')) or p['completed_samples']<=0:return False
        if p.get('device_closed') is True or p.get('closed') is True:return False
        accepted=p.get('accepted_format')
        if isinstance(accepted,dict) and (accepted.get('rate')!={2:16000,4:48000}[mode] or accepted.get('channels')!=1 or accepted.get('bits')!=16):return False
        g=a.get('gain') or {}
        if g.get('pending') is not False or not number(g.get('requested_gain_db')) or not number(g.get('applied_gain_db')) or abs(g['requested_gain_db']-g['applied_gain_db'])>.001:return False
        if mode==4:
            self.ensure_lock(live);wg=s['worker'];pg=wg.get('playback_gain') or {}
            if s['preview'].get('backend_owner')!='active' or s['preview'].get('diagnostic_stale') or wg.get('worker_alive') is not True:return False
            if wg.get('generation')!=p.get('remote_generation') or wg.get('generation') is None:return False
            if pg.get('pending') is not False or pg.get('requested_serial')!=pg.get('applied_serial'):return False
        return True
    def prepare(self,mode,display):
        start=self.status()
        if mode==4:self.ensure_lock(start)
        self.io.emit('preparation_begin',dict(mode=mode,display_enabled=display,sample=compact(start)))
        self.command('diagnostic_display',enabled=display)
        if not self.is_ready(start,mode):self.command('select_audio',mode=mode)
        end=self.clock.monotonic()+self.prepare_timeout;last=start;seen=None
        while self.clock.monotonic()<end:
            last=self.status()
            if last.get('utc')!=seen:
                self.io.emit('preparation_sample',dict(mode=mode,sample=compact(last)));seen=last.get('utc')
            errors=current_audio_errors(last,mode)
            if errors:raise AcceptanceError('AUDIO_PREPARATION_FAILED','; '.join(errors))
            if mode==4:self.ensure_lock(last)
            if last.get('display_enabled') is display and self.is_ready(last,mode):
                self.io.emit('prepared',dict(mode=mode,display_enabled=display,start=compact(start),ready=compact(last)))
                return start,last
            self.clock.sleep(self.poll)
        raise AcceptanceError('AUDIO_PREPARATION_TIMEOUT',compact(last))
    @staticmethod
    def classify(start,last,samples,mode,display):
        a=compact(start);z=compact(last);fail=[];warnings=[];p0=a['native'];p1=z['native']
        for live in samples:
            s=compact(live);p=s['native'];r=s['route']
            if s['mode']!=mode or r.get('mode')!=mode:fail.append('SOURCE_CHANGED_OR_DISABLED')
            if r.get('epoch')!=a['route'].get('epoch'):fail.append('ROUTE_EPOCH_CHANGED_DURING_ROW')
            if r.get('sink_open') is not True or r.get('transition_pending') or r.get('pc_preparing') or r.get('board_preparing'):fail.append('SOURCE_NOT_STEADY_READY')
            if s['display_enabled'] is not display:fail.append('DISPLAY_CONTROL_CHANGED')
            if current_audio_errors(live,mode):fail.append('AUDIO_ERROR')
            if p.get('device_closed') is True or p.get('closed') is True:fail.append('CURRENT_NATIVE_CLOSED')
            if p.get('rate')!={2:16000,4:48000}[mode]:fail.append('NATIVE_FORMAT_CHANGED')
            accepted=p.get('accepted_format')
            if isinstance(accepted,dict) and (accepted.get('rate')!={2:16000,4:48000}[mode] or accepted.get('channels')!=1 or accepted.get('bits')!=16):fail.append('NATIVE_ACCEPTED_FORMAT_MISMATCH')
            if number(p.get('underflow')) and p['underflow']>0:fail.append('NATIVE_UNDERFLOW')
            if number(p.get('overflow')) and p['overflow']>0:fail.append('NATIVE_OVERFLOW')
            if native_drop_evidence(p) is None:fail.append('AUDIO_DROPPED_OR_UNKNOWN')
            for counter in ('rejected_samples','cancelled_samples','cancelled_unsubmitted_samples','cancelled_submitted_unconfirmed_samples'):
                if number(p.get(counter)) and number(p0.get(counter)) and p[counter]>p0[counter]:fail.append('AUDIO_REJECTED_OR_CANCELLED_DURING_ROW')
            if mode==4 and ((live.get('status') or {}).get('mode')!='LOCKED'):fail.append('ACTUAL_BOARD_UNLOCKED')
            if p.get('ledger_balanced') is False or p.get('submitted_ledger_balanced') is False:fail.append('NATIVE_LEDGER_UNBALANCED')
            if mode==4 and p.get('remote_generation')!=p0.get('remote_generation'):fail.append('NATIVE_OWNER_CHANGED_DURING_ROW')
            if s['gain'].get('requested_gain_db')!=a['gain'].get('requested_gain_db'):fail.append('GAIN_CHANGED_DURING_ROW')
            if s['gain'].get('pending') is not False or s['gain'].get('requested_gain_db')!=s['gain'].get('applied_gain_db'):fail.append('GAIN_APPLICATION_NOT_CONFIRMED')
        completed_delta=None if not number(p0.get('completed_samples')) or not number(p1.get('completed_samples')) else p1['completed_samples']-p0['completed_samples']
        if completed_delta is None or completed_delta<=0:fail.append('NO_CURRENT_NATIVE_PROGRESS')
        elif any(number(compact(x)['native'].get('completed_samples')) and compact(x)['native']['completed_samples']<p0['completed_samples'] for x in samples):fail.append('NATIVE_COUNTER_REGRESSION')
        if not any(number((compact(x)['output_level'] or {}).get('peak')) and compact(x)['output_level']['peak']>0 for x in samples):fail.append('NO_NONZERO_OUTPUT_EVIDENCE')
        raw_known=all(number(x['raw'].get('packet_gaps')) for x in (a,z))
        raw_delta=z['raw']['packet_gaps']-a['raw']['packet_gaps'] if raw_known else None
        if raw_delta is None:warnings.append('RAW_GAP_COUNTER_UNKNOWN')
        elif raw_delta>0:warnings.append('RAW_PACKET_LOSS')
        elif raw_delta<0:warnings.append('RAW_GAP_COUNTER_RESET')
        if (z['video_complete'] or 0)<=(a['video_complete'] or 0):warnings.append('VIDEO_NETWORK_INPUT_NOT_PROVEN_CONTINUOUS')
        if (z['raw'].get('datagrams') or 0)<=(a['raw'].get('datagrams') or 0):warnings.append('RAW_INPUT_NOT_PROVEN_CONTINUOUS')
        if not display and z['presented_frames']!=a['presented_frames']:fail.append('DISPLAY_OFF_STILL_PRESENTED_FRAMES')
        conservation=all(compact(x)['native'].get('ledger_balanced') is True and compact(x)['native'].get('submitted_ledger_balanced') is True for x in samples)
        limitations=[] if conservation else ['PER_SNAPSHOT_NATIVE_CONSERVATION_NOT_IMPLEMENTED_OR_UNKNOWN']
        return dict(audio_integrity_pass=not fail,raw_integrity_pass=raw_delta==0,overall_pass=not fail and not warnings and conservation,
          native_drop_evidence=sorted({native_drop_evidence(compact(x)['native']) or 'UNKNOWN_OR_NONZERO' for x in samples}),
          failures=sorted(set(fail)),warnings=sorted(set(warnings)),limitations=limitations,
          native_completed_delta=completed_delta,raw_packet_gap_delta=raw_delta,conservation_verified=conservation,
          acoustic_quality_accepted=False,scope='BOUNDED_METADATA_CONTINUITY_CHECK_NOT_SUBJECTIVE_OR_BOARD_CERTIFICATION')
    def row(self,name,mode,display,seconds=60.):
        if not 0<seconds<=300:raise ValueError('Bounded row duration required')
        initial,ready=self.prepare(mode,display);begun=self.clock.monotonic();end=begun+seconds
        samples=[ready];last_utc=ready.get('utc');failure=None
        self.io.emit('row_begin',dict(row=name,seconds=seconds,baseline=compact(ready)))
        while self.clock.monotonic()<end:
            self.clock.sleep(min(self.poll,max(0,end-self.clock.monotonic())))
            live=self.status()
            if live.get('utc')!=last_utc:
                samples.append(live);last_utc=live.get('utc');self.io.emit('sample',dict(row=name,sample=compact(live)))
            if (live.get('audio_source') or {}).get('mode')!=mode:failure='SOURCE_CHANGED_OR_DISABLED';break
            if current_audio_errors(live,mode):failure='AUDIO_ERROR';break
            if mode==4 and (live.get('status') or {}).get('mode')!='LOCKED':failure='ACTUAL_BOARD_UNLOCKED';break
        last=samples[-1];result=self.classify(ready,last,samples,mode,display)
        result.update(row=name,mode=mode,rate_hz={2:16000,4:48000}[mode],display_enabled=display,
          requested_seconds=seconds,observed_seconds=self.clock.monotonic()-begun,source_updates=len(samples),
          setup_start=compact(initial),start=compact(ready),end=compact(last),completed_full_duration=failure is None)
        span=utc_seconds(last['utc'])-utc_seconds(ready['utc'])
        def delta(path):
            x,y=compact(ready),compact(last)
            for key in path:x=x.get(key,{}) if isinstance(x,dict) else None;y=y.get(key,{}) if isinstance(y,dict) else None
            return y-x if number(x) and number(y) else None
        result['counter_rates_per_source_wall_second']={}
        for name_,path in (('source16_samples',('source_levels','raw','samples')),('raw_datagrams',('raw','datagrams')),
            ('native_fed_samples',('native','fed_samples')),('native_submitted_samples',('native','submitted_samples')),('native_completed_samples',('native','completed_samples'))):
            value=delta(path);result['counter_rates_per_source_wall_second'][name_]=None if value is None or span<=0 else value/span
        result['source_wall_span_seconds']=span
        result['queue_observations']={}
        for name_,path in (('native_samples',('native','queued_samples')),('worker_packets',('worker','admitted_packets')),('oldest_ms',('worker','oldest_age_ms'))):
            values=[]
            for record in samples:
                value=compact(record)
                for key in path:value=value.get(key) if isinstance(value,dict) else None
                if number(value):values.append((utc_seconds(record['utc']),value))
            if values:
                mx=sum(x for x,y in values)/len(values);my=sum(y for x,y in values)/len(values);den=sum((x-mx)**2 for x,y in values)
                result['queue_observations'][name_]=dict(start=values[0][1],end=values[-1][1],maximum=max(y for x,y in values),
                    slope_per_second=None if not den else sum((x-mx)*(y-my) for x,y in values)/den)
        # No invented latency tolerance: a rising trace is retained for review,
        # rather than passing merely because the finite buffer has not overflowed.
        rising=[key for key,value in result['queue_observations'].items()
                if value['end']>value['start'] and number(value['slope_per_second']) and value['slope_per_second']>0]
        result['backlog_growth_requires_review']=rising
        if rising:result['warnings'].append('BACKLOG_POSITIVE_TREND_REQUIRES_REVIEW');result['overall_pass']=False
        setup_gaps=(compact(ready)['raw'].get('packet_gaps') or 0)-(compact(initial)['raw'].get('packet_gaps') or 0)
        result['setup_raw_packet_gap_delta']=setup_gaps
        if setup_gaps>0:result['warnings'].append('RAW_LOSS_DURING_PREPARATION');result['raw_integrity_pass']=False;result['overall_pass']=False
        if failure:result['overall_pass']=False;result['audio_integrity_pass']=False;result['failures']=sorted(set(result['failures']+[failure]))
        self.records.append(result);self.io.emit('row_end',result);return result
    def sequence_test(self,dwell=3.):
        events=[]
        for mode in (2,4,2):
            if mode==4:self.ensure_lock()
            _,live=self.prepare(mode,True);before=compact(live);self.clock.sleep(dwell);after=self.status()
            errors=current_audio_errors(after,mode)
            if errors or not self.is_ready(after,mode):raise AcceptanceError('SEQUENCE_FAILED',dict(mode=mode,errors=errors))
            checked=self.classify(live,after,[live,after],mode,True)
            if not checked['audio_integrity_pass']:raise AcceptanceError('SEQUENCE_AUDIO_INTEGRITY_FAILED',checked['failures'])
            events.append(dict(mode=mode,start=before,end=compact(after),checks=checked))
            if mode==4:
                for gain in (6.,12.):
                    self.command('set_gain',gain_db=gain);limit=self.clock.monotonic()+self.command_timeout
                    while self.clock.monotonic()<limit:
                        actual=self.status();g=(actual.get('audio_source') or {}).get('gain') or {}
                        if g.get('pending') is False and g.get('applied_gain_db')==gain:break
                        self.clock.sleep(self.poll)
                    else:raise AcceptanceError('GAIN_APPLICATION_NOT_CONFIRMED',gain)
                    events.append(dict(gain_db=gain,actual=compact(actual)))
        self.command('select_audio',mode=0);self.clock.sleep(dwell);muted=self.status()
        if (muted.get('audio_source') or {}).get('mode')!=0:raise AcceptanceError('MUTE_NOT_CONFIRMED')
        events.append(dict(mode=0,end=compact(muted)))
        return dict(status='NORMAL_SEQUENCE_OBSERVED',events=events,acoustic_quality_accepted=False)
    def recover_existing_error(self):
        live=self.status();hp=live.get('host_preview') or {}
        if not hp.get('sticky_failure') and not hp.get('error'):return dict(status='NO_EXISTING_ERROR_NO_FAULT_INJECTED')
        self.ensure_lock(live);old=hp.get('generation');_,ready=self.prepare(4,True)
        if (ready.get('host_preview') or {}).get('generation')==old:raise AcceptanceError('RETRY_GENERATION_DID_NOT_CHANGE')
        return dict(status='EXISTING_FAILURE_RESELECTED_AND_CURRENT_NATIVE_READY',before=compact(live),after=compact(ready))
    def cleanup(self):
        outcome=[]
        for name,fields in (('select_audio',dict(mode=0)),('diagnostic_display',dict(enabled=True))):
            try:self.command(name,**fields);outcome.append(dict(command=name,status='APPLIED'))
            except Exception as error:outcome.append(dict(command=name,status='NOT_CONFIRMED_NO_RETRY',error=str(error)));break
        if self.claimed:self.io.release();self.claimed=False
        return outcome

def plan():return dict(status='PLAN_ONLY_NO_IO',rows=[dict(name=n,mode=m,display_enabled=d,seconds=60) for n,m,d in ROWS],
    prerequisites=['Already-running owned PID/manifest','Old competing GUI/audio owners closed','No media recording',
                   'Real fresh board YES lock before enhanced rows; never synthesized'],
    limits='60-second bounded regression, not long-term stability, acoustic acceptance or FPGA certification')

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan-only',action='store_true');p.add_argument('--run-existing',action='store_true')
    p.add_argument('--run-dir',type=Path);p.add_argument('--pid',type=int);p.add_argument('--expected-manifest');p.add_argument('--out',type=Path)
    p.add_argument('--row',action='append',choices=[x[0] for x in ROWS]);p.add_argument('--seconds',type=float,default=60.)
    p.add_argument('--sequence-test',action='store_true');p.add_argument('--recover-existing-error',action='store_true')
    p.add_argument('--no-matrix',action='store_true',help='Only an explicitly selected sequence/recovery check; do not repeat rows')
    a=p.parse_args(argv)
    if not a.run_existing or a.plan_only:print(json.dumps(plan(),indent=2));return 0
    if not all((a.run_dir,a.pid,a.expected_manifest,a.out)):p.error('Explicit run-dir, pid, expected-manifest and new out are required')
    if a.no_matrix and not (a.sequence_test or a.recover_existing_error):p.error('no-matrix requires an explicit sequence or existing-error check')
    io=FileIO(a.run_dir,a.out);c=Controller(io,a.pid,a.expected_manifest);result=dict(scope='EXISTING_GUI_CONTROL_FILES_ONLY_NO_MEDIA',rows=[],acoustic_quality_accepted=False)
    exit_code=0
    try:
        c.acquire()
        if a.recover_existing_error:result['recovery']=c.recover_existing_error()
        for name,mode,display in ROWS:
            if a.no_matrix:break
            if a.row and name not in a.row:continue
            result['rows'].append(c.row(name,mode,display,a.seconds))
        if a.sequence_test:result['sequence']=c.sequence_test()
        result['status']='BOUNDED_CHECKS_COMPLETE'
        if not all(row['overall_pass'] for row in result['rows']):exit_code=2
    except AcceptanceError as error:result.update(status=error.code,error=error.detail);exit_code=3 if error.code=='NEEDS_REAL_USER_YES_LOCK' else 2
    except Exception as error:result.update(status='CONTROLLER_ERROR_NO_BLIND_RETRY',error=repr(error));exit_code=2
    finally:
        result['cleanup']=c.cleanup() if c.claimed else []
        result['rows']=c.records
        io.result(result)
    print(json.dumps(dict(status=result['status'],out=str(a.out),rows=[dict(row=x['row'],overall_pass=x['overall_pass'],failures=x['failures'],warnings=x['warnings'],limitations=x['limitations']) for x in c.records]),indent=2))
    return exit_code
if __name__=='__main__':raise SystemExit(main())
