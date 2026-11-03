"""Opt-in board mono-input scaling; no PCM, model, beam or DOA processing.

A diagnostic reply is required before writing. One write per observed boot,
with an explicit acknowledgement; missing replies never imply success.
"""


class MonoInputProfile:
    def __init__(self, shift=None):
        if shift is not None and (type(shift) is not int or shift not in (4, 6, 8)):
            raise ValueError('mono shift must be 4, 6, 8 or None')
        self.shift = shift
        self.boot = 0
        self.last_frame = None
        self._reset()

    def _reset(self):
        self.phase = 'disabled' if self.shift is None else 'waiting_status'
        self.started = self.sent_at = None
        self.observed = None
        self.probed = False
        self.error = None

    def status(self, frame, now):
        if self.shift is None:
            return
        if self.last_frame is not None and frame < self.last_frame:
            self.boot += 1
            self._reset()
        self.last_frame = frame
        if self.phase == 'waiting_status':
            self.started = now
            self.phase = 'waiting_report'

    def report(self, data, now, acknowledgement=False):
        if self.phase in ('disabled', 'waiting_status', 'failed'):
            return
        # Enforce the same deadline even when a reply arrives before the next
        # polling iteration. A late reply must not turn a timeout into success.
        if self.phase == 'waiting_ack' and now-self.sent_at >= 5:
            self.command(now)
            return
        if self.phase == 'waiting_report' and now-self.started >= 5:
            self.command(now)
            return
        shift = data.get('raw_shift')
        if type(shift) is not int or shift not in (4, 6, 8):
            return
        self.observed = shift
        if self.phase == 'waiting_ack':
            if not acknowledgement:
                return
            if shift != self.shift:
                self.phase = 'failed'
                self.error = '板端输入设置回执不匹配；未自动重试'
            else:
                self.phase = 'confirmed'
        elif self.phase == 'confirmed':
            if shift != self.shift:
                self.phase = 'failed'
                self.error = '板端单麦输入设置已改变；未自动覆盖'
        elif shift == self.shift:
            self.phase = 'confirmed'
        else:
            self.phase = 'ready'

    def command(self, now):
        if self.phase == 'waiting_report':
            if now-self.started >= 5:
                self.phase = 'failed'
                self.error = '未收到支持输入设置的诊断回执，未改变板端参数'
            elif not self.probed:
                return b'p'
        elif self.phase == 'ready':
            return str(self.shift).encode('ascii')
        elif self.phase == 'waiting_ack' and now-self.sent_at >= 5:
            self.phase = 'failed'
            self.error = '单麦输入设置已发送但未获确认；当前设置未知'
        return None

    def sent(self, command, now):
        if command == b'p' and self.phase == 'waiting_report':
            self.probed = True
        elif self.phase == 'ready' and command == str(self.shift).encode('ascii'):
            self.phase = 'waiting_ack'
            self.sent_at = now
        else:
            raise ValueError('Unexpected mono profile command')

    def snapshot(self):
        return dict(requested_shift=self.shift, observed_shift=self.observed,
                    phase=self.phase, boot=self.boot, error=self.error,
                    scope='MONO_AND_AI_INPUT_ONLY_BEAM_AND_DOA_UNCHANGED')


def caption(state):
    if not state or state.get('phase') == 'disabled':
        return ''
    if state['phase'] == 'confirmed':
        shift = state['observed_shift']
        return f'单麦/AI输入：右移{shift}位，较右移8位 ×{1 << (8-shift)}（板端已确认）；定向通道独立'
    return state.get('error') or '单麦输入设置：等待板端确认'
