import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

from sillage.cli import main
from sillage.config import Config, load_config
from sillage.insights import (
    MARK_END,
    MARK_START,
    lessons,
    opportunities,
    render_lessons_block,
    write_lessons_block,
)
from sillage.mcp_server import SillageMCP
from sillage.report_html import render_html
from sillage.report_md import render_report
from sillage.store import LocalStore, export_bundle, load_bundles


# ------------------------------------------------------------ détections
def test_demo_planted_issues_are_detected(demo_analysis):
    opps = opportunities(demo_analysis)
    titles = " | ".join(o.title for o in opps)
    assert "npm test" in titles  # script npm mal nommé
    assert "mvn test" in titles  # proxy / certificats Artifactory
    assert "jq" in titles  # hook de garde-fou cassé
    assert "servicenow-incident" in titles  # skill souvent corrigé
    assert "dynatrace" in titles  # serveur MCP qui ne démarre pas
    assert "aq-tests" in titles  # sous-agent instable
    assert "confluence-recherche" in titles  # skill dormant
    assert "git push" in titles  # refus humain -> garde-fou
    assert "git status" in titles  # approbation répétée -> allowlist
    assert "terraform plan" in titles  # sortie volumineuse
    assert any(o.category == "context" and "définitions d'outils" in o.title for o in opps)
    # pas de faux positif : splunk-investigation est sain
    assert not any(o.target == "splunk-investigation" and o.category == "skill" for o in opps)


def test_lessons_capture_observed_fixes(demo_analysis):
    items = lessons(demo_analysis, repository="dp/portail-citoyen")
    text = " ".join(lsn.text for lsn in items)
    assert "npm run test:unit" in text
    assert all(lsn.scope in ("repo:dp/portail-citoyen", "global") for lsn in items)
    mvn = " ".join(lsn.text for lsn in lessons(demo_analysis, repository="dp/api-dossiers"))
    assert "mvn -s ~/.m2/settings-dp.xml test" in mvn


def test_no_per_person_tables(demo_analysis):
    md = render_report(demo_analysis, opportunities(demo_analysis))
    for rec in demo_analysis.records[:20]:
        assert rec.user not in md


def test_k_anonymity_hides_excerpts_for_single_user(demo_analysis):
    from sillage.analyze import analyze

    recs = demo_analysis.records
    users = sorted({r.user for r in recs})
    few = [r for r in recs if r.user == users[0]] + [r for r in recs if r.user == users[1]][:1]
    a = analyze(few, k_anonymity=3)
    for p in a.errors:
        assert p.excerpts == []


# ----------------------------------------------------------------- rapports
def test_html_is_self_contained(demo_analysis):
    html = render_html(demo_analysis, opportunities(demo_analysis), lessons(demo_analysis))
    assert html.startswith("<!doctype html>")
    assert "http://" not in html.replace("http://www.w3.org", "") and "https://" not in html
    assert "prefers-color-scheme:dark" in html and 'data-theme="dark"' in html
    assert html.count("<svg") >= 4


def test_lessons_block_is_idempotent(tmp_path, demo_analysis):
    f = tmp_path / "AGENTS.md"
    f.write_text("# Mon dépôt\n\nTexte existant.\n")
    block = render_lessons_block(lessons(demo_analysis, repository="dp/portail-citoyen"))
    assert write_lessons_block(f, block) is True
    assert write_lessons_block(f, block) is False
    content = f.read_text()
    assert content.count(MARK_START) == 1 and content.count(MARK_END) == 1
    assert content.startswith("# Mon dépôt\n\nTexte existant.\n")
    write_lessons_block(f, render_lessons_block([]))
    assert "Aucune leçon" in f.read_text() and f.read_text().count(MARK_START) == 1


# ------------------------------------------------------- magasin et bundles
def test_collect_is_incremental(demo_homes):
    store = LocalStore()
    cfg = Config()
    homes = [str(h) for h in list(demo_homes.values())[:2]]
    first = store.collect(cfg, homes)
    assert first.new_or_updated > 0 and first.total == first.new_or_updated
    second = store.collect(cfg, homes)
    assert second.new_or_updated == 0 and second.total == first.total


def test_bundle_roundtrip_strips_text(tmp_path, demo_records):
    cfg = Config()
    path = tmp_path / "b.jsonl.gz"
    export_bundle(demo_records, path, cfg, "metrics")
    with gzip.open(path, "rt") as fh:
        manifest = json.loads(fh.readline())
        raw = fh.read()
    assert manifest["magic"] == "sillage-bundle" and manifest["count"] == len(demo_records)
    assert "mauvais groupe" not in raw and "settings-dp.xml" not in raw
    back = load_bundles([path])
    assert len(back) == len(demo_records)
    assert {r.session_id for r in back}.isdisjoint({r.session_id for r in demo_records})  # ids re-pseudonymisés
    assert all(r.privacy_level == "metrics" for r in back)
    # fusion de deux bundles identiques : dédoublonnage
    path2 = tmp_path / "b2.jsonl.gz"
    export_bundle(demo_records, path2, cfg, "metrics")
    assert len(load_bundles([path, path2])) == len(demo_records)


def test_bundle_rejects_foreign_file(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"hello": 1}\n')
    with pytest.raises(ValueError):
        load_bundles([p])


def test_config_loading(tmp_path):
    f = tmp_path / "sillage.toml"
    f.write_text('[sillage]\nk_anonymity = 5\n[insights]\nmin_sessions = 7\n[corrections]\nextra_patterns = ["zut"]\n')
    cfg = load_config(str(f))
    assert cfg.k_anonymity == 5 and cfg.thresholds.min_sessions == 7 and cfg.extra_correction_patterns == ["zut"]
    f.write_text('[sillage]\nexport_privacy = "full"\n')
    with pytest.raises(ValueError):
        load_config(str(f))


# --------------------------------------------------------------------- MCP
def _rpc(server, method, params=None, mid=1):
    return server.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def test_mcp_protocol(demo_records):
    srv = SillageMCP(lambda: demo_records, Config())
    init = _rpc(srv, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"]["tools"] is not None
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in _rpc(srv, "tools/list")["result"]["tools"]}
    assert names == {"sillage_lessons", "sillage_opportunities", "sillage_overview", "sillage_skill_health"}
    res = _rpc(srv, "tools/call", {"name": "sillage_lessons", "arguments": {"repository": "dp/portail-citoyen"}})
    assert res["result"]["isError"] is False and "npm run test:unit" in res["result"]["content"][0]["text"]
    res = _rpc(srv, "tools/call", {"name": "sillage_skill_health", "arguments": {"skill": "servicenow-incident"}})
    assert res["result"]["structuredContent"]["skill"]["skill"] == "servicenow-incident"
    assert _rpc(srv, "tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert _rpc(srv, "bogus/method")["error"]["code"] == -32601
    unknown = _rpc(srv, "initialize", {"protocolVersion": "1999-01-01"})
    assert unknown["result"]["protocolVersion"] == "2025-11-25"


def test_mcp_stdio_subprocess(tmp_path, demo_homes):
    """Le serveur parle bien JSON-RPC ligne par ligne sur stdio (comme le lance Copilot CLI)."""
    home = str(next(iter(demo_homes.values())))
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "sillage_overview", "arguments": {}}},
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "sillage", "mcp", "--copilot-home", home],
        input="\n".join(json.dumps(m) for m in msgs) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "SILLAGE_HOME": str(tmp_path / "h"),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        },
    )
    lines = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()]
    assert [ln["id"] for ln in lines] == [1, 2], proc.stderr
    assert json.loads(lines[1]["result"]["content"][0]["text"])["overview"]["sessions"] > 0


# --------------------------------------------------------------------- CLI
def test_cli_end_to_end(tmp_path, demo_homes, capsys):
    homes = [a for h in demo_homes.values() for a in ("--copilot-home", str(h))]
    assert main(["collect", *homes]) == 0
    assert main(["report", "--no-collect", "--format", "html", "-o", str(tmp_path / "r.html")]) == 0
    assert (tmp_path / "r.html").read_text().startswith("<!doctype html>")
    assert main(["opportunities", "--no-collect", "--format", "json", "-o", str(tmp_path / "o.json")]) == 0
    assert len(json.loads((tmp_path / "o.json").read_text())) > 5
    assert (
        main(["lessons", "--no-collect", "--repo", "dp/portail-citoyen", "--write", str(tmp_path / "AGENTS.md")]) == 0
    )
    assert "npm run test:unit" in (tmp_path / "AGENTS.md").read_text()
    assert main(["export", "--no-collect", "-o", str(tmp_path / "b.jsonl.gz")]) == 0
    assert main(["report", "--bundle", str(tmp_path / "b.jsonl.gz"), "--since", "3650d"]) == 0
    assert main(["doctor", *homes]) == 0
    assert main(["init-config", str(tmp_path / "s.toml")]) == 0
    assert main(["init-config", str(tmp_path / "s.toml")]) == 1
    capsys.readouterr()


def test_cli_demo(tmp_path):
    assert main(["demo", "--output-dir", str(tmp_path / "d"), "--sessions", "40"]) == 0
    for name in (
        "rapport.html",
        "rapport.md",
        "opportunites.md",
        "lecons.md",
        "AGENTS.exemple.md",
        "bundle-equipe.jsonl.gz",
    ):
        assert (tmp_path / "d" / name).exists(), name


def test_cli_copilot_install(tmp_path):
    target = tmp_path / ".copilot"
    assert main(["copilot-install", "--target", str(target), "--mcp"]) == 0
    assert (target / "agents" / "sillage-coach.agent.md").exists()
    assert (target / "skills" / "sillage-amelioration" / "SKILL.md").exists()
    cfg = json.loads((target / "mcp-config.json").read_text())
    assert cfg["mcpServers"]["sillage"]["args"] == ["mcp"]
    # second passage : conserve les autres serveurs et fait une sauvegarde
    cfg["mcpServers"]["autre"] = {"type": "local", "command": "x"}
    (target / "mcp-config.json").write_text(json.dumps(cfg))
    assert main(["copilot-install", "--target", str(target), "--mcp"]) == 0
    cfg2 = json.loads((target / "mcp-config.json").read_text())
    assert "autre" in cfg2["mcpServers"] and (target / "mcp-config.json.bak").exists()


def test_doctor_without_sessions():
    assert main(["doctor"]) == 1


def test_empty_store_reports_gracefully(capsys):
    assert main(["report"]) == 0
    assert "Aucune session" in capsys.readouterr().out


# --------------------------------------------------- organisation sans MCP
def test_bundle_dir_feeds_team_lessons(tmp_path, demo_records, capsys):
    share = tmp_path / "partage" / "2026-S48"
    share.mkdir(parents=True)
    users = sorted({r.user for r in demo_records})
    for i, u in enumerate(users[:3]):
        export_bundle([r for r in demo_records if r.user == u], share / f"p{i}.jsonl.gz", Config(), "metrics")
    out = tmp_path / "AGENTS.md"
    args = ["lessons", "--bundle-dir", str(tmp_path / "partage"), "--repo", "dp/portail-citoyen", "--write", str(out)]
    assert main(args) == 0
    assert "npm run test:unit" in out.read_text()
    capsys.readouterr()
    assert main(["opportunities", "--bundle-dir", str(tmp_path / "partage"), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)
    assert main(["report", "--bundle-dir", str(tmp_path / "vide-inexistant")]) == 1


def test_copilot_install_without_mcp_writes_no_mcp_config(tmp_path, capsys):
    target = tmp_path / ".copilot"
    assert main(["copilot-install", "--target", str(target)]) == 0
    printed = capsys.readouterr().out
    assert not (target / "mcp-config.json").exists()
    assert "shell(sillage:*)" in printed and "AGENTS.md" in printed
    agent = (target / "agents" / "sillage-coach.agent.md").read_text()
    assert "sillage opportunities" in agent and "sans serveur MCP" in agent


def test_policy_disabled_mcp_is_not_an_outage(events):
    from sillage.analyze import analyze
    from sillage.normalize import normalize

    recs = []
    for i in range(5):
        events.events.clear()
        events.add("session.start", {"context": {"repository": "dp/app"}})
        events.add("session.mcp_servers_loaded", {"servers": [{"name": "splunk", "status": "disabled"}]})
        recs.append(normalize(events.raw(f"s{i}"), salt="s", user_identity=f"u{i}"))
    a = analyze(recs)
    row = next(m for m in a.mcp if m["server"] == "splunk")
    assert row["disabled_sessions"] == 5 and row["failed_status_sessions"] == 0
    assert not [o for o in opportunities(a) if o.category == "mcp"]


def test_example_scripts_are_valid_bash():
    for script in (Path(__file__).resolve().parents[1] / "examples").glob("*.sh"):
        assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0, script
