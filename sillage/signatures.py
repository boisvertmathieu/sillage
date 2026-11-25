"""Signatures d'erreurs et normalisation des commandes.

Une *signature* est un gabarit stable d'un message d'erreur : on caviarde, puis on
remplace chemins, nombres, identifiants et chaînes entre guillemets par des jetons.
Deux erreurs « identiques à des détails près » partagent la même signature, ce qui
permet de repérer les irritants récurrents entre sessions et entre personnes.
"""

from __future__ import annotations

import re
import shlex

from sillage.redact import redact

_ERRORISH = re.compile(
    r"(?i)(error|erreur|err!|failed|failure|échec|echec|not found|introuvable|denied|refus|"
    r"exception|fatal|cannot|can't|could not|unable|impossible|missing|manquant|timed? ?out|"
    r"unauthorized|forbidden|traceback|panic)"
)

_SUBSTITUTIONS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\[(?:CLE_PRIVEE|JETON_GITHUB|CLE_AWS|JETON_SLACK|JWT|SECRET|IDENTIFIANTS|COURRIEL|IP|TEL|NAS|CARTE)\]"
        ),
        "<secret>",
    ),
    (re.compile(r"\[HOTE\]"), "<hote>"),
    (re.compile(r"\[CONFIDENTIEL\]"), "<confidentiel>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"https?://[^\s'\"<>]+"), "<url>"),
    (re.compile(r"(?:~|\.{1,2})?(?:/[\w.@+-]+){2,}/?"), "<chemin>"),
    (re.compile(r"\b[A-Za-z]:\\[^\s'\"]+"), "<chemin>"),
    (re.compile(r"'[^']{1,80}'"), "'<val>'"),
    (re.compile(r"\"[^\"]{1,80}\""), '"<val>"'),
    (re.compile(r"\b\d+(?:[.,:]\d+)*\b"), "<n>"),
]


def pick_error_line(text: str | None) -> str | None:
    """Choisit la ligne la plus représentative d'une sortie en erreur."""
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = [ln for ln in lines if not re.search(r"(?:exited|completed) with exit code", ln)]
    if not lines:
        return None
    for ln in lines:
        if _ERRORISH.search(ln):
            return ln
    return lines[-1]


def signature(text: str | None, max_len: int = 140, extra: list[re.Pattern[str]] | None = None) -> str | None:
    line = pick_error_line(text)
    if not line:
        return None
    out = redact(line, max_len=500, extra=extra) or ""
    for pattern, repl in _SUBSTITUTIONS:
        out = pattern.sub(repl, out)
    out = re.sub(r"\s+", " ", out).strip(" .:")
    if len(out) > max_len:
        # garder le début (contexte) ET la fin (souvent la cause racine : chaînes d'exceptions)
        head = max_len * 2 // 5
        out = out[:head].rstrip() + " … " + out[-(max_len - head - 3) :].lstrip()
    return out or None


# ----------------------------------------------------------------- commandes
_MULTIWORD = {
    "git",
    "npm",
    "pnpm",
    "yarn",
    "npx",
    "dotnet",
    "mvn",
    "gradle",
    "./gradlew",
    "./mvnw",
    "docker",
    "kubectl",
    "helm",
    "az",
    "gh",
    "aws",
    "gcloud",
    "terraform",
    "make",
    "cargo",
    "go",
    "pip",
    "pip3",
    "poetry",
    "uv",
    "copilot",
    "oc",
    "podman",
    "systemctl",
}
_WRAPPERS = {"sudo", "time", "env", "nohup", "command"}
# options courantes qui consomment l'argument suivant (ne pas le prendre pour une sous-commande)
_VALUE_FLAGS = {
    "-n",
    "--namespace",
    "-s",
    "--settings",
    "-f",
    "--file",
    "-C",
    "-c",
    "--context",
    "-p",
    "--profile",
    "--project",
    "-o",
    "--output",
    "-l",
    "--selector",
    "-u",
    "--user",
    "-H",
    "--header",
    "-e",
    "-t",
    "--subscription",
    "-g",
    "--resource-group",
    "--prefix",
    "-w",
    "--workspace",
    "-P",
    "--region",
}


def command_identifiers(command: str | None, limit: int = 4) -> list[str]:
    """``cd app && npm run test:unit -- --watch`` -> ``["cd", "npm run test:unit"]``."""
    if not command:
        return []
    out: list[str] = []
    for part in re.split(r"&&|\|\||;|\|", command):
        part = part.strip()
        if not part:
            continue
        try:
            tokens = shlex.split(part, posix=True)
        except ValueError:
            tokens = part.split()
        # retire les affectations VAR=val et les enrobages
        while tokens and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]) or tokens[0] in _WRAPPERS):
            tokens = tokens[1:]
        if not tokens:
            continue
        head = tokens[0].rsplit("/", 1)[-1] if not tokens[0].startswith("./") else tokens[0]
        ident = head
        # sous-commande = premier mot « simple » (ignore options et valeurs d'options : chemins, URL...)
        rest: list[str] = []
        skip = False
        for tok in tokens[1:]:
            if skip:
                skip = False
                continue
            if tok.startswith("-"):
                skip = tok in _VALUE_FLAGS
                continue
            if re.match(r"^[A-Za-z][\w:.-]*$", tok):
                rest.append(tok)
        if head in _MULTIWORD and rest:
            ident = f"{head} {rest[0]}"
            if head in {"npm", "pnpm", "yarn"} and rest[0] == "run" and len(rest) > 1:
                ident = f"{head} run {rest[1]}"
            if head in {"python", "python3"}:
                ident = head
        elif head in {"python", "python3"} and len(tokens) > 2 and tokens[1] == "-m":
            ident = f"{head} -m {tokens[2]}"
        if not re.match(r"^[\w./:+@-]+(?: [\w./:+@-]+){0,2}$", ident):
            ident = re.sub(r"[^\w./:+@ -]", "", ident)[:40] or "?"
        out.append(ident)
        if len(out) >= limit:
            break
    return out


def primary_command(command: str | None) -> str | None:
    """Commande « significative » : ignore cd/echo/export en tête de chaîne."""
    ids = command_identifiers(command)
    trivial = {"cd", "echo", "export", "set", "source", ".", "pwd", "true", "clear"}
    for ident in ids:
        if ident.split(" ")[0] not in trivial:
            return ident
    return ids[0] if ids else None


_EXIT = re.compile(r"(?:exited|completed) with exit code (\d+)")


def exit_code(text: str | None) -> int | None:
    if not text:
        return None
    m = _EXIT.search(text)
    return int(m.group(1)) if m else None
