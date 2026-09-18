import subprocess
import sys


def test_cli_reports_version() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "core.cli", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip().startswith("callback ")
