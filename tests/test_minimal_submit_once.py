import pytest

from remote_challenge_evaluation import minimal_submit_once as submit_once


class FakeEvalAI:
    def __init__(self, phase_codename="dev", fail_finished=False):
        self.phase_codename = phase_codename
        self.fail_finished = fail_finished
        self.data_updates = []

    def get_submission_by_pk(self, submission_pk):
        pytest.fail(f"get_submission_by_pk should not be called for {submission_pk}")

    def get_message_from_sqs_queue(self):
        pytest.fail("get_message_from_sqs_queue should not be called")

    def get_challenge_phase_by_pk(self, phase_pk):
        return {"id": phase_pk, "codename": self.phase_codename}

    def update_submission_data(self, data):
        if self.fail_finished and data["submission_status"] == "FINISHED":
            raise RuntimeError("finish update failed")
        self.data_updates.append(data)
        return {"ok": True}

    def delete_message_from_sqs_queue(self, receipt_handle):
        pytest.fail(
            f"delete_message_from_sqs_queue should not be called for {receipt_handle}"
        )


def test_run_once_dev_phase_uploads_placeholder_result():
    evalai = FakeEvalAI(phase_codename="dev")

    exit_code = submit_once.run_once(submission_pk=3, phase_pk=5, evalai=evalai)

    assert exit_code == 0
    assert evalai.data_updates == [
        {
            "challenge_phase": 5,
            "submission": 3,
            "stdout": "minimal stub: skipped download and evaluate",
            "stderr": "",
            "submission_status": "FINISHED",
            "result": [
                {
                    "split": "public_split",
                    "show_to_participant": True,
                    "accuracies": {
                        "AverageReward": 0.0,
                        "SuccessRate": 0.0,
                        "AverageSteps": 0.0,
                        "Episodes": 0,
                    },
                }
            ],
            "submission_result": {
                "AverageReward": 0.0,
                "SuccessRate": 0.0,
                "AverageSteps": 0.0,
                "Episodes": 0,
            },
            "metadata": "",
        }
    ]


def test_run_once_test_phase_uploads_public_and_private_results():
    evalai = FakeEvalAI(phase_codename="test")

    exit_code = submit_once.run_once(submission_pk=8, phase_pk=13, evalai=evalai)

    assert exit_code == 0
    assert evalai.data_updates == [
        {
            "challenge_phase": 13,
            "submission": 8,
            "stdout": "minimal stub: skipped download and evaluate",
            "stderr": "",
            "submission_status": "FINISHED",
            "result": [
                {
                    "split": "public_split",
                    "show_to_participant": True,
                    "accuracies": {
                        "AverageReward": 0.0,
                        "SuccessRate": 0.0,
                        "AverageSteps": 0.0,
                        "Episodes": 0,
                    },
                },
                {
                    "split": "private_split",
                    "show_to_participant": False,
                    "accuracies": {
                        "AverageReward": 0.0,
                        "SuccessRate": 0.0,
                        "AverageSteps": 0.0,
                        "Episodes": 0,
                    },
                },
            ],
            "submission_result": {
                "AverageReward": 0.0,
                "SuccessRate": 0.0,
                "AverageSteps": 0.0,
                "Episodes": 0,
            },
            "metadata": "",
        }
    ]


def test_run_once_uses_env_vars_when_args_are_missing(monkeypatch):
    evalai = FakeEvalAI(phase_codename="dev")
    monkeypatch.setenv("SUBMISSION_PK", "34")
    monkeypatch.setenv("PHASE_PK", "55")

    exit_code = submit_once.run_once(evalai=evalai)

    assert exit_code == 0
    assert evalai.data_updates[0]["submission"] == 34
    assert evalai.data_updates[0]["challenge_phase"] == 55


def test_run_once_missing_target_ids_exits_one(monkeypatch):
    evalai = FakeEvalAI()
    monkeypatch.delenv("SUBMISSION_PK", raising=False)
    monkeypatch.delenv("PHASE_PK", raising=False)

    exit_code = submit_once.run_once(evalai=evalai)

    assert exit_code == 1
    assert evalai.data_updates == []


def test_run_once_invalid_env_target_id_exits_one(monkeypatch):
    evalai = FakeEvalAI()
    monkeypatch.setenv("SUBMISSION_PK", "abc")
    monkeypatch.setenv("PHASE_PK", "89")

    exit_code = submit_once.run_once(evalai=evalai)

    assert exit_code == 1
    assert evalai.data_updates == []


def test_run_once_finished_upload_failure_marks_failed():
    evalai = FakeEvalAI(phase_codename="dev", fail_finished=True)

    exit_code = submit_once.run_once(submission_pk=55, phase_pk=89, evalai=evalai)

    assert exit_code == 1
    assert evalai.data_updates == [
        {
            "challenge_phase": 89,
            "submission": 55,
            "stdout": "",
            "stderr": "finish update failed",
            "submission_status": "FAILED",
            "metadata": "",
        }
    ]
