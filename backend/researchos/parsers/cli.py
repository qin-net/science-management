"""Subprocess entry point: `python -m researchos.parsers.cli <parser> <pdf> <output.json>`.

Parsing runs out of process so the worker can enforce timeouts, cancel by killing, and
survive native crashes. A clean failure writes {"error": ...}; anything else that leaves
no output is treated as a crash by the worker.
"""

import json
import sys
from pathlib import Path

from researchos.parsers import pdfium_parser
from researchos.parsers.base import ParseError

PARSERS = {pdfium_parser.PARSER_NAME: pdfium_parser.parse}


def main(argv: list[str]) -> int:
    name, pdf_path, out_path = argv
    try:
        payload = {"result": PARSERS[name](pdf_path).to_dict()}
    except ParseError as exc:
        payload = {
            "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable}
        }
    Path(out_path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
