@echo off
REM ==========================================================================
REM  run_solos_test.bat -- SOLOS -> SFIS automatic test flow
REM  Watch XML/PDF, check route, convert, upload, archive, then wait again.
REM ==========================================================================
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"
set "CONFIG=%ROOT%\solos_test.ini"
set "HELPER=%ROOT%\tools\wait_for_solos_pair.py"
set "PAIR_TMP=%TEMP%\solos_pair_%RANDOM%%RANDOM%.tmp"
set "CONV_TMP=%TEMP%\solos_conv_%RANDOM%%RANDOM%.tmp"
set "DEFERRED=%TEMP%\solos_deferred_%RANDOM%%RANDOM%.json"
set "BASELINE=%ROOT%\logs\solos_handled.txt"
set "ARG_ONCE="
set "ARG_DRY="
set "ARG_USE_EXISTING="
set "ARG_NOSCREEN="
set "ARG_NOSFIS="
set "ARG_DIR="
set "CYCLE_RC=0"

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--dir" (
	set "ARG_DIR=%~2"
	shift
	shift
	goto parse_args
)
if /i "%~1"=="--once" set "ARG_ONCE=1"
if /i "%~1"=="--dry-run" set "ARG_DRY=1"
if /i "%~1"=="--use-existing" set "ARG_USE_EXISTING=1"
if /i "%~1"=="--no-screen" set "ARG_NOSCREEN=1"
if /i "%~1"=="--no-sfis" set "ARG_NOSFIS=1"
if /i "%~1"=="-h" goto usage
if /i "%~1"=="--help" goto usage
if /i "%~1"=="/?" goto usage
shift
goto parse_args
:args_done

where python >nul 2>&1
if errorlevel 1 ( echo ERROR: python not found on PATH. & exit /b 90 )
if not exist "%HELPER%" ( echo ERROR: missing "%HELPER%". & exit /b 90 )
if not exist "%CONFIG%" ( echo ERROR: missing "%CONFIG%". & exit /b 90 )
for /f "usebackq tokens=1,* delims==" %%A in (`python "%HELPER%" --config "%CONFIG%" --print-config`) do set "%%A=%%B"

set "NO_SFIS=0"
if defined ARG_ONCE set "ONCE=1"
if defined ARG_DRY set "DRY_RUN=1"
if defined ARG_USE_EXISTING set "USE_EXISTING=1"
if defined ARG_NOSCREEN set "SCREEN_ENABLED=0"
if defined ARG_NOSFIS set "NO_SFIS=1"
if defined ARG_DIR set "WATCH_DIR=%ARG_DIR%"
if "%POLL_SECONDS%"=="" set "POLL_SECONDS=2"
if "%WAIT_TIMEOUT%"=="" set "WAIT_TIMEOUT=600"
if not exist "%CONVERTER%" ( echo ERROR: missing converter "%CONVERTER%". & exit /b 90 )
if not "%NO_SFIS%"=="1" if not exist "%SFIS_SCRIPT%" ( echo ERROR: missing RESTSFIS script "%SFIS_SCRIPT%". & exit /b 90 )
if "%SCREEN_ENABLED%"=="1" if not exist "%SCREEN_EXE%" (
	echo WARNING: Screen-diag not found at "%SCREEN_EXE%"; continuing without screens.
	set "SCREEN_ENABLED=0"
)
set "DRYARG="
if "%DRY_RUN%"=="1" set "DRYARG=--dry-run"
set "OPARG="
if not "%OPERATOR%"=="" set "OPARG=-op %OPERATOR%"
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"`) do set "STAMP=%%T"
set "LOG=%ROOT%\logs\solos_test_%STAMP%.log"
>> "%LOG%" echo ==== run_solos_test start %date% %time% ====
>> "%LOG%" echo watch=%WATCH_DIR% once=%ONCE% dry_run=%DRY_RUN% use_existing=%USE_EXISTING%
echo ============================================================
echo  SOLOS -^> SFIS test flow
echo  watch : %WATCH_DIR%
echo  log   : %LOG%
echo  mode  : once=%ONCE% dry_run=%DRY_RUN% use_existing=%USE_EXISTING%
echo ============================================================
if "%USE_EXISTING%"=="1" goto RUN
python "%HELPER%" --config "%CONFIG%" --dir "%WATCH_DIR%" --baseline "%BASELINE%" --snapshot-baseline >nul 2>> "%LOG%"
if errorlevel 1 goto CFGERR
>> "%LOG%" echo [%time%] baseline snapshot written to %BASELINE%

:RUN
set "CYCLE_RC=0"
set "CSV_READY=0"
set "XML="
set "PDF="
echo Waiting for SOLOS XML/PDF ...
set BASELINEARG=--baseline "%BASELINE%"
if "%USE_EXISTING%"=="1" set "BASELINEARG=--use-existing"
>> "%LOG%" echo [%time%] waiting for SOLOS output ...
call :show_waiting
python "%HELPER%" --config "%CONFIG%" --dir "%WATCH_DIR%" %BASELINEARG% --deferred "%DEFERRED%" --poll "%POLL_SECONDS%" --timeout "%WAIT_TIMEOUT%" > "%PAIR_TMP%" 2>> "%LOG%"
set "HRC=%ERRORLEVEL%"
if "%HRC%"=="0" goto FOUND
if not "%HRC%"=="1" goto CFGERR
echo ERROR: timed out waiting for a complete SOLOS pair.
set "CYCLE_RC=93"
goto CYCLE_DONE

:CFGERR
echo ERROR: wait_for_solos_pair configuration error.
>> "%LOG%" echo ERROR: config error
set "CYCLE_RC=92"
goto CYCLE_DONE

:FOUND
set "SN="
set "OUT_CSV="
for /f "usebackq tokens=1,* delims==" %%A in ("%PAIR_TMP%") do set "%%A=%%B"
if "%SN%"=="" ( echo ERROR: could not resolve serial number. & set "CYCLE_RC=91" & goto CYCLE_DONE )
for %%F in ("%XML%") do set "XMLNAME=%%~nxF"
>> "%LOG%" echo [%time%] found SN=%SN% xml=%XML%
echo.
echo ------------------------------------------------------------
echo  Found pair for SN : %SN%
echo  XML : %XML%
echo  PDF : %PDF%
echo ------------------------------------------------------------
python "%ROOT%\solos_xml_to_pcbafct.py" --check-serial "%SN%" > "%CONV_TMP%" 2>&1
set "SRC=%ERRORLEVEL%"
type "%CONV_TMP%"
type "%CONV_TMP%" >> "%LOG%"
if not "%SRC%"=="0" goto SERIAL_REJECTED
if "%NO_SFIS%"=="1" goto NO_SFIS_C
set /a CR_TRY=0
:ROUTE_RETRY
>> "%LOG%" echo [%time%] /C -sn %SN% %DRYARG%
python "%SFIS_SCRIPT%" /C -sn "%SN%" --config "%SFIS_INI%" %DRYARG%
set "RC=%ERRORLEVEL%"
>> "%LOG%" echo [%time%] /C exit=%RC%
if "%RC%"=="0" goto ROUTE_OK
if "%RC%"=="10" goto ROUTE_REJECTED
if "%RC%"=="2" goto ROUTE_USAGE
set /a CR_TRY+=1
if %CR_TRY% LEQ %RETRY% (
	>> "%LOG%" echo [%time%] /C retry %CR_TRY%/%RETRY%
	call :sleep 2
	goto ROUTE_RETRY
)
goto ROUTE_FAIL

:SERIAL_REJECTED
echo ERROR: serial number HW ID check failed.
>> "%LOG%" echo [%time%] serial check failed exit=%SRC% SN=%SN%
set "CYCLE_RC=72"
call :msg_box "SOLOS serial number rejected.<br><br>SN: %SN%<br>HW ID 1C / 1G is not supported.<br><br>Press [Enter] to continue." 0x882222 enter
goto ARCHIVE_PAIR
:ROUTE_REJECTED
set "CYCLE_RC=80"
call :msg_box "SFIS Error - Check Route Failure !!<br><br>SN: %SN%<br>Please check the DUT route status.<br><br>Press [Enter] to continue." 0x882222 enter
goto ARCHIVE_PAIR
:ROUTE_USAGE
echo ERROR: RESTSFIS-diag usage/config error.
>> "%LOG%" echo ERROR: /C usage error
set "CYCLE_RC=83"
goto ARCHIVE_PAIR
:ROUTE_FAIL
set "CYCLE_RC=81"
call :msg_box "SFIS Error - Check Route HTTP failure (exit %RC%) !!<br><br>SN: %SN%<br>Please check SFIS connectivity.<br><br>Press [Enter] to continue." 0x882222 enter
goto ARCHIVE_PAIR
:NO_SFIS_C
echo  [--no-sfis] SFIS route check /C skipped.
>> "%LOG%" echo [%time%] --no-sfis: skip /C
:ROUTE_OK
>> "%LOG%" echo [%time%] route check complete
call :show_testing
>> "%LOG%" echo [%time%] converting %XML%
python "%CONVERTER%" "%XML%" -o "%OUT_CSV%" > "%CONV_TMP%" 2>&1
set "CRC=%ERRORLEVEL%"
type "%CONV_TMP%"
type "%CONV_TMP%" >> "%LOG%"
if not "%CRC%"=="0" goto CONVERT_FAIL
set "CSV_READY=1"
set "TESTPASS=0"
findstr /C:"FAIL=0," "%CONV_TMP%" >nul
if not errorlevel 1 set "TESTPASS=1"
>> "%LOG%" echo [%time%] convert exit=%CRC% testpass=%TESTPASS%
if "%NO_SFIS%"=="1" goto NO_SFIS_UP
set /a UP_TRY=0
:UP_RETRY
>> "%LOG%" echo [%time%] /UP -log "%OUT_CSV%" %OPARG% %DRYARG%
python "%SFIS_SCRIPT%" /UP -log "%OUT_CSV%" --config "%SFIS_INI%" %OPARG% %DRYARG%
set "URC=%ERRORLEVEL%"
>> "%LOG%" echo [%time%] /UP exit=%URC%
if "%URC%"=="0" goto RESULT
if "%URC%"=="2" goto UPLOAD_USAGE
set /a UP_TRY+=1
if %UP_TRY% LEQ %RETRY% (
	>> "%LOG%" echo [%time%] /UP retry %UP_TRY%/%RETRY%
	call :sleep 2
	goto UP_RETRY
)
goto UPLOAD_FAIL
:UPLOAD_USAGE
echo ERROR: RESTSFIS-diag usage/config error.
>> "%LOG%" echo ERROR: /UP usage error
set "CYCLE_RC=63"
goto ARCHIVE_PAIR
:UPLOAD_FAIL
set "CYCLE_RC=61"
call :msg_box "SFIS Upload FAIL (exit %URC%) !!<br><br>SN: %SN%<br>Please check SFIS!<br><br>Press [Enter] to continue." 0xBB2222 enter
goto ARCHIVE_PAIR
:NO_SFIS_UP
echo  [--no-sfis] SFIS upload /UP skipped.
>> "%LOG%" echo [%time%] --no-sfis: skip /UP
goto RESULT
:CONVERT_FAIL
echo ERROR: SOLOS conversion failed (exit %CRC%).
set "CYCLE_RC=70"
call :msg_box "SOLOS conversion FAILED (exit %CRC%).<br><br>XML: %XML%<br>Press [Enter] to continue." 0x882222 enter
goto ARCHIVE_PAIR

:RESULT
set "CYCLE_RC=0"
:ARCHIVE_PAIR
>> "%LOG%" echo [%time%] cycle exit=%CYCLE_RC% - archiving source pair
set "CSVARG="
if "%CSV_READY%"=="1" set "CSVARG=--csv "%OUT_CSV%""
python "%HELPER%" --config "%CONFIG%" --archive-pair "%XML%" "%PDF%" %CSVARG% > "%PAIR_TMP%" 2>> "%LOG%"
if errorlevel 1 (
	echo ERROR: could not archive SOLOS files. Check "%LOG%".
	set "CYCLE_RC=71"
	python "%HELPER%" --deferred "%DEFERRED%" --defer-pair "%XML%" "%PDF%" 2>> "%LOG%"
	goto CYCLE_DONE
)
type "%PAIR_TMP%"
type "%PAIR_TMP%" >> "%LOG%"
>> "%BASELINE%" echo %XMLNAME%
if not "%CYCLE_RC%"=="0" goto CYCLE_DONE
if "%TESTPASS%"=="1" goto SHOW_PASS
>> "%LOG%" echo [%time%] result FAIL -^> UILogResult-auto
if "%SCREEN_ENABLED%"=="1" taskkill /IM Screen-diag.exe >nul 2>&1
if "%SCREEN_ENABLED%"=="1" if exist "%UILOGRESULT_EXE%" "%UILOGRESULT_EXE%" -log "%OUT_CSV%" /F
goto CYCLE_DONE
:SHOW_PASS
>> "%LOG%" echo [%time%] result PASS
if "%SCREEN_ENABLED%"=="1" taskkill /IM Screen-diag.exe >nul 2>&1
if "%SCREEN_ENABLED%"=="1" if exist "%SCREEN_EXE%" "%SCREEN_EXE%" -nl -enter /SS 200 "%PASS_MESSAGE%" 0xFFFFFF -bg 0x008800
:CYCLE_DONE
>> "%LOG%" echo [%time%] cycle complete exit=%CYCLE_RC%
if "%SCREEN_ENABLED%"=="1" taskkill /IM Screen-diag.exe >nul 2>&1
del /q "%PAIR_TMP%" "%CONV_TMP%" >nul 2>&1
if "%ONCE%"=="1" (
	>> "%LOG%" echo [%time%] single cycle complete
	echo  Cycle complete.
	del /q "%DEFERRED%" >nul 2>&1
	exit /b %CYCLE_RC%
)
>> "%LOG%" echo [%time%] returning to wait for XML
call :sleep 1
goto RUN

:sleep
set /a _n=%~1+1
ping -n %_n% 127.0.0.1 >nul
exit /b 0
:show_waiting
if not "%SCREEN_ENABLED%"=="1" exit /b 0
if not exist "%SCREEN_EXE%" exit /b 0
taskkill /IM Screen-diag.exe >nul 2>&1
start "" "%SCREEN_EXE%" -nl /SS 55 "%WAIT_MESSAGE%" 0xFFFFFF -bg 0x223366
exit /b 0
:show_testing
if not "%SCREEN_ENABLED%"=="1" exit /b 0
if not exist "%SCREEN_EXE%" exit /b 0
taskkill /IM Screen-diag.exe >nul 2>&1
start "" "%SCREEN_EXE%" -nl /SS 55 "Converting SOLOS data ..." 0xFFFFFF -bg 0x223366
exit /b 0
:msg_box
if not "%SCREEN_ENABLED%"=="1" exit /b 0
if not exist "%SCREEN_EXE%" exit /b 0
taskkill /IM Screen-diag.exe >nul 2>&1
if /i "%~3"=="enter" (
	"%SCREEN_EXE%" -nl -enter /SS 55 "%~1" 0xFFFFFF -bg %~2
) else (
	start "" "%SCREEN_EXE%" -nl /SS 55 "%~1" 0xFFFFFF -bg %~2
)
exit /b 0
:usage
echo Usage: run_solos_test.bat [--once] [--dry-run] [--use-existing] [--no-screen] [--no-sfis] [--dir PATH]
echo.
echo   --once           Run a single cycle then exit (default: keep looping).
echo   --dry-run        Pass --dry-run to RESTSFIS-diag (no network traffic).
echo   --use-existing   Accept a SOLOS pair that already exists in the folder.
echo   --no-screen      Do not launch Screen-diag (headless / testing).
echo   --no-sfis        Skip the SFIS /C and /UP steps (offline test).
echo   --dir PATH       Override the watched folder for this run.
echo.
echo Config: solos_test.ini
exit /b 0
