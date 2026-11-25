from sillage.normalize import is_correction, normalize


def _base(ev):
    return ev.add(
        "session.start",
        {"sessionId": "s1", "copilotVersion": "1.0.115", "context": {"repository": "dp/app", "branch": "feature/x"}},
    )


def test_basic_session(events):
    _base(events)
    events.add("user.message", {"content": "Ajoute un test"})
    events.add("assistant.turn_start", {"turnId": "1"})
    events.add("assistant.usage", {"model": "m1", "inputTokens": 1000, "outputTokens": 50, "cacheReadTokens": 800})
    events.add("assistant.message", {"content": "ok", "model": "m1"})
    events.add("tool.execution_start", {"toolCallId": "c1", "toolName": "view", "arguments": {"path": "src/App.tsx"}})
    events.add("tool.execution_complete", {"toolCallId": "c1", "success": True, "result": {"content": "abc"}})
    events.add(
        "session.shutdown",
        {
            "shutdownType": "routine",
            "totalPremiumRequests": 2,
            "codeChanges": {"linesAdded": 10, "linesRemoved": 2, "filesModified": ["a"]},
        },
    )
    rec = normalize(events.raw(), salt="s", user_identity="u@h")
    assert rec.repository == "dp/app" and rec.branch_kind == "feature"
    assert rec.copilot_version == "1.0.115"
    assert rec.user_messages == 1 and rec.assistant_turns == 1
    assert rec.models == {"m1": 1} and rec.input_tokens == 1000
    assert rec.tool_calls[0].category == "file" and rec.tool_calls[0].target == ".tsx"
    assert rec.tool_calls[0].success is True and rec.tool_calls[0].output_chars == 3
    assert rec.lines_added == 10 and rec.files_modified == 1 and rec.premium_requests == 2
    assert rec.friction_label == "fluide" and not rec.abandoned
    assert rec.duration_s > 0


def test_shell_exit_code_failure_loop_and_recovery(events):
    _base(events)
    events.add("user.message", {"content": "lance les tests"})
    events.add("assistant.message", {"content": "ok"})
    for i in range(3):
        events.add(
            "tool.execution_start", {"toolCallId": f"c{i}", "toolName": "bash", "arguments": {"command": "npm test"}}
        )
        events.add(
            "tool.execution_complete",
            {
                "toolCallId": f"c{i}",
                "success": True,
                "result": {"content": 'npm error Missing script: "test"\n<exited with exit code 1>'},
            },
        )
    events.add(
        "tool.execution_start", {"toolCallId": "ok", "toolName": "bash", "arguments": {"command": "npm run test:unit"}}
    )
    events.add(
        "tool.execution_complete",
        {"toolCallId": "ok", "success": True, "result": {"content": "142 passed\n<exited with exit code 0>"}},
    )
    rec = normalize(events.raw(), salt="s", user_identity="u", privacy_level="redacted")
    fails = [t for t in rec.tool_calls if t.success is False]
    assert len(fails) == 3 and all(t.exit_code == 1 for t in fails)
    assert fails[0].error_signature == 'npm error Missing script: "<val>"'
    assert rec.failure_loops == 1
    assert fails[-1].recovered_by == "npm run test:unit"
    assert rec.tool_calls[-1].success is True


def test_recovery_with_same_identifier_uses_detail_only_when_redacted(events):
    _base(events)
    events.add("assistant.message", {"content": "ok"})
    events.add("tool.execution_start", {"toolCallId": "a", "toolName": "bash", "arguments": {"command": "mvn test"}})
    events.add(
        "tool.execution_complete",
        {
            "toolCallId": "a",
            "success": True,
            "result": {"content": "PKIX path building failed\n<exited with exit code 1>"},
        },
    )
    events.add(
        "tool.execution_start",
        {"toolCallId": "b", "toolName": "bash", "arguments": {"command": "mvn -s ~/.m2/settings-dp.xml test"}},
    )
    events.add(
        "tool.execution_complete",
        {"toolCallId": "b", "success": True, "result": {"content": "BUILD SUCCESS\n<exited with exit code 0>"}},
    )
    red = normalize(events.raw(), salt="s", user_identity="u", privacy_level="redacted")
    met = normalize(events.raw(), salt="s", user_identity="u", privacy_level="metrics")
    assert red.tool_calls[0].recovered_by_detail == "mvn -s ~/.m2/settings-dp.xml test"
    assert met.tool_calls[0].recovered_by_detail is None and met.tool_calls[0].command_excerpt is None


def test_metrics_level_keeps_no_free_text(events):
    _base(events)
    events.add("assistant.message", {"content": "ok"})
    events.add("user.message", {"content": "Non, c'est le mauvais fichier, secret=abc12345"})
    events.add("tool.execution_start", {"toolCallId": "c", "toolName": "bash", "arguments": {"command": "cat x"}})
    events.add("tool.execution_complete", {"toolCallId": "c", "success": False, "error": {"message": "boom jean@x.ca"}})
    rec = normalize(events.raw(), salt="s", user_identity="u", privacy_level="metrics")
    dumped = str(rec.to_dict())
    assert "mauvais fichier" not in dumped and "jean@x.ca" not in dumped and "abc12345" not in dumped
    assert rec.corrections[0].category == "correction" and rec.corrections[0].excerpt is None
    red = normalize(events.raw(), salt="s", user_identity="u", privacy_level="redacted")
    assert "mauvais fichier" in red.corrections[0].excerpt and "abc12345" not in red.corrections[0].excerpt


def test_skill_attribution_window_and_denial(events):
    _base(events)
    events.add("assistant.message", {"content": "ok"})
    events.add("skill.invoked", {"name": "servicenow-incident", "trigger": "agent-invoked"})
    events.add("user.message", {"content": "Non, c'est le mauvais groupe d'assignation"})
    events.add(
        "permission.requested",
        {
            "requestId": "r1",
            "permissionRequest": {
                "kind": "shell",
                "toolCallId": "t",
                "fullCommandText": "git push --force",
                "commands": [{"identifier": "git push", "readOnly": False}],
            },
        },
    )
    events.add("permission.completed", {"requestId": "r1", "result": {"kind": "denied-interactively-by-user"}})
    events.add("user.message", {"content": "Non ! ne pousse jamais sur main"})
    events.add("user.message", {"content": "ok"})
    events.add("user.message", {"content": "c'est faux"})
    rec = normalize(events.raw(), salt="s", user_identity="u")
    cats = [(c.category, c.after_skill) for c in rec.corrections]
    assert cats[0] == ("correction", "servicenow-incident")
    assert cats[1] == ("correction", None)  # vise l'action refusée, pas le skill
    assert cats[2] == ("correction", None)  # hors fenêtre d'attribution
    assert rec.user_denials == 1 and rec.permissions[0].target == "git push"


def test_subagents_mcp_hooks_and_context(events):
    _base(events)
    events.add("session.skills_loaded", {"skills": [{"name": "a", "enabled": True}, {"name": "b", "enabled": False}]})
    events.add(
        "session.mcp_servers_loaded",
        {
            "servers": [
                {"name": "jira", "status": "connected"},
                {"name": "dt", "status": "failed", "error": "spawn dt ENOENT"},
            ]
        },
    )
    events.add("session.usage_info", {"toolDefinitionsTokens": 20000, "tokenLimit": 128000})
    events.add(
        "session.custom_agents_updated", {"agents": [{"name": "aq"}], "errors": ["Unknown tool 'x' in agent 'aq'"]}
    )
    events.add("subagent.started", {"toolCallId": "t1", "agentName": "aq"})
    events.add(
        "subagent.failed", {"toolCallId": "t1", "agentName": "aq", "durationMs": 1000, "error": "max tool calls"}
    )
    events.add("hook.end", {"hookType": "preToolUse", "success": False, "error": {"message": "jq: not found"}})
    events.add("session.compaction_complete", {"success": True})
    events.add("abort", {"reason": "user_initiated"})
    events.add("weird.future_event", {})
    rec = normalize(events.raw(), salt="s", user_identity="u")
    assert rec.skills_available == ["a"]
    assert rec.mcp_servers == {"jira": "connected", "dt": "failed"} and rec.mcp_server_errors[0].startswith("dt:")
    assert rec.tool_definitions_tokens == 20000 and rec.context_token_limit == 128000
    assert rec.agent_definition_errors and rec.subagents[0].success is False
    assert rec.hook_failures == ["preToolUse: jq: not found"]
    assert rec.compactions == 1 and rec.aborts == 1 and rec.abandoned
    assert rec.unknown_event_types == ["weird.future_event"]
    assert rec.friction_score > 15


def test_tolerates_garbage(events):
    events.add("tool.execution_complete", {"toolCallId": "orphan", "success": False})
    events.add("user.message", "pas un dict")
    events.add("session.shutdown", {"modelMetrics": "nope", "codeChanges": None})
    rec = normalize(events.raw(), salt="s", user_identity="u")
    assert rec.tool_calls[0].name == "inconnu"


def test_subagent_events_not_counted_as_user_turns(events):
    _base(events)
    events.add("user.message", {"content": "x"}, agentId="sub-1")
    events.add("assistant.turn_start", {"turnId": "1"}, agentId="sub-1")
    rec = normalize(events.raw(), salt="s", user_identity="u")
    assert rec.user_messages == 0 and rec.assistant_turns == 0


def test_is_correction_fr_en():
    for t in [
        "Non, pas ça",
        "Ça ne marche toujours pas",
        "c'est faux",
        "tu as oublié le test",
        "Je t'ai dit de ne pas",
        "That's wrong",
        "still failing",
        "revert that",
        "Arrête",
    ]:
        assert is_correction(t), t
    for t in ["Continue", "Parfait, merci", "Ajoute la pagination", "Peux-tu vérifier les logs ?", "non-régression OK"]:
        assert not is_correction(t), t
