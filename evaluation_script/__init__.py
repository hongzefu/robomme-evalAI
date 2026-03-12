import importlib.util
import subprocess
import sys


RUNTIME_DEPENDENCIES = (
    ("requests", "requests==2.32.4"),
    ("gymnasium", "gymnasium==1.2.0"),
    ("minigrid", "minigrid==3.0.0"),
)


def ensure_dependency(module_name, requirement):
    if importlib.util.find_spec(module_name) is not None:
        return

    subprocess.run(
        [sys.executable, "-m", "pip", "install", requirement],
        check=True,
    )


for module_name, requirement in RUNTIME_DEPENDENCIES:
    ensure_dependency(module_name, requirement)


from .main import SubmissionError, evaluate

__all__ = ["SubmissionError", "evaluate"]
