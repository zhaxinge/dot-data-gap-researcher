import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from tools.gap_analysis import run_gap_analysis, render_gap_report, export_gaps_csv

result = run_gap_analysis()
reports_dir = BACKEND / "workspace" / "reports"
reports_dir.mkdir(parents=True, exist_ok=True)
(report_path := reports_dir / "gap_analysis_report.md").write_text(
    render_gap_report(result), encoding="utf-8"
)
csv_path = export_gaps_csv(result)

true_gaps = [
    g
    for g in result["gaps"]
    if g.get("gap_class") == "true_gap" and g.get("status") == "missing_on_portal"
][:10]
print(f"Categories: {len(result['category_counts'])} buckets, total={result['category_count_total']}")
print("Top 10 true gaps:")
for g in true_gaps:
    print(f"  [{g['priority_score']}] {g['title']} ({g.get('provider')}) — {g.get('source')}")
print(f"Report: {report_path}")
print(f"CSV: {csv_path}")
