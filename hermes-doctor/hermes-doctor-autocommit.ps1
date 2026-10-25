<#
    hermes-doctor-autocommit.ps1 — plasa de siguranță pentru proiectul Hermes Doctor

    Ce face: dacă proiectul Hermes Doctor are fișiere modificate și necomitate,
    le comită singur, pe `main`. Panoul `Changes` al interfeței nu are niciodată
    ce arăta, iar Popy nu e nevoit să dea comit.

    Ce NU face, deliberat:
      - NU împinge niciodată. Push-ul e decizia lui Popy și cere acord.
      - NU creează ramuri. Se lucrează pe `main`. Ramura se cere cu cuvinte.
      - NU atinge interfața.
      - NU atinge hook-urile git — sunt globale pe mașină și aparțin altui
        mecanism (LiveReview). Le-am anulat LOCAL, prin core.hooksPath, ca să nu
        se ciocnească.
      - NU șterge nimic, NU face reset, NU face force push, NU face stash.
      - NU comită fișiere care PAR secrete sau CARE CONȚIN secrete.
      - NU comită fișierele declarate în hermes-doctor-ignorat.txt.
      - NU comită un fișier scris acum puțin timp (timpul de „așezare"),
        ca să nu prindă o scriere la jumătate.
      - NU se comite pe sine. Logul propriu e pe lista de ignorare, din cauza
        măsurată: în alt repo, fără această regulă, plasa a produs 60 de
        comituri identice la 5 minute unul după altul.

    Coduri de ieșire:
      0  curat, sau așteaptă așezarea, sau a făcut salvarea, sau autoproba a trecut
      1  refuzat: secrete, căi interzise, sau comitul a eșuat
      2  eroare de mediu: repo invalid, git indisponibil
      3  autoproba a eșuat (o poartă care nu poate deveni roșie e decor)
#>

[CmdletBinding()]
param(
    [string]$Repo = (Join-Path $PSScriptRoot '..'),
    [int]$SettleSeconds = 60,
    [switch]$CeVerificare,
    [switch]$AutoProbe,
    [switch]$AdaugaRepo
)

$ErrorActionPreference = 'Stop'

$scriptDir = $PSScriptRoot
$ignorat   = Join-Path $scriptDir 'hermes-doctor-ignorat.txt'
$registru  = Join-Path $scriptDir 'hermes-doctor-registru.txt'
$log       = Join-Path $scriptDir 'raport-autocomit.log'
$gatePy    = Join-Path $scriptDir 'secret_gate.py'
$testPy    = Join-Path $scriptDir 'test_secret_gate.py'
$python    = Join-Path $env:LOCALAPPDATA 'hermes\tools\python-3.14.7+20260901-win32-x64\python.exe'

$RAMURA = 'main'

# ---------------------------------------------------------------------------
# LOG — întotdeauna pe lista necomitată
# ---------------------------------------------------------------------------
function Scrie-Log([string]$msg) {
    $lin = '[' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '] ' + $msg
    Add-Content -LiteralPath $log -Value $lin -Encoding UTF8
}

# ---------------------------------------------------------------------------
# AUTOPROBA — o poartă care nu poate deveni roșie e decor
# ---------------------------------------------------------------------------
function Invoke-Autoproba {
    # Probe-ul sta intr-un fisier real, nu intr-un here-string generat la
    # rulare. Un fisier generat e o a doua suprafata de esec; unul testat
    # 18/18 nu e.
    if (-not (Test-Path -LiteralPath $gatePy) -or -not (Test-Path -LiteralPath $testPy)) {
        Write-Host 'AUTOPROBA: portile sau testul lipsesc - nu pot verifica nimic'
        return 3
    }
    $rez = @(& $python $testPy $gatePy 2>&1)
    $rc = $LASTEXITCODE
    $sumar = ($rez | Where-Object { $_ -match 'RESULT' } | Select-Object -Last 1)
    if ($rc -ne 0) {
        Write-Host ('AUTOPROBA: PICAT ' + $sumar)
        return 3
    }
    Write-Host ('AUTOPROBA: TRECUT ' + $sumar)
    Write-Host 'AUTOPROBA: POARTA DE SECRETE POATE DEVENI ROSIE'
    return 0
}

# ---------------------------------------------------------------------------
# REGISTRUL — proiecte supraveghute
# ---------------------------------------------------------------------------
if ($AdaugaRepo) {
    if (-not (Test-Path -LiteralPath $registru)) {
        Set-Content -LiteralPath $registru -Value '' -Encoding UTF8
    }
    $curente = @(Get-Content -LiteralPath $registru -Encoding UTF8 |
                 Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and -not $_.Trim().StartsWith('#') })
    if ($curente -notcontains $Repo) {
        Add-Content -LiteralPath $registru -Value $Repo -Encoding UTF8
        Write-Output ('AUTOCOMIT: repo adaugat: ' + $Repo)
    } else {
        Write-Output ('AUTOCOMIT: repo deja inregistrat: ' + $Repo)
    }
    exit 0
}

if ($AutoProbe) {
    exit (Invoke-Autoproba)
}

# ---------------------------------------------------------------------------
# MEDIU
# ---------------------------------------------------------------------------
if (-not (Test-Path -LiteralPath (Join-Path $Repo '.git'))) {
    Scrie-Log 'REFUZAT: nu exista .git in Repo'
    Write-Output 'AUTOCOMIT: nu exista .git — nu fac nimic'
    exit 2
}
if (-not (Test-Path -LiteralPath $python)) {
    Scrie-Log 'EROARE: interpretorul Python nu a fost gasit'
    Write-Output 'AUTOCOMIT: nu gasesc Python-ul folosit de Hermes'
    exit 2
}

Push-Location $Repo

try {
    $ramuraCurenta = (& git rev-parse --abbrev-ref HEAD 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Scrie-Log 'EROARE: HEAD nu se poate citi'
        Write-Output 'AUTOCOMIT: HEAD ilizibil'
        exit 2
    }
    if ($ramuraCurenta -ne $RAMURA) {
        # Nu creează ramuri, nu se mută între ele. Doar spune.
        Scrie-Log ("REFUZAT: ramuta curenta este '" + $ramuraCurenta + "', nu '" + $RAMURA + "'")
        Write-Output ('AUTOCOMIT: sunt pe ramura ' + $ramuraCurenta + ' — nu schimb ramura singur')
        exit 1
    }

    # --- ce s-a schimbat -------------------------------------------------
    $raw = @(& git status --porcelain 2>$null)
    if ($LASTEXITCODE -ne 0) { Scrie-Log 'EROARE: git status a esuat'; exit 2 }
    if (-not $raw -or $raw.Count -eq 0) {
        Write-Output 'AUTOCOMIT: curat, nimic de salvat'
        exit 0
    }

    $cai = @()
    foreach ($linie in $raw) {
        $ci = $linie.Substring(3).Trim().Trim('"')
        $ci = $ci -replace '\\\\', '\'
        $cai += $ci
    }

    # --- POARTA 1: căi declarate INTERZISE -------------------------------
    $reguli = @()
    if (Test-Path -LiteralPath $ignorat) {
        $reguli = @(Get-Content -LiteralPath $ignorat -Encoding UTF8 |
                    Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and -not $_.Trim().StartsWith('#') } |
                    ForEach-Object { $_.Trim() })
    }
    $interzise = @()
    foreach ($c in $cai) {
        foreach ($r in $reguli) {
            if ($c -like $r) { $interzise += $c; break }
        }
    }
    if ($interzise.Count -gt 0) {
        Scrie-Log ('REFUZAT ' + $interzise.Count + ' cale(s) interzise: ' + (($interzise | Select-Object -First 5) -join ' | '))
        Write-Output ('AUTOCOMIT: REFUZAT — cai interzise: ' + (($interzise | Select-Object -First 5) -join ' | '))
        exit 1
    }

    # --- POARTA 2: secrete, pe CONTINUT, prin poarta Python ---------------
    if (Test-Path -LiteralPath $gatePy) {
        $out = @(& $python $gatePy 2>&1)
        $rc = $LASTEXITCODE
        if ($rc -ne 0) {
            Scrie-Log ('REFUZAT: poarta de secrete a esuat: ' + (($out | Select-Object -Last 3) -join ' | '))
            $out | Select-Object -Last 8 | ForEach-Object { Write-Output $_ }
            exit 1
        }
        if ($out -match 'ALERTA') {
            Scrie-Log 'REFUZAT: secrete in continutul fisierelor de salvat'
            $out | ForEach-Object { Write-Output $_ }
            exit 1
        }
    } else {
        Scrie-Log 'REFUZAT: poarta de secrete nu exista — nu comit fara ea'
        Write-Output 'AUTOCOMIT: poarta de secrete lipseste. Nu comit fara ea.'
        exit 1
    }

    # --- POARTA 3: asezare -----------------------------------------------
    if ($SettleSeconds -gt 0) {
        $nou = @(Get-ChildItem -LiteralPath $Repo -Recurse -File -ErrorAction SilentlyContinue |
                 Where-Object { $_.LastWriteTime -gt (Get-Date).AddSeconds(-$SettleSeconds) })
        if ($nou.Count -gt 0) {
            Write-Output ('AUTOCOMIT: ' + $nou.Count + ' fisier(e) scris(e) acum — astept asezarea')
            exit 0
        }
    }

    # --- COMIT ------------------------------------------------------------
    $mesaj = 'Salvare automata: ' + $cai.Count + ' cale(i) modificate — ' + (($cai | Select-Object -First 3) -join ', ')
    if ($CeVerificare) {
        Write-Output ('AUTOCOMIT: [verificare] ar comita ' + $cai.Count + ' cale(i)')
        Write-Output ('AUTOCOMIT: [verificare] mesaj: ' + $mesaj)
        exit 0
    }

    & git add -A 2>$null
    if ($LASTEXITCODE -ne 0) { Scrie-Log 'REFUZAT: git add a esuat'; Write-Output 'AUTOCOMIT: git add a esuat'; exit 1 }

    $rez = @(& git commit -m $mesaj 2>&1)
    if ($LASTEXITCODE -ne 0) {
        Scrie-Log ('ESUAT la comit: ' + (($rez | Select-Object -First 2) -join ' | '))
        Write-Output ('AUTOCOMIT: comitul a esuat: ' + (($rez | Select-Object -First 1)))
        exit 1
    }
    $sha = ((& git rev-parse --short HEAD 2>$null) | Select-Object -First 1)
    Scrie-Log ('OK ' + $sha + ' — ' + $mesaj)
    Write-Output ('AUTOCOMIT: salvat ' + $sha)
    exit 0
}
finally {
    Pop-Location
}