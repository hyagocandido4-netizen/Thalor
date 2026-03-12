Param(
    [string]$RepoRoot = "."
)

$root = (Resolve-Path $RepoRoot).Path
$targets = @(
    (Join-Path $root 'scripts\setup\phase2_1_patch.ps1'),
    (Join-Path $root 'scripts\setup\phase2_1_patch_v2.ps1'),
    (Join-Path $root 'scripts\setup\phase2_1_fix_main.ps1'),
    (Join-Path $root 'scripts\tools\package_w_cleanup.ps1')
)

$removed = @()
foreach ($p in $targets) {
    if (Test-Path $p) {
        Remove-Item -Force $p
        $removed += $p
    }
}

[pscustomobject]@{
    repo_root = $root
    removed = $removed
    removed_count = $removed.Count
} | ConvertTo-Json -Depth 3
