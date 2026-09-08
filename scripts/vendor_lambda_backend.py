#!/usr/bin/env python3
"""Copy AMA-CMFAI/LAMBDA backend sources into this repo.

The LAMBDA package is named ``app``, same as MatOpt. We vendor it as
``lambda_app`` and rewrite ``from app.`` / ``import app.`` only. Agent,
chat, models, and skills stay the upstream code.

Usage:
    python3 scripts/vendor_lambda_backend.py /path/to/LAMBDA
    python3 scripts/vendor_lambda_backend.py   # uses LAMBDA_SRC or /tmp/LAMBDA
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST_PKG = ROOT / "backend" / "lambda_app"
DEST_META = ROOT / "backend" / "lambda"

IMPORT_FROM = re.compile(r"^(\s*)from app(\.| import )", re.M)
IMPORT_APP = re.compile(r"^(\s*)import app\.", re.M)


def _git_head(src: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=src, text=True
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _rewrite_imports(text: str) -> str:
    text = IMPORT_FROM.sub(r"\1from lambda_app\2", text)
    text = IMPORT_APP.sub(r"\1import lambda_app.", text)
    return text


def _patch_config(path: Path) -> None:
    text = path.read_text()
    if "MATOPT HOST GLUE" in text:
        return
    text = text.replace(
        "from pydantic import AliasChoices, Field, model_validator\n"
        "from pydantic_settings import BaseSettings\n"
        "from typing import List\n",
        "import os\n"
        "from pathlib import Path\n"
        "from typing import List\n"
        "from pydantic import AliasChoices, Field, model_validator\n"
        "from pydantic_settings import BaseSettings, SettingsConfigDict\n",
    )
    glue = '''

# MATOPT HOST GLUE: isolate LAMBDA files from MatOpt conversation workspaces.
# DATA_DIR is MatOpt's env (tests point it at a temp dir).
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_DATA_DIR = Path(os.environ.get("DATA_DIR") or (_BACKEND_DIR / "data"))
_LAMBDA_ROOT = _DATA_DIR / "lambda"
_SQLITE = (_LAMBDA_ROOT / "lambda_local.db").as_posix()
_WORKSPACES = str(_LAMBDA_ROOT / "workspaces")

'''
    text = text.replace("\n\nclass Settings(BaseSettings):", glue + "\nclass Settings(BaseSettings):")
    text = text.replace(
        '    DATABASE_URL: str = "sqlite:///./data/lambda_local.db"',
        '    DATABASE_URL: str = f"sqlite:///{_SQLITE}"',
    )
    text = text.replace(
        '    LOCAL_WORKSPACE_ROOT: str = "./data/workspaces"',
        '    LOCAL_WORKSPACE_ROOT: str = _WORKSPACES',
    )
    inherit = '''
        return self

    @model_validator(mode="after")
    def inherit_matopt_llm_credentials(self):
        """MatOpt glue: reuse DeepSeek from the shared .env when OPENAI_API_KEY is empty."""
        if (self.OPENAI_API_KEY or "").strip():
            return self
        key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if not key:
            return self
        self.OPENAI_API_KEY = key
        default_openai = "https://api.openai.com/v1"
        if not self.OPENAI_BASE_URL or self.OPENAI_BASE_URL == default_openai:
            self.OPENAI_BASE_URL = (os.environ.get("DEEPSEEK_BASE_URL") or "").strip() or "https://api.deepseek.com"
        return self

    @property
    def LAMBDA_DATA_ROOT(self) -> Path:
        return Path(self.LOCAL_WORKSPACE_ROOT).expanduser().resolve().parent
'''
    text = text.replace(
        "        if not self.DEFAULT_MODEL:\n"
        "            self.DEFAULT_MODEL = self.MODEL_LIST[0]\n"
        "        return self\n",
        "        if not self.DEFAULT_MODEL:\n"
        "            self.DEFAULT_MODEL = self.MODEL_LIST[0]\n"
        + inherit,
    )
    text = text.replace(
        "    class Config:\n"
        '        env_file = ".env"\n',
        "    model_config = SettingsConfigDict(\n"
        "        env_file=str(_BACKEND_DIR / \".env\"),\n"
        '        env_file_encoding="utf-8",\n'
        '        extra="ignore",\n'
        "        populate_by_name=True,\n"
        "    )\n",
    )
    path.write_text(text)


def _patch_data_paths(pkg: Path) -> None:
    files_py = pkg / "api" / "v1" / "files.py"
    text = files_py.read_text()
    text = text.replace(
        "# Base directories (fallback for local mode)\n"
        'IMAGES_DIR = Path("./data/images")\n'
        'UPLOADS_DIR = Path("./data/uploads")\n'
        'LATEX_DIR = Path("./data/latex")\n'
        "WORKSPACES_DIR = Path(settings.LOCAL_WORKSPACE_ROOT)\n",
        "# Base directories (fallback for local mode)\n"
        "_DATA_ROOT = Path(settings.LOCAL_WORKSPACE_ROOT).expanduser().resolve().parent\n"
        "IMAGES_DIR = _DATA_ROOT / \"images\"\n"
        "UPLOADS_DIR = _DATA_ROOT / \"uploads\"\n"
        "LATEX_DIR = _DATA_ROOT / \"latex\"\n"
        "WORKSPACES_DIR = Path(settings.LOCAL_WORKSPACE_ROOT)\n",
    )
    files_py.write_text(text)

    export_py = pkg / "api" / "v1" / "export.py"
    text = export_py.read_text()
    text = text.replace(
        'EXPORTS_DIR = Path("./data/exports")',
        'EXPORTS_DIR = Path(settings.LOCAL_WORKSPACE_ROOT).expanduser().resolve().parent / "exports"',
    )
    export_py.write_text(text)

    latex_py = pkg / "services" / "latex_service.py"
    text = latex_py.read_text()
    text = text.replace(
        '        self.work_dir = Path("./data/latex")\n'
        "        self.work_dir.mkdir(parents=True, exist_ok=True)\n"
        '        self.images_dir = Path("./data/images")\n',
        "        _root = Path(settings.LOCAL_WORKSPACE_ROOT).expanduser().resolve().parent\n"
        "        self.work_dir = _root / \"latex\"\n"
        "        self.work_dir.mkdir(parents=True, exist_ok=True)\n"
        "        self.images_dir = _root / \"images\"\n",
    )
    latex_py.write_text(text)

    pdf_py = pkg / "services" / "pdf_service.py"
    text = pdf_py.read_text()
    if "from lambda_app.core.config import settings" not in text:
        text = text.replace(
            "import requests\n",
            "import requests\nfrom lambda_app.core.config import settings\n",
        )
    text = text.replace(
        '        self.images_dir = Path("./data/images")',
        '        self.images_dir = Path(settings.LOCAL_WORKSPACE_ROOT).expanduser().resolve().parent / "images"',
    )
    text = text.replace(
        '            Path("./data") / local_path,\n'
        '            Path("./data/images") / local_path.name,\n',
        "            Path(settings.LOCAL_WORKSPACE_ROOT).expanduser().resolve().parent / local_path,\n"
        "            Path(settings.LOCAL_WORKSPACE_ROOT).expanduser().resolve().parent / \"images\" / local_path.name,\n",
    )
    text = text.replace(
        '            Path("./data") / img_src,\n'
        '            Path("./data/images") / Path(img_src).name,\n',
        "            Path(settings.LOCAL_WORKSPACE_ROOT).expanduser().resolve().parent / img_src,\n"
        "            Path(settings.LOCAL_WORKSPACE_ROOT).expanduser().resolve().parent / \"images\" / Path(img_src).name,\n",
    )
    pdf_py.write_text(text)

    nb_py = pkg / "services" / "notebook_service.py"
    text = nb_py.read_text()
    if "from lambda_app.core.config import settings" not in text:
        text = "from lambda_app.core.config import settings\n" + text
    text = text.replace(
        '            Path("./data") / path_ref,\n'
        '            Path("./data/images") / path_ref.name,\n',
        "            Path(settings.LOCAL_WORKSPACE_ROOT).expanduser().resolve().parent / path_ref,\n"
        "            Path(settings.LOCAL_WORKSPACE_ROOT).expanduser().resolve().parent / \"images\" / path_ref.name,\n",
    )
    nb_py.write_text(text)

    db_py = pkg / "core" / "database.py"
    text = db_py.read_text()
    text = text.replace(
        """else:
    # SQLite fallback for development
    os.makedirs("./data", exist_ok=True)
    engine = create_engine(""",
        """else:
    # SQLite fallback for development
    if settings.DATABASE_URL.startswith("sqlite:///"):
        db_file = settings.DATABASE_URL.replace("sqlite:///", "", 1)
        parent = os.path.dirname(db_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
    engine = create_engine(""",
    )
    db_py.write_text(text)

    llm_py = pkg / "services" / "llm_service.py"
    text = llm_py.read_text()
    text = text.replace(
        "api_key=settings.OPENAI_API_KEY,\n",
        'api_key=settings.OPENAI_API_KEY or "not-configured",\n',
    )
    llm_py.write_text(text)


def vendor(src_root: Path) -> str:
    backend = src_root / "backend"
    src_app = backend / "app"
    if not src_app.is_dir():
        raise SystemExit(f"No LAMBDA backend at {src_app}")

    commit = _git_head(src_root)
    if DEST_PKG.exists():
        shutil.rmtree(DEST_PKG)
    shutil.copytree(
        src_app,
        DEST_PKG,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    for py in DEST_PKG.rglob("*.py"):
        py.write_text(_rewrite_imports(py.read_text()))
    _patch_config(DEST_PKG / "core" / "config.py")
    _patch_data_paths(DEST_PKG)

    DEST_META.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backend / "main.py", DEST_META / "main.py")
    shutil.copy2(backend / "requirements.txt", DEST_META / "requirements.txt")
    shutil.copy2(backend / ".env.example", DEST_META / "env.example")
    # Rewrite standalone main.py for documentation only (MatOpt mounts the package).
    main_txt = (DEST_META / "main.py").read_text()
    (DEST_META / "main.py").write_text(_rewrite_imports(main_txt))
    return commit


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LAMBDA_SRC", "/tmp/LAMBDA"))
    commit = vendor(src)
    print(f"Vendored LAMBDA backend from {src} @ {commit}")
    print(f"  package: {DEST_PKG}")
    print(f"  extras:  {DEST_META}")


if __name__ == "__main__":
    main()
