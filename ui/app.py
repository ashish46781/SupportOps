from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")
st.set_page_config(page_title="SupportOps", page_icon=None, layout="wide")


def api(method: str, path: str, **kwargs: Any) -> Any:
    try:
        response = httpx.request(method, f"{API_URL}{path}", timeout=300, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        try:
            payload = exc.response.json()
        except ValueError:
            payload = None
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            detail = detail.get("message", "Request failed") + (
                f" · Run {detail['run_id']}" if detail.get("run_id") else ""
            )
        st.error(
            f"API error ({exc.response.status_code}): {detail or exc.response.text or exc.response.reason_phrase}"
        )
    except httpx.HTTPError:
        st.error(
            "The support service is unavailable or the request timed out. Check the service before retrying."
        )
    except ValueError:
        st.error("The support service returned an invalid JSON response.")
    return None


def show_verification(run: dict) -> None:
    verification = run.get("verification", {})
    reason = verification.get("reason", "")
    status = run.get("status", "")
    if status == "APPROVED":
        st.success("Review approved. No customer action has been executed.")
    elif status == "REJECTED":
        st.warning("Review rejected.")
    elif status == "READY_FOR_REVIEW" and verification.get("verdict") == "PASS":
        st.success("Ready for review · " + reason)
    elif status == "ESCALATED":
        st.error("Specialist review required · " + reason)
    elif status == "FAILED":
        st.error(run.get("error", "Investigation failed. Check the service and try again."))
    else:
        st.warning("More evidence needed · " + reason)
    gaps = run.get("missing_sources", []) + run.get("diagnosis", {}).get("missing_facts", [])
    if gaps:
        st.write("**Missing information**")
        for gap in gaps:
            st.write("• " + gap)
    for conflict in verification.get("contradictions", []):
        st.warning("Conflicting evidence: " + conflict)


def show_review(run: dict, ticket_id: str) -> None:
    st.subheader("Review response")
    if run.get("review"):
        review = run["review"]
        st.write("**Decision:** " + review["decision"].replace("_", " ").title())
        st.write(run.get("approved_response") or run.get("draft_response", ""))
        if review.get("reviewer_note"):
            st.caption(review["reviewer_note"])
        if run.get("memory_status") == "FAILED":
            st.warning("Your review is saved. Its memory update failed.")
            if st.button("Retry memory update", key=f"memory-{run['run_id']}"):
                result = api("POST", f"/runs/{run['run_id']}/memory/retry")
                if result:
                    st.session_state.runs[ticket_id] = api("GET", f"/runs/{run['run_id']}") or run
                    st.rerun()
        elif run.get("memory_status") in {"PENDING", "WRITING"}:
            st.caption("Review saved; memory update is pending.")
        elif run.get("memory_status") == "SKIPPED":
            st.caption("No durable fact needed to be remembered.")
        return
    if run.get("status") not in {
        "READY_FOR_REVIEW",
        "ESCALATED",
        "INSUFFICIENT_EVIDENCE",
        "NEEDS_CUSTOMER_INFO",
    }:
        st.caption("A completed investigation is required before review.")
        return
    can_approve = (
        run.get("status") == "READY_FOR_REVIEW"
        and run.get("verification", {}).get("verdict") == "PASS"
    )
    options = (
        ["Approve", "Edit and approve", "Escalate", "Reject"]
        if can_approve
        else ["Escalate", "Reject"]
    )
    with st.form(f"review-{run['run_id']}"):
        decision = st.selectbox("Decision", options)
        response = st.text_area(
            "Response draft", run.get("draft_response", ""), height=160, disabled=not can_approve
        )
        note = st.text_area("Review note", help="Required when escalating or rejecting.")
        submitted = st.form_submit_button("Save review", type="primary")
    if submitted:
        code = decision.upper().replace(" ", "_")
        if code in {"ESCALATE", "REJECT"} and not note.strip():
            st.warning("Add a review note before saving.")
            return
        if code == "APPROVE" and response.strip() != run.get("draft_response", "").strip():
            st.warning("Choose Edit and approve to save your changes to the draft.")
            return
        result = api(
            "POST",
            f"/runs/{run['run_id']}/review",
            json={
                "decision": code,
                "edited_response": response if code == "EDIT_AND_APPROVE" else None,
                "reviewer_note": note.strip() or None,
            },
        )
        if result:
            refreshed = api("GET", f"/runs/{run['run_id']}")
            st.session_state.runs[ticket_id] = refreshed or {
                **run,
                "review": result["review"],
                "memory_status": result["memory_status"],
                "approved_response": response,
                "status": "APPROVED"
                if code in {"APPROVE", "EDIT_AND_APPROVE"}
                else {"REJECT": "REJECTED", "ESCALATE": "ESCALATED"}[code],
            }
            st.rerun()


st.title("SupportOps")
st.caption("Support investigations, with evidence you can review.")
if "runs" not in st.session_state:
    st.session_state.runs = {}

with st.sidebar:
    st.subheader("Support inbox")
    status_filter = st.selectbox(
        "Ticket status",
        ["All", "NEW", "IN_PROGRESS", "WAITING_FOR_CUSTOMER", "ESCALATED", "RESOLVED", "CLOSED"],
    )
    category_filter = st.selectbox(
        "Category", ["All", "PAYMENT", "REFUND", "LOGIN", "IMAGE_UPLOAD", "COUPON", "SECURITY"]
    )
params = {}
if status_filter != "All":
    params["status"] = status_filter
if category_filter != "All":
    params["category"] = category_filter
tickets = api("GET", "/tickets", params=params)
if tickets is None:
    st.stop()
if not tickets:
    st.info("No tickets match these filters.")
    st.stop()
by_id = {item["ticket_id"]: item for item in tickets}
with st.sidebar:
    ticket_id = st.selectbox(
        "Ticket",
        list(by_id),
        format_func=lambda value: f"{value} · {by_id[value]['subject']}",
        key="selected_ticket",
    )
    st.caption(f"{len(tickets)} tickets · fictional ShopFlow data")
bundle = api("GET", f"/tickets/{ticket_id}")
if bundle is None:
    st.stop()
ticket = bundle["ticket"]
customer, order, payment = (bundle.get(key) or {} for key in ("customer", "order", "payment"))
st.subheader(ticket["subject"])
st.caption(
    f"{ticket_id} · {ticket['category'].replace('_', ' ').title()} · {ticket['status'].replace('_', ' ').title()}"
)
amount = payment.get("amount", order.get("amount"))
details = [
    ("Customer", customer.get("name", "Unknown")),
    ("Order", order.get("status", "Not linked")),
    ("Payment", payment.get("status", "Not linked")),
    ("Amount", f"₹{amount:,.2f}" if amount is not None else "Not available"),
]
for column, (label, value) in zip(st.columns(4), details, strict=True):
    with column.container(border=True):
        st.caption(label)
        st.write(f"**{value}**")
st.write(ticket["description"])

case_tab, evidence_tab, activity_tab, memory_tab = st.tabs(
    ["Investigation & review", "Evidence", "Activity", "Customer history"]
)
with case_tab:
    screenshot = ticket.get("screenshot_path")
    if screenshot:
        path = (ROOT / screenshot).resolve()
        if path.is_relative_to(ROOT / "demo_data") and path.is_file():
            with st.expander("Customer screenshot"):
                st.image(str(path), width=480)
    with st.form(f"investigate-{ticket_id}"):
        question = st.text_input(
            "What should we investigate?", placeholder="Optional: add a specific question"
        )
        investigate = st.form_submit_button("Investigate ticket", type="primary")
    if investigate:
        st.session_state.runs.pop(ticket_id, None)
        with st.spinner("Checking records, finding evidence and verifying the response…"):
            result = api(
                "POST",
                f"/tickets/{ticket_id}/investigate",
                json={"question": question.strip() or None},
            )
        if result:
            st.session_state.runs[ticket_id] = result
    run = st.session_state.runs.get(ticket_id)
    if run:
        st.divider()
        show_verification(run)
        diagnosis = run.get("diagnosis", {})
        st.write("**Investigation summary**")
        st.write(diagnosis.get("issue_summary", ""))
        with st.expander(
            "Analysis and limitations", expanded=run.get("status") == "READY_FOR_REVIEW"
        ):
            st.write("**Possible cause**")
            st.write(diagnosis.get("probable_cause", ""))
            st.write("**Suggested next step**")
            st.write(run.get("recommendation", ""))
            for claim in diagnosis.get("claims", []):
                st.write(f"**{claim['kind'].replace('_', ' ').title()}:** {claim['text']}")
                st.caption("Evidence: " + ", ".join(claim["evidence_ids"]))
                if claim.get("limitation"):
                    st.caption(claim["limitation"])
        show_review(run, ticket_id)
    else:
        st.info("Start an investigation to collect evidence and prepare a response for review.")

with evidence_tab:
    run = st.session_state.runs.get(ticket_id)
    if run:
        for item in run.get("evidence", []):
            with st.expander(item["citation_id"] + " · " + item["source_type"].title()):
                st.write(item["text"])
                st.caption("Evidence ID: " + item["chunk_id"])
        graph = run.get("graph_paths", {})
        if graph.get("edges"):
            with st.expander("Related services and sources"):
                st.dataframe(graph["edges"], hide_index=True, width="stretch")
    else:
        st.caption("Evidence appears after this ticket has been investigated.")

with activity_tab:
    run = st.session_state.runs.get(ticket_id)
    if run:
        st.caption(f"Run {run['run_id']} · {run.get('attempt_count', 1)} retrieval attempt(s)")
        st.dataframe(
            [
                {
                    "Step": entry["node"].replace("_", " ").title(),
                    "Duration (ms)": entry["duration_ms"],
                }
                for entry in run.get("trace", [])
            ],
            hide_index=True,
            width="stretch",
        )
        if st.button("Refresh result"):
            st.session_state.runs[ticket_id] = api("GET", f"/runs/{run['run_id']}") or run
            st.rerun()
    else:
        st.caption("Investigation activity appears here.")

with memory_tab:
    st.caption(
        "Historical context can guide investigation. It does not establish the current cause."
    )
    memories = api("GET", f"/customers/{ticket['customer_id']}/memories")
    if memories:
        for item in memories:
            st.write(item.get("memory", item.get("text", "")))
            metadata = item.get("metadata", {})
            st.caption(
                "Approved recommendation"
                if metadata.get("review_id")
                else "Reviewed historical context"
            )
            st.divider()
    elif memories is not None:
        st.caption("No approved history is stored for this customer.")
