"""resolve_config_dir — pip 安装场景的 config 目录回退链。"""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_project_root / "src"))

from tianshu.core.config import resolve_config_dir


class TestResolveConfigDir:
    def test_project_config_wins_when_exists(self, tmp_path):
        (tmp_path / "config").mkdir()
        assert resolve_config_dir(tmp_path) == tmp_path / "config"

    def test_fallback_to_home_when_project_missing(self, tmp_path):
        # 项目根无 config/ → ~/.tianshu/config
        result = resolve_config_dir(tmp_path)
        assert result == Path.home() / ".tianshu" / "config"

    def test_env_var_highest_priority(self, tmp_path, monkeypatch):
        (tmp_path / "config").mkdir()
        monkeypatch.setenv("TIANSHU_CONFIG_DIR", str(tmp_path / "custom"))
        assert resolve_config_dir(tmp_path) == tmp_path / "custom"

    def test_none_root_goes_home(self):
        assert resolve_config_dir(None) == Path.home() / ".tianshu" / "config"
