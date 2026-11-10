param(
    [string]$SdkPackage,
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'bin'),
    [ValidateRange(1, 600)][int]$DownloadTimeoutSeconds = 120
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.Net.Http
$sdkVersion = '1.0.4191.47'
$sdkSha256 = 'F492BBF547D0DA329553B6727435B677579B1E9F91CC9E4A1AD029366D5F23D0'
$sdkUrl = "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$sdkVersion/microsoft.web.webview2.$sdkVersion.nupkg"
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$buildRoot = Join-Path $tempRoot ('fuuko-build-' + [Guid]::NewGuid().ToString('N'))
$output = [IO.Path]::GetFullPath($OutputDirectory)
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw '.NET Framework C# compiler is required.' }
New-Item -ItemType Directory -Path $buildRoot | Out-Null
try {
    if ($SdkPackage) {
        $package = (Resolve-Path -LiteralPath $SdkPackage).Path
    } else {
        $package = Join-Path $buildRoot 'webview2.nupkg'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $client = New-Object Net.Http.HttpClient
        $client.Timeout = [TimeSpan]::FromSeconds($DownloadTimeoutSeconds)
        try {
            $download = $client.GetByteArrayAsync($sdkUrl)
            if (-not $download.Wait(($DownloadTimeoutSeconds + 5) * 1000)) { $client.CancelPendingRequests(); throw 'SDK download timed out.' }
            [IO.File]::WriteAllBytes($package, $download.GetAwaiter().GetResult())
        } catch {
            throw ('SDK download failed or timed out. Retry or use -SdkPackage. ' + $_.Exception.GetBaseException().Message)
        } finally { $client.Dispose() }
    }
    $hashStream = [IO.File]::OpenRead($package)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $actualHash = [BitConverter]::ToString($hasher.ComputeHash($hashStream)).Replace('-', '') }
    finally { $hasher.Dispose(); $hashStream.Dispose() }
    if ($actualHash -ne $sdkSha256) { throw 'WebView2 SDK SHA-256 mismatch.' }
    $unpacked = Join-Path $buildRoot 'sdk'
    [IO.Compression.ZipFile]::ExtractToDirectory($package, $unpacked)
    $stage = Join-Path $buildRoot 'bin'
    New-Item -ItemType Directory -Path $stage | Out-Null
    foreach ($relative in @('lib\net462\Microsoft.Web.WebView2.Core.dll', 'lib\net462\Microsoft.Web.WebView2.WinForms.dll', 'runtimes\win-x64\native\WebView2Loader.dll')) {
        Copy-Item -LiteralPath (Join-Path $unpacked $relative) -Destination $stage
    }
    $license = Get-ChildItem -LiteralPath $unpacked -Filter 'LICENSE*' -File | Select-Object -First 1
    if (-not $license) { throw 'SDK license file is missing.' }
    Copy-Item -LiteralPath $license.FullName -Destination (Join-Path $stage 'WebView2-LICENSE.txt')
    $icon = Join-Path (Split-Path $PSScriptRoot) 'assets\fuuko.ico'
    & $compiler /nologo /target:winexe /platform:x64 /optimize+ /utf8output /codepage:65001 `
        "/out:$(Join-Path $stage 'FuukoHost.exe')" "/win32icon:$icon" "/win32manifest:$(Join-Path $PSScriptRoot 'app.manifest')" `
        /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Web.Extensions.dll `
        "/r:$(Join-Path $stage 'Microsoft.Web.WebView2.Core.dll')" "/r:$(Join-Path $stage 'Microsoft.Web.WebView2.WinForms.dll')" `
        (Join-Path $PSScriptRoot 'FuukoHost.cs')
    if ($LASTEXITCODE -ne 0) { throw "Compilation failed ($LASTEXITCODE)." }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'FuukoHost.exe.config') -Destination $stage
    New-Item -ItemType Directory -Force -Path $output | Out-Null
    Get-ChildItem -LiteralPath $stage -File | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $output -Force }
    Write-Output "Built FuukoHost: $output"
} finally {
    $resolvedBuild = [IO.Path]::GetFullPath($buildRoot)
    if (-not $resolvedBuild.StartsWith($tempRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($resolvedBuild) -notmatch '^fuuko-build-[a-f0-9]{32}$') {
        throw 'Refusing cleanup outside the allocated build directory.'
    }
    if (Test-Path -LiteralPath $resolvedBuild) { Remove-Item -LiteralPath $resolvedBuild -Recurse -Force }
}
