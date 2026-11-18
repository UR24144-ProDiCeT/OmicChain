@echo off
rem Environment of the installed OmicChain: its own Python, R and R packages, isolated from any
rem other Python or R installation of the computer.
set "OC_HOME=%~dp0"
if "%OC_HOME:~-1%"=="\" set "OC_HOME=%OC_HOME:~0,-1%"
set /p OC_VERSION=<"%OC_HOME%\VERSION.txt"
set "PATH=%OC_HOME%\python;%OC_HOME%\python\Scripts;%OC_HOME%\R\bin;%PATH%"
set "R_LIBS=%OC_HOME%\library"
set "R_LIBS_USER=%OC_HOME%\library"
set "R_LIBS_SITE=%OC_HOME%\library"
set "R_PROFILE_USER=%OC_HOME%\empty.Rprofile"
set "R_ENVIRON_USER=%OC_HOME%\empty.Rprofile"
set "RENV_CONFIG_AUTOLOADER_ENABLED=false"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "OMICCHAIN_DISTRIBUTION=OmicChain Windows installer %OC_VERSION%"
if not defined OMICCHAIN_DATA set "OMICCHAIN_DATA=%USERPROFILE%\OmicChain"
if not defined OMICCHAIN_REF_DIR set "OMICCHAIN_REF_DIR=%OMICCHAIN_DATA%\references"
