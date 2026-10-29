#!/usr/bin/env python3
"""Dependency-free, version-parameterized audit of generated dialogue sessions."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


APPRAISAL_LABELS = {
    "positive", "neutral", "negative", "insufficient_evidence"
}
BATCH_PATTERN = re.compile(r"(?<!\d)(\d{8}_\d{6})(?!\d)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze one version and batch of generated dialogue sessions."
    )
    parser.add_argument("--version", required=True, help="Version identifier, e.g. V2 or V3_1")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--sessions", type=Path, action="append", default=[],
        help="Explicit session file or directory; repeat as needed",
    )
    parser.add_argument(
        "--batch", default="latest",
        help="latest, all, or a filename timestamp such as 20260808_232921",
    )
    parser.add_argument("--training-only", action="store_true")
    parser.add_argument("--saturation-threshold", type=float, default=0.999)
    parser.add_argument("--dominance-threshold", type=float, default=0.8)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def version_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def discover_session_inputs(root: Path, version: str) -> tuple[list[Path], list[str]]:
    warnings: list[str] = []
    base = root / "data" / "sessions"
    if not base.exists():
        raise FileNotFoundError(f"session root does not exist: {base}")
    wanted = version_key(version)
    exact = [path for path in base.iterdir() if path.is_dir() and version_key(path.name) == wanted]
    if len(exact) == 1:
        return exact, warnings
    if len(exact) > 1:
        raise RuntimeError(
            "ambiguous version session directories: " + ", ".join(str(path) for path in exact)
        )
    candidates = [
        path for path in base.rglob("*")
        if path.is_dir() and wanted and wanted in version_key(path.name)
    ]
    if len(candidates) == 1:
        warnings.append(f"used fuzzy version directory match: {candidates[0]}")
        return candidates, warnings
    if candidates:
        raise RuntimeError(
            "ambiguous fuzzy version directories; pass --sessions: "
            + ", ".join(str(path) for path in candidates)
        )
    raise FileNotFoundError(
        f"no session directory found for version {version!r}; pass --sessions"
    )


def json_paths(inputs: Iterable[Path]) -> list[Path]:
    paths: set[Path] = set()
    for item in inputs:
        resolved = item.resolve()
        if resolved.is_file() and resolved.suffix.casefold() == ".json":
            paths.add(resolved)
        elif resolved.is_dir():
            paths.update(path.resolve() for path in resolved.glob("*.json") if path.is_file())
        else:
            raise FileNotFoundError(f"session input does not exist: {item}")
    return sorted(paths)


def load_sessions(paths: Iterable[Path]) -> tuple[list[tuple[Path, dict[str, Any]]], list[str]]:
    sessions: list[tuple[Path, dict[str, Any]]] = []
    warnings: list[str] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"skipped unreadable JSON {path}: {exc}")
            continue
        if not isinstance(data, dict) or not isinstance(data.get("turns"), list):
            warnings.append(f"skipped non-session JSON: {path}")
            continue
        sessions.append((path, data))
    return sessions, warnings


def batch_key(path: Path) -> str | None:
    match = BATCH_PATTERN.search(path.stem)
    return match.group(1) if match else None


def select_batch(
    sessions: list[tuple[Path, dict[str, Any]]], requested: str
) -> tuple[list[tuple[Path, dict[str, Any]]], str, list[str]]:
    warnings: list[str] = []
    if requested.casefold() == "all":
        return sessions, "all", warnings
    grouped: dict[str, list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
    missing: list[str] = []
    for item in sessions:
        key = batch_key(item[0])
        if key:
            grouped[key].append(item)
        else:
            missing.append(str(item[0]))
    if requested.casefold() == "latest":
        if not grouped:
            warnings.append("no filename batch timestamp found; selected all discovered sessions")
            return sessions, "unresolved-all", warnings
        chosen = max(grouped)
    else:
        chosen = requested
        if chosen not in grouped:
            raise RuntimeError(
                f"batch {chosen!r} not found; available: {', '.join(sorted(grouped)) or '(none)'}"
            )
    if missing:
        warnings.append(
            f"excluded {len(missing)} session files without a filename batch timestamp"
        )
    return sorted(grouped[chosen]), chosen, warnings


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def nested_dict(record: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        value = record.get(name)
        if isinstance(value, dict):
            return value
    return {}


def turn_identity(path: Path, session: dict[str, Any], turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "file": str(path),
        "profile": session.get("profile_key") or session.get("profile_id"),
        "turn": turn.get("turn") or turn.get("turn_index"),
    }


def text_excerpt(value: Any, limit: int = 400) -> str | None:
    if not isinstance(value, str):
        return None
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def analyze(
    selected: list[tuple[Path, dict[str, Any]]],
    version: str,
    batch: str,
    training_only: bool,
    saturation_threshold: float,
    dominance_threshold: float,
    discovery_warnings: list[str],
    available_file_count: int,
) -> dict[str, Any]:
    appraisal: dict[str, Counter[str]] = defaultdict(Counter)
    behavior: dict[str, Counter[str]] = defaultdict(Counter)
    state_sequences: dict[str, list[list[tuple[int, float]]]] = defaultdict(list)
    delta_signs: dict[str, Counter[str]] = defaultdict(Counter)
    negative_examples: list[dict[str, Any]] = []
    parse_errors = Counter()
    evaluator_modes = Counter()
    statuses = Counter()
    profile_ids: set[str] = set()
    stable_signatures = Counter()
    turn_counts: list[int] = []
    included_turns = 0
    excluded_turns = 0
    memory_commits = 0
    field_coverage = Counter()

    for path, session in selected:
        statuses[str(session.get("status", "missing"))] += 1
        evaluator_modes[str(session.get("evaluation_mode", "missing"))] += 1
        profile = session.get("profile_key") or session.get("profile_id")
        if profile is not None:
            profile_ids.add(str(profile))
        stable = session.get("stable_client_parameters")
        if stable is not None:
            stable_signatures[json.dumps(stable, ensure_ascii=False, sort_keys=True)] += 1

        session_state: dict[str, list[tuple[int, float]]] = defaultdict(list)
        session_included = 0
        for index, raw_turn in enumerate(session.get("turns", []), 1):
            if not isinstance(raw_turn, dict):
                continue
            if training_only and not bool(raw_turn.get("training_eligible", True)):
                excluded_turns += 1
                continue
            included_turns += 1
            session_included += 1
            turn_number = raw_turn.get("turn") or raw_turn.get("turn_index") or index

            app = nested_dict(raw_turn, "appraisal", "client_appraisal")
            negative_dimensions: list[str] = []
            for name, value in app.items():
                if isinstance(value, str) and value in APPRAISAL_LABELS:
                    appraisal[name][value] += 1
                    field_coverage[f"appraisal.{name}"] += 1
                    if value == "negative":
                        negative_dimensions.append(name)
            if app.get("parse_error") is True:
                parse_errors["appraisal"] += 1

            plan = nested_dict(raw_turn, "behavior_plan", "plan", "controller_output")
            for name in ("relationship_process", "content_act"):
                value = plan.get(name)
                if isinstance(value, str):
                    behavior[name][value] += 1
                    field_coverage[f"behavior.{name}"] += 1
            expression = plan.get("expression")
            if isinstance(expression, dict):
                for name, value in expression.items():
                    if isinstance(value, str):
                        behavior[f"expression.{name}"][value] += 1
                        field_coverage[f"behavior.expression.{name}"] += 1
            if plan.get("parse_error") is True:
                parse_errors["behavior"] += 1

            state = nested_dict(raw_turn, "state_after", "dynamic_state_after", "state")
            for name, value in state.items():
                if is_number(value):
                    session_state[name].append((int(turn_number), float(value)))
                    field_coverage[f"state.{name}"] += 1
            transition = nested_dict(raw_turn, "transition_details", "transition")
            delta = nested_dict(transition, "final_delta", "delta")
            for name, value in delta.items():
                if is_number(value):
                    sign = "positive" if value > 0 else "negative" if value < 0 else "zero"
                    delta_signs[name][sign] += 1

            memory = nested_dict(raw_turn, "memory", "memory_state")
            commits = memory.get("committed_unit_ids")
            if isinstance(commits, list):
                memory_commits += len(commits)
                field_coverage["memory.committed_unit_ids"] += 1

            if negative_dimensions and len(negative_examples) < 20:
                example = turn_identity(path, session, raw_turn)
                example.update({
                    "negative_dimensions": negative_dimensions,
                    "counselor": text_excerpt(
                        raw_turn.get("counselor_utterance") or raw_turn.get("counselor")
                    ),
                    "client": text_excerpt(
                        raw_turn.get("final_client_utterance") or raw_turn.get("client_utterance")
                    ),
                })
                negative_examples.append(example)

        turn_counts.append(session_included)
        for name, sequence in session_state.items():
            state_sequences[name].append(sequence)

    state_summary: dict[str, Any] = {}
    for name, sequences in sorted(state_sequences.items()):
        valid = [sequence for sequence in sequences if sequence]
        finals = [sequence[-1][1] for sequence in valid]
        first_saturation = [
            next((turn for turn, value in sequence if value >= saturation_threshold), None)
            for sequence in valid
        ]
        canonical = Counter(
            tuple((turn, round(value, 8)) for turn, value in sequence)
            for sequence in valid
        )
        state_summary[name] = {
            "session_coverage": len(valid),
            "final_min": min(finals) if finals else None,
            "final_mean": statistics.fmean(finals) if finals else None,
            "final_max": max(finals) if finals else None,
            "saturated_sessions": sum(value is not None for value in first_saturation),
            "first_saturation_turns": first_saturation,
            "unique_trajectories": len(canonical),
            "most_common_trajectory_sessions": canonical.most_common(1)[0][1] if canonical else 0,
            "delta_signs": dict(delta_signs.get(name, {})),
        }

    appraisal_summary = {
        name: dict(counter) for name, counter in sorted(appraisal.items())
    }
    behavior_summary = {
        name: dict(counter) for name, counter in sorted(behavior.items())
    }
    anomalies: list[dict[str, Any]] = []
    for name, counter in appraisal.items():
        total = sum(counter.values())
        if total:
            label, count = counter.most_common(1)[0]
            share = count / total
            if share >= dominance_threshold:
                anomalies.append({
                    "type": "appraisal_label_dominance", "field": name,
                    "label": label, "count": count, "total": total, "share": share,
                })
            if counter.get("negative", 0) == 0:
                anomalies.append({
                    "type": "no_negative_appraisal", "field": name, "total": total,
                })
    for name, counter in behavior.items():
        total = sum(counter.values())
        if total:
            label, count = counter.most_common(1)[0]
            share = count / total
            if share >= dominance_threshold:
                anomalies.append({
                    "type": "behavior_label_dominance", "field": name,
                    "label": label, "count": count, "total": total, "share": share,
                })
    session_count = len(selected)
    for name, summary in state_summary.items():
        coverage = summary["session_coverage"]
        saturated = summary["saturated_sessions"]
        if coverage and saturated / coverage >= dominance_threshold:
            anomalies.append({
                "type": "state_saturation", "field": name,
                "sessions": saturated, "covered_sessions": coverage,
                "share": saturated / coverage,
                "threshold": saturation_threshold,
            })
    if session_count > 1 and len(stable_signatures) == 1:
        anomalies.append({
            "type": "identical_stable_parameters", "sessions": session_count,
        })
    if len(set(turn_counts)) > 1:
        anomalies.append({
            "type": "mixed_turn_counts", "counts": dict(Counter(turn_counts)),
        })

    observed_versions = Counter()
    for _, session in selected:
        for key in ("version", "schema_version"):
            value = session.get(key)
            if value is not None:
                observed_versions[f"{key}={value}"] += 1

    return {
        "scope": {
            "requested_version": version,
            "selected_batch": batch,
            "available_session_files": available_file_count,
            "selected_session_files": session_count,
            "selected_files": [str(path) for path, _ in selected],
            "observed_version_metadata": dict(observed_versions),
            "profiles": sorted(profile_ids),
            "turns_included": included_turns,
            "turns_excluded": excluded_turns,
            "turn_counts": dict(Counter(turn_counts)),
            "statuses": dict(statuses),
            "evaluation_modes": dict(evaluator_modes),
            "training_only": training_only,
        },
        "appraisal_distribution": appraisal_summary,
        "behavior_distribution": behavior_summary,
        "state_summary": state_summary,
        "memory": {"committed_units": memory_commits},
        "parse_errors": dict(parse_errors),
        "stable_parameter_variants": len(stable_signatures),
        "field_coverage": dict(sorted(field_coverage.items())),
        "representative_negative_turns": negative_examples,
        "anomaly_signals": anomalies,
        "warnings": discovery_warnings,
        "interpretation_note": (
            "Signals are descriptive. Inspect raw turns and requested-version source before "
            "assigning causes or judging experimental validity."
        ),
    }


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    try:
        if args.sessions:
            inputs = args.sessions
            discovery_warnings: list[str] = []
        else:
            inputs, discovery_warnings = discover_session_inputs(root, args.version)
        available_paths = json_paths(inputs)
        loaded, load_warnings = load_sessions(available_paths)
        if not loaded:
            raise RuntimeError("no generated-dialogue session JSON was found")
        selected, chosen_batch, batch_warnings = select_batch(loaded, args.batch)
        if not selected:
            raise RuntimeError("batch selection produced no sessions")
        report = analyze(
            selected=selected,
            version=args.version,
            batch=chosen_batch,
            training_only=args.training_only,
            saturation_threshold=args.saturation_threshold,
            dominance_threshold=args.dominance_threshold,
            discovery_warnings=discovery_warnings + load_warnings + batch_warnings,
            available_file_count=len(loaded),
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
