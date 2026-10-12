@echo off
REM ============================================================
REM  Double-click to start Simple Accounting (Streamlit version)
REM  Keep this file in the SAME folder as streamlit_app.py,
REM  simple_accounting.py and requirements.txt
REM ============================================================
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)

echo Checking needed packages (the first time this needs internet)...
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 goto :fail

REM skip Streamlit's first-run e-mail question
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  mkdir "%USERPROFILE%\.streamlit" 2>nul
  (echo [general]& echo email = "") > "%USERPROFILE%\.streamlit\credentials.toml"
)

echo.
echo Starting... your browser will open. Keep this black window open while you work.
echo To stop the program, close this window.
%PY% -m streamlit run streamlit_app.py --server.address localhost --browser.gatherUsageStats false
pause
exit /b 0

:fail
echo.
echo Something went wrong while installing. Please take a screenshot of the messages above.
pause
exit /b 1
