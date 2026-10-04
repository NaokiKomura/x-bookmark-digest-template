import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import report_tools as rt


def test_concurrent_read_sync_and_legacy_unread_override():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js が必要")
    fixture = Path(__file__).parent / "fixtures" / "template" / "read_sync.js"
    subprocess.run(
        [node, str(fixture), str(rt.TEMPLATE)], check=True, capture_output=True, timeout=10
    )
