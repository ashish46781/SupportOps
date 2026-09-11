from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (500, "Internal Server Error", "API error (500): Internal Server Error"),
        (502, '{"detail":"Model unavailable"}', "API error (502): Model unavailable"),
        (503, "", "API error (503): Service Unavailable"),
        (500, "[]", "API error (500): []"),
        (200, "not json", "returned an invalid JSON response"),
    ],
)
def test_api_errors_are_displayed_without_crashing(monkeypatch, status, body, message):
    def request(method, url, **kwargs):
        return httpx.Response(status, text=body, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", request)
    app = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "ui" / "app.py"), default_timeout=30
    ).run()
    assert not app.exception
    assert len(app.error) == 1
    assert message in app.error[0].value
    assert not app.info


def test_ticket_switch_keeps_investigations_separate(monkeypatch, fake_services):
    first = fake_services.mongo.ticket
    second = dict(first, ticket_id="TKT-SECOND", subject="A different ticket")
    fake_services.workflow.invoke(
        {
            "ticket_id": first["ticket_id"],
            "run_id": "RUN-UI",
            "thread_id": "T",
            "trace": [],
        }
    )
    saved = fake_services.mongo.runs["RUN-UI"]

    def request(method, url, **kwargs):
        path = httpx.URL(url).path
        if path == "/tickets":
            body = [first, second]
        elif path.endswith("/investigate"):
            body = saved
        elif path.endswith("/memories"):
            body = []
        else:
            body = {
                "ticket": first if path.endswith(first["ticket_id"]) else second,
                "customer": fake_services.mongo.customer,
                "order": fake_services.mongo.order,
                "payment": fake_services.mongo.payment,
            }
        return httpx.Response(200, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", request)
    app = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "ui/app.py"), default_timeout=30
    ).run()
    next(button for button in app.button if button.label == "Investigate ticket").click().run()
    assert not app.exception
    assert any(select.label == "Decision" for select in app.selectbox)
    app.selectbox(key="selected_ticket").select("TKT-SECOND").run()
    assert not any(select.label == "Decision" for select in app.selectbox)
    app.selectbox(key="selected_ticket").select(first["ticket_id"]).run()
    assert any(select.label == "Decision" for select in app.selectbox)
    assert not app.exception


def test_escalated_run_does_not_offer_approval(monkeypatch, fake_services):
    first = fake_services.mongo.ticket

    def request(method, url, **kwargs):
        path = httpx.URL(url).path
        if path == "/tickets":
            body = [first]
        elif path.endswith("/memories"):
            body = []
        else:
            body = {"ticket": first, "customer": {}, "order": {}, "payment": {}}
        return httpx.Response(200, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", request)
    app = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "ui/app.py"), default_timeout=30
    )
    app.session_state["runs"] = {
        first["ticket_id"]: {
            "run_id": "RUN-ESCALATED",
            "status": "ESCALATED",
            "verification": {"verdict": "ESCALATE", "reason": "Security review required"},
        }
    }
    app.run()
    assert not app.exception
    decision = next(select for select in app.selectbox if select.label == "Decision")
    assert decision.options == ["Escalate", "Reject"]
