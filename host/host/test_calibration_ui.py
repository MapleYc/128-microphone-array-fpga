import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import json,math,tempfile,unittest
from pathlib import Path
import sys
sys.path.append(str(__import__('pathlib').Path(__file__).resolve().parent / '.'))
from types import SimpleNamespace as NS
from unittest.mock import patch
from PIL import Image
from calibration_capture import capture_metadata
from video_alignment_ui import AlignmentDialog
from video_direction_overlay import fit_alignment,validate_alignment,project_quick,pixel_from_widget


class Value:
    def set(self,value):self.value=value
    def configure(self,**kw):self.kw=kw


class Calibration(unittest.TestCase):
    def frame(self):return NS(width=1280,height=720,received_at=10,source_frame=10000,frame_id=8)
    def status(self,**kw):return dict(dict(mode='SEARCH',epoch=7,source_frame=10000,azimuth=12,elevation=-4,direction_quality=.8,direction_age_ms=0),**kw)
    def test_search_measurement_is_supported_without_lock(self):
        s=self.status();s.update(locked_azimuth=60,locked_elevation=30)
        result=capture_metadata(self.frame(),[s],7,10.1,10)
        self.assertEqual((result['azimuth'],result['elevation']),(12,-4))
        self.assertEqual(result['measurement_mode'],'SEARCH')
    def test_nearest_report_does_not_hide_better_actual_doa_time(self):
        stale=self.status(direction_age_ms=200)
        better=self.status(source_frame=10096,direction_age_ms=2,azimuth=13)
        result=capture_metadata(self.frame(),[stale,better],7,10.1,10)
        self.assertEqual(result['azimuth'],13);self.assertEqual(result['time_match_samples'],0)
    def test_stale_epoch_quality_and_stale_measurements_rejected(self):
        for s in (self.status(epoch=6),self.status(direction_quality=.4),self.status(direction_age_ms=201)):
            with self.subTest(s=s),self.assertRaises(ValueError):capture_metadata(self.frame(),[s],7,10.1,10)
        with self.assertRaisesRegex(ValueError,'视频帧已过期'):capture_metadata(self.frame(),[self.status()],7,10.4,10.4)
        with self.assertRaisesRegex(ValueError,'方向状态已过期'):capture_metadata(self.frame(),[self.status()],7,10.1,9)
        with self.assertRaisesRegex(ValueError,'10000ms.*持续发声'):
            capture_metadata(self.frame(),[self.status(direction_age_ms=10000)],7,10.1,10)
        with self.assertRaisesRegex(ValueError,'方向质量不足.*40%'):
            capture_metadata(self.frame(),[self.status(direction_quality=.4)],7,10.1,10)
    def test_thumbnail_click_and_letterbox(self):
        self.assertEqual(pixel_from_widget(500,300,(1000,600),(960,540)),(640,360))
        self.assertIsNone(pixel_from_widget(10,300,(1000,600),(960,540)))
    def test_failed_capture_cannot_leave_clickable_old_frame(self):
        d=AlignmentDialog.__new__(AlignmentDialog);d.points=[];d.frozen=('old','old')
        d.message=Value();d.capture=lambda:(_ for _ in ()).throw(ValueError('not fresh'))
        d.snapshot();self.assertIsNone(d.frozen)
    def test_config_only_click_never_writes_image(self):
        d=AlignmentDialog.__new__(AlignmentDialog)
        d.points=[];d.image_digests=[];d.save_images=False;d.image_size=(960,540)
        d.picture=NS(winfo_width=lambda:1000,winfo_height=lambda:600)
        d.message=Value();d.enable=Value();d.candidate=None
        image=Image.new('RGB',(1280,720));d.frozen=(image,self.status())
        d.frozen[1]['doa_source_frame']=10000
        with tempfile.TemporaryDirectory() as folder:
            d.folder=Path(folder)
            with patch.object(Image.Image,'save',side_effect=AssertionError('media save forbidden')):
                d.click(NS(x=500,y=300))
            self.assertEqual([p.name for p in d.folder.iterdir()],['points.yaml'])
            point=store.loads((d.folder/'points.yaml').read_text(encoding='utf-8'))[0]
            self.assertNotIn('image',point);self.assertEqual(point['evidence_storage'],'volatile_rgb_not_saved')
            self.assertEqual((point['pixel_x'],point['pixel_y']),(640,360))
    def test_six_train_four_validation_still_required(self):
        coords=[(-30,-20),(0,-20),(30,-20),(-30,20),(0,20),(30,20),(-15,-10),(15,-10),(-15,10),(15,10)]
        points=[]
        for i,(a,e) in enumerate(coords):
            x=math.tan(math.radians(a));y=-math.tan(math.radians(e))/math.cos(math.radians(a))
            points.append(dict(azimuth=a,elevation=e,pixel_x=640+300*x,pixel_y=360+300*y,
                               split='train' if i<6 else 'validation',epoch=7,doa_source_frame=i,direction_quality=.8))
        c=fit_alignment(points);self.assertFalse(c['verified'])
        with self.assertRaises(ValueError):validate_alignment(c)
        c.update(verified=True,physical_confirmation={'method':'unit test synthetic correspondences'})
        validate_alignment(c)
        u,v=project_quick(c,0,0,1280,720);self.assertAlmostEqual(u,640);self.assertAlmostEqual(v,360)
        self.assertIsNone(project_quick(c,70,0,1280,720))
        with self.assertRaises(ValueError):fit_alignment(points[:6])


if __name__=='__main__':unittest.main()
