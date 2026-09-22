"""Presence merge and Windows logon classification. No database."""

import sys
import types

psycopg = types.ModuleType("psycopg")
rows = types.ModuleType("psycopg.rows")
rows.dict_row = object()
types_mod = types.ModuleType("psycopg.types")
json_mod = types.ModuleType("psycopg.types.json")
json_mod.Json = lambda value: value
websockets = types.ModuleType("websockets")
ws_exc = types.ModuleType("websockets.exceptions")
websockets.exceptions = ws_exc
sys.modules.update({
    "psycopg": psycopg,
    "psycopg.rows": rows,
    "psycopg.types": types_mod,
    "psycopg.types.json": json_mod,
    "websockets": websockets,
    "websockets.exceptions": ws_exc,
})

from nre_bridge import (
    accept_security_event,
    classify_win_event,
    disk_free_pct,
    logon_account,
    merge_telemetry,
    primary_user,
    session_kind_for,
)


def test_partial_sample_keeps_cpu_disk_and_user():
    prev = {
        "cpu_pct": 22.5,
        "mem_pct": 61.0,
        "disk": [{"volume": "C:", "used_pct": 40.0, "free": 100, "total": 200}],
        "processes": [{"name": "Adobe Premiere Pro", "cpu": 12.0, "watch": ["Premiere Pro"]}],
        "windows_sessions": [{"username": "editor", "session_name": "console"}],
        "foreground_app": "Adobe Premiere Pro",
        "idle_sec": 4,
        "active": True,
        "input_keys": 8,
    }
    merged = merge_telemetry(prev, {
        "idle_sec": 90,
        "active": True,
        "cpu_pct": None,
        "mem_pct": None,
        "disk": [],
        "processes": [],
        "windows_sessions": [],
        "foreground_app": "",
        "input_keys": None,
    })
    assert merged["cpu_pct"] == 22.5
    assert merged["mem_pct"] == 61.0
    assert merged["disk"][0]["volume"] == "C:"
    assert merged["processes"][0]["name"] == "Adobe Premiere Pro"
    assert merged["windows_sessions"][0]["username"] == "editor"
    assert merged["foreground_app"] == "Adobe Premiere Pro"
    assert merged["idle_sec"] == 90
    assert merged["input_keys"] == 8


def test_new_reading_replaces_and_empty_procstat_clears():
    prev = {"cpu_pct": 10, "processes": [{"name": "old", "watch": ["x"]}]}
    merged = merge_telemetry(prev, {
        "cpu_pct": 55,
        "processes": [],
        "saw_procs": True,
        "windows_sessions": [{"username": "alex"}],
    })
    assert merged["cpu_pct"] == 55
    assert merged["processes"] == []
    assert primary_user(merged["windows_sessions"]) == "alex"


def test_logoff_clears_carried_user():
    cleared = merge_telemetry(
        {"windows_sessions": [{"username": "editor"}], "user_cleared": True, "cpu_pct": 3},
        {"windows_sessions": [], "cpu_pct": None},
    )
    assert cleared["windows_sessions"] == []
    assert cleared["cpu_pct"] == 3
    restored = merge_telemetry(cleared, {"windows_sessions": [{"username": "editor"}]})
    assert primary_user(restored["windows_sessions"]) == "editor"
    assert restored["user_cleared"] is False


def test_security_events():
    assert classify_win_event(4624, "Microsoft-Windows-Security-Auditing", "") == "logon"
    assert classify_win_event(4647, "", "") == "logoff"
    assert classify_win_event(4800, "", "") == "lock"
    assert classify_win_event(4801, "", "") == "unlock"
    assert classify_win_event(1001, "", "") == "crash"
    assert accept_security_event(4624, "Logon Type: 5") is False
    assert accept_security_event(4624, "Logon Type:\t10") is True
    assert accept_security_event(4800, "") is True
    message = (
        "Subject:\r\n\tAccount Name:\t-\r\n"
        "New Logon:\r\n\tSecurity ID:\tS-1-5-21\r\n\tAccount Name:\tjsmith\r\n\tLogon Type:\t2\r\n"
    )
    assert logon_account(message, "logon") == "jsmith"


def test_fold_idle_and_proc_cpu():
    app = __import__("nre_bridge").App()
    folded = app._fold_metrics("EDIT-01", [
        {"name": "cpu", "tags": {}, "fields": {"usage_idle": 75}},
        {"name": "nre_idle", "tags": {}, "fields": {
            "idle_sec": 3, "foreground": "Adobe Premiere Pro",
            "mouse": 10, "clicks": 1, "keys": 4,
        }},
        {"name": "procstat", "tags": {"exe": "Adobe Premiere Pro.exe"}, "fields": {"cpu_usage": 40, "memory_rss": 500000000}},
        {"name": "procstat", "tags": {"exe": "idle"}, "fields": {"cpu_time": 999999999}},
    ])
    assert folded["cpu_pct"] == 25.0
    assert folded["foreground_app"] == "Adobe Premiere Pro"
    assert folded["input_keys"] == 4
    assert folded["saw_procs"] is True
    premiere = [p for p in folded["processes"] if "Premiere" in p["name"]][0]
    assert premiere["cpu"] == 40
    assert premiere["watch"] == ["Premiere Pro"]
    idle = [p for p in folded["processes"] if p["name"] == "idle"][0]
    assert idle["cpu"] is None


def test_disk_and_kind():
    assert disk_free_pct([
        {"used_pct": 10},
        {"used_pct": 80},
    ]) == 20.0
    assert session_kind_for([{"session_name": "RDP-Tcp#0"}], "") == "rdp"
    assert session_kind_for([{"session_name": "console"}], "alex@example.com") == "jump"


if __name__ == "__main__":
    test_partial_sample_keeps_cpu_disk_and_user()
    test_new_reading_replaces_and_empty_procstat_clears()
    test_logoff_clears_carried_user()
    test_security_events()
    test_disk_and_kind()
    print("ok")
