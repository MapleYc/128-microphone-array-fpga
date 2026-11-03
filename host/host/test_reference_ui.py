"""Pure in-memory protocol-state and coordinate tests; no GUI/device access."""
import math
import sys
import unittest

sys.path.append(str(__import__('pathlib').Path(__file__).resolve().parent / '.'))
from direction_display import angle_point
from interference_control import reference_command
from reference_ui import ReferenceTransaction, angle_from_chart, inverse_quick_pixel, pixel_from_widget
from video_direction_overlay import MODEL, project_quick


def report(**changes):
    result = dict(version=3, hardware_id=0x574e0100, epoch=7,
                  reference_generation=10, reference_azimuth=-45,
                  reference_elevation=0, strength_shift=2,
                  reference_valid=True, reference_pending=0)
    result.update(changes)
    return result


class Transactions(unittest.TestCase):
    def setUp(self):
        self.sent = []; self.tx = ReferenceTransaction(self.sent.append)

    def submit(self, **kw):
        return self.tx.submit(-45, 0, 2, report(**kw), .1, 7, 100.)

    def test_legacy_and_stale_never_send(self):
        self.assertFalse(self.submit(version=2))
        self.assertFalse(self.tx.submit(-45, 0, 2, report(), 1.51, 7, 100))
        self.assertFalse(self.submit(hardware_id=0))
        self.assertEqual(self.sent, [])

    def test_write_is_not_ack_and_single_flight(self):
        self.assertTrue(self.submit())
        self.assertEqual(self.sent, [reference_command(-45, 0, 2)])
        self.assertFalse(self.tx.confirmed)
        self.assertFalse(self.submit())
        self.assertEqual(len(self.sent), 1)

    def test_wrong_or_same_generation_is_not_ack(self):
        self.submit()
        for changes in ({}, {'reference_generation': 9},
                        {'reference_generation': 11, 'reference_azimuth': -44},
                        {'reference_generation': 11, 'reference_elevation': 1},
                        {'reference_generation': 11, 'strength_shift': 3},
                        {'reference_generation': 11, 'reference_valid': False},
                        {'reference_generation': 11, 'reference_pending': 1}):
            result = self.tx.observe(report(**changes), .1, 7, 100.2)
            self.assertFalse(result['confirmed']); self.assertTrue(result['pending'])
        result = self.tx.observe(report(reference_generation=11), .1, 7, 100.3)
        self.assertTrue(result['confirmed']); self.assertFalse(result['pending'])

    def test_generation_wrap_accepts_forward_not_ambiguous_half_range(self):
        self.assertTrue(self.submit(reference_generation=0xffffffff))
        self.assertTrue(self.tx.observe(report(reference_generation=0), .1, 7, 100.1)['confirmed'])
        self.assertTrue(self.submit(reference_generation=10))
        self.assertFalse(self.tx.observe(report(reference_generation=10+0x80000000), .1, 7, 100.2)['confirmed'])

    def test_timeout_stays_unconfirmed_no_automatic_resend(self):
        self.submit()
        value = self.tx.observe(report(), .1, 7, 102.)
        self.assertEqual(value['state'], 'unconfirmed'); self.assertTrue(value['pending'])
        self.assertFalse(self.submit()); self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.tx.observe(report(reference_generation=11), .1, 7, 103.)['confirmed'])

    def test_epoch_and_capability_loss_cancel_without_resend(self):
        self.submit()
        self.assertEqual(self.tx.observe(report(epoch=8), .1, 8, 100.1)['state'], 'cancelled_epoch')
        self.assertIsNone(self.tx.pending); self.assertEqual(len(self.sent), 1)
        self.submit()
        self.assertEqual(self.tx.observe(report(version=2), .1, 7, 100.2)['state'], 'cancelled_capability')
        self.assertIsNone(self.tx.pending); self.assertEqual(len(self.sent), 2)

    def test_send_exception_is_uncertain_and_cannot_retry(self):
        def uncertain(command):
            self.sent.append(command); raise OSError('transport outcome unknown')
        self.tx = ReferenceTransaction(uncertain)
        self.assertFalse(self.submit()); self.assertIsNotNone(self.tx.pending)
        self.assertFalse(self.submit()); self.assertEqual(len(self.sent), 1)

    def test_missing_generation_and_noninteger_request_never_send(self):
        self.assertFalse(self.submit(reference_generation=None))
        self.assertFalse(self.tx.submit(2.5, 0, 2, report(), .1, 7, 100))
        self.assertEqual(self.sent, [])

    def test_confirmed_state_does_not_survive_controller_epoch_change(self):
        self.submit()
        self.tx.observe(report(reference_generation=11), .1, 7, 100.1)
        self.assertTrue(self.tx.confirmed)
        result = self.tx.observe(report(epoch=8), .1, 8, 100.2)
        self.assertFalse(result['confirmed']); self.assertEqual(result['state'], 'cancelled_epoch')

    def test_real_parser_integer_valid_bit_confirms(self):
        self.submit(reference_valid=1)
        result = self.tx.observe(report(reference_generation=11, reference_valid=1), .1, 7, 100.1)
        self.assertTrue(result['confirmed'])

    def test_confirmed_receipt_is_revoked_when_board_report_changes(self):
        for changes in ({'reference_valid': 0}, {'reference_pending': 1},
                        {'reference_generation': 12}, {'reference_azimuth': -44},
                        {'reference_elevation': 1}, {'strength_shift': 1}):
            with self.subTest(changes=changes):
                self.tx = ReferenceTransaction(self.sent.append)
                self.submit()
                self.tx.observe(report(reference_generation=11, reference_valid=1), .1, 7, 100.1)
                changed = report(reference_generation=11, reference_valid=1); changed.update(changes)
                result = self.tx.observe(changed, .1, 7, 100.2)
                self.assertFalse(result['confirmed']); self.assertEqual(result['state'], 'changed')


class Coordinates(unittest.TestCase):
    def test_chart_roundtrip_and_outside(self):
        bounds = (34, 12, 330, 220)
        for az, el in [(-80, -40), (-21, 17), (0, 0), (80, 40)]:
            x, y = angle_point(az, el, bounds)
            actual = angle_from_chart(x, y, bounds)
            self.assertAlmostEqual(actual[0], az); self.assertAlmostEqual(actual[1], el)
        self.assertIsNone(angle_from_chart(33, 100, bounds))
        self.assertIsNone(angle_from_chart(100, 221, bounds))

    def test_verified_measured_pixel_roundtrip_and_margins(self):
        cal = dict(model=MODEL, verified=True, size=[1280, 720],
                   homography=[[300, 0, 640], [0, 300, 360], [0, 0, 1]],
                   domain=[[-.6, -.5], [.6, -.5], [.6, .5], [-.6, .5]])
        for az, el in [(0, 0), (20, 10), (-20, -10)]:
            u, v = project_quick(cal, az, el, 1280, 720)
            actual = inverse_quick_pixel(cal, u, v)
            self.assertAlmostEqual(actual[0], az); self.assertAlmostEqual(actual[1], el)
        pixel = pixel_from_widget(500, 300, (1000, 600), (960, 540))
        self.assertEqual(inverse_quick_pixel(cal, *pixel), (0, -0.0))
        self.assertIsNone(pixel_from_widget(10, 300, (1000, 600), (960, 540)))
        self.assertIsNone(inverse_quick_pixel(cal, 1200, 600))
        self.assertIsNone(inverse_quick_pixel(dict(cal, verified=False), 640, 360))
        self.assertIsNone(inverse_quick_pixel(dict(cal, model='guessed_fov'), 640, 360))
        self.assertIsNone(inverse_quick_pixel(dict(cal, homography=[[0, 0, 0]]*3), 640, 360))


if __name__ == '__main__':
    unittest.main()
