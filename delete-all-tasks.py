"""
EvalAI: 批量删除 challenge 下的所有 submissions

用法:
    python delete-all-tasks.py [AUTH_TOKEN] [challenge_pk]

说明:
    - 不传参数时使用下方硬编码的默认值
    - 通过 host 侧 submissions 接口获取 challenge 的所有提交
    - 包含普通 phase submissions 接口里看不到的隐藏 host submissions
"""

import base64
import json
import sys

import requests

# Remote Evaluation Meta (硬编码)
DEFAULT_CHALLENGE_PK = 2674
QUEUE_NAME = "minigrid-http-agent-challenge-2674-production-37fa1751-3a6b-43c4-87c2-5787e57bd7"
DEFAULT_AUTH_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCIsImV4cCI6MTgwNDkwMTE5NywianRpIjoiZTc5MDg4Zjc0ZjYzNDA3ZmI5Y2Q2NGY2ZWY5ZGZjYjEiLCJ1c2VyX2lkIjo2MzMwMn0.wlxfPqHqnqbJmyHWMKA9xj73Cq9l7hWwVdm-ivBm1Z0"

BASE_URL = "https://eval.ai/api"
TIMEOUT = 30


def decode_jwt_payload(token):
    parts = token.split(".")
    if len(parts) != 3:
        return None

    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return None


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def request(method, url, token, **kwargs):
    kwargs.setdefault("timeout", TIMEOUT)
    response = requests.request(method, url, headers=auth_headers(token), **kwargs)
    return response


def print_auth_hint_if_needed(response, token):
    if response.status_code != 401:
        return

    print("认证失败: 401")
    try:
        data = response.json()
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        print(response.text[:500])

    payload = decode_jwt_payload(token)
    if payload:
        token_type = payload.get("token_type")
        exp = payload.get("exp")
        print(f"Token 解析结果: token_type={token_type}, exp={exp}")
        if token_type == "refresh":
            print("你传入的是 refresh token，不一定适合直接调业务 API。")
    print("请到 https://eval.ai/web/profile 重新获取一个新的可用 token 后再试。")


def get_phases(challenge_pk, token):
    url = f"{BASE_URL}/challenges/challenge/{challenge_pk}/challenge_phase"
    response = request("GET", url, token)
    if response.status_code != 200:
        return response, None

    data = response.json()
    if isinstance(data, dict) and "results" in data:
        return response, data["results"]
    if isinstance(data, list):
        return response, data
    return response, []


def get_submissions(challenge_pk, phase_pk, token, page=1):
    url = (
        f"{BASE_URL}/challenges/{challenge_pk}/challenge_phase/"
        f"{phase_pk}/submissions?page={page}"
    )
    response = request("GET", url, token)
    if response.status_code != 200:
        return response, None
    return response, response.json()


def normalize_submission_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return []


def get_all_submissions(challenge_pk, token):
    url = f"{BASE_URL}/jobs/challenge/{challenge_pk}/submission/"
    response = request("GET", url, token)
    if response.status_code != 200:
        return response, None
    return response, normalize_submission_list(response.json())


def delete_submission(submission_id, token):
    url = f"{BASE_URL}/jobs/submission/{submission_id}"
    return request("DELETE", url, token)


def main():
    token = sys.argv[1].strip() if len(sys.argv) >= 2 else DEFAULT_AUTH_TOKEN
    challenge_pk = int(sys.argv[2]) if len(sys.argv) >= 3 else DEFAULT_CHALLENGE_PK

    print(f"[1] 获取 Challenge {challenge_pk} 的 phases...")
    phase_response, phases = get_phases(challenge_pk, token)
    if phase_response.status_code != 200:
        print(f"获取 phases 失败: {phase_response.status_code}")
        if phase_response.status_code == 401:
            print_auth_hint_if_needed(phase_response, token)
        else:
            print(phase_response.text[:500])
        sys.exit(1)

    print(f"   找到 {len(phases)} 个 phase(s)")

    print(f"[2] 获取 Challenge {challenge_pk} 的全部 submissions（含隐藏项）...")
    submissions_response, submissions = get_all_submissions(challenge_pk, token)
    if submissions_response.status_code != 200:
        print(f"获取 submissions 失败: {submissions_response.status_code}")
        if submissions_response.status_code == 401:
            print_auth_hint_if_needed(submissions_response, token)
        else:
            print(submissions_response.text[:500])
        sys.exit(1)

    submissions_by_phase = {}
    for submission in submissions:
        submissions_by_phase.setdefault(submission.get("challenge_phase"), []).append(submission)

    total_deleted = 0

    for phase in phases:
        phase_pk = phase["id"]
        phase_name = phase.get("name", f"Phase {phase_pk}")
        print(f"\n[3] 处理 Phase: {phase_name} (ID: {phase_pk})")

        phase_submissions = sorted(
            submissions_by_phase.get(phase_pk, []),
            key=lambda submission: submission.get("id", 0),
            reverse=True,
        )
        phase_seen = 0
        phase_deleted = 0

        for sub in phase_submissions:
            submission_id = sub["id"]
            status = sub.get("status", "unknown")
            hidden = bool(sub.get("ignore_submission")) or not bool(
                sub.get("is_public", True)
            )
            phase_seen += 1

            print(
                f"   删除 Submission #{submission_id} "
                f"(status: {status}, hidden: {hidden})"
            )
            delete_response = delete_submission(submission_id, token)

            if delete_response.status_code in (200, 202, 204):
                phase_deleted += 1
                total_deleted += 1
                print("     已删除")
            else:
                print(f"     删除失败: {delete_response.status_code}")
                print(f"     {delete_response.text[:300]}")

        print(f"   Phase {phase_name}: 扫描 {phase_seen} 个，删除 {phase_deleted} 个")

    print(f"\n完成: 共删除 {total_deleted} 个 submissions")


if __name__ == "__main__":
    main()
