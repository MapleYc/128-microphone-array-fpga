"""Display board-reported angles; no acoustic estimation or camera calibration."""
import math


def finite_number(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def angle_point(azimuth, elevation, bounds):
    """Map array right/up angles onto a separate labelled angular chart."""
    if not all(finite_number(v) for v in (azimuth, elevation)):
        return None
    if not -80 <= azimuth <= 80 or not -40 <= elevation <= 40:
        return None
    left, top, right, bottom = bounds
    return (left + (azimuth + 80) / 160 * (right - left),
            bottom - (elevation + 40) / 80 * (bottom - top))


def direction_display_state(status, status_age_seconds):
    """Age received state as well as the board's last accepted DOA result."""
    result = dict(current=None, locked=None, message='等待板端方向')
    if not status:
        return result
    if (not finite_number(status_age_seconds) or status_age_seconds < 0 or
            status_age_seconds > 1):
        result['message'] = '板端状态已过期，等待恢复'
        return result
    az, el = status.get('azimuth'), status.get('elevation')
    quality, age = status.get('direction_quality'), status.get('direction_age_ms')
    if (angle_point(az, el, (0, 0, 1, 1)) is not None and
            finite_number(quality) and 0 < quality <= 1 and
            finite_number(age) and age >= 0):
        age = age / 1000 + status_age_seconds
        kind = 'history' if age > 1 else 'reliable' if quality >= .5 else 'weak'
        result['current'] = dict(azimuth=az, elevation=el, quality=quality,
                                 age_seconds=age, kind=kind)
        label = dict(history='历史方向', reliable='当前方向', weak='低质量方向')[kind]
        result['message'] = (f'{label}：{az:+.1f}° / {el:+.1f}°\n'
                             f'质量 {quality:.0%} · 距今 {age:.2f}s')
    else:
        result['message'] = '尚无有效声源方向'
    # A selected beam persists even when the source becomes silent. Its marker
    # is not evidence of a new DOA measurement or the speaker's current location.
    az, el = status.get('locked_azimuth'), status.get('locked_elevation')
    if status.get('mode') == 'LOCKED' and angle_point(az, el, (0, 0, 1, 1)) is not None:
        result['locked'] = dict(azimuth=az, elevation=el)
        result['message'] += f'\n锁定方向：{az:+.1f}° / {el:+.1f}°'
    return result


class DirectionView:
    """Small independent angle chart, deliberately not overlaid on video."""
    def __init__(self, parent):
        import tkinter as tk
        self.widget = tk.Frame(parent, bg='#111827', width=280)
        self.widget.pack(side='right', fill='y', padx=(8, 12))
        tk.Label(self.widget, text='声源方向（阵列坐标）', fg='#f9fafb',
                 bg='#111827', font=('Microsoft YaHei UI', 11)).pack(pady=(8, 0))
        tk.Label(self.widget, text='横轴：方位角　纵轴：俯仰角', fg='#9ca3af',
                 bg='#111827').pack()
        self.chart = tk.Canvas(self.widget, width=264, height=208,
                               bg='#030712', highlightthickness=0)
        self.chart.pack(pady=6)
        self.bounds = (36, 20, 244, 172)
        for az in (-80, -40, 0, 40, 80):
            x, _ = angle_point(az, 0, self.bounds)
            self.chart.create_line(x, 20, x, 172, fill='#475569' if az == 0 else '#1e293b')
            self.chart.create_text(x, 186, text=f'{az:+d}' if az else '0', fill='#9ca3af')
        for el in (-40, -20, 0, 20, 40):
            _, y = angle_point(0, el, self.bounds)
            self.chart.create_line(36, y, 244, y, fill='#475569' if el == 0 else '#1e293b')
            self.chart.create_text(19, y, text=f'{el:+d}' if el else '0', fill='#9ca3af')
        self.caption = tk.Label(self.widget, text='等待板端方向', justify='left',
                                fg='#f9fafb', bg='#111827', wraplength=264)
        self.caption.pack(anchor='w', pady=4)
        for text, color in [('● 青色：新鲜、质量≥50%的方向', '#22d3ee'),
                            ('○ 橙色：新鲜、质量不足50%', '#fb923c'),
                            ('○ 灰色：超过1秒的历史方向', '#94a3b8'),
                            ('＋ 黄色：板端锁定的拾音方向', '#fbbf24')]:
            tk.Label(self.widget, text=text, fg=color, bg='#111827',
                     justify='left').pack(anchor='w')
        tk.Label(self.widget, text='0° / 0° 为阵列正前方。\n这是角度图；视频落点需声画标定。',
                 fg='#9ca3af', bg='#111827', justify='left').pack(anchor='w', pady=8)

    def update(self, status, status_age_seconds):
        state = direction_display_state(status, status_age_seconds)
        self.chart.delete('direction')
        self.caption.configure(text=state['message'])
        if state['current']:
            d = state['current']
            x, y = angle_point(d['azimuth'], d['elevation'], self.bounds)
            color = dict(reliable='#22d3ee', weak='#fb923c', history='#94a3b8')[d['kind']]
            self.chart.create_oval(x-6, y-6, x+6, y+6, outline=color, width=2,
                                   fill=color if d['kind'] == 'reliable' else '', tags='direction')
        if state['locked']:
            d = state['locked']
            x, y = angle_point(d['azimuth'], d['elevation'], self.bounds)
            self.chart.create_line(x-10, y, x+10, y, fill='#fbbf24', width=2, tags='direction')
            self.chart.create_line(x, y-10, x, y+10, fill='#fbbf24', width=2, tags='direction')
        return state
