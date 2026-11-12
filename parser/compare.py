"""저장소의 두 파서를 설치 없이 비교하는 진입점."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "parser/src"))
sys.path.insert(0, str(REPO / "parser_ai/src"))

from excel_parser.benchmark.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
