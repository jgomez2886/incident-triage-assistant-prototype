"""Incident retrieval with dependency-free offline mode and optional AI answers."""

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
STOP_WORDS = {
    "a", "an", "and", "are", "did", "do", "for", "from", "i", "in", "is",
    "it", "of", "on", "or", "the", "this", "to", "was", "were", "what",
    "why", "with", "after", "before", "there", "evidence", "show", "me",
}
MIN_MATCH_SCORE = 0.30


def load_json(filename):
    with (DATA_DIR / filename).open(encoding="utf-8") as file:
        return json.load(file)


def words(text):
    return {
        word for word in re.findall(r"[a-z0-9]+", text.lower())
        if len(word) > 1 and word not in STOP_WORDS
    }


def searchable_text(record):
    parts = [record.get("title", ""), record.get("summary", ""), record.get("body", "")]
    parts.extend(record.get("tags", []))
    parts.extend(record.get("observations", []))
    return " ".join(parts)


def retrieve(query, records, limit=3):
    query_sentences = [words(sentence) for sentence in re.split(r"[.!?]+", query)]
    query_sentences = [tokens for tokens in query_sentences if tokens]
    if not query_sentences:
        return []

    ranked = []
    for record in records:
        record_words = words(searchable_text(record))
        # Unrelated sentences should not dilute a relevant sentence's keyword match.
        score = max(len(tokens & record_words) / len(tokens) for tokens in query_sentences)
        if score >= MIN_MATCH_SCORE:
            ranked.append((score, record))

    ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
    return ranked[:limit]


def print_results(label, results):
    print(f"\n{label}")
    if not results:
        print("  No matching evidence found in the sample data.")
        return

    for score, record in results:
        print(f"  [{record['id']}] {record['title']} (match: {score:.0%})")
        if "summary" in record:
            if record.get("service"):
                print(f"    Service: {record['service']}")
            print(f"    Summary: {record['summary']}")
            for observation in record.get("observations", []):
                print(f"    - {observation}")
        else:
            print(f"    Guidance: {record['body']}")


def investigate(query, ai=False):
    incidents = load_json("incidents.json")
    runbooks = load_json("runbooks.json")
    print(f"\nQuestion: {query}")
    incident_results = retrieve(query, incidents)
    runbook_results = retrieve(query, runbooks)
    print_results("Relevant incident evidence:", incident_results)
    print_results("Relevant runbook guidance:", runbook_results)
    if ai:
        from ai_answer import AIAnswerError, generate_answer

        try:
            print("\nAI-generated answer:\n" + generate_answer(
                query,
                [record for _, record in incident_results],
                [record for _, record in runbook_results],
            ))
        except AIAnswerError as error:
            print(f"\nAI answer unavailable: {error} Showing offline evidence above.")
    print("\nPrototype limitation: keyword matches are not a diagnosis. Verify the evidence and use the service owner's procedures.")


def main():
    parser = argparse.ArgumentParser(description="Retrieve incident evidence; optionally generate an AI answer.")
    parser.add_argument("--ai", action="store_true", help="Send question and retrieved evidence to OpenAI")
    args = parser.parse_args()
    print("Incident Triage Assistant — evidence retrieval prototype")
    print("Enter a question, or type 'quit' to exit. Try: Why did 5xx errors increase after the deployment?")
    while True:
        try:
            query = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break
        if query.lower() in {"quit", "exit"}:
            print("Goodbye.")
            break
        if query:
            investigate(query, ai=args.ai)


if __name__ == "__main__":
    main()
