"""No-device bounded profile: exact 3-group packet dispatch and full post IPC."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
from smoke_3d_paced import *
import cProfile,pstats,io
from preview import Pipeline

if __name__=='__main__':
    pcm=numerical_source_pcm();cfg=store.loads((HERE/'fixtures/P01.yaml').read_text(encoding='utf-8-sig'))
    p=Pipeline(cfg,lock_deg=-8.,lock_elevation_deg=-14.)
    profile=cProfile.Profile();tic=time.perf_counter()
    try:
        profile.enable()
        for s in range(16000):p.consume_packet(packet(s,pcm[s]))
        p.finish();profile.disable()
        print('TOTAL_SECONDS',time.perf_counter()-tic,'AUDIO_SECONDS',1.,'SPATIAL',p.counts['spatial_frames'])
        out=io.StringIO();pstats.Stats(profile,stream=out).strip_dirs().sort_stats('cumtime').print_stats(22);print(out.getvalue())
    finally:p.close()
