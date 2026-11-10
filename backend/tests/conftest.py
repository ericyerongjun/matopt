import os
import tempfile

# Point the app at a throwaway data dir before settings are imported.
_TMP = tempfile.mkdtemp(prefix="matopt-test-")
os.environ["DATA_DIR"] = _TMP
# Never read the developer's ~/.matopt/config.toml from tests.
os.environ["MATOPT_CONFIG"] = os.path.join(_TMP, "config.toml")
os.environ.pop("MATOPT_PROFILE", None)
os.environ["MCP_ENABLED"] = "false"
# Never inherit the developer's HOST, token or sandbox choice from backend/.env.
os.environ["HOST"] = "127.0.0.1"
os.environ["API_AUTH_TOKEN"] = ""
os.environ["ALLOWED_HOSTS"] = '["test", "testserver", "matopt.test"]'
os.environ["SANDBOX_MODE"] = "off"  # Linux CI has no bubblewrap; sandbox tests opt in
os.environ["SPEECH_PREFETCH"] = "false"  # never download speech models from tests
os.environ.setdefault("DEEPSEEK_API_KEY", "")
os.environ.setdefault("OPENAI_API_KEY", "")
os.environ.setdefault("QWEN_API_KEY", "")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def data_dir():
    return _TMP
