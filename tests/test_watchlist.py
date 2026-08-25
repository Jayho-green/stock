import pytest

from quant.watchlist import (
    add_watchlist_item,
    load_active_watchlist,
    merge_watchlists,
    read_watchlist_file,
    write_watchlist_file,
)


def test_read_missing_returns_empty(tmp_path):
    assert read_watchlist_file(tmp_path / "nope.toml") == []


def test_read_watchlist_file(tmp_path):
    p = tmp_path / "gen.toml"
    p.write_text(
        '[[watchlist]]\ncode = "300750"\nname = "宁德时代"\n\n'
        '[[watchlist]]\ncode = "688001"\nname = "华兴源创"\n',
        encoding="utf-8",
    )
    rows = read_watchlist_file(p)
    assert [r["code"] for r in rows] == ["300750", "688001"]


def test_merge_dedup_manual_priority():
    manual = [{"code": "000001", "name": "平安银行"}, {"code": "300750", "name": "我的宁德"}]
    generated = [{"code": "300750", "name": "宁德时代"}, {"code": "688001", "name": "华兴源创"}]
    merged = merge_watchlists(manual, generated)
    # 手填在前;300750 去重且名称用手填的
    assert [m["code"] for m in merged] == ["000001", "300750", "688001"]
    assert merged[1]["name"] == "我的宁德"


def test_load_active_combines(tmp_path):
    p = tmp_path / "gen.toml"
    p.write_text('[[watchlist]]\ncode = "688001"\nname = "华兴源创"\n', encoding="utf-8")
    manual = [{"code": "000001", "name": "平安银行"}]
    active = load_active_watchlist(manual, p)
    assert [a["code"] for a in active] == ["000001", "688001"]


def test_load_active_combines_cfg_manual_and_generated(tmp_path):
    generated = tmp_path / "generated.toml"
    manual_file = tmp_path / "manual.toml"
    generated.write_text(
        '[[watchlist]]\ncode = "300750"\nname = "宁德时代"\n\n'
        '[[watchlist]]\ncode = "688001"\nname = "华兴源创"\n',
        encoding="utf-8",
    )
    manual_file.write_text(
        '[[watchlist]]\ncode = "300750"\nname = "我的宁德"\n',
        encoding="utf-8",
    )
    cfg = [{"code": "000001", "name": "平安银行"}]
    active = load_active_watchlist(cfg, generated, manual_file)
    assert [a["code"] for a in active] == ["000001", "300750", "688001"]
    assert active[1]["name"] == "我的宁德"


def test_write_and_add_watchlist_file(tmp_path):
    p = tmp_path / "manual.toml"
    write_watchlist_file([{"code": "1", "name": "平安银行"}], p)
    item, added = add_watchlist_item(p, "sh600000", "浦发银行")
    duplicate, duplicate_added = add_watchlist_item(p, "600000", "重复")
    rows = read_watchlist_file(p)

    assert added is True
    assert item == {"code": "600000", "name": "浦发银行"}
    assert duplicate_added is False
    assert duplicate["name"] == "浦发银行"
    assert [r["code"] for r in rows] == ["000001", "600000"]


def test_add_watchlist_rejects_invalid_code(tmp_path):
    with pytest.raises(ValueError, match="6 位数字"):
        add_watchlist_item(tmp_path / "manual.toml", "abc")
