"""Independent synthetic acoustic input for the stage-2 software reference.

No DUT delay table or DUT interpolation is used.  Coordinates are metres;
azimuth/elevation describe the bearing FROM the array centre TO the source.
The optional room is a traceable hybrid approximation, not a measured room.
Nothing is rendered or written on import.
"""

from collections.abc import Mapping
import math

import numpy as np
from scipy.signal import fftconvolve, resample_poly


FS = 48000
C = 343.0
GUARD_SAMPLES = 240
UPSAMPLE = 16
PCM_SCALE = 1 << 23


def _get(scene, name, default):
    return scene.get(name, default) if isinstance(scene, Mapping) else getattr(scene, name, default)


def _unit(az_deg, el_deg):
    az, el = np.deg2rad(az_deg), np.deg2rad(el_deg)
    return np.stack((np.sin(az) * np.cos(el), np.sin(el) + np.zeros_like(az),
                     np.cos(az) * np.cos(el)), axis=-1)


def _energy(x, window):
    a, b = window
    return float(np.mean(np.square(x[a:b]))) if b > a else 0.0


def _sir(target, interference, window):
    et, ei = _energy(target, window), _energy(interference, window)
    return 10.0 * math.log10(et / ei) if et > 0 and ei > 0 else None


def _sample(up, sample_positions):
    """Sample a separately polyphase-upsampled waveform, with zero extension."""
    if up is None:
        return np.zeros_like(sample_positions, dtype=np.float64)
    indices = np.asarray(sample_positions, dtype=np.float64) * UPSAMPLE
    # np.interp deliberately differs from the DUT's four-tap Lagrange filter.
    return np.interp(indices, np.arange(len(up)), up, left=0.0, right=0.0)


def _tail(seed, source_id, channel_id, rt60, n, energy_ratio):
    if rt60 <= 0 or energy_ratio <= 0:
        return np.zeros(1, dtype=np.float64)
    length = min(n, max(1, int(math.ceil(1.5 * rt60 * FS))))
    rng = np.random.default_rng(np.random.SeedSequence([seed, source_id, channel_id + 2, 731]))
    seconds = np.arange(length, dtype=np.float64) / FS
    h = rng.standard_normal(length) * np.exp(-math.log(1000.0) * seconds / rt60)
    h[:min(length, int(round(0.040 * FS)))] = 0.0
    # Mild colour, independent for each source and microphone.  This is a
    # synthetic diffuse late field, not a calibrated spatial coherence model.
    h = np.convolve(h, np.array([0.25, 0.5, 0.25]), mode="full")[:length]
    norm = float(np.linalg.norm(h))
    if norm:
        h *= math.sqrt(energy_ratio) / norm
    return h


def _source_context(source, az, el, radius, end_az, rt60, rho, bounds, seed, source_id):
    n = len(source)
    if end_az is None:
        q = radius * _unit(float(az), float(el))
    else:
        angles = np.linspace(float(az), float(end_az), n)
        q = radius * _unit(angles, float(el))
    paths = [("direct", q, 1.0)]
    if rt60 > 0:
        for axis in range(3):
            for side in range(2):
                image = np.array(q, copy=True)
                image[..., axis] = 2.0 * bounds[axis, side] - image[..., axis]
                paths.append(("reflection_%s_%s" % ("xyz"[axis], side), image, rho))
    nonzero = bool(np.any(source))
    return dict(source=source, up=resample_poly(source, UPSAMPLE, 1) if nonzero else None,
                radius=radius, paths=paths, q=q, source_id=source_id, seed=seed,
                rt60=rt60, moving=end_az is not None)


def _render_source(ctx, mic, sample_axis, mic_delay, tail_energy, channel_id):
    result = np.zeros(len(sample_axis), dtype=np.float64)
    records = []
    for name, q, coefficient in ctx["paths"]:
        distance = np.linalg.norm(q - mic, axis=-1)
        if np.any(distance < 0.05):
            raise ValueError("A source/image is less than 5 cm from a microphone")
        relative_samples = (distance - ctx["radius"]) * FS / C
        if ctx["up"] is not None:
            result += (coefficient * ctx["radius"] / distance) * _sample(
                ctx["up"], sample_axis - GUARD_SAMPLES - relative_samples - mic_delay)
        dd = np.asarray(distance).reshape(-1)
        rr = np.asarray(relative_samples).reshape(-1)
        records.append(dict(name=name, coefficient=float(coefficient),
                            distance_start_m=float(dd[0]), distance_end_m=float(dd[-1]),
                            relative_delay_start_samples=float(rr[0]),
                            relative_delay_end_samples=float(rr[-1])))
    tail = _tail(ctx["seed"], ctx["source_id"], channel_id,
                 ctx["rt60"], len(sample_axis), tail_energy)
    if ctx["up"] is not None and np.any(tail):
        wet = fftconvolve(ctx["source"], tail, mode="full")[:len(sample_axis)]
        result += np.interp(sample_axis - GUARD_SAMPLES - mic_delay,
                            sample_axis, wet, left=0.0, right=0.0)
    return result, records, tail


def _add_sinc_impulse(h, delay, amplitude, half=32):
    centre = int(math.floor(delay))
    indices = np.arange(centre - half + 1, centre + half + 1)
    weights = np.sinc(indices - delay) * np.hanning(2 * half)
    total = float(np.sum(weights))
    if abs(total) > 1.0e-15:
        weights /= total
    valid = (indices >= 0) & (indices < len(h))
    h[indices[valid]] += amplitude * weights[valid]


def _centre_rir(ctx, tail, n, at_end=False):
    """Diagnostic static RIR; moving scenes additionally save the end snapshot.

    Direct/early fractional impulses use a 64-tap windowed sinc, so this saved
    diagnostic RIR is not asserted bit-identical to the polyphase render path.
    Exact path parameters are returned separately.
    """
    points = []
    for _, q, coefficient in ctx["paths"]:
        point = q[-1 if at_end else 0] if np.ndim(q) == 2 else q
        distance = float(np.linalg.norm(point))
        delay = GUARD_SAMPLES + (distance - ctx["radius"]) * FS / C
        points.append((delay, coefficient * ctx["radius"] / distance))
    length = min(n, max(GUARD_SAMPLES + len(tail) + 1,
                        int(math.ceil(max(d for d, _ in points))) + 66))
    h = np.zeros(length, dtype=np.float64)
    for delay, amplitude in points:
        _add_sinc_impulse(h, delay, amplitude)
    count = min(len(tail), max(0, length - GUARD_SAMPLES))
    h[GUARD_SAMPLES:GUARD_SAMPLES + count] += tail[:count]
    return h


def _schroeder_t20(h):
    energy = np.cumsum(np.square(h)[::-1], dtype=np.float64)[::-1]
    if not len(energy) or energy[0] <= 0:
        return dict(rt60_extrapolated_s=None, reason="zero RIR energy")
    db = 10.0 * np.log10(np.maximum(energy / energy[0], 1.0e-300))
    selected = np.flatnonzero((db <= -5.0) & (db >= -25.0))
    if len(selected) < 64 or selected[-1] - selected[0] < 0.010 * FS:
        return dict(rt60_extrapolated_s=None, reason="insufficient -5 to -25 dB decay span")
    times = selected / FS
    slope, intercept = np.polyfit(times, db[selected], 1)
    predicted = slope * times + intercept
    residual = float(np.sum(np.square(db[selected] - predicted)))
    variance = float(np.sum(np.square(db[selected] - np.mean(db[selected]))))
    return dict(rt60_extrapolated_s=float(-60.0 / slope) if slope < 0 else None,
                fitted_slope_db_per_s=float(slope),
                r_squared=float(1.0 - residual / variance) if variance else None,
                fit_start_s=float(times[0]), fit_end_s=float(times[-1]),
                method="Schroeder energy decay, -5..-25 dB least squares, T20 extrapolation")


def render(t, i, positions, scene):
    """Return ``pcm, truth, info, rirs`` without changing either input signal.

    ``pcm`` is N x M int32 containing signed24 words (not shifted by 8).
    Input length must match and be at most six seconds.  Sources are expected
    to include the caller's 0.6 s startup silence.  SIR is set in the common
    0.8..3.8 s direct-centre window, with no source-wise peak normalization.
    """
    target = np.array(t, dtype=np.float64, copy=True)
    interference = np.array(i, dtype=np.float64, copy=True)
    pos = np.array(positions, dtype=np.float64, copy=True)
    if target.ndim != 1 or interference.shape != target.shape or not 0 < len(target) <= 6 * FS:
        raise ValueError("t/i must be equal, nonempty 1D arrays of at most six seconds")
    if pos.ndim != 2 or pos.shape[1] != 3 or len(pos) == 0:
        raise ValueError("positions must be an M x 3 array in metres")
    if not (np.all(np.isfinite(target)) and np.all(np.isfinite(interference)) and np.all(np.isfinite(pos))):
        raise ValueError("Sources and geometry must contain finite values")
    n, channels = len(target), len(pos)
    axis = np.arange(n, dtype=np.float64)
    window = tuple(int(round(float(v) * FS)) for v in _get(scene, 'sir_window_seconds', (0.8, min(n / FS, 3.8))))
    if len(window) != 2 or not 0 <= window[0] < window[1] <= n:
        raise ValueError('Invalid explicit SIR calibration window')
    fallback_window = window[1] <= window[0]
    if fallback_window:
        window = (0, n)
    td = np.interp(axis - GUARD_SAMPLES, axis, target, left=0.0, right=0.0)
    id0 = np.interp(axis - GUARD_SAMPLES, axis, interference, left=0.0, right=0.0)
    et, ei = _energy(td, window), _energy(id0, window)
    requested_sir = float(_get(scene, "sir_db", 0.0))
    i_scale = math.sqrt(et / ei) * 10.0 ** (-requested_sir / 20.0) if et > 0 and ei > 0 else (1.0 if ei else 0.0)
    interference *= i_scale
    idirect = id0 * i_scale
    rt60 = float(_get(scene, "rt60_s", 0.0))
    if not np.isfinite(rt60) or rt60 < 0 or rt60 > 2.0:
        raise ValueError("rt60_s must be finite and within 0..2 seconds")
    seed = int(_get(scene, "seed", 20261029))
    if seed < 0:
        raise ValueError("seed must be nonnegative")
    bounds = np.asarray(_get(scene, "room_bounds_m", [[-4, 4], [-1.5, 1.5], [-2, 6]]), dtype=np.float64)
    if bounds.shape != (3, 2) or not np.all(np.isfinite(bounds)) or np.any(bounds[:, 1] <= bounds[:, 0]):
        raise ValueError("room_bounds_m must contain increasing bounds for x/y/z")
    dims = bounds[:, 1] - bounds[:, 0]
    volume = float(np.prod(dims))
    area = float(2 * (dims[0] * dims[1] + dims[1] * dims[2] + dims[0] * dims[2]))
    mean_free_path = 4 * volume / area
    rho = float(np.clip(np.exp(-math.log(1000.0) * mean_free_path / (C * rt60)), 0.15, 0.92)) if rt60 else 0.0
    late_energy = float(_get(scene, "late_energy_ratio", 0.12))
    if not np.isfinite(late_energy) or late_energy < 0:
        raise ValueError("late_energy_ratio must be finite and nonnegative")
    target_distance = float(_get(scene, "distance_m", 3.0))
    interferer_distance = float(_get(scene, "interferer_distance_m", 3.0))
    if min(target_distance, interferer_distance) <= 0:
        raise ValueError("Source distances must be positive")
    ct = _source_context(target, _get(scene, "target_az_deg", 0.0), _get(scene, "target_el_deg", 0.0),
                         target_distance, _get(scene, "moving_target_end_az_deg", None),
                         rt60, rho, bounds, seed, 0)
    ci = _source_context(interference, _get(scene, "interferer_az_deg", 30.0), _get(scene, "interferer_el_deg", 0.0),
                         interferer_distance, None, rt60, rho, bounds, seed, 1)
    if rt60:
        for points in (pos, np.atleast_2d(ct["q"]), np.atleast_2d(ci["q"])):
            if np.any(points <= bounds[:, 0]) or np.any(points >= bounds[:, 1]):
                raise ValueError("Microphones and source trajectory must be inside the synthetic room")
    rng = np.random.default_rng(np.random.SeedSequence([seed, 809]))
    gain_std = float(_get(scene, "mic_gain_std_db", 0.0))
    delay_std = float(_get(scene, "mic_delay_std_samples", 0.0))
    if not np.isfinite(gain_std + delay_std) or min(gain_std, delay_std) < 0:
        raise ValueError("Microphone error standard deviations must be finite and nonnegative")
    gain_db = rng.normal(0.0, gain_std, channels)
    delays = rng.normal(0.0, delay_std, channels)
    gains = 10.0 ** (gain_db / 20.0)
    dead = sorted(set(([2, 3] if channels == 128 else []) + list(_get(scene, "extra_dead_channels", []))))
    if any(not isinstance(j, (int, np.integer)) or j < 0 or j >= channels for j in dead):
        raise ValueError("Dead-channel indices must be valid integer canonical channels")
    dead_set = set(dead)
    # One scenario at a time.  float32 scratch avoids a large float64 N x M
    # matrix; the exact ADC quantization below is relative to this scratch.
    mixture = np.zeros((n, channels), dtype=np.float32)
    path_records = dict(target=[], interferer=[])
    peak = 0.0
    for ch, mic in enumerate(pos):
        a, pa, _ = _render_source(ct, mic, axis, delays[ch], late_energy, ch)
        b, pb, _ = _render_source(ci, mic, axis, delays[ch], late_energy, ch)
        path_records["target"].append(dict(channel=ch, paths=pa))
        path_records["interferer"].append(dict(channel=ch, paths=pb))
        if ch not in dead_set:
            mixture[:, ch] = (a + b) * gains[ch]
            peak = max(peak, float(np.max(np.abs(mixture[:, ch]))))
    global_gain = min(1.0, 0.749 / peak) if peak else 1.0
    pcm = np.empty((n, channels), dtype=np.int32)
    sum_error_sq, max_error, clipping, sample_count = 0.0, 0.0, 0, 0
    for ch in range(channels):
        scaled = mixture[:, ch].astype(np.float64) * global_gain
        rounded = np.rint(scaled * PCM_SCALE)
        clipping += int(np.count_nonzero((rounded < -PCM_SCALE) | (rounded > PCM_SCALE - 1)))
        quantized = np.clip(rounded, -PCM_SCALE, PCM_SCALE - 1).astype(np.int32)
        pcm[:, ch] = quantized
        if ch not in dead_set:
            error = quantized.astype(np.float64) / PCM_SCALE - scaled
            sum_error_sq += float(np.dot(error, error))
            max_error = max(max_error, float(np.max(np.abs(error))))
            sample_count += n
    del mixture
    for ch in dead:
        pcm[:, ch] = PCM_SCALE - 1 if channels == 128 and ch == 2 else -PCM_SCALE if channels == 128 and ch == 3 else 0
    tw, pt, tail_t = _render_source(ct, np.zeros(3), axis, 0.0, late_energy, -1)
    iw, pi, tail_i = _render_source(ci, np.zeros(3), axis, 0.0, late_energy, -1)
    truth = dict(target_direct_center=td * global_gain,
                 interferer_direct_center=idirect * global_gain,
                 target_wet_center=tw * global_gain,
                 interferer_wet_center=iw * global_gain)
    rirs = dict(target_center_start=_centre_rir(ct, tail_t, n),
                interferer_center_start=_centre_rir(ci, tail_i, n),
                target_center_late_kernel=tail_t, interferer_center_late_kernel=tail_i)
    if ct["moving"]:
        rirs["target_center_end"] = _centre_rir(ct, tail_t, n, at_end=True)
    info = dict(schema="stage2-independent-physics-v1", fs_hz=FS, sound_speed_m_s=C,
                samples=n, channels=channels, guard_samples=GUARD_SAMPLES,
                direction_convention="array-centre TO source; q=R*(sin(az)cos(el),sin(el),cos(az)cos(el))",
                propagation="distance relative to centre: (norm(q-r)-R)/343; amplitude R/norm(q-r)",
                interpolation="scipy.resample_poly 16x then np.interp; no DUT taps or delay table",
                seed=seed, sir_requested_db=requested_sir,
                sir_db_direct_center=_sir(td, idirect, window),
                sir_db_wet_center=_sir(tw, iw, window),
                sir_window_samples=list(window), short_signal_window_fallback=fallback_window,
                interference_source_gain=i_scale, source_sir_defined=bool(et > 0 and ei > 0),
                global_gain=global_gain, healthy_mix_peak_before_gain=peak,
                healthy_mix_peak_after_gain=peak * global_gain,
                input_sources_mutated=False, float_scratch_dtype="float32",
                quantization=dict(scale=PCM_SCALE, format="signed24 sign-extended int32; no <<8",
                                  rounding="numpy.rint ties-to-even", clipped_samples_excluding_faults=clipping,
                                  rms_error=math.sqrt(sum_error_sq / sample_count) if sample_count else 0.0,
                                  max_abs_error=max_error, error_reference="globally-scaled float32 scratch"),
                dead_channels=[int(ch) for ch in dead],
                dead_channel_policy="128 CH2=+8388607 CH3=-8388608; other requested dead channels=0; inject after global gain",
                mic_gain_errors_db=gain_db.tolist(), mic_delay_errors_samples=delays.tolist(),
                room=dict(enabled=bool(rt60), model="synthetic six first-order image sources plus independent coloured exponential late tails",
                          requested_rt60_s=rt60, bounds_m=bounds.tolist(), wall_amplitude_reflection=rho,
                          late_energy_ratio=late_energy, late_start_s=0.040,
                          late_max_duration_s=min(n / FS, 1.5 * rt60),
                          measured_target_center_t20=_schroeder_t20(rirs["target_center_start"]),
                          measured_interferer_center_t20=_schroeder_t20(rirs["interferer_center_start"]),
                          measured_room=False, exact_requested_rt60_claim=False,
                          late_spatial_coherence="independent synthetic per-source/per-microphone tails; not calibrated"),
                moving_target=bool(ct["moving"]),
                motion_model="quasi-static q(t) distances and amplitudes; linear azimuth over input duration; no exact retarded-position solve",
                center_paths=dict(target=pt, interferer=pi), microphone_paths=path_records,
                saved_rir_scope="centre start/end snapshots, 64-tap sinc diagnostic early paths; authoritative render path parameters also saved")
    return pcm, truth, info, rirs
