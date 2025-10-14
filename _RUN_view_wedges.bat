@echo off
REM Get the directory of the current .bat file
set "SCRIPT_DIR=%~dp0"

REM Activate the virtual environment (relative to the script directory)
call "%SCRIPT_DIR%.venv\Scripts\activate.bat"

REM Check if the activation was successful
if "%VIRTUAL_ENV%"=="" (
    echo Failed to activate virtual environment.
    exit /b 1
)

REM Run the Python script (relative to the script directory)
python "%SCRIPT_DIR%wedge_tool\view_wedges.py"

REM Deactivate the virtual environment (optional, but good practice)
deactivate
