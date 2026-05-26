param(
    [string]$HelperRoot = "tests/output/1sttest/.capture_helper",
    [int]$PollIntervalMs = 200
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class WindowBridge {
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindowW(string lpClassName, string lpWindowName);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@

$swRestore = 9
$swMaximize = 3

function Write-Heartbeat {
    param([string]$Path)

    $payload = @{
        timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        pid = $PID
    } | ConvertTo-Json -Compress
    Set-Content -LiteralPath $Path -Value $payload -Encoding UTF8
}

function Restore-TargetWindow {
    param([string]$Title)

    $hwnd = [WindowBridge]::FindWindowW($null, $Title)
    if ($hwnd -eq [IntPtr]::Zero) {
        return $false
    }
    [WindowBridge]::ShowWindow($hwnd, $swRestore) | Out-Null
    [WindowBridge]::SetForegroundWindow($hwnd) | Out-Null
    [WindowBridge]::ShowWindow($hwnd, $swMaximize) | Out-Null
    Start-Sleep -Milliseconds 250
    return $true
}

function Get-DistinctColorCount {
    param(
        [System.Drawing.Bitmap]$Bitmap,
        [int]$Threshold
    )

    $colors = New-Object System.Collections.Generic.HashSet[int]
    for ($y = 0; $y -lt $Bitmap.Height; $y++) {
        for ($x = 0; $x -lt $Bitmap.Width; $x++) {
            $pixel = $Bitmap.GetPixel($x, $y)
            $rgb = ($pixel.R -shl 16) -bor ($pixel.G -shl 8) -bor $pixel.B
            $null = $colors.Add($rgb)
            if ($colors.Count -ge $Threshold) {
                return $colors.Count
            }
        }
    }
    return $colors.Count
}

function Write-Response {
    param(
        [string]$ResponsePath,
        [hashtable]$Payload
    )

    $tmpPath = "$ResponsePath.tmp"
    $Payload | ConvertTo-Json -Compress | Set-Content -LiteralPath $tmpPath -Encoding UTF8
    Move-Item -LiteralPath $tmpPath -Destination $ResponsePath -Force
}

function Process-Request {
    param(
        [System.IO.FileInfo]$RequestFile,
        [string]$ResponseDir
    )

    $request = Get-Content -LiteralPath $RequestFile.FullName -Raw | ConvertFrom-Json
    $responsePath = Join-Path $ResponseDir ($request.request_id + ".json")

    try {
        if (-not (Restore-TargetWindow -Title $request.window_title)) {
            Write-Response -ResponsePath $responsePath -Payload @{
                status = "skipped"
                message = "Window '$($request.window_title)' not found on interactive desktop"
            }
            return
        }

        $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bitmap = New-Object System.Drawing.Bitmap $screen.Width, $screen.Height
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)

            $outputDir = Split-Path -Parent $request.output_path
            New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
            $tmpOutput = "$($request.output_path).tmp"
            $bitmap.Save($tmpOutput, [System.Drawing.Imaging.ImageFormat]::Png)
            Move-Item -LiteralPath $tmpOutput -Destination $request.output_path -Force

            $rgbCount = $null
            if ($request.validate) {
                $rgbCount = Get-DistinctColorCount -Bitmap $bitmap -Threshold ([int]$request.rgb_threshold)
            }

            $fileBytes = (Get-Item -LiteralPath $request.output_path).Length
            $resolution = @([int]$bitmap.Width, [int]$bitmap.Height)
            $targetWidth = [int]$request.target_resolution[0]
            $targetHeight = [int]$request.target_resolution[1]
            $tolerance = [int]$request.resolution_tolerance

            $rgbOk = (-not $request.validate) -or ($rgbCount -ge [int]$request.rgb_threshold)
            $resOk = ([Math]::Abs($bitmap.Width - $targetWidth) -le $tolerance) -and
                ([Math]::Abs($bitmap.Height - $targetHeight) -le $tolerance)
            $sizeOk = $fileBytes -ge [int]$request.min_file_bytes

            $status = "ok"
            $message = $null
            if ($request.validate -and (-not ($rgbOk -and $resOk -and $sizeOk))) {
                $status = "error"
                $reasons = @()
                if (-not $rgbOk) {
                    $reasons += "RGB validation failed"
                }
                if (-not $resOk) {
                    $reasons += "Resolution out of tolerance"
                }
                if (-not $sizeOk) {
                    $reasons += "File size below minimum"
                }
                $message = ($reasons -join "; ")
            }

            Write-Response -ResponsePath $responsePath -Payload @{
                status = $status
                path = $request.output_path
                message = $message
                rgb_count = $rgbCount
                resolution = $resolution
            }
        }
        finally {
            $graphics.Dispose()
            $bitmap.Dispose()
        }
    }
    catch {
        Write-Response -ResponsePath $responsePath -Payload @{
            status = "skipped"
            message = $_.Exception.Message
        }
    }
    finally {
        Remove-Item -LiteralPath $RequestFile.FullName -Force -ErrorAction SilentlyContinue
    }
}

$helperRootPath = if ([System.IO.Path]::IsPathRooted($HelperRoot)) {
    $HelperRoot
} else {
    Join-Path (Get-Location).Path $HelperRoot
}
New-Item -ItemType Directory -Path $helperRootPath -Force | Out-Null
$requestDir = Join-Path $helperRootPath "requests"
$responseDir = Join-Path $helperRootPath "responses"
New-Item -ItemType Directory -Path $requestDir -Force | Out-Null
New-Item -ItemType Directory -Path $responseDir -Force | Out-Null
$heartbeatPath = Join-Path $helperRootPath "heartbeat.json"

while ($true) {
    Write-Heartbeat -Path $heartbeatPath
    $requests = Get-ChildItem -LiteralPath $requestDir -Filter *.json -File | Sort-Object LastWriteTimeUtc
    foreach ($request in $requests) {
        Process-Request -RequestFile $request -ResponseDir $responseDir
    }
    Start-Sleep -Milliseconds $PollIntervalMs
}
