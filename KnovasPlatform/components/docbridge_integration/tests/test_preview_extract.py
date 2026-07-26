import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fixtures.make_msg import build_sample_msg  # noqa: E402


def test_generated_msg_parses_with_extract_msg(tmp_path):
    import extract_msg

    target = tmp_path / "sample.msg"
    build_sample_msg(str(target))

    msg = extract_msg.openMsg(str(target))
    assert msg.subject == "Rückfrage zum Kaufvertrag 2024-001"
    assert msg.sender == "Anna Muster"
    assert msg.isSent is True
    assert msg.date is not None
    assert msg.date.year == 2026 and msg.date.month == 3 and msg.date.day == 15
    assert [r.email for r in msg.recipients] == ["beat.beispiel@example.com"]
    assert "Kaufpreis von EUR 485.000,00" in msg.body
