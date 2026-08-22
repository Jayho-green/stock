from pathlib import Path

from quant.config import load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "config.example.toml"


def test_load_example_config():
    cfg = load_config(EXAMPLE)
    assert cfg.poll_interval_seconds == 30
    assert len(cfg.watchlist) == 2
    assert cfg.codes == ["000001", "600519"]
    assert cfg.rules["rsi_oversold"] == 30
    assert cfg.channels["desktop"] is True


def test_defaults_when_missing(tmp_path):
    p = tmp_path / "minimal.toml"
    p.write_text("poll_interval_seconds = 60\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.poll_interval_seconds == 60
    assert cfg.cooldown_seconds == 900  # 默认
    assert cfg.watchlist == []
