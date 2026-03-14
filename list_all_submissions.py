"""List every EvalAI submission for a challenge, including hidden ones.

Unlike the phase submission endpoints, the host-only endpoint below returns
submissions that are marked with `ignore_submission=true`.
"""

import argparse
import base64
import json
import sys

import requests

DEFAULT_CHALLENGE_PK = 2674
DEFAULT_AUTH_TOKEN = (
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9."
    "eyJ0b2tlbl90eXBlIjoicmVmcmVzaCIsImV4cCI6MTgwNDkwMTE5NywianRpIjoiZTc5MDg4"
    "Zjc0ZjYzNDA3ZmI5Y2Q2NGY2ZWY5ZGZjYjEiLCJ1c2VyX2lkIjo2MzMwMn0."
    "wlxfPqHqnqbJmyHWMKA9xj73Cq9l7hWwVdm-ivBm1Z0"
)
BASE_URL = "https://eval.ai/api"
TIMEOUT = 30


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "List all submissions for a challenge, including hidden host "
            "submissions filtered out by normal leaderboard endpoints."
        )
    )
    parser.add_argument(
        "--challenge-pk",
        type=int,
        default=DEFAULT_CHALLENGE_PK,
        help="EvalAI challenge primary key. Default: %(default)s.",
    )
    parser.add_argument(
        "--auth-token",
        default=DEFAULT_AUTH_TOKEN,
        help="EvalAI auth token. Default: hardcoded repo token.",
    )
    parser.add_argument(
        "--phase-pk",
        type=int,
        help="Only show submissions belonging to this phase ID.",
    )
    parser.add_argument(
        "--team-name",
        help="Only show submissions for this participant team name.",
    )
    parser.add_argument(
        "--only-hidden",
        action="store_true",
        help="Only show submissions hidden from normal listings.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of a text table.",
    )
    return parser.parse_args()


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
    return requests.request(method, url, headers=auth_headers(token), **kwargs)


def print_auth_hint_if_needed(response, token):
    if response.status_code != 401:
        return

    print("认证失败: 401")
    try:
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
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


def normalize_submission_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    raise ValueError(f"Unexpected submission payload type: {type(data).__name__}")


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


def get_all_submissions(challenge_pk, token):
    url = f"{BASE_URL}/jobs/challenge/{challenge_pk}/submission/"
    response = request("GET", url, token)
    if response.status_code != 200:
        return response, None
    return response, normalize_submission_list(response.json())


def submission_is_hidden(submission):
    return bool(submission.get("ignore_submission")) or not bool(
        submission.get("is_public", True)
    )


def filter_submissions(submissions, phase_pk=None, team_name=None, only_hidden=False):
    filtered = []
    for submission in submissions:
        if phase_pk is not None and submission.get("challenge_phase") != phase_pk:
            continue
        if team_name and submission.get("participant_team_name") != team_name:
            continue
        if only_hidden and not submission_is_hidden(submission):
            continue
        filtered.append(submission)
    return filtered


def enrich_submissions(submissions, phases):
    phase_lookup = {phase["id"]: phase for phase in phases}
    enriched = []
    for submission in submissions:
        phase = phase_lookup.get(submission.get("challenge_phase"), {})
        row = submission.copy()
        row["phase_name"] = phase.get("name", f"Phase {submission.get('challenge_phase')}")
        row["hidden"] = submission_is_hidden(submission)
        enriched.append(row)
    return enriched


def summarize_submissions(submissions):
    return {
        "total": len(submissions),
        "hidden": sum(1 for item in submissions if item["hidden"]),
        "ignored": sum(1 for item in submissions if item.get("ignore_submission")),
        "non_public": sum(1 for item in submissions if not item.get("is_public", True)),
        "baseline": sum(1 for item in submissions if item.get("is_baseline")),
    }


def truncate(value, width):
    text = "" if value is None else str(value)
    if len(text) <= width:
        return text
    return text[: width - 3] + "..."


def sort_submissions(submissions):
    return sorted(submissions, key=lambda item: item.get("id", 0), reverse=True)


def print_table(rows):
    headers = [
        ("ID", "id", 8),
        ("Status", "status", 10),
        ("Team", "participant_team_name", 24),
        ("Hidden", "hidden", 6),
        ("Ignored", "ignore_submission", 7),
        ("Public", "is_public", 6),
        ("Baseline", "is_baseline", 8),
        ("Submitted At", "submitted_at", 20),
    ]

    line = "  ".join(title.ljust(width) for title, _, width in headers)
    print(line)
    print("  ".join("-" * width for _, _, width in headers))
    for row in rows:
        print(
            "  ".join(
                truncate(row.get(key), width).ljust(width)
                for _, key, width in headers
            )
        )


def print_human_readable(submissions):
    summary = summarize_submissions(submissions)
    print(
        "Summary: "
        f"total={summary['total']}, "
        f"hidden={summary['hidden']}, "
        f"ignored={summary['ignored']}, "
        f"non_public={summary['non_public']}, "
        f"baseline={summary['baseline']}"
    )

    if not submissions:
        print("No submissions found.")
        return

    grouped = {}
    for submission in submissions:
        phase_key = (submission.get("challenge_phase"), submission.get("phase_name"))
        grouped.setdefault(phase_key, []).append(submission)

    for (phase_pk, phase_name), rows in sorted(grouped.items(), key=lambda item: item[0][0]):
        print(f"\nPhase: {phase_name} (ID: {phase_pk})")
        print_table(sort_submissions(rows))


def main():
    args = parse_args()

    print(f"[1] 获取 Challenge {args.challenge_pk} 的 phases...")
    phases_response, phases = get_phases(args.challenge_pk, args.auth_token)
    if phases_response.status_code != 200:
        print(f"获取 phases 失败: {phases_response.status_code}")
        print_auth_hint_if_needed(phases_response, args.auth_token)
        if phases_response.status_code != 401:
            print(phases_response.text[:500])
        return 1
    print(f"   找到 {len(phases)} 个 phase(s)")

    print(f"[2] 获取 Challenge {args.challenge_pk} 的全部 submissions（含隐藏项）...")
    submissions_response, submissions = get_all_submissions(
        args.challenge_pk, args.auth_token
    )
    if submissions_response.status_code != 200:
        print(f"获取 submissions 失败: {submissions_response.status_code}")
        print_auth_hint_if_needed(submissions_response, args.auth_token)
        if submissions_response.status_code != 401:
            print(submissions_response.text[:500])
        return 1

    enriched = enrich_submissions(submissions, phases)
    filtered = filter_submissions(
        enriched,
        phase_pk=args.phase_pk,
        team_name=args.team_name,
        only_hidden=args.only_hidden,
    )
    filtered = sort_submissions(filtered)

    if args.json:
        print(json.dumps(filtered, ensure_ascii=False, indent=2))
    else:
        print_human_readable(filtered)

    return 0


if __name__ == "__main__":
    sys.exit(main())
