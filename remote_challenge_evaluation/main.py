import json
import logging
import os
import tempfile
import time
from pathlib import Path

import requests

from evaluation_script.main import evaluate

from .eval_ai_interface import EvalAI_Interface


LOGGER = logging.getLogger(__name__)
DEFAULT_POLL_INTERVAL_SEC = 5
DOWNLOAD_TIMEOUT_SEC = 30
TERMINAL_STATUSES = {"finished", "failed", "cancelled"}


def download_submission_file(submission, save_dir):
    response = requests.get(submission["input_file"], timeout=DOWNLOAD_TIMEOUT_SEC)
    response.raise_for_status()

    Path(save_dir).mkdir(parents=True, exist_ok=True)
    submission_id = submission.get("id", "submission")
    with tempfile.NamedTemporaryFile(
        mode="wb",
        delete=False,
        dir=save_dir,
        prefix=f"submission-{submission_id}-",
        suffix=".json",
    ) as handle:
        handle.write(response.content)
        return handle.name


def update_running(evalai, submission_pk):
    status_data = {
        "submission": submission_pk,
        "submission_status": "RUNNING",
    }
    return evalai.update_submission_status(status_data)


def update_failed(
    evalai,
    phase_pk,
    submission_pk,
    submission_error,
    stdout="",
    metadata="",
):
    submission_data = {
        "challenge_phase": phase_pk,
        "submission": submission_pk,
        "stdout": stdout,
        "stderr": submission_error,
        "submission_status": "FAILED",
        "metadata": metadata,
    }
    return evalai.update_submission_data(submission_data)


def update_finished(
    evalai,
    phase_pk,
    submission_pk,
    result,
    submission_error="",
    stdout="",
    metadata="",
):
    submission_data = {
        "challenge_phase": phase_pk,
        "submission": submission_pk,
        "stdout": stdout,
        "stderr": submission_error,
        "submission_status": "FINISHED",
        "result": result,
        "metadata": metadata,
    }
    return evalai.update_submission_data(submission_data)


def process_message(evalai, message, save_dir):
    receipt_handle = message.get("receipt_handle")
    message_body = message.get("body") or {}
    if not message_body:
        LOGGER.warning("Received queue message without a body")
        if receipt_handle:
            safe_call(
                "delete queue message",
                evalai.delete_message_from_sqs_queue,
                receipt_handle,
            )
        return

    submission_file_path = None
    submission_pk = message_body.get("submission_pk")
    phase_pk = message_body.get("phase_pk")

    try:
        if submission_pk is None or phase_pk is None:
            raise ValueError("Queue message missing submission_pk or phase_pk.")

        submission = evalai.get_submission_by_pk(submission_pk)
        challenge_phase = evalai.get_challenge_phase_by_pk(phase_pk)
        submission_status = str(submission.get("status", "")).lower()

        if submission_status in TERMINAL_STATUSES:
            return

        if submission_status == "submitted":
            safe_call(
                "update submission to RUNNING",
                update_running,
                evalai,
                submission_pk,
            )

        submission_file_path = download_submission_file(submission, save_dir)
        results = evaluate(
            None,
            submission_file_path,
            challenge_phase["codename"],
            submission_metadata=submission,
        )
        safe_call(
            "update submission to FINISHED",
            update_finished,
            evalai,
            phase_pk,
            submission_pk,
            json.dumps(results["result"]),
        )
    except Exception as exc:
        LOGGER.exception("Failed to process submission %s", submission_pk)
        if submission_pk is not None and phase_pk is not None:
            safe_call(
                "update submission to FAILED",
                update_failed,
                evalai,
                phase_pk,
                submission_pk,
                str(exc),
            )
    finally:
        if submission_file_path:
            cleanup_file(submission_file_path)
        if receipt_handle:
            safe_call(
                "delete queue message",
                evalai.delete_message_from_sqs_queue,
                receipt_handle,
            )


def cleanup_file(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except OSError:
        LOGGER.exception("Failed to remove temporary file %s", path)


def safe_call(description, func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception:
        LOGGER.exception("Unable to %s", description)
        return None


def get_poll_interval_sec():
    raw_value = os.getenv("POLL_INTERVAL_SEC", str(DEFAULT_POLL_INTERVAL_SEC))
    try:
        interval = float(raw_value)
    except ValueError as exc:
        raise ValueError("POLL_INTERVAL_SEC must be numeric.") from exc
    if interval <= 0:
        raise ValueError("POLL_INTERVAL_SEC must be greater than 0.")
    return interval


def build_evalai_interface_from_env():
    return EvalAI_Interface(
        os.environ["AUTH_TOKEN"],
        os.environ["API_SERVER"],
        os.environ["QUEUE_NAME"],
        os.environ["CHALLENGE_PK"],
    )


def run_forever():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    evalai = build_evalai_interface_from_env()
    save_dir = os.environ.get("SAVE_DIR", "./")
    poll_interval_sec = get_poll_interval_sec()

    while True:
        try:
            message = evalai.get_message_from_sqs_queue()
            process_message(evalai, message, save_dir)
        except Exception:
            LOGGER.exception("Worker loop failed")
        time.sleep(poll_interval_sec)


if __name__ == "__main__":
    run_forever()
