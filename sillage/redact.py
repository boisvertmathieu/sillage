"""Caviardage déterministe des secrets et renseignements personnels.

Principe : on ne conserve jamais de texte libre au niveau ``metrics``. Au niveau
``redacted``, tout extrait passe par :func:`redact` puis est tronqué. Les motifs
couvrent les jetons usuels (GitHub, AWS, Azure, JWT, Bearer, clés privées), les
chaînes de connexion, les courriels, les IP, les numéros de carte (Luhn), les NAS
canadiens (Luhn), les téléphones nord-américains et les chemins personnels.
"""

from __future__ import annotations

import hashlib
import hmac
import re

_LUHN_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){8,18}\d(?!\d)")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _replace_luhn(match: re.Match[str]) -> str:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 9 and _luhn_ok(digits):
        return "[NAS]"
    if 13 <= len(digits) <= 19 and _luhn_ok(digits):
        return "[CARTE]"
    return raw


# L'ordre compte : les motifs les plus spécifiques d'abord.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[CLE_PRIVEE]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), "[JETON_GITHUB]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[JETON_GITHUB]"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "[CLE_AWS]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "[JETON_SLACK]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[JWT]"),
    (re.compile(r"(?i)\b(bearer|basic|api-token|token)\s+[A-Za-z0-9._~+/=-]{12,}"), r"\1 [SECRET]"),
    (re.compile(r"(?i)\b(AccountKey|SharedAccessKey|Password|Pwd)=([^;\s]+)"), r"\1=[SECRET]"),
    (
        re.compile(
            r"(?i)\b([A-Za-z0-9_]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|client[_-]?secret)"
            r"[A-Za-z0-9_]*)\s*[:=]\s*(['\"]?)[^\s'\";,]{4,}\2"
        ),
        r"\1=[SECRET]",
    ),
    (re.compile(r"\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1[IDENTIFIANTS]@"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[COURRIEL]"),
    # noms d'hôtes pleinement qualifiés (serveurs internes, bases de production...). Limité aux
    # suffixes usuels pour ne pas toucher les noms de classes Java (org.postgresql.Driver).
    (
        re.compile(
            r"(?i)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
            r"(?:ca|com|net|org|gouv|gov|local|lan|internal|intra|interne|corp|ad|cloud|io)\b(?![.(])"
        ),
        "[HOTE]",
    ),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
    (re.compile(r"(?i)/mnt/[a-z]/Users/[^/\s]+"), "~"),
    (re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+"), "~"),
    (re.compile(r"/(?:home|Users)/[^/\s]+"), "~"),
    (re.compile(r"\(?\b\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b"), "[TEL]"),
]


def compile_patterns(patterns: list[str] | None) -> list[re.Pattern[str]]:
    """Compile les motifs propres à l'organisation (noms d'instances, numéros de dossier, clients...)."""
    out = []
    for p in patterns or []:
        try:
            out.append(re.compile(p))
        except re.error as exc:
            raise ValueError(f"Motif de caviardage invalide {p!r} : {exc}") from exc
    return out


def redact(text: str | None, max_len: int = 300, extra: list[re.Pattern[str]] | None = None) -> str | None:
    """Caviarde ``text`` et le tronque à ``max_len`` caractères.

    ``extra`` : motifs propres à l'organisation, appliqués en premier, remplacés par ``[CONFIDENTIEL]``.
    """
    if text is None:
        return None
    out = text
    for pattern in extra or []:
        out = pattern.sub("[CONFIDENTIEL]", out)
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    out = _LUHN_CANDIDATE.sub(_replace_luhn, out)
    out = re.sub(r"\s+", " ", out).strip()
    if len(out) > max_len:
        out = out[: max_len - 1] + "…"
    return out


def pseudonymize(value: str, salt: str, length: int = 10) -> str:
    """Pseudonyme stable (HMAC-SHA256) : même valeur + même sel => même pseudonyme."""
    digest = hmac.new(salt.encode(), value.encode(), hashlib.sha256).hexdigest()
    return digest[:length]
