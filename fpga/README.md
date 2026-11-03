FPGA source project
===================

`Prj` contains the project configuration, application RTL, constraints, three IP
descriptors and CPU initialization HEX. Generated Pango implementations are
external dependencies: 79 vendor implementation files and two generated memory
initialization constant files are deliberately absent. Their exact names are in
`external_ip_files.txt`. The PDS source lists retain their real references, so
an incomplete project cannot silently compile against empty substitute modules.

Check the dependency boundary (Python 3, no PDS invocation):

    python tools/prepare_external_ip.py --project Prj --check

Exit 2 with the listed missing files is expected before restoration. This is a
local source preparation directory, not a claim that vendor IP is open source
or licensed for public redistribution. No blanket distribution permission was
found in the reviewed generated files. Required PDS primitives/libraries and
the tool license are external and must be obtained through an authorized PDS
installation. Retained IDF files are configuration; they do not grant rights
to the generated implementations. Third-party attribution is preserved.

Restore in a NEW workspace OUTSIDE this repository using a legally available
Pango Design Suite 2022.2-SP6.4 installation (Python package `psutil` is needed
only for the guarded generation command):

    python tools/prepare_external_ip.py --project Prj --generate --out C:/fpga_local/Prj --pds-bin C:/PDS/bin

Set the local tool license environment as required by your installation, or
pass `--license` with a local license path. The helper invokes only the official
`ip_generate.exe -i <copied-idf>` for the three copied descriptors. It first
checks device/geometry/HEX, temporarily supplies absolute initialization paths,
then restores relative paths after generation. It never overwrites `Prj` and
refuses a pre-existing output directory. Timeout and memory limits apply to
owned generator processes; another EDA worker blocks generation.

Use `--check` against the restored output. Then use that output's README and
`rebuild_sources.cmd` for synthesis, mapping, routing and timing. Generate a new
bitstream only after reviewing the new implementation and timing results.
The source package has no routed database or bitstream; no synthesis, PNR,
timing sign-off or board test is implied by a static dependency check or IP
generation. Changing HEX alone cannot update the embedded ROM/RAM constants:
official regeneration and a new implementation are required.

Restoration was tested in an isolated local workspace with the tool version
above: all three official IP-generation commands completed with zero errors;
all 81 restored implementation/initialization files matched the local reference
files byte for byte. This validates that dependency restoration path for those
configurations. It does not validate synthesis, routing, timing or hardware.
