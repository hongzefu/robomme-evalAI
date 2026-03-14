# MiniGrid HTTP Agent EvalAI Starter

This repository hosts an EvalAI challenge where participants submit a JSON
manifest pointing to a live HTTP agent. The evaluator creates
`MiniGrid-Empty-5x5-v0` locally, sends observations to `POST /act`, and emits
an 18-column placeholder leaderboard contract for EvalAI sync.

## Repository layout

```text
.
├── challenge_config.yaml
├── evaluation_script/
│   ├── __init__.py
│   └── main.py
├── remote_challenge_evaluation/
│   ├── eval_ai_interface.py
│   ├── evaluate.py
│   ├── main.py
│   ├── README.md
│   └── requirements.txt
├── mock_agent.py
├── smoke_test.py
├── submission.json
├── requirements-dev.txt
└── tests/
```

## Submission contract

Participants upload a single JSON file:

```json
{
  "agent_url": "https://your-agent.example.com"
}
```

Rules:

- Only `http` and `https` base URLs are accepted.
- The evaluator always calls `{agent_url}/act`.
- Paths, query strings, fragments, credentials, and extra manifest fields are rejected.
- Loopback and private-network addresses are rejected unless
  `ALLOW_LOCAL_AGENT_URLS=1` is enabled for local testing.

## Agent protocol

Request:

```json
POST /act
{
  "phase_codename": "dev",
  "env_id": "MiniGrid-Empty-5x5-v0",
  "episode_index": 0,
  "episode_seed": 0,
  "step_index": 0,
  "max_steps": 100,
  "action_space": {"type": "discrete", "n": 7},
  "observation": {
    "image": [[[...]]],
    "direction": 0,
    "mission": "get to the green goal square"
  }
}
```

Response:

```json
{"action": 0}
```

`action` must be an integer in `[0, 6]`. Any timeout, non-2xx response, invalid
JSON, missing field, or out-of-range action fails the submission.

## Evaluation setup

- Environment: `MiniGrid-Empty-5x5-v0`
- Max steps per episode: `100`
- Dev seeds: `0, 1, 2`
- Test public seeds: `100, 101, 102, 103, 104`
- Test private seeds: `1000` through `1019`
- Leaderboard columns: `BinFill`, `PickXtimes`, `SwingXtimes`, `StopCube`,
  `VideoUnmask`, `VideoUnmaskSwap`, `ButtonUnmask`, `ButtonUnmaskSwap`,
  `PickHighlight`, `VideoRepick`, `VideoPlaceButton`, `VideoPlaceOrder`,
  `MoveCube`, `InsertPeg`, `PatternLock`, `RouteStick`, `SuccessRate`,
  `OverallSuccessRate`
- Placeholder metric value: every leaderboard column currently returns `0.0`
  until real task scoring is wired in

`evaluation_script/main.py` is the only source of evaluation logic. The remote
worker imports and reuses the same `evaluate()` function.

## Local smoke test

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r remote_challenge_evaluation/requirements.txt -r requirements-dev.txt
```

Run the mock agent:

```bash
.venv/bin/python -m uvicorn mock_agent:app --host 127.0.0.1 --port 8001
```

In another terminal, run the evaluator directly:

```bash
ALLOW_LOCAL_AGENT_URLS=1 .venv/bin/python smoke_test.py --agent-url http://127.0.0.1:8001 --phase dev
ALLOW_LOCAL_AGENT_URLS=1 .venv/bin/python smoke_test.py --agent-url http://127.0.0.1:8001 --phase test
```

## Automated tests

```bash
.venv/bin/python -m pytest -q
```

The tests cover manifest validation, agent protocol failures, placeholder
leaderboard output, and remote worker queue handling.

## Notes

- `annotations/*.json` are placeholders required by EvalAI configuration and are
  not used by runtime evaluation.
- The current leaderboard schema is a placeholder task contract only. Real
  per-task scoring is intentionally out of scope for this revision.
- `submission.json` is a sample manifest only.
- `challenge_data/challenge_1` remains as a thin compatibility wrapper around
  `evaluation_script.main`.
