@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b 1
if not defined PDS_SHELL set "PDS_SHELL=pds_shell.exe"
if not exist "build_from_source\map" mkdir "build_from_source\map"
if not exist "build_from_source\route" mkdir "build_from_source\route"
"%PDS_SHELL%" -file build_map.tcl -work_dir build_from_source\map -project_name ArrayMap
if errorlevel 1 exit /b 1
if not exist "build_from_source\map\device_map\meeting_av_mini200_map.adf" (
  echo MAP database is missing. Review the native logs.
  exit /b 1
)
if not exist "build_from_source\map\device_map\meeting_av_mini200.pcf" (
  echo MAP constraint output is missing. Review the native logs.
  exit /b 1
)
"%PDS_SHELL%" -file route_sta_new.tcl -work_dir build_from_source\route -project_name ArrayRoute
if errorlevel 1 exit /b 1
if not exist "build_from_source\route\place_route\meeting_av_mini200_pnr.adf" (
  echo Routed database is missing. Review the native logs.
  exit /b 1
)
echo PNR and timing-report commands returned. This is not a timing acceptance result.
echo Review all native logs and timing reports before running generate_new_bit.tcl.
exit /b 0
