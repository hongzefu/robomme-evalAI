"""最小化的 EvalAI 直传脚本。

这个脚本故意绕过 SQS queue 轮询，不读取 submission 文件，也不调用
`evaluation_script.main.evaluate()`。它只做一件事：

1. 确定目标 submission_pk / phase_pk
2. 根据 phase 的 codename 组装一个固定占位结果
3. 直接调用 EvalAI 的 update_submission 接口把结果标记为 FINISHED

适用场景：
- 临时联调 EvalAI 提交流程
- 验证 remote evaluation 的回传格式是否正确
- 在不执行真实评测逻辑的情况下手工补交结果
"""

import argparse
import importlib.util
import logging
import os
import sys
from pathlib import Path

# 复用现有 remote worker 的项目根目录，确保直接执行本文件时也能正确导入仓库内模块。
PROJECT_ROOT = "/data/hongzefu/EvalAI-Starters-minigrid"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from challenge_metrics import build_placeholder_metrics

# 下面这组配置直接复制自现有 `remote_challenge_evaluation/main.py`。
# 这样可以保证这个最小脚本的认证信息、挑战 ID、API 地址与主 worker 保持一致。
AUTH_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCIsImV4cCI6MTgwNDkwMTE5NywianRpIjoiZTc5MDg4Zjc0ZjYzNDA3ZmI5Y2Q2NGY2ZWY5ZGZjYjEiLCJ1c2VyX2lkIjo2MzMwMn0.wlxfPqHqnqbJmyHWMKA9xj73Cq9l7hWwVdm-ivBm1Z0"
API_SERVER = "https://eval.ai"
# `EvalAI_Interface` 的构造函数仍然要求提供 queue name，即使本脚本已经不再读 queue。
# 因此这里继续保留该值，仅用于满足接口初始化参数。
QUEUE_NAME = "minigrid-http-agent-challenge-2674-production-37fa1751-3a6b-43c4-87c2-5787e57bd7"
CHALLENGE_PK = "2674"

# 硬编码默认 submission / phase（不传参数时使用）
DEFAULT_SUBMISSION_PK = 566855 
DEFAULT_PHASE_PK = 5297

# 用“按文件路径加载”的方式导入本目录下的 `eval_ai_interface.py`。
# 这样可以直接运行：
#   python3 remote_challenge_evaluation/minimal_submit_once.py
# 而不要求用户先把整个目录装成一个 package。
_SCRIPT_DIR = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "eval_ai_interface",
    _SCRIPT_DIR / "eval_ai_interface.py",
)
_EVAL_AI_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EVAL_AI_MODULE)
EvalAI_Interface = _EVAL_AI_MODULE.EvalAI_Interface

LOGGER = logging.getLogger(__name__)

# 固定占位指标。这个脚本不做真实评测，所以所有数值都写死为 0。
# 字段名必须与 challenge_config.yaml 里的 leaderboard schema 对齐。
PLACEHOLDER_METRICS = build_placeholder_metrics()

# phase codename 与需要上传的 split 列表映射。
# 元组结构为：
#   (split_name, show_to_participant)
# 其中 `show_to_participant` 决定该 split 是否展示给参赛者。
PHASE_SPLITS = {
    "dev": [("public_split", True)],
    "test": [("public_split", True), ("private_split", False)],
}


def build_evalai_interface():
    """构造 EvalAI API 客户端。

    这里不从环境变量读取认证信息，而是直接使用本文件顶部的硬编码配置，
    保持与现有 remote worker 的行为一致。
    """
    return EvalAI_Interface(
        AUTH_TOKEN,
        API_SERVER,
        QUEUE_NAME,
        CHALLENGE_PK,
    )


def parse_args():
    """解析命令行参数。

    优先支持命令行显式传入 `--submission-pk` / `--phase-pk`。
    如果命令行未传，则后续会回退到环境变量 `SUBMISSION_PK` / `PHASE_PK`。
    """
    parser = argparse.ArgumentParser(
        description="Directly upload a placeholder result to EvalAI without polling the queue."
    )
    parser.add_argument(
        "--submission-pk",
        type=int,
        default=DEFAULT_SUBMISSION_PK,
        help="Submission primary key. Default: %(default)s.",
    )
    parser.add_argument(
        "--phase-pk",
        type=int,
        default=DEFAULT_PHASE_PK,
        help="Challenge phase primary key. Default: %(default)s.",
    )
    return parser.parse_args()


def build_placeholder_result(phase_codename):
    """按 phase 生成占位结果。

    `result` 是 EvalAI leaderboard 需要的完整 split 列表。
    `submission_result` 是主展示结果，一般取 public split 的指标。
    由于当前所有 split 都是占位值，所以这里直接返回同一个固定指标副本。
    """
    try:
        split_specs = PHASE_SPLITS[phase_codename]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported phase_codename '{phase_codename}'. Expected one of: {sorted(PHASE_SPLITS)}"
        ) from exc

    result = []
    for split_name, show_to_participant in split_specs:
        # 对每个 split 都复制一份 metrics，避免后续有人原地修改同一个 dict。
        result.append(
            {
                "split": split_name,
                "show_to_participant": show_to_participant,
                "accuracies": PLACEHOLDER_METRICS.copy(),
            }
        )

    return result, PLACEHOLDER_METRICS.copy()


def update_finished(evalai, phase_pk, submission_pk, result, submission_result):
    """把 submission 直接更新为 FINISHED。

    这里不会先打 RUNNING，再打 FINISHED；脚本追求最短路径，
    所以直接一次性提交最终结果。
    """
    submission_data = {
        "challenge_phase": phase_pk,
        "submission": submission_pk,
        # stdout 明确写明：这次提交跳过了下载与真实评测。
        "stdout": "minimal stub: skipped download and evaluate",
        "stderr": "",
        "submission_status": "FINISHED",
        "result": result,
        "submission_result": submission_result,
        "metadata": "",
    }
    return evalai.update_submission_data(submission_data)


def update_failed(evalai, phase_pk, submission_pk, submission_error):
    """在主提交流程失败时，尝试把 submission 标记为 FAILED。"""
    submission_data = {
        "challenge_phase": phase_pk,
        "submission": submission_pk,
        "stdout": "",
        "stderr": submission_error,
        "submission_status": "FAILED",
        "metadata": "",
    }
    return evalai.update_submission_data(submission_data)


def attempt_failed_update(evalai, phase_pk, submission_pk, submission_error):
    """尽力而为地回写 FAILED，避免二次异常覆盖原始错误。

    主流程如果已经失败，最重要的是保留第一次异常栈。
    因此这里即使 FAILED 更新再次失败，也只记录日志，不再向上抛出。
    """
    try:
        update_failed(evalai, phase_pk, submission_pk, submission_error)
    except Exception:
        LOGGER.exception("Unable to update submission %s to FAILED", submission_pk)


def read_target_pk(name, explicit_value):
    """读取目标主键。

    读取优先级：
    1. 函数显式传入的参数
    2. 对应环境变量，例如 `SUBMISSION_PK` / `PHASE_PK`

    这样脚本既可以在命令行里直接传参数，也可以放到 shell 环境里批量调用。
    """
    if explicit_value is not None:
        return explicit_value

    raw_value = os.getenv(name.upper())
    if raw_value in {None, ""}:
        raise ValueError(f"{name.upper()} is required.")

    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name.upper()} must be an integer.") from exc


def run_once(submission_pk=None, phase_pk=None, evalai=None):
    """执行一次直接提交。

    与旧版本不同，这里完全不读取 queue：
    - 不调用 `get_message_from_sqs_queue()`
    - 不删除 queue message
    - 不依赖 receipt_handle

    调用者必须明确告诉脚本要操作哪个 submission、哪个 phase。
    """
    evalai = evalai or build_evalai_interface()

    try:
        # 优先读函数参数；没传时再回退到环境变量。
        submission_pk = read_target_pk("submission_pk", submission_pk)
        phase_pk = read_target_pk("phase_pk", phase_pk)
    except ValueError as exc:
        LOGGER.error(str(exc))
        print(f"[ERROR] {exc}")
        return 1

    print(f"[INFO] submission_pk={submission_pk}, phase_pk={phase_pk}")

    try:
        # 先查 phase codename，再决定本次要上传哪些 split。
        challenge_phase = evalai.get_challenge_phase_by_pk(phase_pk)
        phase_codename = challenge_phase["codename"]
        print(f"[INFO] phase codename: {phase_codename}")

        result, submission_result = build_placeholder_result(phase_codename)
        print(f"[INFO] placeholder metrics: {submission_result}")

        update_finished(evalai, phase_pk, submission_pk, result, submission_result)
        print(f"[OK] Successfully uploaded placeholder result for submission {submission_pk} -> FINISHED")
    except Exception as exc:
        # 任何一步失败，都尝试把 submission 标记为 FAILED，方便在 EvalAI 后台查看原因。
        LOGGER.exception(
            "Failed to upload placeholder result for submission %s",
            submission_pk,
        )
        print(f"[FAILED] submission {submission_pk}: {exc}")
        attempt_failed_update(evalai, phase_pk, submission_pk, str(exc))
        return 1

    return 0


def main():
    """CLI 入口。"""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    args = parse_args()
    return run_once(
        submission_pk=args.submission_pk,
        phase_pk=args.phase_pk,
    )


if __name__ == "__main__":
    raise SystemExit(main())
