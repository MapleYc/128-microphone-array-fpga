"""Independent small physical-wave tests. No network, GUI, audio or board IO.

Run from this directory with python tests_3d_independent.py.
This file deliberately generates physical propagation without DUT steering.
"""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import os,sys,json,math,time,unittest,hashlib
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'base'))
import frozen
frozen.configure()
import numpy as np
from direction3d import host,unit,angle,arc_clear,legal_pair,reference_pair
from doa3d import InterferenceDOA3D
import sprint_blocked_power as old

FS=48000;N=1024;HOP=512

def physical_unit(az,el):
    # Independently reconstructed from the firmware-coordinate convention.
    a=math.radians(az);e=math.radians(el)
    return np.array((math.sin(a)*math.cos(e),math.sin(e),math.cos(a)*math.cos(e)))

def physical_spectrum(positions,bearing,*,distance=None,seed=91):
    """Independent observed spectra: ideal far/3m spherical wave.

    Random source complex bins are shared by sensors. No clean source enters
    a runtime estimator; this is generator/evaluation only.
    """
    p=np.asarray(positions);u=physical_unit(*bearing)
    if distance is None:delay=-(p@u)/343.;amplitude=np.ones(len(p))
    else:r=np.linalg.norm(distance*u-p,axis=1);delay=(r-distance)/343.;amplitude=distance/r
    omega=2*np.pi*np.fft.rfftfreq(N,1/FS)
    rng=np.random.default_rng(seed);source=rng.normal(size=N//2+1)+1j*rng.normal(size=N//2+1)
    source[0]=source[-1]=0
    return amplitude[:,None]*np.exp(-1j*delay[:,None]*omega[None,:])*source[None,:]

def beam_power(positions,incoming,steering):
    """DUT4tap transfer evaluated on independent physical propagation."""
    p=np.asarray(positions);spec=physical_spectrum(p,incoming)
    ii,cc,_=old.geom._taps(p,host(steering),FS)
    omega=2*np.pi*np.arange(513)/N
    transfer=np.sum(cc[None,:,:]*np.exp(-1j*omega[:,None,None]*(ii[None,:,None]+np.arange(-1,3)[None,None,:])),axis=2)
    use=np.array([m for m in range(128) if m not in (2,3)])
    y=np.mean(spec[use].T*transfer[:,use],axis=1)
    bins=np.flatnonzero((np.fft.rfftfreq(N,1/FS)>=1000)&(np.fft.rfftfreq(N,1/FS)<=4000))
    return float(np.mean(abs(y[bins])**2))

class IndependentPhysical3D(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.p=old.geom.geometry()
    def test_direction_matches_independent_unit(self):
        for d in [(0,0),(28,20),(-34,-24),(80,40),(-80,-40)]:
            np.testing.assert_allclose(unit(d),physical_unit(*d),atol=2e-16,rtol=2e-16)
    def test_xy_plane_and_mask(self):
        self.assertEqual(self.p.shape,(128,3));self.assertTrue(np.all(self.p[:,2]==0));self.assertEqual(tuple(old.geom.MASK),(2,3))
    def test_nonzero_el_changes_taps(self):
        ia,ca,_=old.geom._taps(self.p,host((24,20)),FS);ib,cb,_=old.geom._taps(self.p,host((24,-20)),FS)
        self.assertFalse(np.array_equal(ia,ib) and np.array_equal(ca,cb))
    def test_known_positive_negative_elevation_beam_peak(self):
        self.peak_results=[]
        for d in [(24,24),(-28,-24),(0,28),(0,-28)]:
            gain=beam_power(self.p,d,d);other=beam_power(self.p,d,(d[0],-d[1]));wrong=beam_power(self.p,d,(-d[0],-d[1]))
            ratio=10*math.log10(gain/max(other,1e-24));self.assertGreater(ratio,3.)
            self.assertGreater(gain,wrong);self.peak_results.append(dict(direction=d,elevation_discrimination_db=ratio))
        print('PHYSICAL_BEAM_PEAKS',json.dumps(self.peak_results),flush=True)
    def test_elevation_zero_legacy_taps_exact(self):
        for az in [-60.,-20.,0.,28.,60.]:
            a=old.geom._taps(self.p,host((az,0)),FS);b=old.geom._taps(self.p,dict(azimuth_deg=-az,elevation_deg=0.),FS)
            for x,y in zip(a,b):np.testing.assert_array_equal(x,y)
    def test_reference_guards_3d(self):
        self.assertFalse(arc_clear((-30,0),(30,0),(0,0)))
        self.assertTrue(arc_clear((-30,24),(30,24),(0,0)))
        for lock,interferer in [((0,20),(40,-16)),((75,35),(-28,-24)),((-75,-35),(48,24))]:
            pair=reference_pair(interferer,lock);self.assertIsNotNone(pair);self.assertTrue(legal_pair(pair,lock))
    def test_natural_3d_doa_and_target_only(self):
        rows=[]
        for target,interferer in [((0.,20.),(40.,-20.)),((12.,-20.),(-36.,24.))]:
            tracker=InterferenceDOA3D(self.p,target);s=physical_spectrum(self.p,interferer,seed=302)[tracker.channels]
            timings=[]
            for _ in range(16):
                t=time.perf_counter();d=tracker.process(s)
                if d['search_performed']:timings.append((time.perf_counter()-t)*1000)
            self.assertTrue(d['valid'],d);self.assertLess(angle((d['azimuth_deg'],d['elevation_deg']),interferer),6.)
            self.assertEqual(d['applies_from_output_frame'],d['frame']+1)
            tonly=InterferenceDOA3D(self.p,target);t=physical_spectrum(self.p,target,seed=303)[tonly.channels]
            for _ in range(16):solo=tonly.process(t)
            self.assertFalse(solo['valid'],solo)
            rows.append(dict(target=target,source=interferer,estimated=[d['azimuth_deg'],d['elevation_deg']],dictionary_bytes=tracker.dictionary.nbytes,process_ms_p50=float(np.median(timings)),process_ms_max=max(timings),target_only_valid=solo['valid']))
        print('PHYSICAL_3D_DOA',json.dumps(rows),flush=True)

class Runtime3DIntegration(unittest.TestCase):
    def setUp(self):
        import runtime3d
        self.runtime=runtime3d;self.cfg=store.loads((HERE/'fixtures/P01.yaml').read_text(encoding='utf-8-sig'))
    def test_exact_signed24_upper16_decode(self):
        from preview import decode_words
        rng=np.random.default_rng(191);words=rng.integers(-(1<<23),1<<23,size=(256,128),dtype=np.int32)
        words[0,:8]=[-(1<<23),-(1<<23)+1,-257,-256,-1,0,255,(1<<23)-1]
        b=np.empty((*words.shape,3),dtype=np.uint8)
        for k in range(3):b[:,:,k]=(words>>(8*k))&255
        expected=((words>>8).astype(np.float32)/np.float32(32768)).T
        np.testing.assert_array_equal(decode_words(b.tobytes()),expected)
        self.assertEqual(decode_words(b'').shape,(128,0))
        with self.assertRaises(ValueError):decode_words(b'\0')
    def test_search_cadence_does_not_fabricate_evidence(self):
        from machine3d import decision_fresh
        lock=(0.,20.);q=InterferenceDOA3D(old.geom.geometry(),lock);s=physical_spectrum(old.geom.geometry(),(40.,-20.))[q.channels]
        for frame in range(16):
            d=q.process(s if frame%4==0 else None)
            self.assertEqual(d['frame'],frame);self.assertEqual(d['last_search_frame'],frame-frame%4)
            self.assertEqual(d['search_performed'],frame%4==0)
            if frame%4:
                self.assertFalse(d['evidence_valid']);self.assertFalse(decision_fresh(d,lock))
        self.assertTrue(d['valid'])
    def test_explicit_two_axis_target_uncertainty_cases(self):
        p=old.geom.geometry();lock=(0.,20.)
        for actual in [(4.,24.),(-4.,16.)]:
            q=InterferenceDOA3D(p,lock);s=physical_spectrum(p,actual)[q.channels]
            for frame in range(16):d=q.process(s if frame%4==0 else None)
            self.assertFalse(d['valid'],(actual,d))
    def test_zero_el_calibration_legacy_arrays(self):
        from calibrate_reuse import calibrate as legacy
        refs=[(40.,0.),(60.,0.)];new=self.runtime.calibrate(refs,self.cfg,None);old_model=legacy([40.,60.],self.cfg,None)
        for k in ['rT','scale','valid','noise_gain','pI_model','zI_model']:
            np.testing.assert_allclose(new[k],old_model[k],rtol=2e-13,atol=2e-13,equal_nan=True)
    def test_nonzero_el_calibration_finite_and_distinct(self):
        self.cfg['directions']['A']=host((12.,20.));a=self.runtime.calibrate([(44.,-20.),(64.,-20.)],self.cfg,None)
        self.cfg['directions']['A']=host((12.,-20.));b=self.runtime.calibrate([(44.,-20.),(64.,-20.)],self.cfg,None)
        self.assertTrue(np.isfinite(a['rT']).all());self.assertTrue(np.isfinite(a['scale']).all());self.assertTrue(a['valid'].any());self.assertFalse(np.array_equal(a['rT'],b['rT']))
    def test_zero_el_first_complete_nonzero_frame_exact(self):
        from snapshot_spatial_stream import AtomicSnapshotSpatialStream as Legacy
        p=old.geom.geometry();self.cfg['directions']['A']=host((24.,0.))
        spec=physical_spectrum(p,(24.,0.),seed=511);raw=(.02*np.fft.irfft(spec,n=N,axis=1)).astype(np.float32)
        new=self.runtime.AtomicSnapshotSpatialStream(self.cfg);prior=Legacy(self.cfg)
        try:
            a=list(new.push_iter(raw));b=list(prior.push_iter(raw))
            self.assertEqual(len(a),1);self.assertEqual(len(b),1);self.assertGreater(np.count_nonzero(a[0]['samples']),400)
            np.testing.assert_array_equal(a[0]['samples'],b[0]['samples'])
        finally:
            for s in [new,prior]:s.shutdown();self.assertTrue(s.wait_shutdown(3))
    def test_arbitrary_future_suffix_not_consumed(self):
        p=old.geom.geometry();self.cfg['directions']['A']=host((12.,20.));spec=physical_spectrum(p,(12.,20.),seed=542)
        prefix=(.02*np.fft.irfft(spec,n=N,axis=1)).astype(np.float32)
        a=np.concatenate((prefix,np.full((128,1024),np.nan,dtype=np.float32)),axis=1)
        b=np.concatenate((prefix,np.full((128,1024),.4,dtype=np.float32)),axis=1)
        streams=[self.runtime.AtomicSnapshotSpatialStream(self.cfg) for _ in range(2)]
        try:
            results=[]
            for s,x in zip(streams,[a,b]):
                it=s.push_iter(x);event=next(it);self.assertEqual(it.consumed_samples,1024);self.assertEqual(s.input_consumed_total_samples,1024)
                self.assertEqual(event['input_window_end_exclusive'],1024);self.assertEqual(event['next_doa_decision']['applies_from_output_frame'],1)
                results.append(event['samples']);it.close()
            np.testing.assert_array_equal(*results)
        finally:
            for s in streams:s.shutdown();self.assertTrue(s.wait_shutdown(3))
    def test_payload_taps_model_direction_version_atomic(self):
        from worker3d import build_payload,version_for
        from machine3d import Job,SnapshotMachine
        p=old.geom.geometry();lock=(12.,20.);refs=((44.,-20.),(64.,-20.));self.cfg['directions']['A']=host(lock);version=version_for(self.cfg)
        job=Job(1,0,version,lock,refs,0,1,0,1);payload=build_payload(job,self.cfg,p)
        newer=build_payload(Job(2,0,version,lock,refs,1,2,1,2),self.cfg,p)
        self.assertIs(payload.model,newer.model);self.assertIs(payload.taps,newer.taps)
        self.assertNotEqual(payload.seq,newer.seq)
        self.assertEqual(payload.lock,lock);self.assertEqual(payload.pair,refs);self.assertEqual(payload.version,version)
        for physical,(use,delay,coeff) in zip(refs,payload.taps):
            ii,cc,_=old.geom._taps(p,host(physical),FS);np.testing.assert_array_equal(delay,ii[use]);np.testing.assert_array_equal(coeff,cc[use]);self.assertFalse(delay.flags.writeable);self.assertFalse(coeff.flags.writeable)
        machine=SnapshotMachine(lock,version,self.runtime._materialize)
        # Deterministic scheduling API; times are explicit simulation nanoseconds.
        machine.advance(1,10,refs,0,1,0,1);pending=machine.peek_pending();machine.mark_started(pending)
        actual_payload=build_payload(pending,self.cfg,p)
        state=machine.advance(2,20,refs,1,10,1,10,[(pending,actual_payload,None,15)])
        self.assertIsNotNone(state['active']);self.assertEqual(state['active'].payload.pair,refs)
        newcfg=json.loads(json.dumps(self.cfg));newlock=(-20.,-24.);newcfg['directions']['A']=host(newlock);newversion=version_for(newcfg)
        self.assertNotEqual(version,newversion)
        machine.invalidate('new_target',new_lock=newlock,new_version=newversion,reset_frame=True)
        self.assertIsNone(machine.active);self.assertEqual(machine.lock,newlock)
        # An old-target completion cannot re-activate old taps or model.
        changedrefs=((-55.,20.),(-75.,20.))
        state=machine.advance(1,30,changedrefs,0,21,0,21,[(pending,actual_payload,None,25)])
        self.assertIsNone(state['active'])
        events=machine.drain_events();self.assertTrue(any(x['event']=='rejected' and x['reason']=='unknown_completion' for x in events))

if __name__=='__main__':unittest.main(verbosity=2)
