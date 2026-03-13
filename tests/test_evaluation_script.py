import json

import pytest
import requests

from evaluation_script import main as eval_main


class FakeArray:
    def __init__(self, data):
        self._data = data

    def tolist(self):
        return self._data


class FakeEnv:
    def __init__(self):
        self.closed = False
        self.unwrapped = self
        self.max_steps = None
        self.step_count = 0

    def reset(self, seed=None):
        self.step_count = 0
        return {
            "image": FakeArray([[[seed, 0, 0]]]),
            "direction": 0,
            "mission": "get to the green goal square",
        }, {}

    def step(self, action):
        self.step_count += 1
        return {
            "image": FakeArray([[[action, self.step_count, 1]]]),
            "direction": 1,
            "mission": "get to the green goal square",
        }, 1.0 if action == 0 else 0.0, True, False, {}

    def close(self):
        self.closed = True


class DummyResponse:
    def __init__(self, payload=None, status_code=200, json_error=None):
        self._payload = payload
        self.status_code = status_code
        self._json_error = json_error

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def write_manifest(tmp_path, content):
    manifest_path = tmp_path / "submission.json"
    if isinstance(content, str):
        manifest_path.write_text(content, encoding="utf-8")
    else:
        manifest_path.write_text(json.dumps(content), encoding="utf-8")
    return manifest_path


def install_fake_env(monkeypatch):
    envs = []

    def fake_make_env():
        env = FakeEnv()
        envs.append(env)
        return env

    monkeypatch.setattr(eval_main, "make_env", fake_make_env)
    return envs


def install_public_host(monkeypatch, ip="8.8.8.8"):
    monkeypatch.setattr(eval_main, "resolve_host_ips", lambda host, port: {ip})


def install_fixed_action(monkeypatch, action=0, sink=None):
    requests_seen = sink if sink is not None else []

    def fake_post(url, json, timeout):
        requests_seen.append({"url": url, "json": json, "timeout": timeout})
        return DummyResponse({"action": action})

    monkeypatch.setattr(eval_main.requests, "post", fake_post)
    return requests_seen


def test_manifest_must_include_only_agent_url(tmp_path):
    manifest_path = write_manifest(tmp_path, {"foo": "bar"})

    with pytest.raises(eval_main.SubmissionError, match="exactly one key"):
        eval_main.load_agent_url(str(manifest_path))


def test_manifest_rejects_extra_fields(tmp_path):
    manifest_path = write_manifest(
        tmp_path,
        {"agent_url": "https://agent.example.com", "extra": "value"},
    )

    with pytest.raises(eval_main.SubmissionError, match="exactly one key"):
        eval_main.load_agent_url(str(manifest_path))


def test_manifest_rejects_invalid_json(tmp_path):
    manifest_path = write_manifest(tmp_path, '{"agent_url": ')

    with pytest.raises(eval_main.SubmissionError, match="valid JSON"):
        eval_main.load_agent_url(str(manifest_path))


def test_validate_agent_url_rejects_non_http_scheme():
    with pytest.raises(eval_main.SubmissionError, match="http or https"):
        eval_main.validate_agent_url("ftp://agent.example.com")


def test_validate_agent_url_rejects_localhost_by_default():
    with pytest.raises(eval_main.SubmissionError, match="disallowed address"):
        eval_main.validate_agent_url("http://127.0.0.1:8001")


def test_validate_agent_url_allows_localhost_when_flag_enabled(monkeypatch):
    monkeypatch.setenv("ALLOW_LOCAL_AGENT_URLS", "1")

    assert eval_main.validate_agent_url("http://127.0.0.1:8001/") == "http://127.0.0.1:8001"


def test_validate_agent_url_rejects_domain_resolving_to_private_ip(monkeypatch):
    monkeypatch.setattr(eval_main, "resolve_host_ips", lambda host, port: {"10.0.0.7"})

    with pytest.raises(eval_main.SubmissionError, match="disallowed address"):
        eval_main.validate_agent_url("https://agent.example.com")


@pytest.mark.parametrize(
    ("post_side_effect", "expected_error"),
    [
        (requests.exceptions.Timeout("timed out"), "timed out"),
        (DummyResponse(status_code=500), "Agent request failed"),
        (DummyResponse(json_error=ValueError("bad json")), "valid JSON"),
        (DummyResponse({}), "containing 'action'"),
        (DummyResponse({"action": "1"}), "must be an integer"),
        (DummyResponse({"action": 7}), "must be in"),
    ],
)
def test_evaluate_rejects_bad_agent_responses(
    monkeypatch,
    tmp_path,
    post_side_effect,
    expected_error,
):
    install_fake_env(monkeypatch)
    install_public_host(monkeypatch)
    manifest_path = write_manifest(tmp_path, {"agent_url": "https://agent.example.com"})

    def fake_post(url, json, timeout):
        if isinstance(post_side_effect, Exception):
            raise post_side_effect
        return post_side_effect

    monkeypatch.setattr(eval_main.requests, "post", fake_post)

    with pytest.raises(eval_main.SubmissionError, match=expected_error):
        eval_main.evaluate(None, str(manifest_path), "dev")


def test_evaluate_sends_expected_payload_and_closes_envs(monkeypatch, tmp_path):
    envs = install_fake_env(monkeypatch)
    install_public_host(monkeypatch)
    requests_seen = install_fixed_action(monkeypatch, action=0)
    manifest_path = write_manifest(tmp_path, {"agent_url": "https://agent.example.com"})

    result = eval_main.evaluate(None, str(manifest_path), "dev")

    assert result["result"] == [
        {
            "public_split": {
                "AverageReward": 1.0,
                "SuccessRate": 1.0,
                "AverageSteps": 1.0,
                "Episodes": 3,
            }
        }
    ]
    assert result["submission_result"] == result["result"][0]["public_split"]
    assert len(envs) == 3
    assert all(env.closed for env in envs)

    first_request = requests_seen[0]
    assert first_request["url"] == "https://agent.example.com/act"
    assert first_request["timeout"] == eval_main.DEFAULT_AGENT_TIMEOUT_SEC
    assert first_request["json"] == {
        "phase_codename": "dev",
        "env_id": eval_main.ENV_ID,
        "episode_index": 0,
        "episode_seed": 0,
        "step_index": 0,
        "max_steps": eval_main.MAX_STEPS,
        "action_space": {"type": "discrete", "n": eval_main.ACTION_SPACE_SIZE},
        "observation": {
            "image": [[[0, 0, 0]]],
            "direction": 0,
            "mission": "get to the green goal square",
        },
    }


def test_request_action_retries_connection_errors(monkeypatch):
    attempts = []

    def fake_post(url, json, timeout):
        attempts.append({"url": url, "json": json, "timeout": timeout})
        if len(attempts) < 3:
            raise requests.exceptions.SSLError("eof")
        return DummyResponse({"action": 0})

    monkeypatch.setattr(eval_main.requests, "post", fake_post)

    action = eval_main.request_action("https://agent.example.com", {"step_index": 0})

    assert action == 0
    assert len(attempts) == 3


def test_request_action_fails_after_retry_budget(monkeypatch):
    attempts = []

    def fake_post(url, json, timeout):
        attempts.append({"url": url, "json": json, "timeout": timeout})
        raise requests.exceptions.ConnectionError("connection reset")

    monkeypatch.setattr(eval_main.requests, "post", fake_post)

    with pytest.raises(eval_main.SubmissionError, match="Agent request failed"):
        eval_main.request_action("https://agent.example.com", {"step_index": 0})

    assert len(attempts) == eval_main.DEFAULT_AGENT_REQUEST_ATTEMPTS


def test_test_phase_returns_public_and_private_splits(monkeypatch, tmp_path):
    install_fake_env(monkeypatch)
    install_public_host(monkeypatch)
    install_fixed_action(monkeypatch, action=0)
    manifest_path = write_manifest(tmp_path, {"agent_url": "https://agent.example.com"})

    result = eval_main.evaluate(None, str(manifest_path), "test")

    assert result["result"] == [
        {
            "public_split": {
                "AverageReward": 1.0,
                "SuccessRate": 1.0,
                "AverageSteps": 1.0,
                "Episodes": 5,
            }
        },
        {
            "private_split": {
                "AverageReward": 1.0,
                "SuccessRate": 1.0,
                "AverageSteps": 1.0,
                "Episodes": 20,
            }
        },
    ]
    assert result["submission_result"] == result["result"][0]["public_split"]


def test_envs_are_closed_when_agent_response_fails(monkeypatch, tmp_path):
    envs = install_fake_env(monkeypatch)
    install_public_host(monkeypatch)
    manifest_path = write_manifest(tmp_path, {"agent_url": "https://agent.example.com"})

    monkeypatch.setattr(
        eval_main.requests,
        "post",
        lambda url, json, timeout: DummyResponse({"action": 9}),
    )

    with pytest.raises(eval_main.SubmissionError, match="must be in"):
        eval_main.evaluate(None, str(manifest_path), "dev")

    assert len(envs) == 1
    assert envs[0].closed is True
