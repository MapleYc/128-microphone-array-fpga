"""Causal target-conditioned interference SRP for the isolated software sprint.

This module never reads scene labels, source activity, source distance, clean
audio, or a true interferer direction.  Its only changing input is an observed
microphone FFT.  The caller must apply process() results on the NEXT audio hop.

Geometry convention: azimuth is the physical bearing from array centre to the
source, q=R*(sin(az), 0, cos(az)).  The first profile assumes equal source/array
height (elevation zero), a front-hemisphere source, and one dominant interferer.
It is not the existing CPU/PL GCC implementation and is not hardware-verified.
"""
from __future__ import annotations

import math
import numpy as np

def wrapped_delta(a, b):
    """Signed shortest angular displacement from b to a, degrees."""
    return (float(a) - float(b) + 180.0) % 360.0 - 180.0

def outside_target(angle, lock, exclusion):
    return abs(wrapped_delta(angle, lock)) > float(exclusion) + 1e-9

def clear_short_arc(start, end, lock, exclusion):
    """Whether the complete shortest circular arc avoids the closed exclusion."""
    if not outside_target(start, lock, exclusion) or not outside_target(end, lock, exclusion):
        return False
    a = wrapped_delta(start, lock)
    b = a + wrapped_delta(end, start)
    lo, hi = sorted((a, b))
    radius = float(exclusion) + 1e-9
    return not any(lo <= 360.0*k + radius and hi >= 360.0*k - radius for k in (-1, 0, 1))


PROFILE = {
    "name": "target_conditioned_normalized_srp_v1",
    "azimuth_min_deg": -80.0,
    "azimuth_max_deg": 80.0,
    "azimuth_step_deg": 2.0,
    "analysis_band_hz": [750.0, 4000.0],
    "target_exclusion_deg": 10.0,
    "outside_peak_neighbourhood_deg": 10.0,
    "target_subspace": "farfield lock, 3m lock, farfield azimuth tangent",
    "target_tangent_half_step_deg": 2.0,
    "statistics_ema": 0.80,
    "minimum_residual_power_fraction": 0.01,
    "minimum_peak_score": 0.30,
    "minimum_peak_margin": 0.008,
    "minimum_peak_ratio": 1.02,
    "minimum_support_fraction": 0.35,
    "minimum_active_bins": 6,
    "active_bin_relative_power": 1e-4,
    "directional_reliability_floor": 0.12,
    "confirmation_frames": 3,
    "confirmation_tolerance_deg": 4.0,
    "direction_smoothing": 0.25,
    "maximum_direction_step_deg": 4.0,
    "hold_seconds": 0.50,
    "threshold_status": "fixed engineering initial profile, not user acoustic acceptance",
}


def select_channels(positions, count=24, bad_channels=(2, 3)):
    """Deterministic maximin aperture selection; returns canonical indices.

    No observed signals or scene directions participate in this selection.
    The default bad-channel indices follow the frozen 128-channel mask.
    """
    p = np.asarray(positions, dtype=float)
    if p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all():
        raise ValueError("positions must be a finite M x 3 metre-coordinate array")
    bad = set(int(k) for k in bad_channels)
    healthy = np.array([k for k in range(len(p)) if k not in bad], dtype=int)
    count = int(count)
    if not 3 <= count <= len(healthy):
        raise ValueError("count must be between three and healthy-channel count")
    q = p[healthy]
    chosen = [int(np.argmax(np.sum((q - np.mean(q, axis=0)) ** 2, axis=1)))]
    nearest = np.full(len(q), np.inf)
    while len(chosen) < count:
        nearest = np.minimum(nearest, np.sum((q - q[chosen[-1]]) ** 2, axis=1))
        nearest[chosen] = -1.0
        chosen.append(int(np.argmax(nearest)))
    return np.sort(healthy[np.array(chosen, dtype=int)])


class InterferenceDOA:
    """Target-conditioned SRP on selected observed microphone spectra.

    Args:
        positions: full canonical microphone geometry, metres.
        selected_channels: canonical indices in the SAME order as mic_fft rows.
            If None, select_channels(..., count=min(24, healthy count)) is used.
        lock_physical_az: configured locked target bearing, NOT a current peak.

    process(mic_fft) accepts complex M x (n//2+1) spectra for one complete
    window.  It makes no FFTs or file access itself.  Returned azimuth_deg is
    None until stable evidence is available.  held_azimuth_deg is diagnostic
    and MUST NOT be used while valid is false.  Returned decisions are causal
    at window end and are to be used for output frame index + 1, not retrofilled.
    """

    def __init__(self, positions, selected_channels=None, lock_physical_az=0.0,
                 fs=48000, n=1024, hop=512):
        p = np.asarray(positions, dtype=float)
        if p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all():
            raise ValueError("positions must be finite M x 3 coordinates")
        self.fs, self.n, self.hop = int(fs), int(n), int(hop)
        if self.fs <= 0 or self.n < 16 or self.n % 2 or not 0 < self.hop <= self.n:
            raise ValueError("invalid sample rate/FFT/hop")
        self.lock = float(lock_physical_az)
        if not math.isfinite(self.lock) or abs(self.lock) > 90:
            raise ValueError("locked physical azimuth must lie in [-90,90]")
        if selected_channels is None:
            healthy_count = len(p) - len({k for k in (2, 3) if k < len(p)})
            selected_channels = select_channels(p, min(24, healthy_count))
        self.channels = np.asarray(selected_channels, dtype=int)
        if self.channels.ndim != 1 or len(self.channels) < 4:
            raise ValueError("at least four selected microphone indices required")
        if len(set(self.channels.tolist())) != len(self.channels):
            raise ValueError("selected microphone indices must be unique")
        if np.any(self.channels < 0) or np.any(self.channels >= len(p)):
            raise ValueError("selected microphone index outside geometry")
        if len(p) == 128 and any(k in (2, 3) for k in self.channels):
            raise ValueError("frozen 128-channel mask excludes channels 2 and 3")
        self.positions = p[self.channels].copy()
        hz = np.fft.rfftfreq(self.n, 1.0 / self.fs)
        lo, hi = PROFILE["analysis_band_hz"]
        self.bins = np.flatnonzero((hz >= lo) & (hz <= hi))
        if len(self.bins) < PROFILE["minimum_active_bins"]:
            raise ValueError("FFT has too few bins in fixed DOA analysis band")
        self.omega = 2 * np.pi * hz[self.bins]
        self.grid = np.arange(PROFILE["azimuth_min_deg"],
                              PROFILE["azimuth_max_deg"] + 0.1,
                              PROFILE["azimuth_step_deg"])
        self.allowed = np.array([outside_target(a, self.lock, PROFILE["target_exclusion_deg"]) for a in self.grid], dtype=bool)
        if not np.any(self.allowed):
            raise ValueError("no grid points outside locked-target exclusion")
        self._build_geometry()
        self.reset()

    def _steering(self, azimuth, distance=None):
        az = np.deg2rad(float(azimuth))
        u = np.array([np.sin(az), 0.0, np.cos(az)])
        if distance is None:
            # Relative arrival delay is -r dot u / c.
            delay = -(self.positions @ u) / 343.0
            amplitude = np.ones(len(self.positions))
        else:
            distance = float(distance)
            ranges = np.linalg.norm(distance * u - self.positions, axis=1)
            delay = (ranges - distance) / 343.0
            amplitude = distance / ranges
        return amplitude[None, :] * np.exp(-1j * self.omega[:, None] * delay)

    def _build_geometry(self):
        far = self._steering(self.lock)
        near = self._steering(self.lock, 3.0)
        da = PROFILE["target_tangent_half_step_deg"]
        tangent = self._steering(self.lock + da) - self._steering(self.lock - da)
        modes = np.stack([far, near, tangent], axis=-1)  # frequency, mic, mode
        self.target_u = np.zeros_like(modes)
        ranks = []
        for k, matrix in enumerate(modes):
            u, singular, _ = np.linalg.svd(matrix, full_matrices=False)
            rank = int(np.sum(singular > max(1e-12, singular[0] * 1e-9)))
            self.target_u[k, :, :rank] = u[:, :rank]
            ranks.append(rank)
        self.target_rank = np.asarray(ranks, dtype=int)
        dictionary = np.array([self._steering(a) for a in self.grid])
        coeff = np.einsum("fmr,dfm->dfr", np.conj(self.target_u), dictionary)
        dictionary -= np.einsum("fmr,dfr->dfm", self.target_u, coeff)
        power = np.sum(abs(dictionary) ** 2, axis=-1)
        self.dictionary = np.divide(dictionary, np.sqrt(power)[:, :, None],
                                    out=np.zeros_like(dictionary),
                                    where=power[:, :, None] > 1e-12)
        self.dictionary_valid = power > 1e-12

    def reset(self):
        self.frame = 0
        self._score = np.zeros(len(self.grid))
        self._residual_power = 0.0
        self._total_power = 0.0
        self._pending = None
        self._pending_count = 0
        self._angle = None
        self._misses = 0
        self._hold_frames = int(math.ceil(PROFILE["hold_seconds"] * self.fs / self.hop))
        self._last = None

    def process(self, mic_fft):
        x = np.asarray(mic_fft)
        expected = (len(self.channels), self.n // 2 + 1)
        if x.shape != expected or not np.iscomplexobj(x) or not np.isfinite(x).all():
            raise ValueError(f"mic_fft must be finite complex array of shape {expected}")
        x = np.asarray(x[:, self.bins].T, dtype=np.complex128)  # freq, mic
        coeff = np.einsum("fmr,fm->fr", np.conj(self.target_u), x)
        blocked = x - np.einsum("fmr,fr->fm", self.target_u, coeff)
        total_bin = np.sum(abs(x) ** 2, axis=1)
        residual_bin = np.sum(abs(blocked) ** 2, axis=1)
        matched = np.einsum("dfm,fm->df", np.conj(self.dictionary), blocked)
        coherence = np.divide(abs(matched) ** 2, residual_bin[None, :],
                              out=np.zeros_like(matched.real),
                              where=residual_bin[None, :] > 1e-24)
        coherence = np.clip(coherence, 0.0, 1.0)
        coherence[~self.dictionary_valid] = 0.0
        relative = PROFILE["active_bin_relative_power"]
        active = ((total_bin >= max(float(total_bin.max()) * relative, 1e-24)) &
                  (residual_bin >= max(float(residual_bin.max()) * relative, 1e-24)))
        active_count = int(active.sum())
        # Directional concentration downweights incoherent/noise-like bins.  It
        # uses only current observed spectra; it is not a source-activity gate.
        reliability = np.maximum(np.max(coherence[self.allowed], axis=0) -
                                 PROFILE["directional_reliability_floor"], 0.0)
        bin_weights = reliability * active
        if float(bin_weights.sum()) > 1e-24:
            instant = coherence @ bin_weights / bin_weights.sum()
        else:
            instant = np.zeros(len(self.grid))
        alpha = PROFILE["statistics_ema"] if self.frame else 0.0
        self._score = alpha * self._score + (1 - alpha) * instant
        self._total_power = alpha * self._total_power + (1 - alpha) * float(total_bin.sum())
        self._residual_power = alpha * self._residual_power + (1 - alpha) * float(residual_bin.sum())
        fraction = self._residual_power / max(self._total_power, 1e-24)
        allowed_score = np.where(self.allowed, self._score, -np.inf)
        winner = int(np.argmax(allowed_score))
        candidate = float(self.grid[winner])
        peak = float(allowed_score[winner])
        competitor_mask = self.allowed & (abs(self.grid - candidate) >
                                         PROFILE["outside_peak_neighbourhood_deg"])
        competitor = float(np.max(self._score[competitor_mask])) if np.any(competitor_mask) else 0.0
        margin = peak - competitor
        ratio = peak / max(competitor, 1e-12)
        per_bin_peak = np.argmax(np.where(self.allowed[:, None], coherence, -np.inf), axis=0)
        in_support = abs(self.grid[per_bin_peak] - candidate) <= PROFILE["outside_peak_neighbourhood_deg"]
        support = float(bin_weights[in_support].sum() / max(float(bin_weights.sum()), 1e-24))
        checks = {
            "active_bins": active_count >= PROFILE["minimum_active_bins"],
            "residual_power": fraction >= PROFILE["minimum_residual_power_fraction"],
            "peak_score": peak >= PROFILE["minimum_peak_score"],
            "peak_margin": margin >= PROFILE["minimum_peak_margin"],
            "peak_ratio": ratio >= PROFILE["minimum_peak_ratio"],
            "frequency_support": support >= PROFILE["minimum_support_fraction"],
        }
        evidence = bool(all(checks.values()))
        angle_changed = False
        crossing_fallback = False
        if evidence:
            if self._pending is not None and abs(candidate - self._pending) <= PROFILE["confirmation_tolerance_deg"]:
                self._pending_count += 1
            else:
                self._pending_count = 1
            self._pending = candidate
            if self._pending_count >= PROFILE["confirmation_frames"]:
                if self._angle is None:
                    self._angle = candidate
                    angle_changed = True
                else:
                    if not clear_short_arc(self._angle, candidate, self.lock, PROFILE["target_exclusion_deg"]):
                        # A full frame at unity/A precedes the new side. The next
                        # confirmed frame can enter it, with normal WOLA overlap.
                        self._angle = None
                        crossing_fallback = True
                    else:
                        change = np.clip(PROFILE["direction_smoothing"] * wrapped_delta(candidate, self._angle),
                                         -PROFILE["maximum_direction_step_deg"],
                                         PROFILE["maximum_direction_step_deg"])
                        angle_changed = abs(float(change)) >= 1e-12
                        self._angle = (self._angle + float(change) + 180.0) % 360.0 - 180.0
                self._misses = 0
            else:
                self._misses += 1
        else:
            self._pending = None
            self._pending_count = 0
            self._misses += 1
        valid = self._angle is not None and self._misses <= self._hold_frames
        if valid and not outside_target(self._angle, self.lock, PROFILE["target_exclusion_deg"]):
            raise RuntimeError("refusing to apply a direction inside target exclusion")
        state = {
            "frame": self.frame,
            "available_after_input_samples": self.frame * self.hop + self.n,
            "applies_from_output_frame": self.frame + 1,
            "apply_only_next_hop": True,
            "valid": bool(valid),
            "azimuth_deg": float(self._angle) if valid else None,
            "held_azimuth_deg": None if self._angle is None else float(self._angle),
            "elevation_deg": 0.0,
            "coordinate_convention": "physical array-centre-to-source bearing",
            "raw_candidate_azimuth_deg": candidate,
            "evidence_valid": evidence,
            "angle_changed": bool(angle_changed),
            "crossing_fallback": bool(crossing_fallback),
            "pending_confirmations": int(self._pending_count),
            "missed_frames": int(self._misses),
            "held_without_fresh_evidence": bool(valid and not evidence),
            "peak_score": peak,
            "outside_peak_score": competitor,
            "peak_margin": margin,
            "peak_ratio": ratio,
            "residual_power_fraction": float(fraction),
            "frequency_support_fraction": support,
            "active_bins": active_count,
            "checks": checks,
        }
        self._last = state
        self.frame += 1
        return state

    def metadata(self):
        return {
            "profile": dict(PROFILE),
            "selected_channels": self.channels.tolist(),
            "selected_positions_m": self.positions.tolist(),
            "analysis_bins": self.bins.tolist(),
            "target_subspace_rank_min": int(self.target_rank.min()),
            "target_subspace_rank_max": int(self.target_rank.max()),
            "locked_target_physical_azimuth_deg": self.lock,
            "no_scene_or_true_source_inputs": True,
            "scope": "horizontal offline prototype, no hardware/timing verification",
        }
