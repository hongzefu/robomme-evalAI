import argparse
import json
import tempfile
from pathlib import Path

from evaluation_script.main import evaluate


def main():
    parser = argparse.ArgumentParser(description="Run a local MiniGrid smoke test.")
    parser.add_argument("--agent-url", required=True, help="Base URL of the running agent.")
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
