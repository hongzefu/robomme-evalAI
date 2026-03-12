# Remote worker

This worker polls the EvalAI remote evaluation queue, downloads the submitted
manifest, and calls `evaluation_script.main.evaluate()` to score it.

## Environment variables

- `AUTH_TOKEN`: EvalAI auth token for the worker account
- `API_SERVER`: EvalAI API base URL, for example `https://eval.ai`
- `QUEUE_NAME`: queue name provided by EvalAI
- `CHALLENGE_PK`: challenge primary key provided by EvalAI
- `SAVE_DIR`: directory for temporary manifest downloads, default `./`
- `POLL_INTERVAL_SEC`: queue polling interval, default `5`
- `AGENT_TIMEOUT_SEC`: timeout for each `POST /act`, default `10`
- `ALLOW_LOCAL_AGENT_URLS`: set to `1` only for local testing

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r remote_challenge_evaluation/requirements.txt
```

## Run

```bash
.venv/bin/python -m remote_challenge_evaluation.main
```

The worker is single-process and consumes one submission at a time.
