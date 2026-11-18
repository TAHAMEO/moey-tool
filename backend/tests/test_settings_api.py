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


def test_integration_switches_are_enforced(owner_client, db):
    from clipforge.db.models import Source
    from clipforge.ingestion import resolver

    names = {i["name"] for i in owner_client.get("/api/control/integrations").json()}
    assert {"publish:youtube", "source:youtube", "provider:openai"} <= names
    assert owner_client.post("/api/control/disable_integration").status_code == 400  # name required
    assert owner_client.post("/api/control/disable_integration", json={"integration": "typo"}).status_code == 400
    off = owner_client.post("/api/control/disable_integration", json={"integration": "source:youtube"})
    assert off.status_code == 200 and off.json()["disabled_integrations"] == ["source:youtube"]

    source = Source(original_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", platform="youtube",
                    source_type="video", external_id="dQw4w9WgXcQ")
    db.add(source)
    db.flush()
    result = resolver.resolve(db, source)  # must refuse before any network access
    assert result.state == resolver.ACCESS_DENIED
    assert "turned off" in result.reasons[0]
    assert result.checks == [{"check": "integration_switch", "result": "disabled by owner"}]

    on = owner_client.post("/api/control/enable_integration", json={"integration": "source:youtube"})
    assert on.json()["disabled_integrations"] == []
