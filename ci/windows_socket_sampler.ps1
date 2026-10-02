# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Samples the Windows runner's TCP and kernel-pool state while the test suite
# runs, so a `net::ERR_NO_BUFFER_SPACE` (WSAENOBUFS) can be attributed: were
# ephemeral ports exhausted, or nonpaged pool, and by which process?
#
# The suite itself was measured and ruled out (no socket leak, at most ~1.9k
# new loopback connections in any 120 s window against ~16k dynamic ports), so
# the cause is expected outside the pytest process: browsers left running,
# Defender, or the runner. Started in the background by the CI test job and
# left to run until the job ends; it never fails the job.
#
# Usage: windows_socket_sampler.ps1 -Out <log> [-IntervalSeconds 30]

param(
    [Parameter(Mandatory = $true)][string]$Out,
    [int]$IntervalSeconds = 30
)

$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Out) | Out-Null

function Write-Section([string]$Text) {
    Add-Content -Path $Out -Value $Text
}

Write-Section "== dynamic port range (tcp)"
Write-Section ((netsh int ipv4 show dynamicport tcp) -join "`n")

while ($true) {
    try {
        $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        $conns = Get-NetTCPConnection -ErrorAction SilentlyContinue
        $names = @{}
        Get-Process -ErrorAction SilentlyContinue | ForEach-Object { $names[$_.Id] = $_.ProcessName }
        $byState = $conns | Group-Object State | Sort-Object Count -Descending |
            ForEach-Object { "$($_.Name)=$($_.Count)" }
        $byProcess = $conns | Group-Object OwningProcess | Sort-Object Count -Descending |
            Select-Object -First 10 |
            ForEach-Object {
                $owner = [int]$_.Name
                $label = if ($names.ContainsKey($owner)) { $names[$owner] } else { "?" }
                "$label($owner)=$($_.Count)"
            }
        $pool = (Get-Counter '\Memory\Pool Nonpaged Bytes' -ErrorAction SilentlyContinue).CounterSamples |
            Select-Object -First 1 -ExpandProperty CookedValue
        $poolMb = if ($null -ne $pool) { [math]::Round($pool / 1MB, 1) } else { "?" }
        Write-Section "$stamp total=$($conns.Count) nonpaged_mb=$poolMb states: $($byState -join ' ')"
        Write-Section "$stamp top: $($byProcess -join ' ')"
    } catch {
        Write-Section "sample failed: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $IntervalSeconds
}
