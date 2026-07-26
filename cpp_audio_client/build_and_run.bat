@echo off
setlocal enabledelayedexpansion

echo ========================================================
echo  Building Pokonyan C++ GPU Audio Client (CUDA)
echo ========================================================

set "PATH=C:\Program Files\mingw64\bin;C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin;%PATH%"

set "VCVARS="
if exist "D:\Z-Visual Studio\Z-Visual Studio IDE\VC\Auxiliary\Build\vcvarsall.bat" (
    set "VCVARS=D:\Z-Visual Studio\Z-Visual Studio IDE\VC\Auxiliary\Build\vcvarsall.bat"
) else if exist "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat" (
    set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat"
)

if not "%VCVARS%"=="" (
    echo [Info] Initializing MSVC Environment...
    call "%VCVARS%" x64
)

if not exist "models" (
    mkdir models
)

if not exist "models\ggml-base.bin" (
    echo [Info] Downloading Whisper GGML Base model...
    curl -L -o "models\ggml-base.bin" "https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
)

if not exist "build" (
    mkdir build
)

echo [Info] Configuring CMake project with CUDA enabled...
cmake -B build -S . -G "Visual Studio 17 2022" -A x64 -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=89
if %ERRORLEVEL% neq 0 (
    echo [Error] CMake configuration failed!
    pause
    exit /b %ERRORLEVEL%
)

echo [Info] Compiling C++ executable...
cmake --build build --config Release
if %ERRORLEVEL% neq 0 (
    echo [Error] Compilation failed!
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ========================================================
echo  Starting Pokonyan C++ GPU Audio Client...
echo ========================================================

set "PI_HOST=100.80.242.72"
if not "%~1"=="" set "PI_HOST=%~1"

if exist "build\Release\cpp_audio_client.exe" (
    build\Release\cpp_audio_client.exe --pi-host %PI_HOST% -m models\ggml-base.bin -l en
) else if exist "build\cpp_audio_client.exe" (
    build\cpp_audio_client.exe --pi-host %PI_HOST% -m models\ggml-base.bin -l en
) else (
    echo [Error] Executable not found.
)

pause
