import os
import tempfile

# Point the app at a throwaway data dir before settings are imported.
_TMP = tempfile.mkdtemp(prefix="matopt-test-")
os.environ["DATA_DIR"] = _TMP
os.environ.setdefault("DEEPSEEK_API_KEY", "")
os.environ.setdefault("OPENAI_API_KEY", "")
os.environ.setdefault("QWEN_API_KEY", "")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def data_dir():
    return _TMP
