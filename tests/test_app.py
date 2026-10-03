import contextlib
import copy
import io
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app
from ai_answer import AIAnswerError, VALIDATION_MESSAGE, generate_answer, render_answer


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.incidents = app.load_json("incidents.json")
        self.runbooks = app.load_json("runbooks.json")
        self.answer = {
            "observed_facts": [{"text": "503 responses increased after deployment.", "source_ids": ["INC-1042"]}],
            "possible_explanations": [{"text": "Unhealthy targets may explain the errors; this is unconfirmed.", "source_ids": ["INC-1042"]}],
            "next_checks": [{"text": "Check the health-check path and timeout.", "source_ids": ["RB-LOAD-BALANCER-HEALTH"]}],
            "limitations": "The evidence is insufficient to establish a single root cause.",
        }
        self.create = MagicMock(return_value=SimpleNamespace(
            status="completed", output_text=json.dumps(self.answer)))
        self.client = MagicMock()
        self.client.responses.create = self.create
        self.factory = MagicMock()
        self.factory.return_value.__enter__.return_value = self.client
        self.sdk = SimpleNamespace(OpenAI=self.factory, OpenAIError=RuntimeError)

    def call_fake(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only", "OPENAI_MODEL": "test-model"}, clear=True), patch.dict(sys.modules, {"openai": self.sdk}):
            return generate_answer("Why did 5xx errors increase after the deployment?", self.incidents[:1], self.runbooks[:1])

    def test_retrieval_regression(self):
        query = "Why did 5xx errors increase after the deployment?"
        self.assertEqual([r["id"] for _, r in app.retrieve(query, self.incidents)], ["INC-1042"])
        self.assertEqual([r["id"] for _, r in app.retrieve(query, self.runbooks)], ["RB-LOAD-BALANCER-HEALTH"])
        self.assertEqual(app.retrieve("Is there evidence of a database failure?", self.incidents), [])
        self.assertEqual(app.retrieve("Is there evidence of a database failure?", self.runbooks), [])
        self.assertEqual([r["id"] for _, r in app.retrieve("Why are requests returning 429?", self.incidents)], ["INC-1088"])

    def test_incident_display_includes_optional_service(self):
        for incident in self.incidents:
            with self.subTest(service=incident["service"]), contextlib.redirect_stdout(io.StringIO()) as output:
                app.print_results("Relevant incident evidence:", [(1.0, incident)])
            self.assertIn(f"    Service: {incident['service']}\n", output.getvalue())
            self.assertIn(f"    Summary: {incident['summary']}\n", output.getvalue())

        incident_without_service = {key: value for key, value in self.incidents[0].items() if key != "service"}
        with contextlib.redirect_stdout(io.StringIO()) as output:
            app.print_results("Relevant incident evidence:", [(1.0, incident_without_service)])
        self.assertNotIn("Service:", output.getvalue())
        self.assertIn("Summary:", output.getvalue())

    def test_runbook_display_remains_unchanged(self):
        runbook = {**self.runbooks[0], "service": "example-service"}
        with contextlib.redirect_stdout(io.StringIO()) as output:
            app.print_results("Relevant runbook guidance:", [(1.0, runbook)])
        self.assertEqual(output.getvalue(), (
            "\nRelevant runbook guidance:\n"
            f"  [{runbook['id']}] {runbook['title']} (match: 100%)\n"
            f"    Guidance: {runbook['body']}\n"
        ))

    def test_offline_never_generates(self):
        with patch("ai_answer.generate_answer", side_effect=AssertionError("Must remain offline")), contextlib.redirect_stdout(io.StringIO()) as output:
            app.investigate("Why did 5xx errors increase after the deployment?")
        self.assertIn("[INC-1042]", output.getvalue())
        self.assertNotIn("AI-generated", output.getvalue())

    def test_request_contains_only_retrieved_records(self):
        result = self.call_fake()
        request = self.create.call_args.kwargs
        self.assertEqual(json.loads(request["input"]), {
            "question": "Why did 5xx errors increase after the deployment?",
            "incidents": self.incidents[:1], "runbooks": self.runbooks[:1],
        })
        self.assertEqual(request["model"], "test-model")
        self.assertFalse(request["store"])
        self.assertNotIn("tools", request)
        self.assertNotIn("previous_response_id", request)
        self.assertEqual(request["text"]["format"]["schema"]["properties"]["observed_facts"]["items"]["properties"]["source_ids"]["items"]["enum"], ["INC-1042", "RB-LOAD-BALANCER-HEALTH"])
        self.assertIn("[INC-1042]", result)
        self.assertIn("Possible explanations (unconfirmed)", result)

    def test_answer_markdown_sections_preserve_claims_and_citations(self):
        self.answer["observed_facts"].append({
            "text": "Target health checks began failing shortly after rollout.",
            "source_ids": ["INC-1042"],
        })
        self.answer["possible_explanations"][0]["source_ids"].append("RB-LOAD-BALANCER-HEALTH")
        result = render_answer(json.dumps(self.answer), self.incidents[:1], self.runbooks[:1])
        self.assertEqual(result, (
            "### Observed facts\n\n"
            "- 503 responses increased after deployment. [INC-1042]\n"
            "- Target health checks began failing shortly after rollout. [INC-1042]\n\n"
            "### Possible explanations (unconfirmed)\n\n"
            "- Unhealthy targets may explain the errors; this is unconfirmed. [INC-1042] [RB-LOAD-BALANCER-HEALTH]\n\n"
            "### Suggested checks (not observations)\n\n"
            "- Check the health-check path and timeout. [RB-LOAD-BALANCER-HEALTH]\n\n"
            "### Evidence limitations\n\n"
            "The evidence is insufficient to establish a single root cause."
        ))

    def test_empty_answer_sections_have_markdown_spacing(self):
        answer = {"observed_facts": [], "possible_explanations": [], "next_checks": [],
                  "limitations": "Insufficient evidence."}
        result = render_answer(json.dumps(answer), self.incidents[:1], self.runbooks[:1])
        self.assertIn("### Observed facts\n\nNo supported claims in the retrieved evidence.\n\n### Possible explanations", result)

    def test_no_evidence_never_calls_sdk(self):
        with patch.dict(sys.modules, {"openai": self.sdk}):
            self.assertIn("insufficient", generate_answer("database failure?", [], []))
        self.factory.assert_not_called()

    def test_missing_configuration_never_calls_sdk(self):
        for env in ({}, {"OPENAI_API_KEY": "test-only"}, {"OPENAI_MODEL": "test-model"}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, {"openai": self.sdk}):
                with self.assertRaises(AIAnswerError):
                    generate_answer("5xx", self.incidents[:1], [])
        self.factory.assert_not_called()

    def test_missing_sdk(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only", "OPENAI_MODEL": "test-model"}, clear=True), patch.dict(sys.modules, {"openai": None}):
            with self.assertRaisesRegex(AIAnswerError, "Install"):
                generate_answer("5xx", self.incidents[:1], [])

    def test_invalid_citations_and_guidance_as_fact(self):
        for ids in (["INC-9999"], ["INC-1088"], [], ["RB-LOAD-BALANCER-HEALTH"]):
            with self.subTest(ids=ids):
                self.answer["observed_facts"][0]["source_ids"] = ids
                with self.assertRaises(AIAnswerError):
                    render_answer(json.dumps(self.answer), self.incidents[:1], self.runbooks[:1])

    def test_malformed_and_uncited_prose_ids(self):
        for raw in ("not JSON", "null", "{}", json.dumps({**self.answer, "limitations": "See INC-9999"}), json.dumps({**self.answer, "limitations": ""})):
            with self.subTest(raw=raw), self.assertRaises(AIAnswerError):
                render_answer(raw, self.incidents[:1], self.runbooks[:1])

    def test_incomplete_or_refused_answer(self):
        for status, text in (("incomplete", json.dumps(self.answer)), ("completed", "")):
            self.create.return_value = SimpleNamespace(status=status, output_text=text)
            with self.assertRaises(AIAnswerError):
                self.call_fake()

    def test_mocked_validation_failure_codes(self):
        cases = [
            ("invalid_json", "private model text: not JSON"),
            ("invalid_answer_shape", "null"),
            ("invalid_answer_shape", "{}"),
        ]
        mutations = [
            ("invalid_limitations", "limitations", ""),
            ("invalid_section", "observed_facts", {}),
            ("invalid_claim_shape", "observed_facts", [{"text": "private model text"}]),
            ("invalid_claim_text", "observed_facts", [{"text": "", "source_ids": ["INC-1042"]}]),
            ("invalid_citation_type", "observed_facts", [{"text": "private model text", "source_ids": "INC-1042"}]),
            ("invalid_citation_type", "observed_facts", [{"text": "private model text", "source_ids": [None]}]),
            ("missing_citation", "observed_facts", [{"text": "private model text", "source_ids": []}]),
            ("unknown_source_id", "observed_facts", [{"text": "private model text", "source_ids": ["INC-9999"]}]),
            ("unknown_source_id", "observed_facts", [{"text": "private model text", "source_ids": ["INC-1088"]}]),
            ("observed_fact_source_mismatch", "observed_facts", [{"text": "private model text", "source_ids": ["RB-LOAD-BALANCER-HEALTH"]}]),
            ("next_check_source_mismatch", "next_checks", [{"text": "private model text", "source_ids": ["INC-1042"]}]),
            ("source_id_in_prose", "limitations", "private model text INC-9999"),
            ("source_id_in_prose", "observed_facts", [{"text": "private model text INC-1042", "source_ids": ["INC-1042"]}]),
        ]
        for code, field, value in mutations:
            answer = copy.deepcopy(self.answer)
            answer[field] = value
            cases.append((code, json.dumps(answer)))
        for code, raw in cases:
            with self.subTest(code=code, raw=raw):
                self.create.return_value = SimpleNamespace(status="completed", output_text=raw)
                with self.assertLogs("ai_answer", level="DEBUG") as logs:
                    with self.assertRaises(AIAnswerError) as caught:
                        self.call_fake()
                self.assertEqual(caught.exception.validation_code, code)
                self.assertEqual(str(caught.exception), VALIDATION_MESSAGE)
                self.assertEqual(logs.output, [
                    f"DEBUG:ai_answer:AI answer validation failed: category={caught.exception.validation_category} code={code}"
                ])
                self.assertIsNone(logs.records[0].exc_info)

    def test_validation_failure_categories_are_content_free(self):
        cases = [
            ("malformed_structure", "invalid_json", "private-response-marker"),
            ("malformed_structure", "invalid_answer_shape", json.dumps({**self.answer, "extra": "private-response-marker"})),
            ("invalid_citation", "missing_citation", json.dumps({**self.answer, "observed_facts": [{"text": "private-response-marker", "source_ids": []}]})),
            ("invalid_citation", "unknown_source_id", json.dumps({**self.answer, "observed_facts": [{"text": "private-response-marker", "source_ids": ["INC-9999"]}]})),
            ("wrong_source_type", "observed_fact_source_mismatch", json.dumps({**self.answer, "observed_facts": [{"text": "private-response-marker", "source_ids": ["RB-LOAD-BALANCER-HEALTH"]}]})),
            ("wrong_source_type", "next_check_source_mismatch", json.dumps({**self.answer, "next_checks": [{"text": "private-response-marker", "source_ids": ["INC-1042"]}]})),
            ("source_id_in_prose", "source_id_in_prose", json.dumps({**self.answer, "limitations": "private-response-marker INC-1042"})),
        ]
        for category, code, raw in cases:
            with self.subTest(category=category, code=code):
                self.create.return_value = SimpleNamespace(status="completed", output_text=raw)
                with self.assertLogs("ai_answer", level="DEBUG") as logs, self.assertRaises(AIAnswerError) as caught:
                    self.call_fake()
                error = caught.exception
                self.assertEqual(error.validation_category, category)
                self.assertEqual(error.validation_code, code)
                self.assertEqual(error.args, (VALIDATION_MESSAGE,))
                self.assertEqual(logs.records[0].args, (category, code))
                self.assertIsNone(logs.records[0].exc_info)
                diagnostics = "\n".join(logs.output) + str(error)
                for private_value in ("private-response-marker", "test-only", "INC-1042", "RB-LOAD-BALANCER-HEALTH", "Why did 5xx", self.incidents[0]["summary"]):
                    self.assertNotIn(private_value, diagnostics)

    def test_valid_answer_emits_no_validation_diagnostics(self):
        with patch("ai_answer.LOGGER.debug") as diagnostic:
            self.call_fake()
        diagnostic.assert_not_called()

    def test_cli_validation_failure_stays_generic(self):
        self.create.return_value = SimpleNamespace(status="completed", output_text="private model text")
        with self.assertRaises(AIAnswerError) as caught:
            self.call_fake()
        with patch("ai_answer.generate_answer", side_effect=caught.exception), contextlib.redirect_stdout(io.StringIO()) as output:
            app.investigate("5xx deployment", ai=True)
        self.assertIn(VALIDATION_MESSAGE, output.getvalue())
        self.assertNotIn("invalid_json", output.getvalue())
        self.assertNotIn("private model text", output.getvalue())

    def test_api_errors_do_not_expose_body(self):
        self.create.side_effect = RuntimeError("sensitive error body")
        with self.assertRaises(AIAnswerError) as caught:
            self.call_fake()
        self.assertNotIn("sensitive", str(caught.exception))

    def test_ai_failure_keeps_offline_evidence(self):
        with patch("ai_answer.generate_answer", side_effect=AIAnswerError("Request failed")), contextlib.redirect_stdout(io.StringIO()) as output:
            app.investigate("5xx deployment", ai=True)
        self.assertIn("[INC-1042]", output.getvalue())
        self.assertIn("Showing offline evidence above", output.getvalue())


if __name__ == "__main__":
    unittest.main()
