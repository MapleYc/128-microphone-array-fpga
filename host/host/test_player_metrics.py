"""Real snapshot bodies, allocated with __new__: no audio constructors/devices."""
import collections
import importlib.util
from pathlib import Path
import sys
import threading
import unittest

sys.path.append(str(__import__('pathlib').Path(__file__).resolve().parent / '.'))
legacy_spec=importlib.util.spec_from_file_location('metrics_historical_waveout16',Path(__file__).parent/'regression_baselines/legacy_audio16/audio_playback.py')
legacy_player=importlib.util.module_from_spec(legacy_spec);legacy_spec.loader.exec_module(legacy_player)
WaveOut=legacy_player.WaveOut # Historical schema remains supported; new strict16 is tested separately.
from player_metrics import normalize_player

spec = importlib.util.spec_from_file_location('pure_metrics_playback48', Path(__file__).parent/'base'/'playback48.py')
playback48 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(playback48)


def legacy_snapshot(**changes):
    p = WaveOut.__new__(WaveOut)
    p.lock = threading.Lock(); p.data = bytearray(8)
    p.fed_samples = 8; p.submitted_samples = 4; p.completed_samples = 0
    p.dropped = 0; p.error = None; p.last_completed = None
    p.__dict__.update(changes)
    return p.snapshot()


def wave48_snapshot(**changes):
    p = playback48.WaveOut48.__new__(playback48.WaveOut48)
    p.lock = threading.Lock(); p.counts = collections.Counter()
    p.q = collections.deque(); p.busy = [False]*4; p.error = None; p.gain = 1.0
    p.__dict__.update(changes)
    return p.snapshot()


class PlayerMetrics(unittest.TestCase):
    def test_strict16_policy_and_cancellation_fields_are_not_relabelled_as_lossy(self):
        native=dict(fed_samples=2048,submitted_samples=1024,completed_samples=512,
            queued_samples=1024,inflight_samples=512,dropped_samples=0,error=None,completion_age_seconds=.01,
            strict_no_drop=True,drop_policy='STRICT_FAIL_STOP_NO_DISCARD',cancelled_samples=0,ledger_balanced=True)
        p=normalize_player(native,rate_hint=16000)
        self.assertEqual(p['drop_policy'],'STRICT_FAIL_STOP_NO_DISCARD');self.assertEqual(p['dropped_samples'],0)
        self.assertTrue(p['ledger_balanced']);self.assertEqual(p['cancelled_samples'],0)
    def test_no_player_and_unknown_schema_do_not_invent_counters(self):
        self.assertIsNone(normalize_player(None))
        p = normalize_player({'error': None}, rate_hint=16000, observed_completion_age=0)
        self.assertEqual(p['backend_schema'], 'unknown')
        for key in ('fed_samples', 'submitted_samples', 'completed_samples', 'dropped_samples',
                    'queued_samples', 'completion_age_seconds', 'completion_recent'):
            self.assertIsNone(p[key], key)
        self.assertEqual(p['rate'], 16000)

    def test_actual_legacy_initial_schema_and_backend_copy(self):
        raw = legacy_snapshot()
        p = normalize_player(raw, rate_hint=16000)
        self.assertEqual(p['backend_schema'], 'waveout16')
        self.assertEqual((p['fed_samples'], p['submitted_samples'], p['completed_samples']), (8, 4, 0))
        self.assertEqual(p['queued_samples'], 4); self.assertEqual(p['dropped_samples'], 0)
        self.assertIsNone(p['completion_age_seconds']); self.assertIsNone(p['inflight_samples'])
        self.assertEqual(p['backend_snapshot'], raw); self.assertIsNot(p['backend_snapshot'], raw)

    def test_actual_wave48_sparse_counter_means_zero_but_drop_is_unknown(self):
        raw = wave48_snapshot()
        self.assertNotIn('completed_samples', raw)
        p = normalize_player(raw, observed_completion_age=0)
        self.assertEqual(p['backend_schema'], 'waveout48')
        for key in ('fed_samples', 'submitted_samples', 'completed_samples', 'clip_samples', 'overflow', 'underflow'):
            self.assertEqual(p[key], 0)
        self.assertEqual(p['rate'], 48000); self.assertIsNone(p['dropped_samples'])
        self.assertFalse(p['dropped_samples_known']); self.assertIn('no-silent-drop', p['drop_policy'])
        self.assertIsNone(p['completion_age_seconds']); self.assertIsNone(p['completion_recent'])
        self.assertEqual(p['backend_snapshot'], raw)

    def test_wave48_counter_observation_never_claims_event_timestamp(self):
        raw = wave48_snapshot(counts=collections.Counter(fed_samples=2048, submitted_samples=1024, completed_samples=512),
                              q=collections.deque([b'x', b'y']), busy=[True, False, False, False])
        unknown = normalize_player(raw)
        self.assertIsNone(unknown['completion_age_seconds']); self.assertIsNone(unknown['completion_recent'])
        p = normalize_player(raw, observed_completion_age=.25)
        self.assertEqual(p['completion_age_seconds'], .25)
        self.assertEqual(p['completion_age_source'], 'counter-observation')
        self.assertFalse(p['completion_age_is_event']); self.assertTrue(p['completion_recent'])
        self.assertEqual((p['queued_samples'], p['inflight_samples'], p['total_buffered_samples']), (1024, 512, 1536))
        self.assertAlmostEqual(p['total_buffered_seconds'], 1536/48000)

    def test_native_completion_age_has_priority_and_explicit_none_not_overridden(self):
        p = normalize_player(legacy_snapshot(completed_samples=512, last_completed=0), observed_completion_age=0)
        self.assertEqual(p['completion_age_source'], 'backend-event'); self.assertTrue(p['completion_age_is_event'])
        self.assertGreater(p['completion_age_seconds'], 0)
        raw = wave48_snapshot(counts=collections.Counter(completed_samples=512))
        raw['completion_age_seconds'] = None
        self.assertIsNone(normalize_player(raw, observed_completion_age=0)['completion_age_seconds'])
        raw['completion_age_seconds'] = 2.0
        p = normalize_player(raw, observed_completion_age=0)
        self.assertEqual(p['completion_age_seconds'], 2.0); self.assertFalse(p['completion_recent'])

    def test_errors_and_event_counts_are_preserved_not_converted_to_drops(self):
        raw = wave48_snapshot(error='playback underflow; no concealment', counts=collections.Counter(underflow=1, overflow=2))
        p = normalize_player(raw)
        self.assertEqual(p['error'], raw['error']); self.assertEqual((p['underflow'], p['overflow']), (1, 2))
        self.assertIsNone(p['dropped_samples'])
        self.assertEqual(normalize_player(legacy_snapshot(error='waveOut error 5'))['error'], 'waveOut error 5')

    def test_malformed_numeric_metrics_stay_unknown(self):
        large = 2**60 + 1
        self.assertEqual(normalize_player(wave48_snapshot(counts=collections.Counter(completed_samples=large)))['completed_samples'], large)
        for value in (None, True, -1, 1.5, float('nan'), float('inf'), '512'):
            raw = wave48_snapshot(counts=collections.Counter(completed_samples=value))
            p = normalize_player(raw, observed_completion_age=0)
            self.assertIsNone(p['completed_samples']); self.assertIsNone(p['completion_recent'])
        raw = wave48_snapshot(counts=collections.Counter(completed_samples=512))
        for value in (True, -1, float('nan'), float('inf'), '0'):
            self.assertIsNone(normalize_player(raw, observed_completion_age=value)['completion_age_seconds'])

    def test_normalization_is_idempotent_and_preserves_observation_provenance(self):
        raw = wave48_snapshot(counts=collections.Counter(completed_samples=512))
        first = normalize_player(raw, observed_completion_age=.1)
        second = normalize_player(first)
        self.assertEqual(second, first)
        self.assertEqual(second['backend_snapshot'], raw)
        self.assertNotIn('backend_snapshot', second['backend_snapshot'])
        self.assertEqual(second['completion_age_source'], 'counter-observation')
        self.assertFalse(second['completion_age_is_event'])
        for native, kwargs in ((legacy_snapshot(), {'rate_hint': 16000}),
                               (wave48_snapshot(), {}),
                               ({'error': None}, {'rate_hint': 16000})):
            first = normalize_player(native, **kwargs)
            self.assertEqual(normalize_player(first), first)


if __name__ == '__main__':
    unittest.main()
