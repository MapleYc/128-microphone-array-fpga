"""Run the historical source generator only inside an isolated temporary copy."""
import ast
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PROBE = r'''
from pathlib import Path
import importlib.util
import json
import socket
import sys
import types

project = Path(sys.argv[1]).resolve()
host = project / 'host'
sys.path.insert(0, str(host))

def forbidden(*args, **kwargs):
    raise AssertionError('Device or GUI construction is forbidden in this test')

socket.socket = forbidden
sys.modules['serial'] = types.SimpleNamespace(Serial=forbidden)
sys.modules['audio_playback'] = types.SimpleNamespace(WaveOut=forbidden)
import tkinter
tkinter.Tk = forbidden
tkinter.Toplevel = forbidden

path = host / 'monitor' / 'meeting_av_monitor.py'
spec = importlib.util.spec_from_file_location('generated_storage_monitor', path)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
import structured_store as store
assert Path(store.__file__).resolve() == host / 'structured_store.py'
assert monitor.Session._audio_event.__module__ == monitor.__name__

class Gate:
    def __init__(self):
        self.entries = 0
    def __enter__(self):
        self.entries += 1
        return self
    def __exit__(self, *args):
        return False

class Player:
    def __init__(self):
        self.closed = 0
    def close(self):
        self.closed += 1

class Gain:
    def __init__(self):
        self.resets = 0
    def reset(self):
        self.resets += 1

session = monitor.Session.__new__(monitor.Session)
session.play_lock = Gate()
session.audio_log_lock = Gate()
session.lock = Gate()
session.errors = {}
session.audio_router = None
session.raw_preview = None
player = Player()
session.player = player
session.audio_mode = 2
session.listen_gain = Gain()
session.listen_last = {2: (1, 3)}
output = project / 'migration_probe'
output.mkdir()
event_path = output / 'audio_events.log'
with event_path.open('w', encoding='utf-8', newline='\n') as log:
    session.audio_log = log
    assert session._audio_event(type='before_selection')
    session.listen(0)
    assert session._audio_event(type='after_selection')

assert player.closed == 1 and session.player is None
assert session.audio_mode == 0 and session.listen_gain.resets == 1
assert session.play_lock.entries == 1
assert session.audio_log_lock.entries == 3
assert not session.errors
text = event_path.read_text(encoding='utf-8')
assert text.startswith('---\n')
events = store.load_stream(text)
assert [row['type'] for row in events] == [
    'before_selection', 'listen_selection', 'after_selection']
assert events[1]['mode'] == 0 and events[1]['source'] == 'mute'
assert all(isinstance(row['host_monotonic'], (float, int)) for row in events)
try:
    json.loads(text)
except json.JSONDecodeError:
    pass
else:
    raise AssertionError('Generated event log contains disguised JSON')
assert not [p for p in project.rglob('*') if p.suffix.lower() in ('.json', '.jsonl')]
print('GENERATED_LISTEN_REAL_YAML_PASS_NO_DEVICES')
'''


class StorageMigration(unittest.TestCase):
    def test_generated_listen_uses_actual_yaml_event_writer_without_devices(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='storage_migration_') as temporary:
            project = Path(temporary) / 'Prj'
            shutil.copytree(source, project, ignore=shutil.ignore_patterns(
                '__pycache__', '*.pyc', 'runs', 'native_build'))
            generator = project / 'host' / 'integrate_monitor.py'
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            generated = subprocess.run([sys.executable, '-I', '-B', str(generator)],
                cwd=project, capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=30, creationflags=flags)
            self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
            path = project / 'host' / 'monitor' / 'meeting_av_monitor.py'
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            compile(tree, str(path), 'exec')
            probe = subprocess.run([sys.executable, '-I', '-B', '-c', PROBE, str(project)],
                cwd=project, capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=30, creationflags=flags)
            self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
            self.assertIn('GENERATED_LISTEN_REAL_YAML_PASS_NO_DEVICES', probe.stdout)


if __name__ == '__main__':
    unittest.main()
