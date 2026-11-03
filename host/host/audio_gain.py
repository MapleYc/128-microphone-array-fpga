"""Explicit playback gain only. Never used by recording, keyword recognition or DOA decisions."""
import math
import struct
import threading


class ListenGain:
    RATE = 16000
    LIMIT = 29000.0

    def __init__(self, gain_db=12):
        self.lock = threading.Lock()
        self.gain_db = self.validate(gain_db)
        self.target = self.current = 10 ** (self.gain_db / 20)
        self._reset()

    @staticmethod
    def validate(value):
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 48:
            raise ValueError('监听增益必须在 0～48 dB 之间')
        return value

    def _reset(self):
        self.previous = self.highpass = 0.0
        self.ready = False
        self.limiter = 1.0
        self.limited = self.samples = 0

    def reset(self):
        with self.lock:
            self._reset()
            self.current = self.target

    def set_gain(self, gain_db):
        value = self.validate(gain_db)
        with self.lock:
            self.gain_db = value
            self.target = 10 ** (value / 20)

    def snapshot(self):
        with self.lock:
            return dict(gain_db=self.gain_db, limit=int(self.LIMIT),
                        limiter_reduction_db=-20*math.log10(self.limiter),
                        limited_samples=self.limited, samples=self.samples,
                        scope='PLAYBACK_ONLY_RECORDING_UNCHANGED')

    def process(self, pcm):
        if len(pcm) % 2:
            raise ValueError('PCM16 byte count must be even')
        values = struct.unpack('<%dh' % (len(pcm)//2), pcm)
        result = []
        with self.lock:
            for x in values:
                if not self.ready:
                    self.previous = x
                    self.ready = True
                # 10 Hz DC blocker. State persists across UART packets.
                self.highpass = x-self.previous + .99608*self.highpass
                self.previous = x
                # Smooth manual changes (~20 ms); fixed gain, no noise-pumping AGC.
                self.current += (self.target-self.current)/320
                value = self.highpass*self.current
                required = min(1.0, self.LIMIT/max(abs(value), 1.0))
                # Instant attack, 100 ms release; final hard clamp is a backstop.
                self.limiter = min(required, self.limiter+(1-self.limiter)/1600)
                if self.limiter < .999:
                    self.limited += 1
                result.append(max(-29000, min(29000, round(value*self.limiter))))
            self.samples += len(values)
        return struct.pack('<%dh' % len(result), *result)
