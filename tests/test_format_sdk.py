"""Conformité au format des journaux décrit par les types générés de ``@github/copilot-sdk``
(``dist/generated/session-events.d.ts``) : champs récents et points de reprise."""

from sillage.analyze import analyze
from sillage.insights import opportunities
from sillage.normalize import KNOWN_EVENT_TYPES, normalize


def _start(ev):
    return ev.add(
        "session.start",
        {
            "sessionId": "s1",
            "copilotVersion": "1.0.116",
            "producer": "copilot-agent",
            "startTime": "2026-11-20T14:00:00Z",
            "version": 1,
            "context": {"cwd": "/home/u/app", "gitRoot": "/home/u/app", "repository": "dp/app", "hostType": "github"},
        },
    )


def test_shell_execution_exit_code_is_preferred(events):
    _start(events)
    events.add("tool.execution_start", {"toolCallId": "c1", "toolName": "bash", "arguments": {"command": "npm test"}})
    # aucun marqueur textuel : seul shellExecution.exitCode signale l'échec
    events.add(
        "tool.execution_complete",
        {"toolCallId": "c1", "success": True, "result": {"content": "boom"}, "shellExecution": {"exitCode": 2}},
    )
    rec = normalize(events.raw(), salt="s", user_identity="u@h")
    assert rec.tool_calls[0].success is False and rec.tool_calls[0].exit_code == 2


def test_checkpoint_records_are_not_counted_twice(events):
    _start(events)
    events.add("user.message", {"content": "Corrige le test"})
    events.add("assistant.message", {"content": "ok", "messageId": "m1"})
    events.add("tool.execution_start", {"toolCallId": "c1", "toolName": "bash", "arguments": {"command": "ls"}})
    events.add("tool.execution_complete", {"toolCallId": "c1", "success": False, "error": {"message": "x"}})
    # reprise : le journal rejoue l'historique marqué checkpoint
    events.add("session.resume", {"resumeTime": "2026-11-20T15:00:00Z", "eventCount": 4}, checkpoint=True)
    events.add("user.message", {"content": "Corrige le test"}, checkpoint=True)
    events.add(
        "tool.execution_start",
        {"toolCallId": "c1b", "toolName": "bash", "arguments": {"command": "ls"}},
        checkpoint=True,
    )
    events.add(
        "tool.execution_complete", {"toolCallId": "c1b", "success": False, "error": {"message": "x"}}, checkpoint=True
    )
    rec = normalize(events.raw(), salt="s", user_identity="u@h")
    assert rec.user_messages == 1 and len(rec.tool_calls) == 1
    assert rec.replayed_events == 3
    assert rec.repository == "dp/app"


def test_ai_units_aborts_permissions_and_mcp_status(events):
    _start(events)
    events.add("assistant.usage", {"model": "m", "inputTokens": 10, "copilotUsage": {"totalNanoAiu": 400_000_000}})
    events.add("session.usage_checkpoint", {"totalNanoAiu": 900_000_000})
    events.add("abort", {"reason": "autopilot_credit_limit"})  # pas une interruption humaine
    events.add("abort", {"reason": "user_initiated"})
    events.add(
        "permission.requested",
        {
            "requestId": "p1",
            "permissionRequest": {
                "kind": "shell",
                "fullCommandText": "git status",
                "commands": [{"identifier": "git status", "readOnly": True}],
                "canOfferSessionApproval": True,
                "hasWriteFileRedirection": False,
                "intention": "x",
                "possiblePaths": [],
                "possibleUrls": [],
            },
        },
    )
    events.add("permission.completed", {"requestId": "p1", "result": {"kind": "approved-read-only-for-session"}})
    events.add("session.mcp_servers_loaded", {"servers": [{"name": "jira", "status": "stopped"}]})
    events.add("model.call_finished", {"dispatchDurationMs": 5, "editClassifierVersion": 1, "outcome": "ok"})
    events.add("session.shutdown", {"shutdownType": "routine", "totalNanoAiu": 1_200_000_000, "codeChanges": {}})
    rec = normalize(events.raw(), salt="s", user_identity="u@h")
    assert rec.nano_aiu == 1_200_000_000
    assert rec.aborts == 1
    assert rec.permissions[0].read_only is True
    assert rec.unknown_event_types == []
    a = analyze([rec])
    assert a.overview["ai_units"] == 1.2
    assert a.permissions[0]["approved"] == 1 and a.permissions[0]["read_only"] is True
    assert next(m for m in a.mcp if m["server"] == "jira")["disabled_sessions"] == 1


def test_no_allowlist_suggestion_for_writing_commands(events):
    _start(events)
    for i in range(6):
        events.add(
            "permission.requested",
            {
                "requestId": f"p{i}",
                "permissionRequest": {"kind": "shell", "commands": [{"identifier": "rm", "readOnly": False}]},
            },
        )
        events.add("permission.completed", {"requestId": f"p{i}", "result": {"kind": "approved"}})
    a = analyze([normalize(events.raw(), salt="s", user_identity="u@h")])
    assert not [o for o in opportunities(a) if o.category == "permissions"]


def test_known_types_cover_recent_sdk_events():
    for t in ("session.usage_checkpoint", "model.call_finished", "subagent.configured", "session.todos_changed"):
        assert t in KNOWN_EVENT_TYPES
