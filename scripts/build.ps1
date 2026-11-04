<#
    Сборка + подпись + проверка + контрольная сумма.

    Запуск (из корня проекта):
        powershell -ExecutionPolicy Bypass -File scripts\build.ps1 [-SkipSmoke] [-AllowNewCert]

    Шаги: smoke-тест ядра -> PyInstaller -> sign.ps1 -> проверка подписи ->
    SHA256-файл рядом с exe (dist\Hranilka.exe.sha256).
#>
param([switch]$SkipSmoke, [switch]$AllowNewCert)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root 'venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$exe = Join-Path $root 'dist\Hranilka.exe'

if (-not $SkipSmoke) {
    & $py scripts\smoke_test.py
    if ($LASTEXITCODE -ne 0) { throw 'Smoke-тест не пройден' }
}

# 1. Сборка (чистая).
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $py -m PyInstaller Hranilka.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершился с кодом $LASTEXITCODE" }
if (-not (Test-Path $exe)) { throw "Не найден $exe после сборки" }

# 2. Подпись.
& (Join-Path $PSScriptRoot 'sign.ps1') -AllowNewCert:$AllowNewCert

# 3. Проверка подписи. Самоподпись на «чужой» машине даёт UnknownError/
# NotTrusted — это допустимо; недопустимы отсутствие подписи и порча файла.
$sig = Get-AuthenticodeSignature $exe
Write-Host "Подпись: $($sig.Status) ($($sig.StatusMessage))"
if ($sig.Status -in 'NotSigned', 'HashMismatch', 'Incompatible') {
    throw "Подпись недействительна: $($sig.Status)"
}
if (-not $sig.SignerCertificate) { throw 'В подписи нет сертификата' }

# 4. SHA256 уже ПОДПИСАННОГО файла.
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
$sumFile = "$exe.sha256"
"$hash *Hranilka.exe" | Set-Content -Path $sumFile -Encoding ascii
Write-Host "SHA256: $hash"
Write-Host "Готово: $exe ; $sumFile"
