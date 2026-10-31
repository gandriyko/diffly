from diffly.services.session_store import load_sessions, remember, save_sessions


def test_saved_and_recent_comparisons_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    data = load_sessions()
    pair = {"kind": "files", "left": "/tmp/a", "right": "/tmp/b",
            "options": {"ignore_case": True}}
    data["saved"].append({**pair, "name": "Example"})
    remember(data, pair)
    remember(data, pair)
    assert len(data["recent"]) == 1
    save_sessions(data)
    assert load_sessions() == data


def test_merge_session_paths_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    data = load_sessions()
    merge = {"kind": "merge", "base": "/tmp/base", "left": "/tmp/left",
             "right": "/tmp/right", "result": "/tmp/result"}
    remember(data, merge)
    save_sessions(data)
    assert load_sessions()["recent"] == [merge]
