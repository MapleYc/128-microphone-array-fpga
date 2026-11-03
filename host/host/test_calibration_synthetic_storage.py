"""Regression from synthetic angle/pixel observations; no image or device access."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from calibration_store import save_and_readback
from video_direction_overlay import fit_alignment,validate_alignment

FIXTURE=Path(__file__).parent/'fixtures'/'synthetic_camera_alignment'


def synthetic_calibration():
    points=store.loads((FIXTURE/'points.yaml').read_text(encoding='utf-8'))
    cal=fit_alignment(points)
    cal.update(verified=True,physical_confirmation={'method':'synthetic test fixture only; not deployment approval'})
    return cal


class SyntheticCalibrationStorage(unittest.TestCase):
    def test_synthetic_ten_point_fit_reproduces_old_raw_comparison_false_negative(self):
        value=synthetic_calibration();stored=json.loads(json.dumps(value,allow_nan=False))
        self.assertIsInstance(value['domain'][0],tuple)
        self.assertIsInstance(stored['domain'][0],list)
        self.assertNotEqual(stored,value)
        self.assertEqual({key for key in value if value[key]!=stored[key]},{'domain'})
        validate_alignment(stored)
        self.assertLess(value['train_rms_pixels'],1e-6)
        self.assertLess(value['validation_p90_pixels'],1e-6)
        self.assertLess(value['validation_max_pixels'],1e-6)

    def test_synthetic_fit_saves_reads_validates_and_keeps_all_json_content(self):
        value=synthetic_calibration()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'recovered_alignment.yaml'
            loaded=save_and_readback(path,value,validate_alignment)
            validate_alignment(loaded)
            self.assertEqual(loaded,json.loads(json.dumps(value,allow_nan=False)))
            self.assertEqual(loaded,store.loads(path.read_text(encoding='utf-8')))

    def test_actual_metadata_change_still_rejects_and_restores_previous_bytes(self):
        value=synthetic_calibration();changed=json.loads(json.dumps(value))
        changed['physical_confirmation']['method']='different persisted metadata'
        validate_alignment(changed)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'alignment.yaml';old=b'old_configuration_preserved: true\n';path.write_bytes(old)
            with patch('calibration_store._read_configuration',return_value=changed):
                with self.assertRaisesRegex(ValueError,'不一致'):save_and_readback(path,value,validate_alignment)
            self.assertEqual(path.read_bytes(),old)
    def test_json_boolean_to_number_change_is_not_mistaken_for_equal_python_values(self):
        value=json.loads(json.dumps(synthetic_calibration()));value['physical_confirmation']['flag']=True
        changed=json.loads(json.dumps(value));changed['physical_confirmation']['flag']=1
        self.assertEqual(changed,value)  # Python's bool/int equality must not hide this JSON change.
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'alignment.yaml';old=b'previous: true\n';path.write_bytes(old)
            with patch('calibration_store._read_configuration',return_value=changed):
                with self.assertRaisesRegex(ValueError,'不一致'):save_and_readback(path,value,validate_alignment)
            self.assertEqual(path.read_bytes(),old)


if __name__=='__main__':unittest.main()
