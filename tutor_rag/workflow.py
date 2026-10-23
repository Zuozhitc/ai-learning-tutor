"""Model-neutral tutoring kernel and file-based development harness. No network API."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Protocol

from .engine import ROOT, Retriever

MAX_OUTPUT_CHARS = 1200
MAX_CONTEXT_CHARS = 3200
VERDICTS = {"needs_support", "evidence_recorded", "uncertain"}


class GenerationPort(Protocol):
    """Internal boundary only; this project deliberately supplies no service adapter."""

    def generate(self, request: dict) -> dict: ...


class Workflow:
    def __init__(self):
        self.retriever = Retriever()
        units = json.loads((ROOT / "curriculum" / "units.json").read_text(encoding="utf-8"))
        self.units = {u["id"]: u for u in units}
        for unit in units:
            if not unit["material_ids"] or any(c not in self.retriever.by_id for c in unit["material_ids"]):
                raise ValueError("Invalid curriculum materials")

    def start(self, unit_id: str) -> dict:
        if unit_id not in self.units:
            raise ValueError("Unknown unit")
        return {"schema_version": 1, "session_id": uuid.uuid4().hex, "unit_id": unit_id,
                "goal": self.units[unit_id]["goal"], "phase": "ready", "pending": None,
                "deferred": [], "events": [], "learner_answer": None,
                "attempts": 0, "assistance_used": False, "assessment": None,
                "answer_history": [], "previous_units": [],
                "learning_claim": "not_established"}

    def _check_state(self, state):
        if state.get("schema_version") != 1 or state.get("unit_id") not in self.units:
            raise ValueError("Unsupported session")
        if state.get("goal") != self.units[state["unit_id"]]["goal"]:
            raise ValueError("Session goal differs from curriculum")

    def request(self, state: dict, kind: str, question: str = "") -> tuple[dict, dict]:
        self._check_state(state)
        allowed = {"explain": {"ready"}, "assess": {"assessment_ready"},
                   "remediate": {"remediation_ready"}, "summarize": {"summary_ready"},
                   "clarify": {"checkpoint_ready", "awaiting_answer", "remediation_ready", "summary_ready", "review_required"}}
        if state.get("pending") or state["phase"] not in allowed.get(kind, set()):
            raise ValueError("Request not allowed in current phase or another request is pending")
        if kind == "clarify" and not 1 <= len(question.strip()) <= 2000:
            raise ValueError("Clarification requires a question of 1..2000 characters")
        unit = self.units[state["unit_id"]]
        if kind == "clarify":
            ids = [h.card_id for h in self.retriever.search(question)]
        else:
            # Main lesson uses explicit curriculum materials, not open-ended lesson discovery.
            ids = list(unit["material_ids"])
        materials, used = [], 0
        for key in ids:
            card = self.retriever.by_id[key]
            item = {"id": key, "title": card["title"], "content": card["content"],
                    "sources": [self.retriever.sources[s]["url"] for s in card["sources"]]}
            size = len(json.dumps(item, ensure_ascii=False))
            if used + size > MAX_CONTEXT_CHARS:
                if kind != "clarify":
                    raise ValueError("Required lesson material exceeds character budget")
                break
            materials.append(item)
            used += size
        request = {
            "request_id": uuid.uuid4().hex, "session_id": state["session_id"], "kind": kind,
            "goal": state["goal"], "question": question,
            "materials": materials, "context_chars": used,
            "evidence_status": "candidate_materials" if materials else "no_materials",
            "constraints": {"one_goal": True, "check_understanding_at_section_end": True,
                            "defer_optional_branches": True, "max_output_chars": MAX_OUTPUT_CHARS,
                            "no_unsupported_claims": True},
            "output_contract": {"request_id": "same as request", "kind": kind,
                                "text": "Chinese teaching text or feedback",
                                "citations": "list of provided material IDs",
                                "status": ["grounded", "insufficient_evidence"]}}
        if kind == "assess":
            request["checkpoint"] = unit["checkpoint"]
            request["learner_answer"] = state["learner_answer"]
            request["output_contract"]["verdict"] = sorted(VERDICTS)
        if kind in {"remediate", "summarize"}:
            request["assessment"] = state["assessment"]
            request["learner_answer"] = state["learner_answer"]
            request["assistance_used"] = state["assistance_used"]
        new = copy.deepcopy(state)
        new["pending"] = {"request_id": request["request_id"], "kind": kind,
                          "allowed_citations": [m["id"] for m in materials],
                          "return_phase": state["phase"]}
        new["events"].append({"type": "request_prepared", "kind": kind, "request_id": request["request_id"]})
        return new, request

    def apply(self, state: dict, response: dict, origin: str = "unverified_external_file") -> dict:
        self._check_state(state)
        pending = state.get("pending")
        if not pending or state["phase"] == "paused":
            raise ValueError("No active pending request, or session is paused")
        if not isinstance(response, dict):
            raise ValueError("Response must be an object")
        allowed_keys = {"request_id", "kind", "text", "citations", "status", "verdict"}
        if set(response) - allowed_keys:
            raise ValueError("Response contains unknown fields; model cannot control workflow")
        if response.get("request_id") != pending["request_id"] or response.get("kind") != pending["kind"]:
            raise ValueError("Stale or mismatched response")
        text = response.get("text")
        refs = response.get("citations")
        status = response.get("status")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_OUTPUT_CHARS:
            raise ValueError("Invalid output length")
        if not isinstance(refs, list) or any(not isinstance(r, str) for r in refs):
            raise ValueError("Citations must be a list of material IDs")
        if set(refs) - set(pending["allowed_citations"]):
            raise ValueError("Citation not present in request")
        if status not in {"grounded", "insufficient_evidence"}:
            raise ValueError("Invalid evidence status")
        if status == "grounded" and not refs:
            raise ValueError("Grounded response needs provided citations")
        if pending["kind"] == "assess" and status == "grounded":
            if response.get("verdict") not in VERDICTS:
                raise ValueError("Invalid assessment verdict")
        elif "verdict" in response:
            raise ValueError("Verdict is only permitted in a grounded assessment")
        new = copy.deepcopy(state)
        new["pending"] = None
        kind = pending["kind"]
        if status == "grounded":
            if kind in {"explain", "remediate"}:
                new["phase"] = "checkpoint_ready"
                if kind == "remediate":
                    new["assistance_used"] = True
            elif kind == "assess":
                new["assessment"] = {"verdict": response["verdict"], "feedback": text,
                                     "origin": origin, "semantic_review": "not_independently_verified",
                                     "attempt": state["attempts"],
                                     "assistance_used_at_submission": state["answer_history"][-1]["assistance_used"]}
                new["phase"] = {"needs_support": "remediation_ready", "evidence_recorded": "summary_ready",
                                "uncertain": "review_required"}[response["verdict"]]
            elif kind == "summarize":
                new["phase"] = "completed"
                # Closing a unit is a workflow state, not proof of mastery or 8B capability.
                new["learning_claim"] = ("workflow_closed_checkpoint_skipped"
                                         if state["assessment"]["verdict"] == "not_checked"
                                         else "workflow_closed_assessment_unverified")
            else:
                new["phase"] = pending["return_phase"]
                new["assistance_used"] = True
        new["events"].append({"type": "output_applied", "kind": kind, "status": status,
                              "origin": origin, "text": text, "citations": refs,
                              "validation": "structure_only_not_factual_entailment"})
        return new

    def event(self, state: dict, kind: str, text: str = "") -> dict:
        self._check_state(state)
        new = copy.deepcopy(state)
        if kind == "defer":
            if not 1 <= len(text.strip()) <= 2000:
                raise ValueError("Deferred question must contain 1..2000 characters")
            new["deferred"].append({"question": text.strip(), "status": "unresolved"})
        elif kind == "pause":
            if state["phase"] in {"paused", "completed"}:
                raise ValueError("Cannot pause current phase")
            new["resume_phase"] = state["phase"]
            new["phase"] = "paused"
        elif kind == "resume":
            if state["phase"] != "paused":
                raise ValueError("Session is not paused")
            new["phase"] = new.pop("resume_phase")
        elif kind == "cancel_request":
            if not state.get("pending") or state["phase"] == "paused":
                raise ValueError("No active request to cancel")
            new["pending"] = None
        elif state.get("pending") or state["phase"] == "paused":
            raise ValueError("Wait for pending output or resume first")
        elif kind == "skip_checkpoint":
            if state["phase"] not in {"checkpoint_ready", "awaiting_answer", "review_required"}:
                raise ValueError("No checkpoint to skip in current phase")
            new["assessment"] = {"verdict": "not_checked", "feedback": "Learner chose to skip checkpoint",
                                 "origin": "learner_action", "semantic_review": "not_assessed"}
            new["phase"] = "summary_ready"
        elif kind == "checkpoint":
            if state["phase"] != "checkpoint_ready":
                raise ValueError("Finish the explanation before checkpoint")
            new["phase"] = "awaiting_answer"
            new["checkpoint_question"] = self.units[state["unit_id"]]["checkpoint"]["question"]
        elif kind == "answer":
            if state["phase"] not in {"awaiting_answer", "review_required"} or not 1 <= len(text.strip()) <= 3000:
                raise ValueError("Cannot submit this answer in current phase")
            new["learner_answer"] = text.strip()
            new["attempts"] += 1
            new["answer_history"].append({"attempt": new["attempts"], "text": text.strip(),
                                           "assistance_used": state["assistance_used"]})
            new["phase"] = "assessment_ready"
        else:
            raise ValueError("Unknown event")
        new["events"].append({"type": kind, "text": text, "phase": new["phase"]})
        return new

    def next_unit(self, state: dict, unit_id: str) -> dict:
        """Explicit learner/operator choice; generated responses cannot select the next goal."""
        self._check_state(state)
        if state["phase"] != "completed" or state.get("pending"):
            raise ValueError("Close the current unit before selecting another")
        new = self.start(unit_id)
        new["session_id"] = state["session_id"]
        new["deferred"] = copy.deepcopy(state["deferred"])
        new["previous_units"] = copy.deepcopy(state["previous_units"]) + [{
            "unit_id": state["unit_id"], "assessment": state["assessment"],
            "answer_history": state["answer_history"], "learning_claim": state["learning_claim"]}]
        new["events"] = copy.deepcopy(state["events"]) + [{"type": "unit_selected", "unit_id": unit_id}]
        return new


def replay_demo(workflow: Workflow) -> dict:
    """Codex-authored deterministic fixture: validates orchestration, not model quality."""
    fixture = json.loads((ROOT / "examples" / "framework_replay.json").read_text(encoding="utf-8"))
    state = workflow.start(fixture["unit_id"])
    trace = [{"action": "start", "phase": state["phase"]}]
    for step in fixture["steps"]:
        if step["action"] == "event":
            state = workflow.event(state, step["kind"], step.get("text", ""))
        else:
            state, request = workflow.request(state, step["kind"], step.get("question", ""))
            response = dict(step["response"], request_id=request["request_id"], kind=step["kind"])
            state = workflow.apply(state, response, origin="codex_authored_fixture")
        trace.append({"action": step["action"], "kind": step["kind"], "phase": state["phase"],
                      "goal": state["goal"], "deferred_count": len(state["deferred"])})
    return {"verification_type": "deterministic_codex_authored_fixture_replay", "network_calls": 0,
            "real_model_tested": False, "small_model_capability_established": False,
            "trace": trace, "final_state": state}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="开发验证工具：无服务地址、无密钥、无真实模型调用")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("unit")
    start.add_argument("state")
    req = commands.add_parser("request")
    req.add_argument("state")
    req.add_argument("kind", choices=["explain", "assess", "remediate", "summarize", "clarify"])
    req.add_argument("out")
    req.add_argument("--question", default="")
    apply = commands.add_parser("apply")
    apply.add_argument("state")
    apply.add_argument("response")
    event = commands.add_parser("event")
    event.add_argument("state")
    event.add_argument("kind", choices=["defer", "pause", "resume", "checkpoint", "answer", "cancel_request", "skip_checkpoint"])
    event.add_argument("--text", default="")
    demo = commands.add_parser("demo")
    demo.add_argument("--out", default="output/framework-demo.json")
    next_unit = commands.add_parser("next")
    next_unit.add_argument("state")
    next_unit.add_argument("unit")
    args = parser.parse_args()
    try:
        w = Workflow()
        if args.command == "demo":
            report = replay_demo(w)
            write_json(args.out, report)
            result = {k: v for k, v in report.items() if k != "final_state"}
        elif args.command == "start":
            result = w.start(args.unit)
            write_json(args.state, result)
        elif args.command == "request":
            if Path(args.out).resolve() == Path(args.state).resolve():
                raise ValueError("Request file and session file must differ")
            state, request = w.request(read_json(args.state), args.kind, args.question)
            write_json(args.out, request)
            write_json(args.state, state)
            result = {"phase": state["phase"], "pending": state["pending"], "request_file": args.out}
        elif args.command == "apply":
            result = w.apply(read_json(args.state), read_json(args.response))
            write_json(args.state, result)
        elif args.command == "next":
            result = w.next_unit(read_json(args.state), args.unit)
            write_json(args.state, result)
        else:
            result = w.event(read_json(args.state), args.kind, args.text)
            write_json(args.state, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
