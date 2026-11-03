"""Fake widgets/variables only: no Tk root, devices, network, audio or media."""
import math,sys,unittest
# Same explicit legacy UI-support location used by launcher.load_monitor;
# imports only, never instantiate Session, Tk root, network or native playback.
sys.path.append(str(__import__('pathlib').Path(__file__).resolve().parent / '.'))
from meeting_dashboard import Dashboard
from ui_audio_controls import gain_control_state

class Widget:
    def __init__(self):self.options={}
    def configure(self,**kw):self.options.update(kw)

class Variable:
    def __init__(self,value,callback):self.value=value;self.callback=callback
    def get(self):return self.value
    def set(self,value):self.value=value;self.callback(value)

def dashboard():
    d=Dashboard.__new__(Dashboard);calls=[]
    d.callbacks={'gain':calls.append};d.scale=Widget();d.gain_caption=Widget()
    d.controls={'gain_'+str(db):Widget() for db in (0,12,24,36,48)}
    d._last_gain=12.;d._syncing_gain=False
    d._gain_control=gain_control_state(dict(mode=0,gain=dict(gain_db=12)))
    d.gain_value=Variable(12.,d._gain_changed)
    return d,calls

def pc(requested=12,applied=None,**extra):
    return dict(mode=4,gain=dict(gain_db=requested,requested_gain_db=requested,
        applied_gain_db=applied,min_db=0,max_db=18,controllable=True,**extra))

class GainUI(unittest.TestCase):
    def test_pcm_gain_available_before_device_preparation(self):
        d,calls=dashboard();d._update_gain_controls(pc())
        self.assertEqual(d.scale.options,dict(from_=0.,to=18.,state='normal'))
        self.assertEqual(d.controls['gain_0'].options['state'],'normal')
        self.assertEqual(d.controls['gain_12'].options['state'],'normal')
        for db in (24,36,48):self.assertEqual(d.controls['gain_'+str(db)].options['state'],'disabled')
        self.assertIn('待应用',d.gain_caption.options['text']);self.assertEqual(calls,[])

    def test_preset_and_variable_trace_dispatch_once(self):
        d,calls=dashboard();d._update_gain_controls(pc(0,0))
        self.assertTrue(d.set_gain(12));self.assertEqual(calls,[12.])
        self.assertFalse(d.set_gain(12));self.assertEqual(calls,[12.])
        d.gain_value.set(18);self.assertEqual(calls,[12.,18.])

    def test_mode_switch_reflects_independent_values_without_callback(self):
        d,calls=dashboard();d._update_gain_controls(dict(mode=2,gain=dict(gain_db=36)))
        self.assertEqual(d.gain_value.get(),36.)
        self.assertEqual(d.scale.options['to'],48.)
        d._update_gain_controls(pc(6,6))
        self.assertEqual(d.gain_value.get(),6.)
        d._update_gain_controls(dict(mode=2,gain=dict(gain_db=36)))
        self.assertEqual(d.gain_value.get(),36.);self.assertEqual(calls,[])

    def test_known_applied_gain_does_not_claim_pending(self):
        d,calls=dashboard();d._update_gain_controls(pc(12,12))
        text=d.gain_caption.options['text']
        self.assertIn('监听增益',text);self.assertNotIn('待应用',text)
        self.assertNotIn('抑制',text);self.assertNotIn('PC',text);self.assertEqual(calls,[])

    def test_applied_mismatch_and_explicit_pending_remain_visible(self):
        self.assertTrue(gain_control_state(pc(12,6))['pending'])
        self.assertTrue(gain_control_state(pc(12,12,pending=True))['pending'])
        self.assertFalse(gain_control_state(pc(12,12))['pending'])

    def test_unsupported_pcm_gain_does_not_become_label_only_control(self):
        d,calls=dashboard();d._update_gain_controls(dict(mode=4,gain=dict(gain_db=0)))
        self.assertEqual(d.scale.options['state'],'disabled')
        self.assertFalse(d.set_gain(12));self.assertEqual(calls,[])
        self.assertIn('不可用',d.gain_caption.options['text'])

    def test_bad_metadata_or_out_of_range_cannot_dispatch(self):
        d,calls=dashboard();d._update_gain_controls(pc())
        for value in (24,48,-1,float('nan'),float('inf'),'bad'):
            self.assertFalse(d.set_gain(value))
        self.assertEqual(calls,[])
        state=gain_control_state(dict(mode=4,gain=dict(gain_db=12,max_db=48,controllable=True)))
        self.assertFalse(state['enabled'])

    def test_legacy_presets_keep_the_existing_range(self):
        d,calls=dashboard();d._update_gain_controls(dict(mode=2,gain=dict(gain_db=12)))
        self.assertEqual(d.scale.options['to'],48.)
        for db in (0,12,24,36,48):self.assertEqual(d.controls['gain_'+str(db)].options['state'],'normal')
        self.assertTrue(d.set_gain(48));self.assertEqual(calls,[48.])

if __name__=='__main__':unittest.main()
