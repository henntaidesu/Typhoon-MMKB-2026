# =====================================================================
#  install_pgvector.ps1  —  在数据库服务器 10.0.10.20 上运行
#  作用: 自动加载 MSVC 编译环境 -> 下载 pgvector 源码 -> 编译并安装到 PG18
#
#  用法(在 10.0.10.20 上, 管理员 PowerShell):
#     powershell -ExecutionPolicy Bypass -File .\install_pgvector.ps1
#  安装完成后回到开发机执行:  python backend/init_db.py
# =====================================================================
$ErrorActionPreference = "Stop"
$PGVER = "v0.8.0"

function Fail($msg) { Write-Host "`n[FAIL] $msg" -ForegroundColor Red; exit 1 }
function Info($msg) { Write-Host "[*] $msg" -ForegroundColor Cyan }

# --- 1. 定位 PostgreSQL 18 -------------------------------------------------
$PGROOT = "C:\Program Files\PostgreSQL\18"
if (-not (Test-Path $PGROOT)) {
    $found = Get-ChildItem "C:\Program Files\PostgreSQL" -Directory -ErrorAction SilentlyContinue |
             Sort-Object Name -Descending | Select-Object -First 1
    if ($found) { $PGROOT = $found.FullName } else { Fail "找不到 PostgreSQL 安装目录 (C:\Program Files\PostgreSQL\18)" }
}
$env:PGROOT = $PGROOT
Info "PGROOT = $PGROOT"
if (-not (Test-Path "$PGROOT\include\server\postgres.h")) {
    Fail "缺少 PG 开发头文件 ($PGROOT\include\server). 安装 PG 时需勾选开发组件, 或用 EDB 安装包重装。"
}

# --- 2. 定位并加载 MSVC (vcvars64) ----------------------------------------
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    Write-Host "`n未检测到 Visual Studio Build Tools。请先安装 C++ 生成工具:" -ForegroundColor Yellow
    Write-Host '  winget install Microsoft.VisualStudio.2022.BuildTools --override "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --quiet"' -ForegroundColor Yellow
    Write-Host "安装后重新运行本脚本。" -ForegroundColor Yellow
    Fail "缺少 Visual Studio Build Tools"
}
$vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsPath) { Fail "已装 VS 但缺少 C++ 组件 (VC.Tools.x86.x64)。用 VS Installer 添加 '使用 C++ 的桌面开发'。" }
$vcvars = Join-Path $vsPath "VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path $vcvars)) { Fail "找不到 vcvars64.bat: $vcvars" }
Info "加载 MSVC 环境: $vcvars"
cmd /c "`"$vcvars`" >nul 2>&1 && set" | ForEach-Object {
    if ($_ -match '^(.*?)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] }
}
if (-not (Get-Command nmake -ErrorAction SilentlyContinue)) { Fail "加载后仍找不到 nmake" }
Info "nmake OK"

# --- 3. 获取 pgvector 源码 -------------------------------------------------
$work = Join-Path $env:TEMP "pgvector_build"
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory -Path $work | Out-Null
Set-Location $work

if (Get-Command git -ErrorAction SilentlyContinue) {
    Info "git clone pgvector $PGVER"
    git clone --branch $PGVER --depth 1 https://github.com/pgvector/pgvector.git src
} else {
    Info "无 git, 改用 zip 下载"
    $zip = Join-Path $work "pgvector.zip"
    Invoke-WebRequest "https://github.com/pgvector/pgvector/archive/refs/tags/$PGVER.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath $work
    Rename-Item (Get-ChildItem $work -Directory -Filter "pgvector-*" | Select-Object -First 1).FullName "src"
}
Set-Location (Join-Path $work "src")

# --- 4. 编译 + 安装 --------------------------------------------------------
Info "nmake /F Makefile.win"
nmake /F Makefile.win
if ($LASTEXITCODE -ne 0) { Fail "编译失败 (见上方输出)" }
Info "nmake /F Makefile.win install"
nmake /F Makefile.win install
if ($LASTEXITCODE -ne 0) { Fail "安装失败 (需要管理员权限写入 $PGROOT)" }

# --- 5. 校验 ---------------------------------------------------------------
if (Test-Path "$PGROOT\lib\vector.dll") {
    Write-Host "`n[OK] vector.dll 已安装到 $PGROOT\lib" -ForegroundColor Green
    Write-Host "现在到开发机运行:  python backend/check_db.py  然后  python backend/init_db.py" -ForegroundColor Green
} else {
    Fail "未找到 $PGROOT\lib\vector.dll, 安装可能未成功"
}
