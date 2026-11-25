"""Classification déterministe des signatures d'erreurs.

Sert à deux choses :

* proposer une action (humain) et une consigne (agent) selon le type d'erreur ;
* produire, au niveau d'export ``strict``, une signature **sans aucun texte** issu du
  message d'origine : ``<type>#<empreinte>``. L'empreinte (SHA-1 tronqué de la signature
  caviardée) permet encore de regrouper les occurrences d'une même erreur entre postes,
  sans révéler son contenu.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorRule:
    id: str
    label: str
    pattern: str
    category: str  # catégorie d'opportunité
    advice: str  # action pour l'humain
    hint: str  # consigne pour l'agent


RULES: list[ErrorRule] = [
    ErrorRule(
        "tests",
        "tests en échec",
        r"(?i)\btests? (failed|failures?)\b|assertionerror|assertion failed|\b<n> failing\b|failing tests?|"
        r"expected .* (got|but|to)|tests run: .*failures: |\blint(er|ing)? (error|failed)|(?-i:\bFAILED\b|\bFAIL\b)",
        "instructions",
        "Échecs de tests : normal en cycle TDD, à surveiller seulement s'ils persistent.",
        "Lire le premier test en échec et corriger la cause avant de relancer toute la suite.",
    ),
    ErrorRule(
        "script-manquant",
        "script introuvable",
        r"(?i)missing script|script .* not found|unknown script",
        "instructions",
        "Documenter la bonne commande de test/build dans AGENTS.md.",
        "Lister les scripts disponibles (`npm run`) avant d'en lancer un.",
    ),
    ErrorRule(
        "outil-absent",
        "outil absent",
        r"(?i)command not found|not recognized as|introuvable|no such command|is not installed|not found$",
        "environment",
        "Outil absent de l'environnement : installer l'outil ou documenter l'alternative.",
        "Vérifier la présence de l'outil (`command -v`) et utiliser l'alternative documentée.",
    ),
    ErrorRule(
        "reseau",
        "réseau, proxy ou certificats",
        r"(?i)could not resolve dependencies|artifactory|nexus|econnrefused|etimedout|proxy|certificate|ssl|"
        r"unable to get local issuer|pkix|certification path|resolve dependencies",
        "environment",
        "Problème réseau / proxy / dépôt d'artefacts : documenter la configuration (settings.xml, .npmrc, certificats).",
        "Utiliser la configuration d'équipe (settings.xml, .npmrc, certificats) documentée dans ce dépôt.",
    ),
    ErrorRule(
        "authentification",
        "authentification ou droits d'accès",
        r"(?i)\b401\b|unauthori[sz]ed|\b403\b|forbidden|token .*expired|expired token|authentication",
        "mcp",
        "Authentification expirée ou droits manquants : documenter le renouvellement ou corriger le serveur.",
        "En cas de 401/403, demander à l'utilisateur de renouveler son jeton au lieu de réessayer en boucle.",
    ),
    ErrorRule(
        "droits-fichiers",
        "droits sur les fichiers",
        r"(?i)permission denied|eacces|access is denied",
        "environment",
        "Droits insuffisants : préciser les chemins accessibles ou l'utilisateur attendu.",
        "Ne pas forcer les droits (pas de sudo/chmod) : signaler le blocage.",
    ),
    ErrorRule(
        "delai",
        "délai dépassé",
        r"(?i)timed? ?out|timeout|deadline exceeded",
        "environment",
        "Opération trop lente : réduire la portée (filtres, pagination) ou augmenter le délai.",
        "Réduire la portée (filtres, période, pagination) avant de relancer.",
    ),
    ErrorRule(
        "chemin",
        "chemin inexistant",
        r"(?i)no such file|enoent|cannot find (the )?(path|file)|does not exist",
        "instructions",
        "Décrire la structure du dépôt (dossiers clés) dans AGENTS.md.",
        "Vérifier l'arborescence (`ls`, `glob`) avant d'utiliser un chemin.",
    ),
    ErrorRule(
        "git",
        "flux git refusé",
        r"(?i)non-fast-forward|rejected|merge conflict|protected branch",
        "guardrail",
        "Rappeler la stratégie de branches et interdire les poussées directes.",
        "Travailler sur une branche et passer par une PR ; ne jamais forcer une poussée.",
    ),
]
DEFAULT_RULE = ErrorRule(
    "autre",
    "autre erreur",
    "",
    "instructions",
    "Erreur récurrente : documenter la cause et le contournement là où l'agent les lira.",
    "Vérifier les prérequis avant d'exécuter ; ne pas réessayer à l'identique.",
)
_BY_ID = {r.id: r for r in [*RULES, DEFAULT_RULE]}
_STRICT = re.compile(r"^([a-z-]+)#[0-9a-f]{8}$")


def classify(signature: str) -> ErrorRule:
    """Règle correspondant à une signature (texte ou forme stricte ``type#empreinte``)."""
    m = _STRICT.match(signature or "")
    if m:
        return _BY_ID.get(m.group(1), DEFAULT_RULE)
    for rule in RULES:
        if re.search(rule.pattern, signature or ""):
            return rule
    return DEFAULT_RULE


def is_strict(signature: str | None) -> bool:
    return bool(signature and _STRICT.match(signature))


def strict_signature(signature: str | None) -> str | None:
    """``"npm error Missing script: ..."`` -> ``"script-manquant#3f2a9c1e"`` (aucun texte d'origine)."""
    if not signature or is_strict(signature):
        return signature
    digest = hashlib.sha1(signature.encode()).hexdigest()[:8]
    return f"{classify(signature).id}#{digest}"


def display(signature: str) -> str:
    """Texte lisible d'une signature, y compris en forme stricte."""
    if is_strict(signature):
        return f"erreur de type « {classify(signature).label} »"
    return f"« {signature} »"
