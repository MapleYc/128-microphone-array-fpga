# Source and attribution notice

This directory is a small, fixed-commit subset, not the complete TensorFlow or TFLM distribution. `UPSTREAM_SOURCE.txt` retains the pinned source revisions. This package contains only the source subset needed by the firmware; the broader reference corpus is not distributed here.

- TensorFlow Lite Micro / TensorFlow source and model examples: Copyright The TensorFlow Authors. Apache License 2.0 retained verbatim in `LICENSE`; original file notices remain intact.
- KissFFT v130: Copyright Mark Borgerding. Original BSD-style terms retained verbatim in `kissfft_v130/COPYING`. Its original files are retained unmodified. The official TFLM patch is retained separately and applied only to a build copy in `work/`.
- Audio fixtures are the exact WAV files published in the pinned official TFLM `micro_speech/testdata` directory. The pinned official `train/README.md` credits Google's Speech Commands dataset and states it is released under a CC BY license. This notice preserves that attribution; it does not relicense the audio as newly authored project data. No complete training dataset was downloaded, and the speaker identities/source recordings were not independently reconstructed.
- Historical precomputed yes/no features come from the separate pinned TensorFlow v2.5 source revision. They are classifier regression inputs, not exact golden output of the current audio preprocessor.

Local integer adapters and generated constant headers live outside this vendor directory in `firmware/kws/`. Generators record their source in comments and in the regression reports.
