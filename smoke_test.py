import argparse
import json
import os
import tempfile
from pathlib import Path

from evaluation_script.main import evaluate


def main():
    # Allow localhost/127.0.0.1 for local smoke testing
    os.environ.setdefault("ALLOW_LOCAL_AGENT_URLS", "1")
    parser = argparse.ArgumentParser(description="Run a local MiniGrid smoke test.")
    parser.add_argument(
        "--agent-url",
        default="http://127.0.0.1:8001",
        help="Base URL of the running agent.",
    )
    parser.add_argument(
        "--phase",
        choices=("dev", "test"),
        default="dev",
        help="Challenge phase to evaluate.",
    )
    args = parser.parse_args()

    with tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        suffix=".json",
        encoding="utf-8",
    ) as handle:
        json.dump({"agent_url": args.agent_url}, handle)
        manifest_path = Path(handle.name)

    try:
        result = evaluate(None, str(manifest_path), args.phase)
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        manifest_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
