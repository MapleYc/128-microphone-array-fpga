"""Explicit hybrid HOST mode. No devices are opened by --preflight/help."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import argparse,hashlib,importlib.util,json,sys
from pathlib import Path
from board_follow import BoardLockAdapter
from legacy_adapter import RawPreviewAdapter,load_base

HERE=Path(__file__).resolve().parent
def load_monitor():
    p=HERE/'monitor/meeting_av_monitor.py'
    hashes=store.loads((HERE/'MONITOR_HASHES.yaml').read_text(encoding='utf-8'))
    for name,h in hashes.items():
        if hashlib.sha256((HERE/name).read_bytes()).hexdigest()!=h:raise RuntimeError('bridge source changed: '+name)
    sys.path.insert(0,str(p.parent));sys.path.append(str(HERE/'vendor'))
    spec=importlib.util.spec_from_file_location('hybrid_board_lock_monitor',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def main():
    ap=argparse.ArgumentParser(add_help=False)
    ap.add_argument('--host-preview',action='store_true');ap.add_argument('--preview-config',type=Path)
    ap.add_argument('--dsp-process',action='store_true',help='Optional isolated full DSP/native worker; thread baseline remains default')
    ap.add_argument('--preview-lock-source',choices=['board','manual'],default='board')
    ap.add_argument('--preview-lock-deg',type=float);ap.add_argument('--enable-playback',action='store_true')
    ap.add_argument('--preview-monitor-gain',type=float,default=1.)
    ap.add_argument('--preview-inbox-packets',type=int,choices=(512,1024),default=512,
        help='Explicit bounded transport queue; 512=32ms, 1024=64ms at48k/3groups perpacket')
    ap.add_argument('--preflight',action='store_true');ap.add_argument('--preview-help',action='store_true')
    a,rest=ap.parse_known_args()
    if a.dsp_process and a.preview_inbox_packets!=1024:raise ValueError('--dsp-process requires explicit --preview-inbox-packets1024 (64ms global packet credits)')
    if a.preview_help:
        ap.print_help();print('Other args go to existing UART/video monitor. No device opened.');return
    if a.enable_playback and not a.host_preview:raise ValueError('playback requires --host-preview')
    if a.host_preview and a.preview_config is None:raise ValueError('explicit --preview-config required')
    if a.preview_lock_source=='board' and a.preview_lock_deg is not None:raise ValueError('board mode cannot override target angle from CLI')
    if a.preview_lock_source=='manual' and a.host_preview and a.preview_lock_deg is None:raise ValueError('manual mode requires explicit angle')
    m=load_monitor()
    if a.preflight:
        frozen,_,v=load_base();print(json.dumps(dict(status='PREFLIGHT_PASS_NO_DEVICE_OPENED',versions=v,lock_source=a.preview_lock_source,
            scope='HOST 3D azimuth/elevation software candidate, measured geometry, front-hemisphere assumption; not full FPGA enhancement',
            inbox_capacity_packets=a.preview_inbox_packets,inbox_audio_capacity_ms=a.preview_inbox_packets*3/48,
            execution_mode='process' if a.dsp_process else 'thread',
            network_audio_or_board_opened=False),indent=2));return
    adapter=None
    try:
        if a.host_preview:
            cfg=store.loads(a.preview_config.read_text(encoding='utf-8'))
            if a.preview_lock_source=='board':
                from audio_source_router import AudioSourceRouter
                router=AudioSourceRouter(monitor_gain=a.preview_monitor_gain) if a.enable_playback else None
                adapter=BoardLockAdapter(cfg,enable_playback=a.enable_playback,monitor_gain=a.preview_monitor_gain,capacity=a.preview_inbox_packets,playback_router=router,dsp_process=a.dsp_process)
                print('HOST DSP: actual UART LOCKED azimuth and elevation enter 3D beam/model/tracker. SEARCH/NO/stale/gap stops enhancement. Front-hemisphere software candidate; not full FPGA DSP.',flush=True)
            else:
                from audio_source_router import AudioSourceRouter
                router=AudioSourceRouter(monitor_gain=a.preview_monitor_gain) if a.enable_playback else None
                if a.dsp_process:
                    from process_adapter import ProcessRawAdapter
                    factory=ProcessRawAdapter
                else:factory=RawPreviewAdapter
                adapter=factory(cfg,lock_deg=a.preview_lock_deg,enable_playback=a.enable_playback,monitor_gain=a.preview_monitor_gain,capacity=a.preview_inbox_packets,playback_router=router)
                print('PC HOST DSP: MANUAL FIXED HORIZONTAL TARGET; does not follow board yes/no.',flush=True)
            m.RAW_PREVIEW_ADAPTER=adapter
        sys.argv=[str(HERE/'monitor/meeting_av_monitor.py'),*rest];m.main()
    finally:
        if adapter is not None:adapter.close();adapter.wait_closed(20)

if __name__=='__main__':main()
