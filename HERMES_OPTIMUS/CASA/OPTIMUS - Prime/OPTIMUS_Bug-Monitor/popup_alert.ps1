<#
    popup_alert.ps1 -- Windows-style critical alert for the Hermes Bug-Monitor.

    Invoked detached by hermes_bug-monitor.py as:
        powershell -NoProfile -STA -ExecutionPolicy Bypass -File popup_alert.ps1 -Payload <json>

    WinForms is used rather than MessageBoxW because MessageBox cannot label its
    buttons; the operator needs the actions named, not "Yes"/"No".

    Buttons (as specified):
        [ Copy Log Report ]  copies the full incident report to the clipboard
        [ Fix It Now ]       runs `hermes_bug-monitor.py fix` (quarantine + re-apply guards)
    Closing via the window X is equivalent to dismissing without acting.
#>
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Payload)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms | Out-Null
Add-Type -AssemblyName System.Drawing | Out-Null

try {
    $data = Get-Content -LiteralPath $Payload -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    $data = [pscustomobject]@{
        title        = 'HERMES BUG-MONITOR'
        event        = 'ERROR'
        timestamp    = (Get-Date).ToString('o')
        summary      = "Could not read payload: $_"
        report_path  = ''
        fix_command  = ''
    }
}

$reportPath = [string]$data.report_path
$reportText = ''
if ($reportPath -and (Test-Path -LiteralPath $reportPath)) {
    try { $reportText = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 } catch { $reportText = '' }
}
if (-not $reportText) { $reportText = [string]$data.summary }

# Re-apply the console font for a monospaced, full report in the detail box.
$mono = New-Object System.Drawing.Font('Consolas', 9)

$form = New-Object System.Windows.Forms.Form
$form.Text            = [string]$data.title
$form.StartPosition   = 'CenterScreen'
$form.Size            = New-Object System.Drawing.Size(920, 700)
$form.MinimumSize     = New-Object System.Drawing.Size(640, 420)
$form.BackColor       = [System.Drawing.Color]::White
$form.TopMost         = $true
$form.ShowInTaskbar   = $true

# --- header banner ---------------------------------------------------------
$banner = New-Object System.Windows.Forms.Panel
$banner.Dock      = 'Top'
$banner.Height    = 74
$banner.BackColor = [System.Drawing.Color]::FromArgb(155, 18, 18)

$icon = New-Object System.Windows.Forms.PictureBox
$icon.Size     = New-Object System.Drawing.Size(44, 44)
$icon.Location = New-Object System.Drawing.Point(16, 15)
$icon.SizeMode = 'Normal'
try {
    $ico = [System.Drawing.SystemIcons]::Error
    $icon.Image = $ico.ToBitmap()
} catch { }

$title = New-Object System.Windows.Forms.Label
$title.Text      = 'GHOST PATH DETECTED'
$title.ForeColor = [System.Drawing.Color]::White
$title.Font      = New-Object System.Drawing.Font('Segoe UI', 15, [System.Drawing.FontStyle]::Bold)
$title.AutoSize  = $true
$title.Location = New-Object System.Drawing.Point(74, 12)

$subtitle = New-Object System.Windows.Forms.Label
$subtitle.Text      = "$($data.event)  -  $($data.timestamp)"
$subtitle.ForeColor = [System.Drawing.Color]::FromArgb(255, 210, 210)
$subtitle.Font      = New-Object System.Drawing.Font('Segoe UI', 9)
$subtitle.AutoSize  = $true
$subtitle.Location = New-Object System.Drawing.Point(76, 44)

$banner.Controls.AddRange(@($icon, $title, $subtitle))

# --- summary ---------------------------------------------------------------
$summary = New-Object System.Windows.Forms.Label
$summary.Dock      = 'Top'
$summary.Height    = 44
$summary.Padding   = New-Object System.Windows.Forms.Padding(14, 8, 14, 4)
$summary.Font      = New-Object System.Drawing.Font('Segoe UI', 9.5)
$summary.ForeColor = [System.Drawing.Color]::FromArgb(60, 60, 60)
$summary.Text      = [string]$data.summary

# --- report body -----------------------------------------------------------
$body = New-Object System.Windows.Forms.TextBox
$body.Multiline      = $true
$body.ReadOnly       = $true
$body.ScrollBars     = 'Vertical'
$body.Dock           = 'Fill'
$body.WordWrap       = $false
$body.BackColor      = [System.Drawing.Color]::FromArgb(250, 250, 250)
$body.BorderStyle    = 'FixedSingle'
$body.Font           = $mono
$body.Text           = $reportText

# --- buttons ---------------------------------------------------------------
$footer = New-Object System.Windows.Forms.Panel
$footer.Dock      = 'Bottom'
$footer.Height    = 64
$footer.BackColor = [System.Drawing.Color]::FromArgb(240, 240, 240)
$footer.Padding   = New-Object System.Windows.Forms.Padding(12, 12, 12, 12)

$status = New-Object System.Windows.Forms.Label
$status.Dock      = 'Fill'
$status.TextAlign = 'MiddleLeft'
$status.Font      = New-Object System.Drawing.Font('Segoe UI', 9)
$status.ForeColor = [System.Drawing.Color]::FromArgb(90, 90, 90)
$status.Text      = 'The tree is quarantined, never deleted. Forensics are preserved under the monitor.'

$btnCopy = New-Object System.Windows.Forms.Button
$btnCopy.Text     = 'Copy Log Report'
$btnCopy.Size     = New-Object System.Drawing.Size(150, 36)
$btnCopy.Anchor   = 'Right'
$btnCopy.Location = New-Object System.Drawing.Point(560, 14)

$btnFix = New-Object System.Windows.Forms.Button
$btnFix.Text     = 'Fix It Now'
$btnFix.Size     = New-Object System.Drawing.Size(130, 36)
$btnFix.Anchor   = 'Right'
$btnFix.Location = New-Object System.Drawing.Point(716, 14)
$btnFix.BackColor = [System.Drawing.Color]::FromArgb(155, 18, 18)
$btnFix.ForeColor = [System.Drawing.Color]::White

$btnClose = New-Object System.Windows.Forms.Button
$btnClose.Text     = 'Close'
$btnClose.Size     = New-Object System.Drawing.Size(90, 36)
$btnClose.Anchor   = 'Right'
$btnClose.Location = New-Object System.Drawing.Point(852, 14)

$form.AcceptButton = $btnCopy
$form.CancelButton = $btnClose

# --- behaviour -------------------------------------------------------------
$btnCopy.Add_Click({
    try {
        [System.Windows.Forms.Clipboard]::SetText($script:report)
        $status.Text = 'Report copied to clipboard.'
    } catch {
        $status.Text = "Clipboard failed: $($_.Exception.Message)"
    }
})

$btnFix.Add_Click({
    $status.Text = 'Remediating: quarantining tree and re-applying source guards...'
    [System.Windows.Forms.Application]::DoEvents()
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = 'cmd.exe'
        $psi.Arguments = "/c $($data.fix_command) > `"$env:TEMP\bugmon-fix.log`" 2>&1"
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $p = [System.Diagnostics.Process]::Start($psi)
        $p.WaitForExit(180000)
        $code = $p.ExitCode
        if ($code -eq 0) {
            $status.Text = 'Remediation complete. Re-scanning...'
            $btnFix.Enabled = $false
            $btnFix.Text = 'Done'
        } else {
            $status.Text = "Remediation returned exit code $code. See %TEMP%\bugmon-fix.log"
        }
    } catch {
        $status.Text = "Fix failed: $($_.Exception.Message)"
    }
})

$btnClose.Add_Click({ $form.Close() })
$form.Add_FormClosing({ Set-Clipboard -Value '' -ErrorAction SilentlyContinue })

$script:report = $reportText
$form.Controls.AddRange(@($body, $summary, $banner, $footer))
$footer.Controls.AddRange(@($status, $btnCopy, $btnFix, $btnClose))

[void]$form.ShowDialog()
$form.Dispose()
