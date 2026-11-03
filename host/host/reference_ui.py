"""Pure manual-reference UI state; no port, socket, worker or device access.

ReferenceTransaction(send, timeout=2.0): ``send`` receives the official bytes
from interference_control.reference_command. Its return value is never an ACK.
submit(az, el, strength, report, age, epoch, now) returns whether send returned
normally. observe(report, age, epoch, now) returns a UI snapshot with state,
message, confirmed and pending. ``message``/``status_text`` are readable text.

Timeout leaves the one transaction unresolved and blocks another submit. A late
matching ACK can resolve it; epoch/capability loss cancels it without retrying.
An exception from send has an uncertain outcome and is treated the same way.
The caller supplies monotonic seconds, report age and the current epoch.
"""
import math

from interference_control import reference_command, reference_supported
from video_direction_overlay import MODEL, inside, pixel_from_widget, ray_xy


def _uint32(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 0xffffffff


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _valid_bit(value):
    """The real UART parser retains uint32 0/1; test mocks may use bool."""
    return type(value) in (int, bool) and value == 1


class ReferenceTransaction:
    def __init__(self, send, timeout=2.0):
        if not callable(send) or not _finite(timeout) or timeout <= 0:
            raise ValueError('send must be callable and timeout must be positive')
        self.send, self.timeout = send, float(timeout)
        self.pending = None
        self.confirmed_epoch = None
        self.confirmed_receipt = None
        self.confirmed = False
        self.state = 'idle'
        self.message = '尚未发送干扰方向设置'

    @property
    def status_text(self):
        return self.message

    def _snapshot(self):
        return dict(state=self.state, message=self.message, confirmed=self.confirmed,
                    pending=self.pending is not None)

    def observe(self, report, age, epoch, now):
        report = report if isinstance(report, dict) else {}
        supported = reference_supported(report, age, epoch)
        if self.pending is not None:
            p = self.pending
            if epoch != p['epoch'] or report.get('epoch') != p['epoch']:
                self.pending = None; self.confirmed = False
                self.state = 'cancelled_epoch'
                self.message = '控制器版本已变化，原设置未确认；未自动重发'
            elif not supported:
                self.pending = None; self.confirmed = False
                self.state = 'cancelled_capability'
                self.message = '方向设置能力或新鲜回执已丢失，原设置未确认；未自动重发'
            else:
                generation = report.get('reference_generation')
                newer = (_uint32(generation) and
                         0 < ((generation - p['generation']) & 0xffffffff) < 0x80000000)
                matching = (report.get('reference_azimuth') == p['azimuth'] and
                            report.get('reference_elevation') == p['elevation'] and
                            report.get('strength_shift') == p['strength'])
                if (newer and matching and _valid_bit(report.get('reference_valid')) and
                        report.get('reference_pending') == 0):
                    self.pending = None; self.confirmed = True
                    self.confirmed_epoch = epoch
                    self.confirmed_receipt = (generation, p['azimuth'], p['elevation'], p['strength'])
                    self.state = 'confirmed'
                    self.message = '干扰方向设置已由新一代板端回执确认'
                elif _finite(now) and now - p['sent_at'] >= self.timeout:
                    self.confirmed = False; self.state = 'unconfirmed'
                    self.message = '设置回执超时，执行状态未确认；不会自动重发'
        elif self.confirmed and epoch != self.confirmed_epoch:
            self.confirmed = False; self.state = 'cancelled_epoch'
            self.message = '控制器版本已变化，先前确认不再代表当前设置'
        elif not supported:
            self.confirmed = False; self.state = 'unsupported'
            self.message = '当前系统不支持新鲜且同版本的 B 方向设置；未发送'
        elif self.confirmed and (not _valid_bit(report.get('reference_valid')) or
                                 report.get('reference_pending') != 0 or
                                 (report.get('reference_generation'), report.get('reference_azimuth'),
                                  report.get('reference_elevation'), report.get('strength_shift')) != self.confirmed_receipt):
            self.confirmed = False; self.state = 'changed'
            self.message = '先前请求曾获确认，但当前回执已变化；当前设置未确认'
        return self._snapshot()

    def submit(self, az, el, strength, report, age, epoch, now):
        if self.pending is not None:
            self.message = '上一设置尚未确认，请勿重复发送'
            return False
        report = report if isinstance(report, dict) else {}
        if not reference_supported(report, age, epoch):
            self.confirmed = False; self.state = 'unsupported'
            self.message = '当前系统不支持新鲜且同版本的 B 方向设置；未发送'
            return False
        if not _finite(now) or not _uint32(report.get('reference_generation')):
            self.confirmed = False; self.state = 'invalid_report'
            self.message = '缺少有效的时间或 32 位回执代号；未发送'
            return False
        if report.get('reference_pending') != 0:
            self.confirmed = False; self.state = 'busy'
            self.message = '系统已有待提交方向设置；未发送'
            return False
        try:
            values = (az, el, strength)
            if not all(_finite(v) and int(v) == v for v in values):
                raise ValueError('方向与强度必须是整数')
            az, el, strength = map(int, values)
            command = reference_command(az, el, strength)
        except (ValueError, TypeError, OverflowError) as exc:
            self.confirmed = False; self.state = 'invalid_request'; self.message = str(exc)
            return False
        self.pending = dict(azimuth=az, elevation=el, strength=strength,
                            generation=report['reference_generation'], epoch=epoch,
                            sent_at=float(now))
        self.confirmed = False; self.state = 'pending'
        self.message = '设置已请求，等待板端新一代提交回执'
        try:
            self.send(command)
        except Exception as exc:
            self.state = 'unconfirmed'
            self.message = '发送结果未确认，未自动重发：' + str(exc)
            return False
        return True


def angle_from_chart(x, y, bounds):
    """Inverse of direction_display.angle_point; reject clicks outside chart."""
    try:
        left, top, right, bottom = bounds
        if not all(_finite(v) for v in (x, y, left, top, right, bottom)):
            return None
        if right <= left or bottom <= top or not left <= x <= right or not top <= y <= bottom:
            return None
        return ((x-left)*160/(right-left)-80, (bottom-y)*80/(bottom-top)-40)
    except (TypeError, ValueError):
        return None


def inverse_quick_pixel(cal, u, v):
    """Raw 1280x720 pixel -> physical bearing within verified measured domain.

    Widget coordinates must first pass through ``pixel_from_widget`` with the
    actual displayed thumbnail size. No uncalibrated FOV assumption is made.
    """
    if (not isinstance(cal, dict) or cal.get('model') != MODEL or
            cal.get('verified') is not True or cal.get('size') != [1280, 720] or
            not all(_finite(n) for n in (u, v)) or not 0 <= u < 1280 or not 0 <= v < 720):
        return None
    try:
        import numpy as np
        h = np.asarray(cal['homography'], dtype=float)
        if h.shape != (3, 3) or not np.isfinite(h).all():
            return None
        value = np.linalg.solve(h, np.array([u, v, 1.0]))
        if not np.isfinite(value).all() or abs(value[2]) < 1e-12:
            return None
        x, y = (value[:2] / value[2]).tolist()
        polygon = [list(p) for p in cal['domain']]
        if not inside((x, y), polygon):
            return None
        az = math.atan(x)
        el = math.atan(-y * math.cos(az))
        result = math.degrees(az), math.degrees(el)
        ray_xy(*result)  # same field restriction as the forward projection
        return result
    except (KeyError, TypeError, ValueError, OverflowError, ArithmeticError, np.linalg.LinAlgError):
        return None
