"""Local Streamlit interface for incident retrieval and optional AI answers."""

import re

import streamlit as st

from app import ROOT, load_json, retrieve


def timeline_rows(observations):
    """Separate explicit timestamp prefixes, preserving every event and source order."""
    rows = []
    for observation in observations:
        match = re.fullmatch(r"((?:[01]\d|2[0-3]):[0-5]\d UTC) (.+)", observation)
        if not match:
            return None
        rows.append({"Timestamp": match[1], "Recorded event": match[2]})
    return rows


def style_answer_headings(answer):
    """Highlight only known caution headings; leave all claim text/citations intact."""
    return re.sub(
        r"^### (Possible explanations \(unconfirmed\)|Evidence limitations)$",
        r"### :orange[\1]", answer, flags=re.MULTILINE,
    )


def show_evidence(label, results):
    st.subheader(label)
    st.caption(f"{len(results)} matching source{'s' if len(results) != 1 else ''}")
    if not results:
        st.info("No matching evidence found in the sample data.")
        return
    for score, record in results:
        with st.container(border=False, key=f"evidence_{record['id']}"):
            st.subheader(record["title"])
            st.text(f"[{record['id']}]")
            if record.get("service"):
                st.text(f"Service: {record['service']}")
            st.caption(f"Keyword match: {score:.0%}")
            if "summary" in record:
                st.write(record["summary"])
                observations = record.get("observations", [])
                rows = timeline_rows(observations)
                if rows:
                    st.caption("Recorded timeline · timestamps and events in source order")
                    st.table(rows)
                elif observations:
                    st.caption("Recorded observations")
                    for observation in observations:
                        st.text(f"- {observation}")
            else:
                st.write(record["body"])


def show_workspace():
    st.caption("INCIDENT RESPONSE WORKSPACE · FICTIONAL EVIDENCE")
    st.title("Incident Triage Assistant")
    st.write("Search fictional incident evidence and runbook guidance to plan your next checks.")
    with st.container(width=1000), st.form("incident_question"):
        st.subheader("Start an investigation")
        question = st.text_area(
            "Incident question", height=120,
            placeholder="Why did 5xx errors increase after deployment?",
        )
        use_ai = st.checkbox(
            "Generate AI answer", value=False,
            help="On submission, sends your question and retrieved evidence to OpenAI.",
        )
        st.caption("AI is optional. When enabled, submitting sends your question and only the retrieved evidence to the OpenAI API.")
        submitted = st.form_submit_button("Investigate", type="primary", use_container_width=True)

    if not submitted:
        return
    question = question.strip()
    if not question:
        st.info("Enter an incident question before submitting.")
        return

    incidents = retrieve(question, load_json("incidents.json"))
    runbooks = retrieve(question, load_json("runbooks.json"))
    if use_ai:
        from ai_answer import AIAnswerError, generate_answer

        st.divider()
        with st.container(border=False, width=1000, key="answer_panel"):
            st.subheader("AI-generated answer")
            st.caption("Grounded in retrieved sources. Review unconfirmed explanations and evidence limitations before drawing conclusions.")
            try:
                with st.spinner("Preparing answer…"):
                    answer = generate_answer(
                        question,
                        [record for _, record in incidents],
                        [record for _, record in runbooks],
                    )
                st.markdown(style_answer_headings(answer), unsafe_allow_html=False)
            except AIAnswerError as error:
                st.info(f"AI answer unavailable: {error} Showing offline evidence below.")

    st.divider()
    st.subheader("Supporting evidence")
    st.caption(f"{len(incidents)} incident sources · {len(runbooks)} runbook sources · {len(incidents) + len(runbooks)} total matches")
    st.caption("Keyword match measures word overlap, not confidence or proof of a root cause.")
    incident_column, runbook_column = st.columns(2, gap="large")
    with incident_column:
        show_evidence("Relevant incident evidence", incidents)
    with runbook_column:
        show_evidence("Relevant runbook guidance", runbooks)

    st.divider()
    st.caption("Keyword matches are not a diagnosis. Verify the evidence and use the service owner's procedures.")


def main():
    st.set_page_config(page_title="Incident Triage Assistant", layout="wide")
    # Static, local CSS only: never interpolate questions, evidence, or model output.
    st.html(ROOT / ".streamlit" / "workspace.css")
    # Native pixel widths shrink to the parent width on smaller screens.
    with st.container(width=1200):
        show_workspace()


if __name__ == "__main__":
    main()
