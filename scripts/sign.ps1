<#
    Самоподписание Hranilka.exe.

    Запуск (из корня проекта):
        powershell -ExecutionPolicy Bypass -File scripts\sign.ps1

    ВНИМАНИЕ: самоподписанный сертификат снимает предупреждения Windows/SmartScreen
    ТОЛЬКО на машинах, где ваш сертификат добавлен в доверенные (см. блок «Экспорт»).
    Для чужих компьютеров он не убирает предупреждение «Неизвестный издатель».
    Для публичного релиза нужен сертификат от центра сертификации (OV/EV).
#>

$ErrorActionPreference = 'Stop'
$exe = Join-Path $PSScriptRoot '..\dist\Hranilka.exe'
if (-not (Test-Path $exe)) { throw "Не найден $exe. Сначала соберите: pyinstaller Hranilka.spec" }

# 1. Берём работоспособный сертификат Hranilka или создаём новый (5 лет).
# Старый сертификат может остаться в хранилище с битой привязкой к ключу
# (NTE_BAD_KEY_STATE). HasPrivateKey в этом случае ещё True, но подписать им
# невозможно, поэтому проверяем реальную операцию RSA до выбора.
$subject = 'CN=Alexander Kondratyev, O=Hranilka'
function Test-CodeSigningKey($candidate) {
    try {
        $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($candidate)
        if ($null -eq $rsa) { return $false }
        try {
            $data = [Text.Encoding]::UTF8.GetBytes('Hranilka code-signing key check')
            $null = $rsa.SignData($data,
                [System.Security.Cryptography.HashAlgorithmName]::SHA256,
                [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
            return $true
        } finally { $rsa.Dispose() }
    } catch { return $false }
}

$cert = Get-ChildItem Cert:\CurrentUser\My |
    Where-Object { $_.Subject -eq $subject -and $_.HasPrivateKey } |
    Sort-Object NotAfter -Descending |
    Where-Object { Test-CodeSigningKey $_ } |
    Select-Object -First 1

if (-not $cert) {
    Write-Host 'Нет работоспособного сертификата: создаю замену для подписи кода...'
    # Legacy CSP + KeySpec Signature совместимы и с Windows PowerShell, и с
    # SignTool. Это не заменяет старый сертификат в хранилище: его можно
    # оставить для проверки исторических файлов.
    $cert = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject $subject `
        -KeyAlgorithm RSA -KeyLength 3072 `
        -HashAlgorithm SHA256 `
        -KeySpec Signature `
        -Provider 'Microsoft Enhanced RSA and AES Cryptographic Provider' `
        -CertStoreLocation Cert:\CurrentUser\My `
        -NotAfter (Get-Date).AddYears(5)
}
Write-Host "Сертификат: $($cert.Thumbprint)"

# 2. SignTool надёжнее Set-AuthenticodeSignature для ключей из CurrentUser.
$signTool = Get-Command signtool.exe -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty Source -First 1
if (-not $signTool) {
    $signTool = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\bin' `
        -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
        Select-Object -ExpandProperty FullName -First 1
}
if (-not $signTool) { throw 'Не найден Microsoft SignTool (Windows SDK).' }

& $signTool sign /s My /sha1 $cert.Thumbprint /fd SHA256 `
    /tr 'http://timestamp.digicert.com' /td SHA256 $exe
if ($LASTEXITCODE -ne 0) { throw "SignTool завершился с кодом $LASTEXITCODE" }

# 3. Проверка.
Get-AuthenticodeSignature $exe | Format-List Status, StatusMessage, SignerCertificate

<#
    Экспорт сертификата, чтобы доверять подписи на ДРУГИХ своих машинах:

        # только публичная часть (.cer) — для установки в доверенные:
        Export-Certificate -Cert $cert -FilePath dist\Hranilka.cer

    На целевой машине (от админа) поставить в доверенные корневые и издатели:
        Import-Certificate -FilePath Hranilka.cer -CertStoreLocation Cert:\LocalMachine\Root
        Import-Certificate -FilePath Hranilka.cer -CertStoreLocation Cert:\LocalMachine\TrustedPublisher

    Резервная копия сертификата С ПРИВАТНЫМ ключом (.pfx, хранить в секрете!):
        $pwd = Read-Host -AsSecureString 'Пароль для .pfx'
        Export-PfxCertificate -Cert $cert -FilePath Hranilka.pfx -Password $pwd
#>
