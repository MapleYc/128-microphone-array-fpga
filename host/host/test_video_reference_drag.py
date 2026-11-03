import sys,unittest
from types import SimpleNamespace as NS
sys.path.append(str(__import__('pathlib').Path(__file__).resolve().parent / '.'))
from video_reference_drag import VideoReferenceDrag
from video_direction_overlay import MODEL,draw_overlay
from meeting_dashboard import DirectionPanel,Dashboard
from reference_ui import ReferenceTransaction


class VideoDrag(unittest.TestCase):
    def setUp(self):
        self.cal={'model':MODEL,'verified':True,'size':[1280,720],
                  'homography':[[300,0,640],[0,300,360],[0,0,1]],
                  'domain':[[-.8,-.5],[.8,-.5],[.8,.5],[-.8,.5]]}
        self.allowed=True;self.sent=[];self.messages=[]
        self.d=VideoReferenceDrag(lambda:self.cal,lambda:((1000,600),(960,540)),
            lambda:(self.allowed,'固件不支持，未发送'),lambda *p:self.sent.append(p) or True,self.messages.append)
    def test_right_drag_only_commits_on_release_with_exact_display_geometry(self):
        self.d.begin(NS(x=500,y=300));self.d.move(NS(x=500,y=300));self.assertEqual(self.sent,[])
        self.assertTrue(self.d.release(NS(x=500,y=300)));self.assertEqual(self.sent,[(0.,-0.)])
    def test_unsupported_cancel_and_letterbox_never_send(self):
        self.allowed=False;self.d.begin(NS(x=500,y=300));self.d.release(NS(x=500,y=300));self.assertEqual(self.sent,[])
        self.allowed=True;self.d.begin(NS(x=500,y=300));self.d.cancel();self.d.release(NS(x=500,y=300))
        self.d.begin(NS(x=500,y=300));self.d.release(NS(x=10,y=300));self.assertEqual(self.sent,[])
        self.d.begin(NS(x=500,y=300));self.d.move(NS(x=10,y=300));self.d.release(NS(x=500,y=300));self.assertEqual(self.sent,[])
    def test_changed_calibration_or_capability_cancels_pending_gesture(self):
        self.d.begin(NS(x=500,y=300));self.cal=dict(self.cal);self.d.release(NS(x=500,y=300));self.assertEqual(self.sent,[])
        self.d.begin(NS(x=500,y=300));self.allowed=False;self.d.release(NS(x=500,y=300));self.assertEqual(self.sent,[])
    def test_angle_panel_keeps_confirmed_marker_until_report_and_cancels_outside(self):
        p=DirectionPanel.__new__(DirectionPanel);p.bounds=(10,10,170,90);p.reference_editable=True
        p.drag_reference=None;p.state={'current':None,'locked':None,'reference':{'azimuth':-30,'elevation':4}}
        p.chart=NS(focus_set=lambda:None);p._markers=lambda:None;p.reference_callback=lambda *v:self.sent.append(v)
        p._reference_begin(NS(x=90,y=50));self.assertEqual(self.sent,[])
        p._reference_release(NS(x=90,y=50));self.assertEqual(self.sent,[(0.,0.)])
        self.assertEqual(p.state['reference'],{'azimuth':-30,'elevation':4})
        p._reference_begin(NS(x=90,y=50));p._reference_release(NS(x=0,y=50));self.assertEqual(len(self.sent),1)
    def test_dashboard_submit_is_capability_guarded_and_waits_for_ack(self):
        d=Dashboard.__new__(Dashboard);d.reference_values=(-45,0,0);d.show_message=lambda *args:None
        d.reference_transaction=ReferenceTransaction(self.sent.append)
        d.reference_report={'version':2};d.reference_report_age=.1;d.reference_epoch=7
        self.assertFalse(d.request_reference(20,4));self.assertEqual(self.sent,[])
        d.reference_report={'version':3,'hardware_id':0x574e0100,'epoch':7,'reference_generation':2,'reference_pending':0}
        self.assertTrue(d.request_reference(20.2,3.8));self.assertEqual(len(self.sent),1)
        self.assertFalse(d.reference_transaction.confirmed);self.assertEqual(d.reference_values,(20,4,0))
    def test_applied_reference_overlay_accepts_distinct_reference_marker(self):
        from PIL import Image
        image=Image.new('RGB',(1280,720))
        draw_overlay(image,[{'kind':'reference','azimuth':20,'elevation':4}],lambda a,e:(640,360),MODEL)
        self.assertNotEqual(image.getbbox(),None)


if __name__=='__main__':unittest.main()
