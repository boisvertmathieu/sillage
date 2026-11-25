#!/usr/bin/env bash
# Rapport d'équipe à partir des bundles déposés sur le partage (aucune donnée individuelle).
set -euo pipefail
DEST="${SILLAGE_PARTAGE:?Définir SILLAGE_PARTAGE}"
OUT="${1:-rapport-equipe-$(date +%Y-%m).html}"
mapfile -t BUNDLES < <(find "${DEST}" -name '*.jsonl.gz' -mtime -35)
sillage report --bundle "${BUNDLES[@]}" --format html --title "Sillage — sessions Copilot de l'équipe" -o "${OUT}"
sillage opportunities --bundle "${BUNDLES[@]}" --limit 10 -o "${OUT%.html}-opportunites.md"
