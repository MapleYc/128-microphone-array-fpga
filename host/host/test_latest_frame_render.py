"""Pure PIL/worker tests. No Tk, soundcard, socket or board access."""
import sys
import threading
import time
from types import SimpleNamespace
import unittest

sys.path.append(str(__import__('pathlib').Path(__file__).resolve().parent / '.'))
from PIL import Image
from video_protocol import rgb_image
from latest_frame_render import LatestFrameRenderer


def frame(number=1):
    width, height = 32, 20
    values = [(i*173 + number*31) & 0xffff for i in range(width*height)]
    pixels = b''.join(v.to_bytes(2, 'little') for v in values)
    return SimpleNamespace(frame_id=number, source_frame=number*4800, width=width, height=height, pixels=pixels)


def wait_for(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.002)
    raise AssertionError('timed out waiting for display-only worker')


class LatestFrameTests(unittest.TestCase):
    def test_exact_original_rgb_thumbnail_and_frame_metadata(self):
        f = frame(7)
        renderer = LatestFrameRenderer(rgb_image)
        try:
            seq = renderer.submit(f, (17, 13), version=42)
            result = wait_for(renderer.poll_latest)
            expected = rgb_image(f); expected.thumbnail((17, 13))
            self.assertEqual((result['image'].mode, result['image'].size, result['image'].tobytes()),
                             (expected.mode, expected.size, expected.tobytes()))
            self.assertIs(result['frame'], f)
            self.assertEqual((result['seq'], result['frame_id'], result['source_frame']), (seq, 7, 33600))
            self.assertEqual((result['source_size'], result['viewport_size'], result['geometry_version']),
                             ((32, 20), (17, 13), 42))
            self.assertGreaterEqual(result['convert_ms'], 0); self.assertGreaterEqual(result['resize_ms'], 0)
            before = result['image'].tobytes()
            renderer.submit(frame(8), (3, 3), version=43)
            wait_for(renderer.poll_latest)
            self.assertEqual(result['image'].tobytes(), before)
        finally:
            self.assertTrue(renderer.close())

    def test_blocked_converter_merges_latest_request_with_bounded_slots(self):
        entered, release = threading.Event(), threading.Event()
        converted = []
        def convert(f):
            converted.append(f.frame_id)
            if f.frame_id == 1:
                entered.set()
                if not release.wait(2): raise RuntimeError('test release timeout')
            return rgb_image(f)
        renderer = LatestFrameRenderer(convert)
        try:
            renderer.submit(frame(1), (20, 20))
            self.assertTrue(entered.wait(1))
            latest = None
            for n in range(2, 52):
                latest = frame(n); renderer.submit(latest, (20, 20), version=n)
                s = renderer.snapshot()
                self.assertLessEqual(s['pending'], 1); self.assertLessEqual(s['inflight'], 1); self.assertLessEqual(s['result'], 1)
            self.assertEqual(renderer.snapshot()['counts']['replaced_requests'], 49)
            release.set()
            wait_for(lambda: renderer.snapshot()['counts'].get('published') == 2)
            result = renderer.poll_latest()
            self.assertIs(result['frame'], latest)
            self.assertEqual(result['geometry_version'], 51)
            self.assertEqual(converted, [1, 51])
            self.assertEqual(renderer.snapshot()['counts']['replaced_results'], 1)
        finally:
            release.set(); self.assertTrue(renderer.close())

    def test_error_is_explicit_and_later_valid_frame_can_render(self):
        def convert(f):
            if f.frame_id == 1: raise ValueError('bad RGB frame')
            return rgb_image(f)
        renderer = LatestFrameRenderer(convert)
        try:
            renderer.submit(frame(1), (20, 20))
            wait_for(lambda: renderer.snapshot()['counts'].get('errors') == 1)
            s = renderer.snapshot()
            self.assertIn('bad RGB frame', s['error']); self.assertEqual(s['last_error']['stage'], 'convert')
            self.assertEqual(s['last_error']['frame_id'], 1); self.assertIsNone(renderer.poll_latest())
            renderer.submit(frame(2), (20, 20))
            self.assertEqual(wait_for(renderer.poll_latest)['frame_id'], 2)
            self.assertEqual(renderer.snapshot()['counts']['errors'], 1)
            self.assertIn('bad RGB frame', renderer.snapshot()['error'])  # error evidence persists
        finally:
            self.assertTrue(renderer.close())
        self.assertFalse(renderer.snapshot()['worker_alive'])

    def test_close_timeout_reports_live_worker_then_cleanly_stops(self):
        entered, release = threading.Event(), threading.Event()
        def convert(f):
            entered.set(); release.wait(2); return rgb_image(f)
        renderer = LatestFrameRenderer(convert)
        renderer.submit(frame(1), (20, 20))
        self.assertTrue(entered.wait(1))
        renderer.submit(frame(2), (20, 20))
        try:
            self.assertFalse(renderer.close(timeout=.001))
            self.assertTrue(renderer.snapshot()['worker_alive'])
            self.assertEqual(renderer.snapshot()['pending'], 0)
            with self.assertRaises(RuntimeError): renderer.submit(frame(3), (20, 20))
        finally:
            release.set(); self.assertTrue(renderer.close())
        self.assertFalse(renderer.snapshot()['worker_alive'])
        self.assertIsNone(renderer.poll_latest())
        self.assertEqual(renderer.snapshot()['counts']['started'], 1)

    def test_resize_failure_is_recorded_and_invalid_viewport_rejected(self):
        renderer = LatestFrameRenderer(lambda f: object())
        try:
            with self.assertRaises(ValueError): renderer.submit(frame(), (0, 10))
            renderer.submit(frame(), (10, 10))
            wait_for(lambda: renderer.snapshot()['counts'].get('resize_errors') == 1)
            self.assertEqual(renderer.snapshot()['last_error']['stage'], 'resize')
        finally:
            self.assertTrue(renderer.close())


if __name__ == '__main__':
    unittest.main()
