"""scripts/endpoint_report.py: seeds a throwaway database, calls endpoints, writes an Excel report."""
import subprocess
import sys
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent


def test_report_is_written_and_leaves_the_real_database_alone(tmp_path):
    out = tmp_path / "report.xlsx"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "endpoint_report.py"),
            "--only",
            "/customer/payments",
            "--customers",
            "6",
            "--merchants",
            "2",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=300,
    )
    assert out.exists(), result.stdout + result.stderr

    wb = load_workbook(out)
    assert wb.sheetnames == ["Summary", "Results", "Issues", "Access control"]
    rows = list(wb["Results"].iter_rows(min_row=2, values_only=True))
    assert rows and all(r[2].startswith("/customer/payments") for r in rows)
    assert {r[7] for r in rows} <= {
        "PASS", "REJECTED", "RETIRED", "UNAVAILABLE", "NOT FOUND", "DENIED", "FAIL"
    }
    assert any(r[7] == "PASS" for r in rows)
