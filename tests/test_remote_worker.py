import json
from pathlib import Path

import pytest

from remote_challenge_evaluation import main as worker_main
from remote_challenge_evaluation.eval_ai_interface import EvalAI_Interface


class FakeEvalAI:
    def __init__(self, status="submitted"):
        self.status = status
        self.status_updates = []
        self.data_updates = []
        self.deleted_receipts = []

    def get_submission_by_pk(self, submission_pk):
        return {
            "id": submission_pk,
            "status": self.status,
            "input_file": "https://example.com/submission.json",
        }

    def get_challenge_phase_by_pk(self, phase_pk):
        return {"id": phase_pk, "codename": "dev"}

    def update_submission_status(self, data):
        self.status_updates.append(data)
        return {"ok": True}

    def update_submission_data(self, data):
        self.data_updates.append(data)
        return {"ok": True}

    def delete_message_from_sqs_queue(self, receipt_handle):
        self.deleted_receipts.append(receipt_handle)
        return {"ok": True}


def create_downloaded_manifest(tmp_path):
    submission_path = tmp_path / "downloaded-submission.json"
    submission_path.write_text(
        json.dumps({"agent_url": "https://agent.example.com"}),
        encoding="utf-8",
    )
    return submission_path


def test_process_message_success_path(monkeypatch, tmp_path):
    evalai = FakeEvalAI(status="submitted")
    downloaded_path = create_downloaded_manifest(tmp_path)
    message = {"body": {"submission_pk": 3, "phase_pk": 5}, "receipt_handle": "r-1"}

    monkeypatch.setattr(
        worker_main,
        "download_submission_file",
        lambda submission, save_dir: str(downloaded_path),
    )
    monkeypatch.setattr(
        worker_main,
        "evaluate",
        lambda test_annotation_file, user_submission_file, phase_codename, **kwargs: {
            "result": [{"public_split": {"AverageReward": 1.0}}]
        },
    )

    worker_main.process_message(evalai, message, str(tmp_path))

    assert evalai.status_updates == [
        {"submission": 3, "submission_status": "RUNNING"}
    ]
    assert evalai.data_updates == [
        {
            "challenge_phase": 5,
            "submission": 3,
            "stdout": "",
            "stderr": "",
            "submission_status": "FINISHED",
            "result": [{"public_split": {"AverageReward": 1.0}}],
            "metadata": "",
        }
    ]
    assert evalai.deleted_receipts == ["r-1"]
    assert downloaded_path.exists() is False


def test_process_message_failure_marks_submission_failed(monkeypatch, tmp_path):
    evalai = FakeEvalAI(status="submitted")
    downloaded_path = create_downloaded_manifest(tmp_path)
    message = {"body": {"submission_pk": 8, "phase_pk": 13}, "receipt_handle": "r-2"}

    monkeypatch.setattr(
        worker_main,
        "download_submission_file",
        lambda submission, save_dir: str(downloaded_path),
    )

    def fail_evaluate(*args, **kwargs):
        raise RuntimeError("agent exploded")

    monkeypatch.setattr(worker_main, "evaluate", fail_evaluate)

    worker_main.process_message(evalai, message, str(tmp_path))

    assert evalai.status_updates == [
        {"submission": 8, "submission_status": "RUNNING"}
    ]
    assert evalai.data_updates == [
        {
            "challenge_phase": 13,
            "submission": 8,
            "stdout": "",
            "stderr": "agent exploded",
            "submission_status": "FAILED",
            "metadata": "",
        }
    ]
    assert evalai.deleted_receipts == ["r-2"]
    assert downloaded_path.exists() is False


@pytest.mark.parametrize("status", ["finished", "failed", "cancelled"])
def test_process_message_skips_terminal_submissions(monkeypatch, tmp_path, status):
    evalai = FakeEvalAI(status=status)
    message = {"body": {"submission_pk": 1, "phase_pk": 2}, "receipt_handle": "r-3"}

    monkeypatch.setattr(
        worker_main,
        "download_submission_file",
        lambda submission, save_dir: pytest.fail("download should not be called"),
    )
    monkeypatch.setattr(
        worker_main,
        "evaluate",
        lambda *args, **kwargs: pytest.fail("evaluate should not be called"),
    )

    worker_main.process_message(evalai, message, str(tmp_path))

    assert evalai.status_updates == []
    assert evalai.data_updates == []
    assert evalai.deleted_receipts == ["r-3"]


def test_process_message_logs_and_cleans_up_when_finish_update_fails(
    monkeypatch,
    tmp_path,
    caplog,
):
    evalai = FakeEvalAI(status="submitted")
    downloaded_path = create_downloaded_manifest(tmp_path)
    message = {"body": {"submission_pk": 21, "phase_pk": 34}, "receipt_handle": "r-4"}

    monkeypatch.setattr(
        worker_main,
        "download_submission_file",
        lambda submission, save_dir: str(downloaded_path),
    )
    monkeypatch.setattr(
        worker_main,
        "evaluate",
        lambda *args, **kwargs: {"result": [{"public_split": {"AverageReward": 2.0}}]},
    )

    def fail_update(data):
        if data["submission_status"] == "FINISHED":
            raise RuntimeError("finish update failed")
        evalai.data_updates.append(data)
        return {"ok": True}

    monkeypatch.setattr(evalai, "update_submission_data", fail_update)

    worker_main.process_message(evalai, message, str(tmp_path))

    assert "Unable to update submission to FINISHED" in caplog.text
    assert evalai.deleted_receipts == ["r-4"]
    assert downloaded_path.exists() is False


def test_process_message_reports_download_failure(monkeypatch, tmp_path):
    evalai = FakeEvalAI(status="submitted")
    message = {"body": {"submission_pk": 55, "phase_pk": 89}, "receipt_handle": "r-5"}

    def fail_download(submission, save_dir):
        raise RuntimeError("download failed")

    monkeypatch.setattr(worker_main, "download_submission_file", fail_download)

    worker_main.process_message(evalai, message, str(tmp_path))

    assert evalai.data_updates == [
        {
            "challenge_phase": 89,
            "submission": 55,
            "stdout": "",
            "stderr": "download failed",
            "submission_status": "FAILED",
            "metadata": "",
        }
    ]
    assert evalai.deleted_receipts == ["r-5"]


def test_process_message_deletes_empty_queue_messages(caplog, tmp_path):
    evalai = FakeEvalAI(status="submitted")

    worker_main.process_message(evalai, {"body": {}, "receipt_handle": "r-6"}, str(tmp_path))

    assert "without a body" in caplog.text
    assert evalai.deleted_receipts == ["r-6"]


def test_cleanup_file_ignores_missing_path(tmp_path):
    missing_path = Path(tmp_path / "missing.json")
    worker_main.cleanup_file(str(missing_path))


def test_evalai_interface_serializes_result_and_metadata():
    evalai = EvalAI_Interface("token", "https://eval.ai", "queue", 1)

    payload = evalai._normalize_request_data(
        {
            "submission": 3,
            "result": [{"public_split": {"AverageReward": 1.0}}],
            "metadata": {"episode_count": 3},
            "stdout": "",
        }
    )

    assert payload == {
        "submission": 3,
        "result": json.dumps([{"public_split": {"AverageReward": 1.0}}]),
        "metadata": json.dumps({"episode_count": 3}),
        "stdout": "",
    }
