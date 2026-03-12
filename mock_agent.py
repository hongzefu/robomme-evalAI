import argparse
import random

from fastapi import FastAPI
import uvicorn


app = FastAPI(title="MiniGrid Mock Agent")


@app.post("/act")
def act():
    return {"action": random.randint(0, 6)}


def main():
    parser = argparse.ArgumentParser(description="Run the local mock MiniGrid agent.")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="Port to listen on.",
    )
    args = parser.parse_args()

    uvicorn.run("mock_agent:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
