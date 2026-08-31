import sys
from pathlib import Path

# make the project root importable no matter where pytest is invoked from
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
