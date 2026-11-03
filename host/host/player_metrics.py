"""Pure adapter for the two existing waveOut telemetry schemas.

No sound device is opened. Unknown/malformed numeric metrics stay None rather
than becoming invented zeroes. A caller-observed completion age is usable only
for the known WaveOut48 schema, and must refer to an observed counter increase
after establishing that sink's initial baseline. It is not a completion event
timestamp. Reset that observation state whenever the sink instance changes.
"""
import math
from collections.abc import Mapping
from numbers import Integral, Real


def _number(value):
    if isinstance(value, Real) and not isinstance(value, bool):
        try:
            value = float(value)
            if math.isfinite(value) and value >= 0:
                return value
        except (TypeError, ValueError, OverflowError):
            pass
    return None


def _count(value):
    if isinstance(value, Integral) and not isinstance(value, bool):
        return int(value) if value >= 0 else None
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def _rate(value):
    count = _count(value)
    return count if count is not None and count > 0 else None


def normalize_player(snapshot, *, rate_hint=None, observed_completion_age=None):
    """Return a UI-safe telemetry copy, or None when no player exists.

    ``backend_snapshot`` preserves the backend's original keys/values. Known
    WaveOut48 Counter keys absent before an event mean exactly zero; absence in
    an unknown schema means unknown. ``completion_age_source`` is one of
    backend-event / backend-reported / counter-observation / None.
    ``completion_recent`` is evidence of recent completion, never audibility or
    acoustic success. Unknown age gives None, not a fabricated fresh timestamp.
    """
    if snapshot is None:
        return None
    if not isinstance(snapshot, Mapping):
        raise TypeError('player snapshot must be a mapping or None')
    # UI and diagnostics may both normalize an already adapted router value.
    # Recover the native schema rather than treating derived zeroes/age as
    # backend measurements. Carry an observation age only with its provenance.
    native = snapshot
    seen = set()
    while (native.get('backend_schema') in ('waveout16', 'waveout48', 'unknown') and
           isinstance(native.get('backend_snapshot'), Mapping)):
        if id(native) in seen:
            raise ValueError('cyclic normalized player snapshot')
        seen.add(id(native))
        if rate_hint is None:
            rate_hint = _rate(native.get('rate'))
        if (observed_completion_age is None and
                native.get('completion_age_source') == 'counter-observation'):
            observed_completion_age = _number(native.get('completion_age_seconds'))
        native = native['backend_snapshot']
    raw = dict(native)
    legacy_keys = {'fed_samples', 'submitted_samples', 'completed_samples',
                   'queued_samples', 'dropped_samples', 'error', 'completion_age_seconds'}
    wave48_keys = {'queued_blocks', 'inflight_blocks', 'error', 'rate', 'monitor_gain'}
    if wave48_keys <= raw.keys() and _rate(raw.get('rate')) == 48000:
        schema = 'waveout48'
    elif legacy_keys <= raw.keys():
        schema = 'waveout16'
    else:
        schema = 'unknown'
    out = dict(raw)
    out['backend_snapshot'] = raw
    out['backend_schema'] = schema
    for key in ('fed_samples', 'submitted_samples', 'completed_samples'):
        out[key] = _count(raw.get(key, 0 if schema == 'waveout48' else None))
    rate = _rate(raw.get('rate')) or _rate(rate_hint)
    if rate is None and schema == 'waveout16':
        rate = 16000
    out['rate'] = rate
    if schema == 'waveout48':
        queued = _count(raw.get('queued_blocks'))
        inflight = _count(raw.get('inflight_blocks'))
        out['queued_samples'] = queued * 512 if queued is not None else None
        out['inflight_samples'] = inflight * 512 if inflight is not None else None
        out['dropped_samples'] = None
        out['drop_policy'] = 'no-silent-drop; queue overflow and playback underflow are errors'
        for key in ('clip_samples', 'overflow', 'underflow', 'peak_blocks'):
            out[key] = _count(raw.get(key, 0))
    else:
        out['queued_samples'] = _count(raw.get('queued_samples'))
        out['inflight_samples'] = _count(raw.get('inflight_samples'))
        out['dropped_samples'] = _count(raw.get('dropped_samples'))
        out['drop_policy'] = (raw.get('drop_policy','strict-no-silent-drop') if raw.get('strict_no_drop') is True
                              else 'bounded-queue-may-drop' if schema == 'waveout16' else 'unknown')
    out['dropped_samples_known'] = out['dropped_samples'] is not None
    error = raw.get('error')
    out['error'] = None if error is None else str(error)
    age = _number(raw.get('completion_age_seconds'))
    source = ('backend-event' if schema == 'waveout16' else 'backend-reported') if age is not None else None
    # An explicit native age key has priority, including its None/invalid value:
    # it must not be replaced by an independently inferred optimistic age.
    if ('completion_age_seconds' not in raw and schema == 'waveout48' and
            out['completed_samples'] is not None and out['completed_samples'] > 0):
        age = _number(observed_completion_age)
        source = 'counter-observation' if age is not None else None
    out['completion_age_seconds'] = age
    out['completion_age_source'] = source
    out['completion_age_is_event'] = source == 'backend-event'
    completed = out['completed_samples']
    out['completion_recent'] = (completed > 0 and age < 1.0) if completed is not None and age is not None else None
    queued, inflight = out['queued_samples'], out['inflight_samples']
    total = queued + inflight if queued is not None and inflight is not None else None
    out['total_buffered_samples'] = total
    for name, value in (('queued_seconds', queued), ('inflight_seconds', inflight),
                        ('total_buffered_seconds', total)):
        out[name] = value / rate if value is not None and rate is not None else None
    return out
