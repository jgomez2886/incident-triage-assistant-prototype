"""Exercise web submission behavior with a fake UI; no SDK or network needed."""

import contextlib
import runpy
import sys
import unittest
from unittest.mock import MagicMock, patch

from app import ROOT
from ai_answer import AIAnswerError


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.ui = MagicMock()
        self.ui.form.return_value = contextlib.nullcontext()
        self.ui.container.side_effect = lambda **kwargs: contextlib.nullcontext()
        self.ui.columns.side_effect = lambda *args, **kwargs: [contextlib.nullcontext(), contextlib.nullcontext()]
        self.ui.spinner.return_value = contextlib.nullcontext()
        self.ui.text_area.return_value = "Why are requests returning 429?"
        self.ui.checkbox.return_value = False
        self.ui.form_submit_button.return_value = True
        with patch.dict(sys.modules, {"streamlit": self.ui}):
            self.web = runpy.run_path(str(ROOT / "web_app.py"))
            self.main = self.web["main"]

    def displayed_text(self):
        return "\n".join(call.args[0] for call in self.ui.text.call_args_list)

    def test_no_generation_before_submit_even_when_checked(self):
        self.ui.form_submit_button.return_value = False
        self.ui.checkbox.return_value = True
        with patch("ai_answer.generate_answer") as generate:
            self.main()
        generate.assert_not_called()
        self.ui.text.assert_not_called()

    def test_offline_submission_and_default_option(self):
        with patch("ai_answer.generate_answer") as generate:
            self.main()
        generate.assert_not_called()
        self.assertFalse(self.ui.checkbox.call_args.kwargs["value"])
        text = self.displayed_text()
        self.assertIn("[INC-1088]", text)
        self.assertIn("[RB-THROTTLING-429]", text)
        self.assertIn("Service: eligibility-worker", text)
        captions = [call.args[0] for call in self.ui.caption.call_args_list]
        self.assertTrue(any(caption.startswith("Keyword match:") for caption in captions))
        self.ui.set_page_config.assert_called_once_with(page_title="Incident Triage Assistant", layout="wide")
        self.assertEqual(self.ui.form_submit_button.call_args.kwargs["type"], "primary")
        self.ui.columns.assert_called_once_with(2, gap="large")
        widths = [call.kwargs.get("width") for call in self.ui.container.call_args_list]
        self.assertIn(1200, widths)
        self.assertIn(1000, widths)
        self.assertIn("1 incident sources · 1 runbook sources · 2 total matches", captions)
        self.ui.html.assert_called_once_with(ROOT / ".streamlit" / "workspace.css")

    def test_ai_submission_uses_only_retrieved_records(self):
        self.ui.checkbox.return_value = True
        with patch("ai_answer.generate_answer", return_value="Mocked cited answer") as generate:
            self.main()
        query, incidents, runbooks = generate.call_args.args
        self.assertEqual(query, "Why are requests returning 429?")
        self.assertEqual([item["id"] for item in incidents], ["INC-1088"])
        self.assertEqual([item["id"] for item in runbooks], ["RB-THROTTLING-429"])
        generate.assert_called_once()
        self.ui.markdown.assert_called_once_with("Mocked cited answer", unsafe_allow_html=False)
        calls = self.ui.mock_calls
        answer_position = next(i for i, call in enumerate(calls) if call[0] == "markdown")
        evidence_position = next(i for i, call in enumerate(calls) if call[0] == "subheader" and call.args == ("Relevant incident evidence",))
        self.assertLess(answer_position, evidence_position)

    def test_blank_question_never_generates(self):
        self.ui.text_area.return_value = "  "
        self.ui.checkbox.return_value = True
        with patch("ai_answer.generate_answer") as generate:
            self.main()
        generate.assert_not_called()
        self.ui.info.assert_called_once()

    def test_failure_preserves_evidence(self):
        self.ui.checkbox.return_value = True
        with patch("ai_answer.generate_answer", side_effect=AIAnswerError("Request failed")):
            self.main()
        self.assertIn("[INC-1088]", self.displayed_text())
        self.assertIn("[RB-THROTTLING-429]", self.displayed_text())
        self.assertIn("Showing offline evidence below", self.ui.info.call_args.args[0])

    def test_no_evidence_uses_local_insufficient_message(self):
        self.ui.text_area.return_value = "Is there evidence of a database failure?"
        self.ui.checkbox.return_value = True
        with patch.dict(sys.modules, {"openai": None}):
            self.main()
        self.assertEqual(self.ui.info.call_count, 2)
        answer = self.ui.markdown.call_args.args[0]
        self.assertIn("Retrieved evidence is insufficient", answer)
        self.assertIn("no API request was made", answer)

    def test_timeline_preserves_explicit_timestamps_events_and_order(self):
        from app import load_json

        for record in load_json("incidents.json"):
            rows = self.web["timeline_rows"](record["observations"])
            self.assertEqual([row["Timestamp"] + " " + row["Recorded event"] for row in rows], record["observations"])
        self.assertIsNone(self.web["timeline_rows"](["No timestamp was recorded."]))
        self.assertEqual(self.web["timeline_rows"]([]), [])

    def test_timeline_is_displayed_without_inventing_events(self):
        self.main()
        rows = self.ui.table.call_args.args[0]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], {"Timestamp": "09:30 UTC", "Recorded event": "batch job eligibility-refresh started with 120 concurrent workers"})

    def test_answer_caution_styling_preserves_claims_and_citations(self):
        answer = "### Observed facts\n\n- A recorded fact. [INC-1042]\n\n### Possible explanations (unconfirmed)\n\n- An unconfirmed explanation. [INC-1042]\n\n### Evidence limitations\n\nInsufficient evidence."
        styled = self.web["style_answer_headings"](answer)
        self.assertEqual(styled, answer.replace("### Possible explanations (unconfirmed)", "### :orange[Possible explanations (unconfirmed)]").replace("### Evidence limitations", "### :orange[Evidence limitations]"))


if __name__ == "__main__":
    unittest.main()
