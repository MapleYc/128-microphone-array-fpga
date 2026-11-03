"""Physical +X right/+Y up/+Z front directions; no device access."""
import math
from functools import lru_cache
import numpy as np

def direction(value):
    if isinstance(value,(float,int,np.floating)): value=(float(value),0.)
    a,e=map(float,value)
    if not math.isfinite(a+e) or abs(a)>85. or abs(e)>44.:raise ValueError('3D bearing outside supported front sector')
    return (a,e)

def unit(value):
    return _unit(direction(value))

@lru_cache(maxsize=4096)
def _unit(value):
    a,e=np.deg2rad(value)
    u=np.array([np.sin(a)*np.cos(e),np.sin(e),np.cos(a)*np.cos(e)])
    u.setflags(write=False)
    return u

def from_unit(u):
    u=np.asarray(u,dtype=float);u=u/np.linalg.norm(u)
    return (float(np.rad2deg(np.arctan2(u[0],u[2]))),float(np.rad2deg(np.arcsin(np.clip(u[1],-1,1)))))

def angle(a,b):
    return _angle(direction(a),direction(b))

@lru_cache(maxsize=16384)
def _angle(a,b):
    return float(np.rad2deg(np.arccos(np.clip(np.dot(_unit(a),_unit(b)),-1,1))))

def host(value):
    a,e=direction(value)
    # HOST delay=dref-p.u_host, physical receive delay=dref+p.u_phys.
    # All actual geometry z=0. Both xy signs must reverse.
    return dict(azimuth_deg=-a,elevation_deg=-e)

def from_host(d):return direction((-float(d['azimuth_deg']),-float(d['elevation_deg'])))

def arc_clear(a,b,lock,exclusion=10.):
    return _arc_clear(direction(a),direction(b),direction(lock),float(exclusion))

@lru_cache(maxsize=4096)
def _arc_clear(a,b,lock,exclusion):
    # Exact minimum dot with lock along a shortest great-circle arc.
    ua,ub,ut=unit(a),unit(b),unit(lock);theta=math.acos(np.clip(np.dot(ua,ub),-1,1))
    if theta<1e-12:return angle(a,lock)>exclusion+1e-9
    if theta>=math.pi-1e-10:return False
    v=(ub-ua*math.cos(theta))/math.sin(theta);A=float(ut@ua);B=float(ut@v)
    ts=[0.,theta];t=math.atan2(B,A)
    if 0<t<theta:ts.append(t)
    return max(A*math.cos(x)+B*math.sin(x) for x in ts)<math.cos(math.radians(exclusion))-1e-12

def legal_pair(pair,lock):
    if pair is None or len(pair)!=2:return False
    try:a,b=map(direction,pair);lock=direction(lock)
    except (ValueError,TypeError,OverflowError):return False
    return angle(a,lock)>10.+1e-9 and angle(b,lock)>10.+1e-9 and angle(a,b)>1e-6 and arc_clear(a,b,lock)

def reference_pair(interferer,lock):
    a=direction(interferer);lock=direction(lock)
    if angle(a,lock)<=10.+1e-9:return None
    # Preserve horizontal legacy B2 when legal, then try a great-circle step
    # away from the target. Every endpoint/arc is independently checked.
    side=1. if a[0]>=lock[0] else -1.
    trials=[(a[0]+side*20.,a[1]),(a[0]-side*20.,a[1]),(side*85.,a[1])]
    ua,ut=unit(a),unit(lock);away=ua-ut*(ua@ut)
    if np.linalg.norm(away)>1e-12:
        tangent=(ua*(ua@ut)-ut);tangent/=np.linalg.norm(tangent)
        trials.append(from_unit(ua*math.cos(math.radians(20))+tangent*math.sin(math.radians(20))))
    for b in trials:
        if legal_pair((a,b),lock):return (a,direction(b))
    return None
