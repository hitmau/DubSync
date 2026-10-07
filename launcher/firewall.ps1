param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 65535)]
    [int]$Port
)

$ErrorActionPreference = 'Stop'
$ruleName = 'DubSync-LAN-8766'
$displayName = "DubSync - rede local TCP $Port"
$rule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if ($rule) {
    Set-NetFirewallRule -Name $ruleName -NewDisplayName $displayName -Enabled True `
        -Direction Inbound -Action Allow -Profile Private -Protocol TCP `
        -LocalPort $Port -RemoteAddress LocalSubnet
} else {
    New-NetFirewallRule -Name $ruleName -DisplayName $displayName `
        -Description 'Permite acessar o DubSync apenas a partir da rede local.' `
        -Direction Inbound -Action Allow -Enabled True -Protocol TCP `
        -LocalPort $Port -Profile Private -RemoteAddress LocalSubnet | Out-Null
}
