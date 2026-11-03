"""Causal B2 selection for the independent sector-guard prototype.

Inputs are the configured physical target lock and previously decided physical
interferer azimuth. No observed samples, source labels, or future state are used.
This does not modify the frozen baseline or its Wiener/calibration parameters.
"""
import math
from sprint_doa_sector_guard import wrapped_delta, outside_target, clear_short_arc

EXCLUSION_DEG=10.0
VIEW_EDGE_DEG=85.0
LEGACY_OFFSET_DEG=20.0
MIN_SEPARATION_DEG=1e-6

def _wrap(a):
    a=float(a)
    # Preserve legacy in-range floating-point inputs exactly.
    return a if -180.0 <= a < 180.0 else (a+180.0)%360.0-180.0

def select_reference_pair_info(physical, lock):
    """Return {'refs': [B1,B2] or None, 'reason': ...} in physical degrees.

    Retain legacy B2 exactly if it remains in view, outside the exclusion,
    distinct from B1, and its short arc to B1 is outside the exclusion.
    Otherwise prefer the outer view edge on the same target side, then a
    midpoint toward the exclusion. No legal pair means fallback to A.
    This function selects endpoints, not a waveform transition: the caller
    must ensure safe transition and retain the true two-reference calibration.
    """
    try: p,l=float(physical),float(lock)
    except (TypeError,ValueError): return {'refs':None,'reason':'invalid_input'}
    if not math.isfinite(p) or not math.isfinite(l): return {'refs':None,'reason':'invalid_input'}
    p,l=_wrap(p),_wrap(l)
    if abs(l)>90 or abs(p)>VIEW_EDGE_DEG or not outside_target(p,l,EXCLUSION_DEG):
        return {'refs':None,'reason':'invalid_B1_or_lock'}
    def legal(q):
        return (math.isfinite(q) and abs(q)<=VIEW_EDGE_DEG and
                abs(wrapped_delta(q,p))>MIN_SEPARATION_DEG and
                outside_target(q,l,EXCLUSION_DEG) and
                clear_short_arc(p,q,l,EXCLUSION_DEG))
    side=1.0 if wrapped_delta(p,l)>0 else -1.0
    q=p+side*LEGACY_OFFSET_DEG
    if abs(q)>VIEW_EDGE_DEG: q=p-side*LEGACY_OFFSET_DEG
    if legal(q): return {'refs':[p,q],'reason':'legacy_unchanged'}
    edge=side*VIEW_EDGE_DEG
    if legal(edge): return {'refs':[p,edge],'reason':'same_side_view_edge'}
    # On the edge, moving halfway toward the exclusion boundary leaves both
    # strict margins positive when any same-side interval is available.
    boundary=l+side*EXCLUSION_DEG
    midpoint=(p+boundary)/2.0
    if legal(midpoint): return {'refs':[p,midpoint],'reason':'same_side_midpoint'}
    return {'refs':None,'reason':'no_legal_distinct_B2'}

def select_reference_pair(physical, lock):
    """Return pair for calibrate(refs, ...) or None to select unity/A."""
    return select_reference_pair_info(physical,lock)['refs']
