@echo off
REM Refresh PriceCatcher data on a Windows PC.
REM Double-click to run, or schedule it with Task Scheduler (see README).
REM Needs Python 3.10+ from python.org (the "py" launcher).

setlocal
cd /d "%~dp0.."
if not exist logs mkdir logs
set LOG=logs\refresh.log

echo ==== %DATE% %TIME% ==== >> "%LOG%"

py -m pip install --quiet -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 goto fail

py etl\fetch_pricecatcher.py --months 12 >> "%LOG%" 2>&1
if errorlevel 1 echo Some downloads failed. Building with what we have. >> "%LOG%"

py etl\build_dataset.py --months 12 >> "%LOG%" 2>&1
if errorlevel 1 goto fail

REM Optional: publish. Remove the REM on the next 3 lines once this folder is its own git repo.
REM git add web/data
REM git commit -m "Data refresh %DATE%"
REM git push

echo Done. See %LOG%

exit /b 0

:fail
echo Refresh FAILED. See %LOG%
exit /b 1
