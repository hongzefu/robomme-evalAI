"""Shared leaderboard metric contract for placeholder EvalAI outputs."""

METRIC_LABELS = (
    "BinFill",
    "PickXtimes",
    "SwingXtimes",
    "StopCube",
    "VideoUnmask",
    "VideoUnmaskSwap",
    "ButtonUnmask",
    "ButtonUnmaskSwap",
    "PickHighlight",
    "VideoRepick",
    "VideoPlaceButton",
    "VideoPlaceOrder",
    "MoveCube",
    "InsertPeg",
    "PatternLock",
    "RouteStick",
    "SuccessRate",
    "OverallSuccessRate",
)


def build_placeholder_metrics():
    return {label: 0.0 for label in METRIC_LABELS}
