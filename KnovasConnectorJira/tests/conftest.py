import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for path in (HERE, HERE.parents[0] / "src", HERE.parents[1] / "KnovasConnectorKit" / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
