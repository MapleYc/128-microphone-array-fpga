import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from calibration_store import save_and_readback


def validate(value):
    if value.get('verified') is not True:raise ValueError('not verified')
    return value


class CalibrationStore(unittest.TestCase):
    def test_saved_configuration_is_reloaded_and_validated_before_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'alignment.yaml';old={'verified':True,'revision':1}
            p.write_text(store.dumps(old),encoding='utf-8');new={'verified':True,'revision':2}
            loaded=save_and_readback(p,new,validate,Path(folder)/'previous.yaml')
            self.assertEqual(loaded,new);self.assertIsNot(loaded,new)
            self.assertEqual(store.loads(p.read_text(encoding='utf-8')),new)
            self.assertEqual(store.loads((Path(folder)/'previous.yaml').read_text(encoding='utf-8')),old)
    def test_corrupt_readback_does_not_publish_and_restores_previous_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'alignment.yaml';old={'verified':True,'revision':1};p.write_text(store.dumps(old),encoding='utf-8')
            active=old
            with patch('calibration_store._read_configuration',side_effect=ValueError('Invalid YAML metadata')):
                with self.assertRaises(ValueError):active=save_and_readback(p,{'verified':True,'revision':2},validate)
            self.assertIs(active,old);self.assertEqual(store.loads(p.read_text(encoding='utf-8')),old)
    def test_valid_but_different_readback_or_invalid_input_cannot_replace_active(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'alignment.yaml';old={'verified':True,'revision':1};p.write_text(store.dumps(old),encoding='utf-8')
            with patch('calibration_store._read_configuration',return_value={'verified':True,'revision':99}):
                with self.assertRaisesRegex(ValueError,'不一致'):save_and_readback(p,{'verified':True,'revision':2},validate)
            self.assertEqual(store.loads(p.read_text(encoding='utf-8')),old)
            with self.assertRaisesRegex(ValueError,'not verified'):save_and_readback(p,{'verified':False},validate)
            self.assertEqual(store.loads(p.read_text(encoding='utf-8')),old)


class YAMLReadbackTests(unittest.TestCase):
    def test_actual_yaml_parser_failure_restores_previous_disk_bytes(self):
        import calibration_store
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'alignment.yaml'
            old=b'verified: true\nrevision: 1\n';path.write_bytes(old)
            original=calibration_store._replace_bytes
            writes=[]
            def corrupt_first_write(target,payload):
                writes.append(payload)
                original(target,b'invalid: [unterminated' if len(writes)==1 else payload)
            with patch('calibration_store._replace_bytes',side_effect=corrupt_first_write):
                with self.assertRaisesRegex(ValueError,'Invalid YAML'):
                    save_and_readback(path,{'verified':True,'revision':2},validate)
            self.assertEqual(path.read_bytes(),old)
            self.assertEqual(store.load_file(path),{'verified':True,'revision':1})
            self.assertEqual(len(writes),2)


if __name__=='__main__':unittest.main()
