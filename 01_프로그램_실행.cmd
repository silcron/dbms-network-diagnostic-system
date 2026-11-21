@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0" || exit /b 1
for %%P in (
  "%USERPROFILE%\AppData\Local\Python\pythoncore-3.14-64\python.exe"
  "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
  "%USERPROFILE%\AppData\Local\Programs\Python\Python314\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
  "%ProgramFiles%\Python314\python.exe"
) do if exist "%%~P" (
  "%%~P" -c "import sys;sys.exit(0 if sys.version_info >= (3,14,3) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "DEMO_PYTHON=%%~P"
    goto direct
  )
)
py -3.14 -c "import sys;sys.exit(0 if sys.version_info >= (3,14,3) else 1)" >nul 2>&1
if not errorlevel 1 goto py314
py -3 -c "import sys;sys.exit(0 if sys.version_info >= (3,14,3) else 1)" >nul 2>&1
if not errorlevel 1 goto py3
python -c "import sys;sys.exit(0 if sys.version_info >= (3,14,3) else 1)" >nul 2>&1
if not errorlevel 1 goto python
echo Python 3.14.3 이상이 필요합니다.
echo README.md의 Python 준비 안내를 확인해 주세요.
echo https://www.python.org/downloads/windows/
popd
if "%~1"=="--self-test" exit /b 2
pause
exit /b 2
:direct
"%DEMO_PYTHON%" -B start_demo.py %*
set "runExit=%errorlevel%"
goto finished
:py314
py -3.14 -B start_demo.py %*
set "runExit=%errorlevel%"
goto finished
:py3
py -3 -B start_demo.py %*
set "runExit=%errorlevel%"
goto finished
:python
python -B start_demo.py %*
set "runExit=%errorlevel%"
:finished
popd
if not "%runExit%"=="0" if not "%~1"=="--self-test" pause
exit /b %runExit%
