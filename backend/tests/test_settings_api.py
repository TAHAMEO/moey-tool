from __future__ import annotations


def test_settings_defaults_and_validation(owner_client):
    settings = owner_client.get("/api/settings").json()
    assert settings["ai"]["local_first"] is True
    assert settings["publishing"]["policy"] == "approval_required"
    assert settings["clips"]["aspects"] == ["9:16"]
    ok = owner_client.patch("/api/settings/clips", json={"default_count": 7, "caption_style": "boxed"})
    assert ok.status_code == 200 and ok.json()["default_count"] == 7
    bad = owner_client.patch("/api/settings/clips", json={"default_count": 999})
    assert bad.status_code == 400
    typo = owner_client.patch("/api/settings/ai", json={"dailybudget": 1})
    assert typo.status_code == 400  # unknown keys are rejected, not ignored
    assert owner_client.patch("/api/settings/nope", json={}).status_code == 400


def test_control_pause_resume(owner_client):
    assert owner_client.post("/api/control/pause").json()["paused"] is True
    assert owner_client.get("/api/settings").json()["control"]["paused"] is True
    assert owner_client.post("/api/control/resume").json()["paused"] is False
    assert owner_client.post("/api/control/bogus").status_code == 400


def test_usage_summary_shape(owner_client):
    summary = owner_client.get("/api/usage/summary").json()
    assert summary["today_usd"] == 0 and summary["budgets"]["daily"] == 2.0
