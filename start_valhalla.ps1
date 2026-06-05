param(
    [string]$ValhallaRoot = "C:\valhalla",
    [string]$ContainerName = "cvrp_valhalla",
    [string]$Image = "ghcr.io/valhalla/valhalla-scripted:latest",
    [string]$TileUrls = "https://download.geofabrik.de/europe/bulgaria-latest.osm.pbf",
    [string]$MapFileName = "bulgaria-latest.osm.pbf",
    [int]$Port = 8002,
    [int]$ServerThreads = 0,
    [switch]$SkipPull,
    [switch]$Recreate,
    [switch]$ForceRebuild,
    [switch]$UpdateMap,
    [switch]$SkipDownload,
    [switch]$KeepOldData,
    [int]$ReadyAttempts = 60,
    [switch]$FollowLogs
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Test-DockerAvailable {
    try {
        docker version --format "{{.Server.Version}}" | Out-Null
    }
    catch {
        throw "Docker is not running or is not installed. Start Docker Desktop and run this script again."
    }
}

function Test-ContainerExists {
    param([string]$Name)
    $names = docker ps -a --format "{{.Names}}"
    return @($names | Where-Object { $_ -eq $Name }).Count -gt 0
}

function Test-ContainerRunning {
    param([string]$Name)
    $names = docker ps --format "{{.Names}}"
    return @($names | Where-Object { $_ -eq $Name }).Count -gt 0
}

function Assert-SafeDataDirectory {
    param([string]$Path)

    $resolved = [System.IO.Path]::GetFullPath($Path)
    $trimmed = $resolved.TrimEnd("\")

    if ([string]::IsNullOrWhiteSpace($trimmed)) {
        throw "Invalid Valhalla data directory."
    }

    if ($trimmed -match "^[A-Za-z]:$") {
        throw "Refusing to clean a drive root: $resolved"
    }

    if ($trimmed.Length -lt 10) {
        throw "Refusing to clean a suspiciously short path: $resolved"
    }

    return $resolved
}

function Stop-ValhallaContainer {
    param([string]$Name)

    if (Test-ContainerRunning -Name $Name) {
        Write-Step "Stopping container $Name"
        docker stop $Name | Out-Null
    }
}

function Remove-ValhallaContainer {
    param([string]$Name)

    if (Test-ContainerExists -Name $Name) {
        Write-Step "Removing existing container $Name"
        docker rm -f $Name | Out-Null
    }
}

function Clear-ValhallaData {
    param([string]$Path)

    $safePath = Assert-SafeDataDirectory -Path $Path
    New-Item -ItemType Directory -Force -Path $safePath | Out-Null

    Write-Step "Cleaning old Valhalla data in $safePath"

    $filePatterns = @(
        "*.osm.pbf",
        "*.pbf",
        "*.osm",
        "*.osm.gz",
        "*.tar",
        "*.tar.gz",
        "*.sqlite",
        "*.db",
        "*.log"
    )

    foreach ($pattern in $filePatterns) {
        Get-ChildItem -LiteralPath $safePath -Filter $pattern -File -Force -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }

    $knownFiles = @(
        "valhalla.json",
        "valhalla_tiles.tar",
        "traffic.tar",
        "admin.sqlite",
        "admins.sqlite",
        "timezones.sqlite",
        "tz_world.sqlite"
    )

    foreach ($name in $knownFiles) {
        $target = Join-Path $safePath $name
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
        }
    }

    $knownDirs = @(
        "valhalla_tiles",
        "tiles",
        "elevation_tiles",
        "transit_tiles",
        "traffic",
        "logs"
    )

    foreach ($name in $knownDirs) {
        $target = Join-Path $safePath $name
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-PrimaryTileUrl {
    param([string]$Urls)

    return ($Urls -split "\s+" | Where-Object { $_ -and $_.Trim() } | Select-Object -First 1)
}

function Download-ValhallaMap {
    param(
        [string]$Url,
        [string]$OutputPath
    )

    if ([string]::IsNullOrWhiteSpace($Url)) {
        throw "TileUrls is empty. Cannot download map data."
    }

    Write-Step "Downloading fresh map data"
    Write-Host "Source: $Url"
    Write-Host "Target: $OutputPath"

    $tempPath = "$OutputPath.download"
    if (Test-Path -LiteralPath $tempPath) {
        Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
    }

    Invoke-WebRequest -Uri $Url -OutFile $tempPath

    if (-not (Test-Path -LiteralPath $tempPath)) {
        throw "Download failed: $tempPath was not created."
    }

    Move-Item -LiteralPath $tempPath -Destination $OutputPath -Force
}

function Start-NewValhallaContainer {
    param(
        [string]$Name,
        [string]$DockerImage,
        [string]$DataDir,
        [string]$Urls,
        [int]$PublicPort,
        [int]$Threads,
        [bool]$ForceBuild
    )

    Write-Step "Creating and starting Valhalla container"

    $buildTarValue = "True"
    $forceRebuildValue = "False"
    if ($ForceBuild) {
        $buildTarValue = "Force"
        $forceRebuildValue = "True"
    }

    $dockerArgs = @(
        "run", "-d",
        "--name", $Name,
        "--restart", "unless-stopped",
        "-p", "${PublicPort}:8002",
        "-v", "${DataDir}:/custom_files",
        "-e", "tile_urls=$Urls",
        "-e", "build_admins=True",
        "-e", "build_time_zones=True",
        "-e", "build_elevation=False",
        "-e", "build_tar=$buildTarValue",
        "-e", "force_rebuild=$forceRebuildValue",
        "-e", "use_tiles_ignore_pbf=False",
        "-e", "serve_tiles=True",
        "-e", "server_threads=$Threads",
        $DockerImage
    )

    docker @dockerArgs | Out-Null
}

function Wait-ValhallaStatus {
    param(
        [string]$Url,
        [string]$ContainerName = "",
        [int]$Attempts = 60,
        [int]$DelaySeconds = 3
    )

    $lastError = ""

    for ($i = 1; $i -le $Attempts; $i++) {
        if ($ContainerName -and (Test-ContainerExists -Name $ContainerName) -and -not (Test-ContainerRunning -Name $ContainerName)) {
            Write-Host "Valhalla container stopped before /status became ready."
            Write-Host "Last container logs:"
            docker logs --tail 80 $ContainerName
            return $false
        }

        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                return $true
            }
            $lastError = "HTTP $($response.StatusCode)"
        }
        catch {
            $lastError = $_.Exception.Message
        }

        if ($i -eq 1 -or $i % 10 -eq 0) {
            Write-Host "Waiting for Valhalla /status ($i/$Attempts). Last check: $lastError"
            if ($ContainerName -and $i % 20 -eq 0) {
                Write-Host "Recent Valhalla build logs:"
                docker logs --tail 12 $ContainerName
            }
        }

        Start-Sleep -Seconds $DelaySeconds
    }

    return $false
}

Write-Host "CVRP Optimizer - Valhalla Docker starter"
Write-Host "Container : $ContainerName"
Write-Host "Data dir  : $ValhallaRoot"
Write-Host "URL       : http://127.0.0.1:$Port"
if ($UpdateMap) {
    Write-Host "Mode      : update map data"
}

Test-DockerAvailable

if ($ServerThreads -le 0) {
    $cpuCount = [Environment]::ProcessorCount
    $ServerThreads = [Math]::Max(2, [Math]::Min($cpuCount - 1, 8))
}

if ($UpdateMap -and $ReadyAttempts -lt 600) {
    $ReadyAttempts = 600
}

Write-Step "Preparing Valhalla data directory"
New-Item -ItemType Directory -Force -Path $ValhallaRoot | Out-Null

if (-not $SkipPull) {
    Write-Step "Pulling Valhalla image"
    docker pull $Image
}

$containerExists = Test-ContainerExists -Name $ContainerName

if ($UpdateMap) {
    Stop-ValhallaContainer -Name $ContainerName

    if ($Recreate -and $containerExists) {
        Remove-ValhallaContainer -Name $ContainerName
        $containerExists = $false
    }

    if (-not $KeepOldData) {
        Clear-ValhallaData -Path $ValhallaRoot
    }

    if (-not $SkipDownload) {
        $primaryUrl = Get-PrimaryTileUrl -Urls $TileUrls
        $mapPath = Join-Path $ValhallaRoot $MapFileName
        Download-ValhallaMap -Url $primaryUrl -OutputPath $mapPath
    }
}
elseif ($Recreate -and $containerExists) {
    Remove-ValhallaContainer -Name $ContainerName
    $containerExists = $false
}

if ($containerExists) {
    Write-Step "Starting the existing container"
    docker restart $ContainerName | Out-Null
}
else {
    Start-NewValhallaContainer `
        -Name $ContainerName `
        -DockerImage $Image `
        -DataDir $ValhallaRoot `
        -Urls $TileUrls `
        -PublicPort $Port `
        -Threads $ServerThreads `
        -ForceBuild ([bool]$ForceRebuild)
}

$statusUrl = "http://127.0.0.1:$Port/status"
Write-Step "Checking Valhalla status"

if (Wait-ValhallaStatus -Url $statusUrl -ContainerName $ContainerName -Attempts $ReadyAttempts) {
    Write-Host "Valhalla is ready: $statusUrl"
}
else {
    Write-Host "Valhalla container is running, but the API is not ready yet."
    Write-Host "It may still be downloading/building Bulgaria map tiles."
    Write-Host "Watch progress with:"
    Write-Host "  docker logs -f $ContainerName"
}

Write-Host ""
Write-Host "CVRP Valhalla base_url should be: http://localhost:$Port"
Write-Host "To update map data manually:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\start_valhalla.ps1 -UpdateMap"
Write-Host "To recreate the container only:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\start_valhalla.ps1 -Recreate"

if ($FollowLogs) {
    Write-Step "Following container logs"
    docker logs -f $ContainerName
}
