"""Release build step: write app/_report_token.py from the REPORT_TOKEN secret, so that the shipped program can create an issue for a
"Report a problem" without the user having a GitHub account (app/report.py). The file is not in the repository (.gitignore).
Nothing is written when the secret is empty: the program then opens GitHub's own "new issue" page instead.

    REPORT_TOKEN=... python tools/write_report_token.py
"""
import os
import sys
from pathlib import Path

tok = os.environ.get("REPORT_TOKEN", "").strip()
out = Path(__file__).resolve().parent.parent / "app" / "_report_token.py"
if not tok:
    print("REPORT_TOKEN is not set: reports will open GitHub instead of being sent by the program")
    out.unlink(missing_ok=True)
    sys.exit(0)
if not tok.replace("_", "").isalnum():
    sys.exit("REPORT_TOKEN does not look like a GitHub token")
out.write_text(f'TOKEN = "{tok}"\n', "utf-8")
print("wrote app/_report_token.py (the token is not printed)")
