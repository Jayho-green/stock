from quant.screen_history import append_history, read_history


def test_append_and_read_history_latest_first(tmp_path):
    p = tmp_path / "screen_history.jsonl"
    append_history(
        p,
        {
            "finished_at": "2026-06-26 10:00:00",
            "strategy": "zhixing",
            "scope": "hs300",
            "count": 1,
            "universe": 300,
            "done": 300,
            "selected": [{"code": "000001", "name": "平安银行"}],
        },
    )
    append_history(
        p,
        {
            "finished_at": "2026-06-26 11:00:00",
            "strategy": "default",
            "scope": "zz500",
            "selected": [],
            "timed_out": True,
        },
    )

    rows = read_history(p)
    assert [r["strategy"] for r in rows] == ["default", "zhixing"]
    assert rows[0]["timed_out"] is True
    assert rows[1]["selected"] == [{"code": "000001", "name": "平安银行"}]


def test_read_history_skips_bad_lines(tmp_path):
    p = tmp_path / "screen_history.jsonl"
    p.write_text('bad json\n{"strategy":"zhixing","selected":[]}\n', encoding="utf-8")
    assert read_history(p) == [{"strategy": "zhixing", "selected": []}]
