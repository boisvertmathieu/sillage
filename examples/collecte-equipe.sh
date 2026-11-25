#!/usr/bin/env bash
# Collecte hebdomadaire d'un membre de l'équipe : bundle pseudonymisé vers le partage d'équipe.
# Le niveau d'export vient de sillage.toml (défaut « strict » : aucun texte issu des messages d'erreur).
# Planification recommandée : Planificateur de tâches Windows (voir docs/flux-equipe.md).
set -euo pipefail
DEST="${SILLAGE_PARTAGE:?Définir SILLAGE_PARTAGE (ex. /mnt/p/equipe-ia/sillage)}"
SEMAINE="$(date +%G-S%V)"
# Nom de fichier aléatoire : il ne doit pas permettre de retrouver qui l'a déposé
# (un hachage du nom d'utilisateur se retrouve facilement en testant les noms des collègues).
NOM="$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
sillage collect
sillage export --since 8d -o "${DEST}/${SEMAINE}/${NOM}.jsonl.gz"
