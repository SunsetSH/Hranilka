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

# 1. Берём существующий сертификат Hranilka или создаём новый (действителен 5 лет).
$subject = 'CN=Alexander Kondratyev, O=Hranilka'
$cert = Get-ChildItem Cert:\CurrentUser\My |
    Where-Object { $_.Subject -eq $subject -and $_.HasPrivateKey } |
    Sort-Object NotAfter -Descending | Select-Object -First 1

if (-not $cert) {
    Write-Host 'Создаю самоподписанный сертификат для подписи кода...'
    $cert = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject $subject `
        -KeyAlgorithm RSA -KeyLength 3072 `
        -HashAlgorithm SHA256 `
        -CertStoreLocation Cert:\CurrentUser\My `
        -NotAfter (Get-Date).AddYears(5)
}
Write-Host "Сертификат: $($cert.Thumbprint)"

# 2. Подпись с меткой времени (подпись остаётся валидной после истечения сертификата).
Set-AuthenticodeSignature -FilePath $exe -Certificate $cert `
    -HashAlgorithm SHA256 `
    -TimestampServer 'http://timestamp.digicert.com' | Format-List

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
