"""Diagnostic d'onboarding : points flous levés automatiquement sur tout l'historique."""

import json

from sillage.cli import main
from sillage.config import Config
from sillage.onboard import count_ide_chats, run_onboarding, suggest_patterns
from sillage.store import LocalStore


def _session(ev, repo_root, n):
    ev.add(
        "session.start",
        {
            "sessionId": f"s{n}",
            "copilotVersion": "1.0.116",
            "context": {"repository": "dp/app", "gitRoot": str(repo_root), "cwd": str(repo_root)},
        },
    )
    ev.add("user.message", {"content": "Lance le script de purge"})
    ev.add("assistant.message", {"content": "ok", "messageId": "m"})
    ev.add(
        "tool.execution_start",
        {"toolCallId": "c1", "toolName": "bash", "arguments": {"command": "./purge-dpprod.sh"}},
    )
    ev.add("tool.execution_complete", {"toolCallId": "c1", "success": True, "shellExecution": {"exitCode": 1}})
    ev.add("user.message", {"content": "Utilise plutôt le README du module"})  # correction non reconnue
    ev.add("user.message", {"content": "Non, c'est faux"}, checkpoint=True)  # rejoué : ignoré
    ev.add("session.nouveau_type_inconnu", {})


def _home(tmp_path, events_factory, n=12):
    home = tmp_path / "copilot"
    repo_root = tmp_path / "clone-app"
    repo_root.mkdir()
    (repo_root / "AGENTS.md").write_text("# AGENTS\n")
    for i in range(n):
        ev = events_factory()
        _session(ev, repo_root, i)
        ev.write(home, f"s{i}")
    return home, repo_root


def _run(tmp_path, home, ide_roots, cfg=None):
    cfg = cfg or Config()
    store = LocalStore()
    store.collect(cfg, [str(home)])
    return run_onboarding(list(store.load().values()), cfg, homes=[home], ide=ide_roots, copilot_target=tmp_path / "cp")


def test_onboarding_resolves_unclear_points(tmp_path):
    from conftest import Events

    home, repo_root = _home(tmp_path, Events)
    ide = tmp_path / "Code"
    chats = ide / "User" / "workspaceStorage" / "ab12" / "chatSessions"
    chats.mkdir(parents=True)
    for i in range(30):
        (chats / f"{i}.json").write_text("{}")
    o = _run(tmp_path, home, [ide])
    by = {c.id: c for c in o.checks}

    assert by["sessions"].status == "ok" and "12 sessions" in " ".join(by["sessions"].findings)
    fmt = " ".join(by["format"].findings)
    assert by["format"].status == "attention" and "session.nouveau_type_inconnu" in fmt
    assert "checkpoint" in fmt and "shellExecution.exitCode" in fmt
    assert o.depots == [("dp/app", str(repo_root))]
    assert by["depots"].status == "a-confirmer" and "sans l'extrait" in " ".join(by["depots"].findings)
    assert by["ide"].status == "a-confirmer"
    assert any(s["pattern"] == r"\bdpprod\b" and s["leaves_now"] for s in o.suggested_patterns)
    assert by["confidentialite"].status == "a-confirmer"
    assert by["corrections"].status == "a-confirmer"
    assert "plutôt le README" in " ".join(by["corrections"].findings)
    assert "c'est faux" not in " ".join(by["corrections"].findings)  # événement rejoué ignoré
    assert by["installation"].status == "attention"
    assert o.ready  # rien de bloquant
    assert any("participeront" in q for q in o.questions)


def test_shareable_output_contains_no_session_text(tmp_path):
    from conftest import Events

    home, repo_root = _home(tmp_path, Events, n=3)
    o = _run(tmp_path, home, [])
    shared = json.dumps(o.to_dict(shareable=True), ensure_ascii=False)
    for secret in ("dp/app", "dpprod", "README", str(repo_root), str(home)):
        assert secret not in shared
    full = json.dumps(o.to_dict(), ensure_ascii=False)
    assert "dpprod" in full  # la fiche locale, elle, montre les exemples


def test_configured_patterns_silence_suggestions(tmp_path):
    from conftest import Events

    home, _ = _home(tmp_path, Events, n=3)
    o = _run(tmp_path, home, [], Config(redaction_patterns=[r"\bdpprod\b"]))
    assert not [s for s in o.suggested_patterns if "dpprod" in s["pattern"]]


def test_suggest_patterns_heuristics():
    from collections import Counter

    vals = Counter(
        {
            "kubectl get pods prod-paiements": 3,
            "dossier DOS-123456 introuvable": 2,
            "Client TREMBLAY Jean": 1,
            "ERROR Failed to compile": 5,
            "npm run test:unit": 9,
            "reproduce the bug": 4,
        }
    )
    pats = {s["pattern"]: s for s in suggest_patterns(vals)}
    assert r"\bprod[-_][a-z0-9-]+\b" in pats
    assert r"\bDOS-\d{6}\b" in pats
    assert any("personne" in s["kind"] for s in pats.values())
    assert not any("ERROR" in ex for s in pats.values() for ex in s["examples"])
    assert not any("reproduce" in ex for s in pats.values() for ex in s["examples"])


def test_count_ide_chats_counts_without_reading(tmp_path):
    root = tmp_path / "Code"
    d = root / "User" / "globalStorage" / "emptyWindowChatSessions"
    d.mkdir(parents=True)
    (d / "a.json").write_text("secret")
    assert count_ide_chats([root, tmp_path / "absent"]) == {str(root): [1, 1]}


def test_onboard_cli(tmp_path, capsys):
    from conftest import Events

    home, _ = _home(tmp_path, Events, n=2)
    depots = tmp_path / "depots.txt"
    assert main(["onboard", "--copilot-home", str(home), "--depots", str(depots)]) == 0
    out = capsys.readouterr().out
    assert "Fiche d'onboarding" in out and "Document local" in out
    assert "dp/app" in depots.read_text()
    assert main(["onboard", "--copilot-home", str(home), "--partageable", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True
    # aucune session : bloquant, code de sortie 2
    assert main(["onboard", "--copilot-home", str(tmp_path / "vide")]) == 2
