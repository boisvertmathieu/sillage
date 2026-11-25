#!/usr/bin/env bash
# Publie les leçons d'équipe dans le AGENTS.md de chaque dépôt, par PR (canal principal sans MCP).
#
# Usage : SILLAGE_PARTAGE=/mnt/p/equipe-ia/sillage ./publier-lecons.sh depots.txt
#   depots.txt : une ligne par dépôt « owner/nom  /chemin/vers/le/clone »
#                (lignes vides et commentaires # ignorés)
# Prérequis : git, gh (authentifié), sillage. Une PR n'est ouverte que si le bloc de leçons change.
set -euo pipefail
PARTAGE="${SILLAGE_PARTAGE:?Définir SILLAGE_PARTAGE (dossier partagé des bundles)}"
LISTE="${1:?Fichier de dépôts requis (owner/nom /chemin/clone par ligne)}"
FICHIER="${SILLAGE_FICHIER_INSTRUCTIONS:-AGENTS.md}"
BRANCHE="sillage/lecons-$(date +%Y-%m)"

while read -r depot chemin; do
  [[ -z "${depot}" || "${depot}" == \#* ]] && continue
  echo "== ${depot} (${chemin})"
  (
    cd "${chemin}"
    if [[ -n "$(git status --porcelain)" ]]; then
      echo "   ignoré : modifications locales non commitées"; exit 0
    fi
    base="$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@' || echo main)"
    git fetch -q origin "${base}"
    git fetch -q origin "${BRANCHE}" 2>/dev/null || true  # PR du mois déjà publiée ?
    git switch -q -C "${BRANCHE}" "origin/${base}"
    sillage lessons --bundle-dir "${PARTAGE}" --repo "${depot}" --write "${FICHIER}"
    if git diff --quiet -- "${FICHIER}" && git ls-files --error-unmatch "${FICHIER}" >/dev/null 2>&1; then
      echo "   leçons déjà à jour"; git switch -q "${base}"; exit 0
    fi
    if git rev-parse -q --verify "origin/${BRANCHE}" >/dev/null \
       && git diff --quiet "origin/${BRANCHE}" -- "${FICHIER}"; then
      echo "   PR du mois déjà à jour"; git checkout -q -- "${FICHIER}"; git switch -q "${base}"; exit 0
    fi
    git add "${FICHIER}"
    git commit -q -m "sillage: mise à jour des leçons apprises ($(date +%Y-%m))"
    git push -q -u origin "${BRANCHE}" --force-with-lease
    gh pr create --base "${base}" --head "${BRANCHE}" \
      --title "sillage: leçons apprises des sessions Copilot ($(date +%Y-%m))" \
      --body "Mise à jour automatique du bloc Sillage de ${FICHIER} à partir des sessions anonymisées de l'équipe. Relire avant fusion." \
      || echo "   PR existante mise à jour"
    git switch -q "${base}"
  )
done < "${LISTE}"
