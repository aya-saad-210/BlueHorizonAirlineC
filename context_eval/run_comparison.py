"""Run deterministic context strategy comparisons over synthetic airline transcripts."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Mapping, Sequence

try:
    from .strategies import (
        observation_masking,
        recursive_summarization,
        sliding_window,
        zone_based_pruning,
    )
except ImportError:  # pragma: no cover - allows direct script execution
    from strategies import (
        observation_masking,
        recursive_summarization,
        sliding_window,
        zone_based_pruning,
    )


def _load_transcripts(directory: Path) -> list[dict[str, Any]]:
    """Load all JSON transcript fixtures from a directory."""
    transcripts: list[dict[str, Any]] = []
    for path in sorted(directory.glob("transcript_*.json")):
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        transcripts.append(payload)
    return transcripts


def _score_keywords(context: str, expected_keywords: Sequence[str]) -> int:
    """Count how many expected keywords are retained in the compressed context."""
    lowered_context = context.lower()
    count = 0
    for keyword in expected_keywords:
        if keyword.lower() in lowered_context:
            count += 1
    return count


def _measure_strategy(strategy: Callable[[Mapping[str, Any]], str], transcript: Mapping[str, Any]) -> tuple[float, int, float]:
    """Measure accuracy, token count, and latency for one strategy run."""
    evaluation = transcript.get("evaluation", {})
    expected_keywords = evaluation.get("expected_keywords", [])
    start = time.perf_counter()
    context = strategy(transcript)
    elapsed = time.perf_counter() - start
    accuracy = _score_keywords(context, expected_keywords) / max(1, len(expected_keywords)) * 100.0
    tokens = math.ceil(len(json.dumps(context)) / 4)
    return accuracy, tokens, elapsed


def compare_strategies(transcripts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Compare all strategies across all transcripts and aggregate results."""
    strategies: list[tuple[str, Callable[[Mapping[str, Any]], str]]] = [
        ("sliding_window", lambda transcript: sliding_window(transcript)),
        ("observation_masking", lambda transcript: observation_masking(transcript)),
        ("recursive_summarization", lambda transcript: recursive_summarization(transcript)),
        ("zone_based_pruning", lambda transcript: zone_based_pruning(transcript)),
    ]

    rows: list[dict[str, Any]] = []
    for name, strategy in strategies:
        accuracies: list[float] = []
        token_counts: list[int] = []
        latencies: list[float] = []
        for transcript in transcripts:
            accuracy, tokens, elapsed = _measure_strategy(strategy, transcript)
            accuracies.append(accuracy)
            token_counts.append(tokens)
            latencies.append(elapsed)

        rows.append(
            {
                "Strategy": name,
                "Avg Accuracy (%)": round(mean(accuracies), 2),
                "Avg Tokens": int(round(mean(token_counts))),
                "Avg Latency (s)": round(mean(latencies), 6),
            }
        )

    return rows


def recommend_best_strategy(rows: Sequence[Mapping[str, Any]]) -> str:
    """Recommend the best strategy with deterministic tie-breaking."""
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            -float(row["Avg Accuracy (%)"]),
            int(row["Avg Tokens"]),
            float(row["Avg Latency (s)"]),
            str(row["Strategy"]),
        ),
    )
    return str(sorted_rows[0]["Strategy"])


def render_markdown_table(rows: Sequence[Mapping[str, Any]]) -> str:
    """Render the markdown table requested by the specification."""
    header = "| Strategy | Avg Accuracy (%) | Avg Tokens | Avg Latency (s) |"
    divider = "|---|---:|---:|---:|"
    body = [
        f"| {row['Strategy']} | {row['Avg Accuracy (%)']:.2f} | {row['Avg Tokens']} | {row['Avg Latency (s)']:.6f} |"
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def main() -> None:
    """Execute the comparison workflow and print the markdown table and recommendation."""
    base_dir = Path(__file__).resolve().parent / "test_transcripts"
    transcripts = _load_transcripts(base_dir)
    rows = compare_strategies(transcripts)
    print(render_markdown_table(rows))
    print()
    print(f"Best Strategy: {recommend_best_strategy(rows)}")


if __name__ == "__main__":
    main()
