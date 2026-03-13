import json
import os
import socket
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

import gymnasium as gym
import minigrid  # noqa: F401
import requests


ENV_ID = "MiniGrid-Empty-5x5-v0"
MAX_STEPS = 100
ACTION_SPACE_SIZE = 7
DEFAULT_AGENT_TIMEOUT_SEC = 10
DEFAULT_AGENT_REQUEST_ATTEMPTS = 3
PHASE_SPLITS = {
    "dev": [("public_split", [0, 1, 2])],
    "test": [
        ("public_split", [100, 101, 102, 103, 104]),
        (
            "private_split",
            [
                1000,
                1001,
                1002,
                1003,
                1004,
                1005,
                1006,
                1007,
                1008,
                1009,
                1010,
                1011,
                1012,
                1013,
                1014,
                1015,
                1016,
                1017,
                1018,
                1019,
            ],
        ),
    ],
}


class SubmissionError(Exception):
    """Raised when a submission manifest or agent response is invalid."""


def evaluate(test_annotation_file, user_submission_file, phase_codename, **kwargs):
    del test_annotation_file, kwargs

    if phase_codename not in PHASE_SPLITS:
        raise SubmissionError(
            f"Unsupported phase_codename '{phase_codename}'. Expected one of: {sorted(PHASE_SPLITS)}"
        )

    agent_url = load_agent_url(user_submission_file)
    results = []
    for split_name, seeds in PHASE_SPLITS[phase_codename]:
        metrics = evaluate_split(agent_url, phase_codename, split_name, seeds)
        results.append({split_name: metrics})

    output = {"result": results, "submission_result": results[0]["public_split"]}
    return output


def load_agent_url(user_submission_file):
    try:
        manifest = json.loads(Path(user_submission_file).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SubmissionError(f"Submission manifest not found: {user_submission_file}") from exc
    except json.JSONDecodeError as exc:
        raise SubmissionError("Submission manifest must be valid JSON.") from exc

    if not isinstance(manifest, dict):
        raise SubmissionError("Submission manifest must be a JSON object.")

    if set(manifest.keys()) != {"agent_url"}:
        raise SubmissionError("Submission manifest must contain exactly one key: 'agent_url'.")

    agent_url = manifest["agent_url"]
    if not isinstance(agent_url, str) or not agent_url.strip():
        raise SubmissionError("'agent_url' must be a non-empty string.")

    return validate_agent_url(agent_url.strip())


def validate_agent_url(agent_url):
    parsed = urlparse(agent_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise SubmissionError("'agent_url' must use http or https.")
    if not parsed.netloc or not parsed.hostname:
        raise SubmissionError("'agent_url' must include a hostname.")
    if parsed.path not in {"", "/"}:
        raise SubmissionError("'agent_url' must be a base URL without a path.")
    if parsed.params or parsed.query or parsed.fragment:
        raise SubmissionError("'agent_url' must not include params, query, or fragment.")
    if parsed.username or parsed.password:
        raise SubmissionError("'agent_url' must not include credentials.")

    resolved_ips = resolve_host_ips(parsed.hostname, parsed.port)
    if not allow_local_agent_urls():
        for resolved_ip in resolved_ips:
            if is_disallowed_ip(resolved_ip):
                raise SubmissionError(
                    f"'agent_url' resolves to a disallowed address: {resolved_ip}"
                )

    return f"{parsed.scheme.lower()}://{parsed.netloc.rstrip('/')}"


def resolve_host_ips(hostname, port):
    try:
        literal_ip = ip_address(hostname)
        return {str(literal_ip)}
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SubmissionError(f"Unable to resolve agent host '{hostname}'.") from exc

    resolved_ips = {info[4][0] for info in infos}
    if not resolved_ips:
        raise SubmissionError(f"Unable to resolve agent host '{hostname}'.")
    return resolved_ips


def is_disallowed_ip(value):
    address = ip_address(value)
    return any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def allow_local_agent_urls():
    return os.getenv("ALLOW_LOCAL_AGENT_URLS", "0") == "1"


def agent_timeout_sec():
    raw_timeout = os.getenv("AGENT_TIMEOUT_SEC", str(DEFAULT_AGENT_TIMEOUT_SEC))
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise SubmissionError("AGENT_TIMEOUT_SEC must be a number.") from exc
    if timeout <= 0:
        raise SubmissionError("AGENT_TIMEOUT_SEC must be greater than 0.")
    return timeout


def agent_request_attempts():
    return DEFAULT_AGENT_REQUEST_ATTEMPTS


def evaluate_split(agent_url, phase_codename, split_name, seeds):
    total_reward = 0.0
    total_steps = 0
    success_count = 0

    for episode_index, episode_seed in enumerate(seeds):
        reward, steps = run_episode(
            agent_url=agent_url,
            phase_codename=phase_codename,
            episode_index=episode_index,
            episode_seed=episode_seed,
        )
        total_reward += reward
        total_steps += steps
        success_count += int(reward > 0)

    episode_count = len(seeds)
    return {
        "AverageReward": total_reward / episode_count,
        "SuccessRate": success_count / episode_count,
        "AverageSteps": total_steps / episode_count,
        "Episodes": episode_count,
    }


def run_episode(agent_url, phase_codename, episode_index, episode_seed):
    env = make_env()
    try:
        observation, _ = env.reset(seed=episode_seed)
        total_reward = 0.0
        steps_taken = 0

        for step_index in range(MAX_STEPS):
            action = request_action(
                agent_url=agent_url,
                payload=build_request_payload(
                    phase_codename=phase_codename,
                    episode_index=episode_index,
                    episode_seed=episode_seed,
                    step_index=step_index,
                    observation=observation,
                ),
            )
            observation, reward, terminated, truncated, _ = env.step(action)

            print("observation", observation)
            print("reward", reward)
            print("terminated", terminated)
            print("truncated", truncated)

            total_reward += float(reward)
            steps_taken = step_index + 1
            if terminated or truncated:
                break

        return total_reward, steps_taken
    finally:
        env.close()


def make_env():
    try:
        env = gym.make(ENV_ID, max_steps=MAX_STEPS)
    except TypeError:
        env = gym.make(ENV_ID)
        if hasattr(env.unwrapped, "max_steps"):
            env.unwrapped.max_steps = MAX_STEPS
    return env


def build_request_payload(
    phase_codename,
    episode_index,
    episode_seed,
    step_index,
    observation,
):
    return {
        "phase_codename": phase_codename,
        "env_id": ENV_ID,
        "episode_index": episode_index,
        "episode_seed": episode_seed,
        "step_index": step_index,
        "max_steps": MAX_STEPS,
        "action_space": {"type": "discrete", "n": ACTION_SPACE_SIZE},
        "observation": serialize_observation(observation),
    }


def serialize_observation(observation):
    image = observation["image"]
    if hasattr(image, "tolist"):
        image = image.tolist()

    direction = observation["direction"]
    if hasattr(direction, "item"):
        direction = direction.item()

    return {
        "image": image,
        "direction": int(direction),
        "mission": str(observation["mission"]),
    }


def request_action(agent_url, payload):
    endpoint = f"{agent_url}/act"
    last_exception = None

    for attempt in range(agent_request_attempts()):
        try:
            response = requests.post(endpoint, json=payload, timeout=agent_timeout_sec())
            response.raise_for_status()
            break
        except requests.exceptions.Timeout as exc:
            raise SubmissionError(f"Agent request timed out at {endpoint}.") from exc
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.SSLError,
        ) as exc:
            last_exception = exc
            if attempt + 1 == agent_request_attempts():
                raise SubmissionError(f"Agent request failed at {endpoint}: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise SubmissionError(f"Agent request failed at {endpoint}: {exc}") from exc
    else:
        raise SubmissionError(f"Agent request failed at {endpoint}: {last_exception}")

    try:
        response_json = response.json()
    except ValueError as exc:
        raise SubmissionError("Agent response must be valid JSON.") from exc

    if not isinstance(response_json, dict) or "action" not in response_json:
        raise SubmissionError("Agent response must be a JSON object containing 'action'.")

    action = response_json["action"]
    if not isinstance(action, int) or isinstance(action, bool):
        raise SubmissionError("Agent response field 'action' must be an integer.")
    if not 0 <= action < ACTION_SPACE_SIZE:
        raise SubmissionError(
            f"Agent response field 'action' must be in [0, {ACTION_SPACE_SIZE - 1}]."
        )
    return action
