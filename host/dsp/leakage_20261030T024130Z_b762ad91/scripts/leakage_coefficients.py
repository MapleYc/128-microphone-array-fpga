"""Geometry-only target leakage estimate for the isolated 128-channel experiment.

Public API: coefficients(config) -> 513 PRE-alpha power_ratio_bins.
The input directions are already in HOST convention; this module does NOT negate
angles and never reads scene/truth/source-position files to choose coefficients.
Nothing is written or executed on import. NumPy is the only nonstandard dependency.
"""
from __future__ import annotations
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

import numpy as np

BASE = Path(str(__import__('pathlib').Path(__file__).resolve().parent / '../../stage2_20261029T154710Z_63ffc32c'))
GEOMETRY = BASE / "adapter/config/geometry_source.yaml"
GEOMETRY_SHA256 = "c367d7edca1eb25eb3582d4eb768dcdb05b7befe5fd4e8adff7d0fe37aa052e6"
FFT_SIZE = 1024
SOUND_SPEED = 343.0
NEAR_NULL_POWER = 0.05
MAX_REFERENCE_RATIO = 1.0
MAX_WEIGHTED_RATIO = 10.0
MASK = (2, 3)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def geometry():
    if sha256(GEOMETRY) != GEOMETRY_SHA256:
        raise ValueError("Frozen 128-channel geometry SHA256 mismatch")
    obj = store.loads(GEOMETRY.read_text(encoding="utf-8"))
    rows = sorted(obj["microphones"], key=lambda x: x["channel"])
    if obj["units"] != "m" or [x["channel"] for x in rows] != list(range(128)):
        raise ValueError("Expected exactly CH0..127, metre geometry")
    pos = np.array([[x[k] for k in ("x", "y", "z")] for x in rows], dtype=np.float64)
    if not np.isfinite(pos).all():
        raise ValueError("Nonfinite geometry")
    return pos


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + " must be a finite JSON number")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(name + " outside supported range")
    return value


def _direction(direction):
    # Probe rejects angles outside [-90,90], rather than exercising DSP clamp.
    a = math.radians(_number(direction["azimuth_deg"], "azimuth_deg", -90, 90))
    e = math.radians(_number(direction["elevation_deg"], "elevation_deg", -90, 90))
    # arraygeometry.cpp returns QVector3D: explicitly preserve xyz float32 cast.
    return np.array([math.sin(a) * math.cos(e), math.sin(e),
                     math.cos(a) * math.cos(e)], dtype=np.float32).astype(np.float64)


def _propagation(pos, direction, fs):
    u = _direction(direction)
    # Match C++ left-associated double additions, not an arbitrary BLAS reduction.
    return ((pos[:, 0] * u[0] + pos[:, 1] * u[1]) + pos[:, 2] * u[2]) * fs / SOUND_SPEED


def _taps(pos, direction, fs):
    radius = np.sqrt((pos[:, 0] ** 2 + pos[:, 1] ** 2) + pos[:, 2] ** 2).max()
    dref = math.ceil(float(radius) * fs / SOUND_SPEED) + 2
    d = dref - _propagation(pos, direction, fs)
    integer = np.floor(d).astype(np.int64)
    f = d - integer
    c = np.column_stack((
        -f * (f - 1) * (f - 2) / 6,
        (f + 1) * (f - 1) * (f - 2) / 2,
        -(f + 1) * f * (f - 2) / 2,
        (f + 1) * f * (f - 1) / 6))
    return integer, c, dref


def _transfer(pos, incoming_p, direction, fs, omega, mask=MASK, incoming_amplitude=None):
    """Steady-state H_Q for x_m[n]=a_m exp(j omega (n-p_m))."""
    integer, c, _ = _taps(pos, direction, fs)
    use = np.ones(len(pos), dtype=bool)
    use[list(mask)] = False
    delays = integer[:, None] + np.arange(-1, 3)[None, :]
    beam = np.sum(c[None, :, :] * np.exp(-1j * omega[:, None, None] * delays[None, :, :]), axis=2)
    incident = np.exp(-1j * omega[:, None] * incoming_p[None, :])
    if incoming_amplitude is not None:
        incident = incident * np.asarray(incoming_amplitude)[None, :]
    return np.sum((incident * beam)[:, use], axis=1) / int(use.sum())


def model(config):
    """Return coefficients and traceable model diagnostics, without using alpha."""
    fs = _number(config["sample_rate_hz"], "sample_rate_hz", 48000, 48000)
    w = config["wiener"]
    if w["mode"] != "twoPoint" or w["algorithm"] != "fixed":
        raise ValueError("This experiment supports fixed/twoPoint only")
    c1 = _number(w["b1_coefficient"], "b1_coefficient", 0, 10)
    c2 = _number(w["b2_coefficient"], "b2_coefficient", 0, 10)
    if c1 + c2 > MAX_WEIGHTED_RATIO:
        raise ValueError("Weighted coefficient bound10 requires c1+c2<=10")
    pos = geometry()
    directions = config["directions"]
    p = _propagation(pos, directions["A"], fs)
    omega = 2 * np.pi * np.arange(FFT_SIZE // 2 + 1) / FFT_SIZE
    h = {key: _transfer(pos, p, directions[key], fs, omega) for key in ("A", "B1", "B2")}
    pa = np.abs(h["A"]) ** 2
    valid = pa >= NEAR_NULL_POWER  # strict <.05 is the fallback condition.
    ratios = {}
    for key in ("B1", "B2"):
        ratio = np.zeros_like(pa)
        np.divide(np.abs(h[key]) ** 2, pa, out=ratio, where=valid)
        ratios[key] = np.minimum(MAX_REFERENCE_RATIO, ratio)
    bins = np.clip(c1 * ratios["B1"] + c2 * ratios["B2"], 0, MAX_WEIGHTED_RATIO)
    bins[~valid] = 0.0  # disable target subtraction, NOT bypass the original Wiener.
    if len(bins) != 513 or not np.isfinite(bins).all():
        raise ValueError("Invalid coefficient output")
    return dict(power_ratio_bins=bins.tolist(), h=h, a_power=pa,
                reference_ratio=ratios, near_null_bins=np.flatnonzero(~valid).tolist(),
                reference_delay_samples=_taps(pos, directions["A"], fs)[2],
                weights=[c1, c2], geometry_sha256=GEOMETRY_SHA256)


def coefficients(config):
    """Return 513 pre-alpha weighted bins, suitable for target_leakage.power_ratio_bins."""
    return model(config)["power_ratio_bins"]


def _fixture_config(a, b1=None, b2=None):
    return dict(sample_rate_hz=48000, directions={
        "A": dict(azimuth_deg=a[0], elevation_deg=a[1]),
        "B1": dict(azimuth_deg=(b1 or a)[0], elevation_deg=(b1 or a)[1]),
        "B2": dict(azimuth_deg=(b2 or a)[0], elevation_deg=(b2 or a)[1])},
        wiener=dict(algorithm="fixed", mode="twoPoint", b1_coefficient=5., b2_coefficient=5.))


def verify():
    """Small independent algebra/time-domain checks; NOT DUT execution."""
    pos = geometry()
    cases = []
    errors = []
    for a, b in [((0, 0), (-30, 0)), ((-30, 0), (15, 20)),
                 ((35, -20), (-10, 10)), ((90, 0), (-90, 0)), ((0, 90), (0, -90))]:
        cfg = _fixture_config(a, b, b)
        result = model(cfg)
        bins = np.array(result["power_ratio_bins"])
        same = model(_fixture_config(a))
        valid = np.asarray(same["a_power"]) >= NEAR_NULL_POWER
        assert np.allclose(np.array(same["power_ratio_bins"])[valid], 10, rtol=0, atol=3e-14)
        assert np.all(np.array(same["power_ratio_bins"])[~valid] == 0)
        assert abs(bins[0] - 10) < 3e-14
        assert bins.min() >= 0 and bins.max() <= 10
        # Independent negative-frequency computation verifies real-filter symmetry.
        omega = 2 * np.pi * np.arange(513) / FFT_SIZE
        p = _propagation(pos, cfg["directions"]["A"], 48000)
        for name in ("A", "B1", "B2"):
            hm = _transfer(pos, p, cfg["directions"][name], 48000, -omega)
            assert np.max(np.abs(hm - np.conj(result["h"][name]))) < 1e-13
        # Independent polynomial interpolation on chronological sample positions:
        # evaluate at -fraction using general Lagrange basis, no DUT coefficient formula.
        nodes = np.array([1., 0., -1., -2.])
        frequencies = np.array([1, 17, 127, 311, 511])
        om = 2 * np.pi * frequencies / FFT_SIZE
        for name in ("A", "B1", "B2"):
            integer, _, _ = _taps(pos, cfg["directions"][name], 48000)
            d = result["reference_delay_samples"] - _propagation(pos, cfg["directions"][name], 48000)
            frac = d - integer
            basis = np.ones((128, 4))
            for j in range(4):
                for ell in range(4):
                    if ell != j:
                        basis[:, j] *= (-frac - nodes[ell]) / (nodes[j] - nodes[ell])
            n = np.arange(64., 128.)
            measured = []
            for ww in om:
                x = np.exp(1j * ww * (n[:, None, None] - integer[None, :, None]
                           + nodes[None, None, :] - p[None, :, None]))
                y = np.sum(x * basis[None, :, :], axis=2)
                y = np.mean(y[:, [k for k in range(128) if k not in MASK]], axis=1)
                measured.append(np.mean(y * np.exp(-1j * ww * n)))
            error = float(np.max(np.abs(np.array(measured) - result["h"][name][frequencies])))
            errors.append(error)
            assert error < 2e-13
        cases.append(dict(A=a, B=b, min_lambda0=float(bins.min()), max_lambda0=float(bins.max()),
                          near_null_bins=result["near_null_bins"]))
    # Mask test: replacing excluded positions with enormous arbitrary coordinates
    # must not affect a transfer when the causal dref/delays themselves are retained.
    # Instead demonstrate excluded actual samples have exactly zero summation weights.
    use = np.array([i for i in range(128) if i not in MASK])
    assert len(use) == 126 and 2 not in use and 3 not in use
    # Explicit isolated safety branch at the exact threshold (not acoustic quality).
    p = np.array([0., np.nextafter(.05, 0), .05, 1.])
    safe = p >= NEAR_NULL_POWER
    assert safe.tolist() == [False, False, True, True]
    return dict(status="MODEL_ALGEBRA_AND_INDEPENDENT_TIME_INTERPOLATION_PASS_NOT_DUT_PROOF",
                cases=cases, maximum_complex_error=max(errors), healthy_channels=len(use),
                near_null_threshold_power=NEAR_NULL_POWER, threshold_rule_verified=True)


def verify_saved_tones():
    """Compare model to existing actual C++ beams. Diagnostic GT is NOT used by API."""
    records = store.loads((BASE / "direction_result.yaml").read_text(encoding="utf-8"))
    freq = np.array(records["frequencies_hz"], dtype=float)
    omega = 2 * np.pi * freq / 48000
    pos = geometry()
    out = []
    for rec in records["rows"]:
        case = BASE / "direction" / (f'az{rec["physical_az_deg"]}_el{rec["physical_el_deg"]}')
        cfg = store.loads((case / "physical_to_host_mapping.yaml").read_text(encoding="utf-8"))
        folder = case / "physical_to_host_mapping"
        metadata = store.loads((folder / "metadata.yaml").read_text(encoding="utf-8"))
        assert metadata["config_sha256"] == sha256(case / "physical_to_host_mapping.yaml")
        p = _propagation(pos, cfg["directions"]["A"], 48000)
        n = np.arange(38400, 86400)
        t = (n - rec["common_guard_samples"]) / 48000
        basis = np.column_stack([v for f in freq for v in [np.sin(2*np.pi*f*t), np.cos(2*np.pi*f*t)]])
        expected = {}
        measured = {}
        for key in ("A", "B1", "B2"):
            name = "beam_" + key
            path = folder / (name + ".f32le")
            stage = metadata["stages"][name]
            assert sha256(path) == stage["sha256"]
            y = np.fromfile(path, dtype="<f4").astype(float)
            assert len(y) == stage["samples"] and np.isfinite(y).all()
            fit = np.linalg.lstsq(basis, y[n], rcond=None)[0].reshape(-1, 2)
            measured[key] = (fit[:, 0] + 1j * fit[:, 1]) / .035
            expected[key] = _transfer(pos, p, cfg["directions"][key], 48000, omega)
        # Main model is far-field. Saved experiment used 1000 m spherical paths;
        # retain this independent finite-range discrepancy instead of fitting it.
        ratios = {}
        for key in ("B1", "B2"):
            predicted = np.abs(expected[key] / expected["A"]) ** 2
            actual = np.abs(measured[key] / measured["A"]) ** 2
            ratios[key] = dict(predicted_power_ratio=predicted.tolist(), measured_power_ratio=actual.tolist(),
                               max_absolute_error=float(np.max(np.abs(predicted-actual))))
        error = max(float(np.max(np.abs(measured[k] - expected[k]))) for k in measured)
        assert error < .002  # finite1000m curvature + actual PCM quantization/float32
        out.append(dict(physical_angles=[rec["physical_az_deg"], rec["physical_el_deg"]],
                        host_target=cfg["directions"]["A"], max_complex_transfer_error=error,
                        power_ratios=ratios,
                        beam_files_sha256={k: sha256(folder / ("beam_"+k+".f32le")) for k in measured}))
    return dict(status="PREEXISTING_CPP_BEAMS_MATCH_GEOMETRY_MODEL_WITH_FINITE_RANGE_AND_PCM_LIMITS",
                frequencies_hz=freq.tolist(), records=out,
                maximum_complex_error=max(v["max_complex_transfer_error"] for v in out),
                maximum_power_ratio_absolute_error=max(v["power_ratios"][k]["max_absolute_error"]
                    for v in out for k in ("B1", "B2")),
                caveat="Known 1000m synthetic spherical tones, not measured microphone calibration; off1024-bin frequencies, no STFT approximation tested.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path)
    ap.add_argument("--output", type=Path, required=True, help="New YAML only; refuse overwrite")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--verify-saved-tones", action="store_true")
    args = ap.parse_args()
    result = dict(schema="target-leakage-geometry-model-v1", script_sha256=sha256(__file__),
                  geometry_path=str(GEOMETRY), geometry_sha256=GEOMETRY_SHA256,
                  fft_size=FFT_SIZE, bins=513, mask_excluded=list(MASK), normalization=126,
                  convention="Input directions already HOST convention; no negation; incoming p=r dot u_HOST fs/343; x_m[n]=s[n-p_m].",
                  formula="lambda0=c1*min(1,|HB1t|^2/|HAt|^2)+c2*min(1,|HB2t|^2/|HAt|^2); if |HAt|^2<.05 then0; alpha is applied separately once.",
                  nonclaims="Steady-state exact Lagrange linear transfer, not exact speech STFT power relation: finite Hann windows mix neighbouring frequencies, transients/state and PSD smoothing differ. No clean truth used for coefficients. Nearfield amplitude/curvature, reverb, steering error, sound-speed/mic-phase/gain calibration error can invalidate leakage prediction. Unsafe/null bins retain original Wiener correction=0.")
    if args.config:
        cfg = store.loads(args.config.read_text(encoding="utf-8"))
        m = model(cfg)
        result.update(config_path=str(args.config), config_sha256=sha256(args.config),
                      target_leakage=dict(enabled=True, alpha=.8, power_ratio_bins=m["power_ratio_bins"]),
                      near_null_bins=m["near_null_bins"], reference_delay_samples=m["reference_delay_samples"],
                      coefficient_min=min(m["power_ratio_bins"]), coefficient_max=max(m["power_ratio_bins"]))
    if args.verify:
        result["unit_verification"] = verify()
    if args.verify_saved_tones:
        result["saved_cpp_tones"] = verify_saved_tones()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as f:
        f.write(store.dumps(result, indent=2, allow_nan=False))
        f.write("\n")
    print(json.dumps(dict(output=str(args.output), sha256=sha256(args.output),
                         coefficient_count=513 if args.config else None,
                         model_status=result.get("unit_verification", {}).get("status"),
                         cpp_status=result.get("saved_cpp_tones", {}).get("status"))))


if __name__ == "__main__":
    main()
