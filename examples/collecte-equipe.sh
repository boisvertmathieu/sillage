#!/usr/bin/env bash
# Collecte hebdomadaire d'un membre de l'équipe : bundle anonymisé (niveau metrics) vers le partage d'équipe.
# Exemple de planification (crontab WSL) :  17 16 * * 5  ~/sillage/examples/collecte-equipe.sh
set -euo pipefail
DEST="${SILLAGE_PARTAGE:?Définir SILLAGE_PARTAGE (ex. /mnt/p/equipe-ia/sillage)}"
SEMAINE="$(date +%G-S%V)"
sillage collect
sillage export --since 8d --level metrics -o "${DEST}/${SEMAINE}/$(whoami | sha256sum | cut -c1-8).jsonl.gz"
