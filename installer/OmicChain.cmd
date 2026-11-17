@echo off
setlocal
call "%~dp0omicchain-env.cmd"
title OmicChain %OC_VERSION%
if not exist "%OMICCHAIN_DATA%" mkdir "%OMICCHAIN_DATA%"
robocopy "%OC_HOME%\app" "%OMICCHAIN_DATA%" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo The files of OmicChain could not be copied to %OMICCHAIN_DATA%.
  pause
  exit /b 1
)
cd /d "%OMICCHAIN_DATA%"
echo.
echo   OmicChain %OC_VERSION%
echo   Your analyses are in %OMICCHAIN_DATA%
echo.
echo   OmicChain opens in your browser at http://localhost:8501
echo   Keep this window open while you use it. Close it to stop OmicChain.
echo.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 8; Start-Process 'http://localhost:8501'"
"%OC_HOME%\python\python.exe" -m streamlit run OmicChain.py --server.headless true --browser.gatherUsageStats false
echo.
echo OmicChain has stopped.
pause
