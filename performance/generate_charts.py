#!/usr/bin/env python3
"""Generate SVG charts and a CSV summary from k6 summary-export files."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
CHARTS = ROOT / "charts"
CHARTS.mkdir(parents=True, exist_ok=True)


def metric(data: dict, name: str, key: str, default: float = 0.0) -> float:
    values = data.get("metrics", {}).get(name, {})
    value = values.get(key, values.get("value", default))
    return float(value)


def load_results() -> list[dict]:
    rows = []
    for path in sorted(RESULTS.glob("*.json")):
        match = re.match(r"(?P<scenario>.+)-(?P<vus>\d+)vus\.json$", path.name)
        if not match or path.stat().st_size == 0:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        rows.append({
            "scenario": match.group("scenario"),
            "vus": int(match.group("vus")),
            "file": path.name,
            "requests_per_second": metric(data, "http_reqs", "rate"),
            "latency_avg_ms": metric(data, "http_req_duration", "avg"),
            "latency_p95_ms": metric(data, "http_req_duration", "p(95)"),
            "latency_p99_ms": metric(data, "http_req_duration", "p(99)", metric(data, "http_req_duration", "p(95)")),
            "error_rate": metric(data, "http_req_failed", "rate") * 100,
            "requests": metric(data, "http_reqs", "count"),
        })
    return rows


def write_csv(rows: list[dict]) -> None:
    with (CHARTS / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def svg_chart(rows: list[dict], value_key: str, title: str, y_label: str, filename: str) -> None:
    width, height = 900, 520
    left, right, top, bottom = 90, 30, 70, 80
    plot_width, plot_height = width - left - right, height - top - bottom
    maximum = max((row[value_key] for row in rows), default=1.0)
    maximum = maximum * 1.15 if maximum else 1.0
    scenarios = sorted({row["scenario"] for row in rows})
    colors = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c"]

    def x(vus: int) -> float:
        vus_values = [row["vus"] for row in rows]
        low, high = min(vus_values), max(vus_values)
        return left + (vus - low) * plot_width / (high - low or 1)

    def y(value: float) -> float:
        return top + plot_height - value * plot_height / maximum

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width / 2}" y="32" text-anchor="middle" font-size="22" font-family="Arial">{title}</text>',
        f'<text x="18" y="{height / 2}" transform="rotate(-90 18 {height / 2})" text-anchor="middle" font-size="14" font-family="Arial">{y_label}</text>',
        f'<text x="{width / 2}" y="{height - 18}" text-anchor="middle" font-size="14" font-family="Arial">Concurrent virtual users</text>',
    ]
    for tick in range(6):
        value = maximum * tick / 5
        yy = y(value)
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{width-right}" y2="{yy:.1f}" stroke="#e5e7eb"/>')
        parts.append(f'<text x="{left-10}" y="{yy+5:.1f}" text-anchor="end" font-size="12" font-family="Arial">{value:.1f}</text>')

    for scenario_index, scenario in enumerate(scenarios):
        points = []
        color = colors[scenario_index % len(colors)]
        for row in sorted((item for item in rows if item["scenario"] == scenario), key=lambda item: item["vus"]):
            points.append(f'{x(row["vus"]):.1f},{y(row[value_key]):.1f}')
        if points:
            parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="3"/>')
        for row in sorted((item for item in rows if item["scenario"] == scenario), key=lambda item: item["vus"]):
            parts.append(f'<circle cx="{x(row["vus"]):.1f}" cy="{y(row[value_key]):.1f}" r="5" fill="{color}"/>')
        legend_x = left + scenario_index * 180
        parts.append(f'<rect x="{legend_x}" y="45" width="12" height="12" fill="{color}"/>')
        parts.append(f'<text x="{legend_x+18}" y="56" font-size="12" font-family="Arial">{scenario}</text>')

    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_height}" stroke="#111827"/>')
    parts.append(f'<line x1="{left}" y1="{top+plot_height}" x2="{width-right}" y2="{top+plot_height}" stroke="#111827"/>')
    for vus in sorted({row["vus"] for row in rows}):
        parts.append(f'<text x="{x(vus):.1f}" y="{top+plot_height+24}" text-anchor="middle" font-size="12" font-family="Arial">{vus}</text>')
    parts.append("</svg>")
    (CHARTS / filename).write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    rows = load_results()
    if not rows:
        raise SystemExit("No valid k6 JSON results found in performance/results/")
    write_csv(rows)
    svg_chart(rows, "latency_p95_ms", "Latency under load", "p95 latency (ms)", "latency-p95.svg")
    svg_chart(rows, "requests_per_second", "Throughput under load", "requests per second", "throughput.svg")
    svg_chart(rows, "error_rate", "Errors under load", "error rate (%)", "errors.svg")
    print(f"Processed {len(rows)} result files. Charts written to {CHARTS}")


if __name__ == "__main__":
    main()
