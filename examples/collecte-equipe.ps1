# Collecte hebdomadaire d'un membre de l'equipe, Windows natif (sans WSL).
# Equivalent de collecte-equipe.sh : bundle pseudonymise (niveau "strict" par defaut) vers le partage.
# Compatible Windows PowerShell 5.1 et PowerShell 7 (fichier volontairement en ASCII).
#
# Planification (une fois, adapter le chemin du partage et du script) :
#   schtasks /Create /SC WEEKLY /D FRI /ST 16:17 /TN "Sillage collecte" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File %USERPROFILE%\sillage\examples\collecte-equipe.ps1 -Partage \\serveur\equipe-ia\sillage"
param(
    [string]$Partage = $env:SILLAGE_PARTAGE
)
$ErrorActionPreference = 'Stop'
if (-not $Partage) { throw "Definir -Partage ou SILLAGE_PARTAGE (ex. \\serveur\equipe-ia\sillage)" }

# Semaine ISO (meme dossier que collecte-equipe.sh : AAAA-Sww)
$jour = Get-Date
$jeudi = $jour.AddDays(3 - (([int]$jour.DayOfWeek + 6) % 7))
$cal = [System.Globalization.CultureInfo]::InvariantCulture.Calendar
$num = $cal.GetWeekOfYear($jeudi, [System.Globalization.CalendarWeekRule]::FirstFourDayWeek, [DayOfWeek]::Monday)
$semaine = '{0}-S{1:D2}' -f $jeudi.Year, $num

# Nom de fichier aleatoire : il ne doit pas permettre de retrouver qui l'a depose.
$nom = -join ((1..16) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
$dest = Join-Path (Join-Path $Partage $semaine) "$nom.jsonl.gz"

sillage collect
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
sillage export --since 8d -o $dest
exit $LASTEXITCODE
