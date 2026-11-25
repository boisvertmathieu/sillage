"""Interface en ligne de commande ``sillage``."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from importlib import resources
from pathlib import Path

from sillage import __version__
from sillage.analyze import analyze
from sillage.config import SAMPLE_CONFIG, Config, load_config
from sillage.insights import lessons, opportunities, render_lessons_block, write_lessons_block
from sillage.model import SessionRecord
from sillage.reader import candidate_homes, iter_session_files, load_events
from sillage.report_html import render_html
from sillage.report_md import render_lessons, render_opportunities, render_report
from sillage.store import (
    LocalStore,
    data_dir,
    export_bundle,
    filter_records,
    find_bundles,
    load_bundles,
    parse_since,
)


# ------------------------------------------------------------------ helpers
def _emit(text: str, output: str | None) -> None:
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(text, encoding="utf-8")
        print(f"Écrit : {output}", file=sys.stderr)
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _records(args: argparse.Namespace, cfg: Config) -> list[SessionRecord]:
    bundles = [Path(p) for p in (getattr(args, "bundle", None) or [])]
    for d in getattr(args, "bundle_dir", None) or []:
        found = find_bundles(Path(os.path.expanduser(d)))
        if not found:
            raise ValueError(f"Aucun bundle (*.jsonl.gz) dans {d}")
        bundles.extend(found)
    if bundles:
        recs = load_bundles(bundles)
    else:
        store = LocalStore()
        if not getattr(args, "no_collect", False):
            store.collect(cfg, getattr(args, "copilot_home", None))
        recs = list(store.load().values())
    return filter_records(recs, parse_since(getattr(args, "since", None)), getattr(args, "repo", None))


def _json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=lambda o: sorted(o) if isinstance(o, set) else str(o))


# ----------------------------------------------------------------- commandes
def cmd_collect(args: argparse.Namespace, cfg: Config) -> int:
    res = LocalStore().collect(cfg, args.copilot_home)
    print(f"Répertoires examinés : {', '.join(res.scanned_homes) or 'aucun'}")
    print(f"Sessions nouvelles ou mises à jour : {res.new_or_updated} · total en magasin : {res.total}")
    if res.bad_lines:
        print(f"Lignes illisibles ignorées : {res.bad_lines} (sessions en cours ou journaux tronqués)")
    if res.unknown_event_types:
        print(
            f"Types d'événements inconnus (nouvelle version de Copilot ?) : {', '.join(sorted(res.unknown_event_types))}"
        )
    return 0


def cmd_report(args: argparse.Namespace, cfg: Config) -> int:
    a = analyze(_records(args, cfg), cfg.k_anonymity)
    opps = opportunities(a, cfg.thresholds)
    if args.format == "json":
        payload = {
            "overview": a.overview,
            "context": a.context,
            "weekly": a.weekly,
            "skills": a.skills,
            "subagents": a.subagents,
            "mcp": a.mcp,
            "tools": a.tools,
            "shell_commands": a.shell_commands[:50],
            "repositories": a.repositories,
            "models": a.models,
            "permissions": a.permissions[:50],
            "opportunities": [o.to_dict() for o in opps],
        }
        _emit(_json(payload), args.output)
    elif args.format == "html":
        _emit(render_html(a, opps, lessons(a, cfg.thresholds), title=args.title), args.output)
    else:
        _emit(render_report(a, opps, title=args.title), args.output)
    return 0


def cmd_opportunities(args: argparse.Namespace, cfg: Config) -> int:
    a = analyze(_records(args, cfg), cfg.k_anonymity)
    opps = opportunities(a, cfg.thresholds)
    if args.category:
        opps = [o for o in opps if o.category == args.category]
    opps = opps[: args.limit]
    if args.format == "json":
        _emit(_json([o.to_dict() for o in opps]), args.output)
    else:
        _emit(render_opportunities(opps), args.output)
    return 0


def cmd_lessons(args: argparse.Namespace, cfg: Config) -> int:
    a = analyze(_records(argparse.Namespace(**{**vars(args), "repo": None}), cfg), cfg.k_anonymity)
    items = lessons(a, cfg.thresholds, repository=args.repo, skill=args.skill)[: args.limit]
    if args.write:
        changed = write_lessons_block(Path(args.write), render_lessons_block(items))
        print(f"{args.write} : {'mis à jour' if changed else 'déjà à jour'} ({len(items)} leçon(s))")
        return 0
    if args.format == "json":
        _emit(_json([lsn.to_dict() for lsn in items]), args.output)
    elif args.format == "agents-md":
        _emit(render_lessons_block(items), args.output)
    else:
        _emit(render_lessons(items), args.output)
    return 0


def cmd_export(args: argparse.Namespace, cfg: Config) -> int:
    recs = _records(args, cfg)
    n = export_bundle(recs, Path(args.output), cfg, args.level)
    print(f"Bundle écrit : {args.output} ({n} sessions, niveau « {args.level or cfg.export_privacy} »)")
    print(
        "Contenu : compteurs, noms d'outils/skills/agents, signatures d'erreurs caviardées. "
        "Aucun prompt, code ou sortie brute."
    )
    return 0


def cmd_doctor(args: argparse.Namespace, cfg: Config) -> int:
    print(f"Sillage {__version__} — Python {sys.version.split()[0]}")
    print(f"Configuration : {cfg.source or 'valeurs par défaut'}")
    print(f"Magasin local : {data_dir()}")
    print(f"Sel de pseudonymisation : {'partagé ($' + cfg.salt_env + ')' if os.environ.get(cfg.salt_env) else 'local'}")
    print(f"Confidentialité : magasin « {cfg.local_privacy} », export « {cfg.export_privacy} », k={cfg.k_anonymity}")
    homes = candidate_homes(args.copilot_home or cfg.copilot_homes or None)
    total = 0
    versions: Counter = Counter()
    types: Counter = Counter()
    mcp_disabled: set[str] = set()
    bad = 0
    for home in homes:
        files = list(iter_session_files([home]))
        exists = (home / "session-state").is_dir()
        print(f"- {home} : {f'{len(files)} session(s)' if exists else 'absent'}")
        total += len(files)
        for _, ev_path, _, _ in files[-50:]:  # échantillon : 50 plus récentes
            events, b = load_events(ev_path)
            bad += b
            for ev in events:
                types[ev.get("type")] += 1
                data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                if ev.get("type") == "session.start":
                    versions[data.get("copilotVersion")] += 1
                elif ev.get("type") == "session.mcp_servers_loaded":
                    for srv in data.get("servers") or []:
                        if isinstance(srv, dict) and srv.get("status") == "disabled":
                            mcp_disabled.add(str(srv.get("name")))
    from sillage.normalize import KNOWN_EVENT_TYPES

    unknown = sorted(t for t in types if t not in KNOWN_EVENT_TYPES)
    print(f"Sessions trouvées : {total}")
    if versions:
        print("Versions de Copilot CLI (échantillon) : " + ", ".join(f"{v} ({n})" for v, n in versions.most_common(5)))
    if unknown:
        print(f"⚠ Types d'événements inconnus : {', '.join(unknown)} — Sillage les ignore ; mettre à jour Sillage.")
    if bad:
        print(f"Lignes illisibles dans l'échantillon : {bad}")
    if mcp_disabled:
        print(
            f"Serveurs MCP désactivés (politique de l'organisation probable) : {', '.join(sorted(mcp_disabled))}. "
            "Sillage fonctionne sans MCP : les leçons passent par AGENTS.md."
        )
    if total == 0:
        print(
            "Aucune session : vérifiez COPILOT_HOME, ou utilisez --copilot-home. "
            "Pour essayer sans données : `sillage demo`."
        )
        return 1
    print("OK")
    return 0


def cmd_demo(args: argparse.Namespace, cfg: Config) -> int:
    from sillage.demo import generate
    from sillage.normalize import normalize
    from sillage.reader import read_sessions

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        homes = generate(Path(tmp), sessions=args.sessions, seed=args.seed)
        recs: list[SessionRecord] = []
        for ident, home in homes.items():
            for raw in read_sessions([home]):
                recs.append(
                    normalize(
                        raw,
                        salt="demo",
                        user_identity=ident,
                        privacy_level="redacted",
                        extra_correction_patterns=cfg.extra_correction_patterns,
                        weights=cfg.friction,
                    )
                )
    a = analyze(recs, cfg.k_anonymity)
    opps = opportunities(a, cfg.thresholds)
    lsns = lessons(a, cfg.thresholds)
    title = "Sillage — démonstration (données synthétiques)"
    (out / "rapport.html").write_text(render_html(a, opps, lsns, title=title), encoding="utf-8")
    (out / "rapport.md").write_text(render_report(a, opps, title=title), encoding="utf-8")
    (out / "opportunites.md").write_text(render_opportunities(opps), encoding="utf-8")
    (out / "lecons.md").write_text(render_lessons(lsns), encoding="utf-8")
    agents = out / "AGENTS.exemple.md"
    agents.write_text("# AGENTS.md — dp/portail-citoyen\n\nInstructions du dépôt pour les agents.\n", encoding="utf-8")
    write_lessons_block(agents, render_lessons_block(lessons(a, cfg.thresholds, repository="dp/portail-citoyen")))
    previous = os.environ.get(cfg.salt_env)
    os.environ[cfg.salt_env] = "demo"  # sel fixe : n'écrit rien dans le magasin local de l'utilisateur
    try:
        export_bundle(recs, out / "bundle-equipe.jsonl.gz", cfg, "metrics")
    finally:
        if previous is None:
            os.environ.pop(cfg.salt_env, None)
        else:
            os.environ[cfg.salt_env] = previous
    print(
        f"Démo générée dans {out}/ : rapport.html, rapport.md, opportunites.md, lecons.md, "
        f"AGENTS.exemple.md, bundle-equipe.jsonl.gz"
    )
    print(f"{a.overview['sessions']} sessions synthétiques, {len(opps)} opportunités, {len(lsns)} leçons.")
    return 0


def cmd_mcp(args: argparse.Namespace, cfg: Config) -> int:
    from sillage.mcp_server import SillageMCP

    def loader() -> list[SessionRecord]:
        return _records(args, cfg)

    SillageMCP(loader, cfg).serve()
    return 0


def cmd_init_config(args: argparse.Namespace, cfg: Config) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"{path} existe déjà (utilisez --force pour l'écraser).", file=sys.stderr)
        return 1
    path.write_text(SAMPLE_CONFIG, encoding="utf-8")
    print(f"Écrit : {path}")
    return 0


def cmd_copilot_install(args: argparse.Namespace, cfg: Config) -> int:
    target = Path(os.path.expanduser(args.target))
    assets = resources.files("sillage") / "assets" / "copilot"
    copied = []
    for sub in ("agents", "skills"):
        src = assets / sub
        with resources.as_file(src) as src_path:
            for item in Path(src_path).iterdir():
                dest = target / sub / item.name
                if dest.exists() and not args.force:
                    print(f"Existe déjà, ignoré : {dest} (--force pour remplacer)")
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
                copied.append(str(dest))
    for c in copied:
        print(f"Installé : {c}")
    print(
        "\nMode par défaut (sans MCP) : les leçons arrivent aux agents par le bloc Sillage d'AGENTS.md "
        "(voir `sillage lessons --write` et examples/publier-lecons.sh).\n"
        "Pour que l'agent coach et le skill appellent `sillage` sans demande d'autorisation :\n"
        "  copilot --allow-tool='shell(sillage:*)'"
    )
    server = {"type": "local", "command": shutil.which("sillage") or "sillage", "args": ["mcp"], "tools": ["*"]}
    if args.mcp:
        cfg_path = target / "mcp-config.json"
        data = json.loads(cfg_path.read_text()) if cfg_path.exists() else {"mcpServers": {}}
        if cfg_path.exists():
            shutil.copy2(cfg_path, cfg_path.with_suffix(".json.bak"))
        data.setdefault("mcpServers", {})["sillage"] = server
        cfg_path.write_text(json.dumps(data, indent=2) + "\n")
        print(f"Serveur MCP « sillage » ajouté à {cfg_path} (sauvegarde : mcp-config.json.bak)")
    else:
        print(
            "\nOptionnel — seulement si votre organisation autorise les serveurs MCP tiers dans Copilot : "
            "relancez avec --mcp pour ajouter le serveur « sillage » (mémoire collective en direct)."
        )
    snippet = (assets / "instructions-snippet.md").read_text(encoding="utf-8")
    print("\nExtrait à ajouter aux instructions de vos dépôts (AGENTS.md) :\n")
    print(snippet)
    return 0


# -------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sillage",
        description="Observatoire des sessions GitHub Copilot CLI et moteur d'auto-amélioration des agents/skills.",
    )
    p.add_argument("--version", action="version", version=f"sillage {__version__}")
    p.add_argument("--config", help="fichier sillage.toml")
    sub = p.add_subparsers(dest="command", required=True)

    def data_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--since", help="période : 30d, 12w, 48h ou date ISO")
        sp.add_argument("--repo", help="filtrer sur un dépôt (owner/nom)")
        sp.add_argument("--bundle", nargs="+", help="utiliser des bundles d'équipe au lieu du magasin local")
        sp.add_argument(
            "--bundle-dir",
            action="append",
            help="dossier de bundles d'équipe (récursif, répétable), ex. partage réseau",
        )
        sp.add_argument("--copilot-home", action="append", help="répertoire .copilot (répétable)")
        sp.add_argument("--no-collect", action="store_true", help="ne pas rafraîchir le magasin avant l'analyse")
        sp.add_argument("-o", "--output", help="fichier de sortie (sinon : sortie standard)")

    sp = sub.add_parser("collect", help="lire les sessions Copilot (incrémental) vers le magasin local")
    sp.add_argument("--copilot-home", action="append")
    sp.set_defaults(func=cmd_collect)

    sp = sub.add_parser("report", help="rapport d'ensemble (md, html, json)")
    data_opts(sp)
    sp.add_argument("--format", choices=["md", "html", "json"], default="md")
    sp.add_argument("--title", default="Sillage — rapport des sessions Copilot")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("opportunities", help="opportunités d'amélioration priorisées")
    data_opts(sp)
    sp.add_argument("--format", choices=["md", "json"], default="md")
    sp.add_argument("--limit", type=int, default=15)
    sp.add_argument(
        "--category",
        choices=["skill", "agent", "instructions", "guardrail", "permissions", "mcp", "environment", "context"],
    )
    sp.set_defaults(func=cmd_opportunities)

    sp = sub.add_parser("lessons", help="leçons apprises (réinjectables dans AGENTS.md)")
    data_opts(sp)
    sp.add_argument("--skill")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--format", choices=["md", "json", "agents-md"], default="md")
    sp.add_argument("--write", metavar="FICHIER", help="insérer/mettre à jour le bloc de leçons dans ce fichier")
    sp.set_defaults(func=cmd_lessons)

    sp = sub.add_parser("export", help="bundle anonymisé partageable avec l'équipe")
    data_opts(sp)
    sp.add_argument("--level", choices=["metrics", "redacted"], help="niveau de confidentialité (défaut : config)")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("doctor", help="diagnostic : sessions trouvées, versions, dérive de format")
    sp.add_argument("--copilot-home", action="append")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("demo", help="générer une démonstration complète sur données synthétiques")
    sp.add_argument("--output-dir", default="sillage-demo")
    sp.add_argument("--sessions", type=int, default=150)
    sp.add_argument("--seed", type=int, default=7)
    sp.set_defaults(func=cmd_demo)

    sp = sub.add_parser("mcp", help="serveur MCP stdio, optionnel (si l'organisation autorise les serveurs MCP tiers)")
    sp.add_argument("--since")
    sp.add_argument("--repo")
    sp.add_argument("--bundle", nargs="+")
    sp.add_argument("--bundle-dir", action="append")
    sp.add_argument("--copilot-home", action="append")
    sp.add_argument("--no-collect", action="store_true")
    sp.set_defaults(func=cmd_mcp)

    sp = sub.add_parser("init-config", help="écrire un sillage.toml commenté")
    sp.add_argument("path", nargs="?", default="sillage.toml")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init_config)

    sp = sub.add_parser("copilot-install", help="installer l'agent coach et le skill dans ~/.copilot")
    sp.add_argument("--target", default=os.environ.get("COPILOT_HOME", "~/.copilot"))
    sp.add_argument(
        "--mcp",
        action="store_true",
        help="ajouter aussi le serveur MCP à mcp-config.json (si la politique MCP de l'organisation le permet)",
    )
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_copilot_install)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        return int(args.func(args, cfg) or 0)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
