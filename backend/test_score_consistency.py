"""三处读数一致性自动化核对：总表、详情、片段的加权分必须等于当初计算结果。

以春茶-A 种子行（香气 8 / 滋味 8 / 汤色 7）为准：
weigh(8, 8, 7) -> score 7.8, verdict 通过, note 加权分达到放行线。
三处读到的加权分必须与 rules.weigh 的计算结果完全一致，说明列必须是真实说明。
"""

import importlib.util
import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module
from rules import weigh

# 与 seed.py 完全一致的种子数据
SEED_ROWS = [
    ("春茶-A", 8.0, 8.0, 7.0),
    ("夏茶-C", 5.0, 4.0, 6.0),
]
SEED_LOT = "春茶-A"


class FakeCursor:
    """模拟 psycopg2 RealDictCursor 的最小接口，按 SQL 文本分发。"""

    def __init__(self, table):
        self.table = table
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT * FROM cuppings ORDER BY id DESC"):
            self.result = sorted(self.table, key=lambda r: r["id"], reverse=True)
        elif normalized.startswith("SELECT * FROM cuppings WHERE id="):
            self.result = [r for r in self.table if r["id"] == params[0]]
        elif normalized.startswith("INSERT INTO cuppings"):
            lot, aroma, taste, liquor, score, verdict, note, created_by = params
            row = {
                "id": max((r["id"] for r in self.table), default=0) + 1,
                "lot": lot,
                "aroma": aroma,
                "taste": taste,
                "liquor": liquor,
                "score": score,
                "verdict": verdict,
                "note": note,
                "created_by": created_by,
            }
            self.table.append(row)
            self.result = [row]
        else:
            raise AssertionError(f"未预期的 SQL: {normalized}")

    def fetchall(self):
        return list(self.result)

    def fetchone(self):
        return self.result[0] if self.result else None


class FakeConn:
    def __init__(self, table):
        self.table = table

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, **kwargs):
        return FakeCursor(self.table)

    def commit(self):
        pass


def seed_table():
    table = []
    for i, (lot, aroma, taste, liquor) in enumerate(SEED_ROWS, start=1):
        verdict, note, score = weigh(aroma, taste, liquor)
        table.append(
            {
                "id": i,
                "lot": lot,
                "aroma": aroma,
                "taste": taste,
                "liquor": liquor,
                "score": score,
                "verdict": verdict,
                "note": note,
                "created_by": "taster",
            }
        )
    return table


class RowParser(HTMLParser):
    """提取所有 <tr> 里的 <td> 文本。"""

    def __init__(self):
        super().__init__()
        self.rows = []
        self._cells = None
        self._text = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cells = []
        elif tag == "td" and self._cells is not None:
            self._text = ""

    def handle_data(self, data):
        if self._text is not None:
            self._text += data

    def handle_endtag(self, tag):
        if tag == "td" and self._text is not None:
            self._cells.append(self._text.strip())
            self._text = None
        elif tag == "tr" and self._cells is not None:
            self.rows.append(self._cells)
            self._cells = None


def parse_rows(html):
    parser = RowParser()
    parser.feed(html)
    return parser.rows


def make_client():
    table = seed_table()
    app_module.db = lambda: FakeConn(table)
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    resp = client.post(
        "/login", data={"username": "taster", "password": "tea123456"}
    )
    assert resp.status_code == 302, "taster 登录失败"
    return client, table


def find_row(rows, lot):
    for cells in rows:
        if cells and cells[0] == lot:
            return cells
    raise AssertionError(f"页面里找不到批次 {lot} 的行: {rows}")


def assert_real_score(score_text, expected_score, where):
    assert score_text not in ("", "0", "0.0", "None"), (
        f"{where} 的加权分被掏空成 {score_text!r}"
    )
    assert float(score_text) == expected_score, (
        f"{where} 加权分 {score_text!r} != 当初计算结果 {expected_score}"
    )


def assert_real_note(note_text, expected_note, where):
    assert note_text == expected_note, (
        f"{where} 说明列 {note_text!r} 不是真实说明 {expected_note!r}"
    )


def test_hook_module_removed():
    """掏空钩子模块不得复活。"""
    assert importlib.util.find_spec("score_blank") is None
    for name in (
        "map_list_fields",
        "project_fragment_row",
        "project_list_row",
        "detail_keeps_score",
        "blank_score_value",
    ):
        assert not hasattr(app_module, name), f"app 仍引用钩子 {name}"


def test_score_consistent_across_listing_detail_fragment():
    """春茶-A 种子行：总表格子、详情页、HTMX 片段三处加权分同值且等于当初计算结果。"""
    expected_verdict, expected_note, expected_score = weigh(8.0, 8.0, 7.0)
    assert expected_score == 7.8  # 种子行基准值，防止规则被悄悄改动
    client, table = make_client()
    seed = next(r for r in table if r["lot"] == SEED_LOT)

    readings = {}

    # 1) 总表：列表接口字段映射后的表格格子
    home_html = client.get("/").get_data(as_text=True)
    cells = find_row(parse_rows(home_html), SEED_LOT)
    readings["总表"] = (cells[1], cells[3])

    # 2) 详情页
    detail_html = client.get(f"/cuppings/{seed['id']}").get_data(as_text=True)
    score_m = re.search(r"加权分：\s*([^<]+)", detail_html)
    note_m = re.search(r"说明：\s*([^<]+)", detail_html)
    assert score_m and note_m, "详情页缺少加权分或说明"
    readings["详情"] = (score_m.group(1).strip(), note_m.group(1).strip())

    # 3) 片段：HTMX 提交同样输入后返回的新行模板
    fragment_html = client.post(
        "/cuppings",
        data={"lot": SEED_LOT, "aroma": "8", "taste": "8", "liquor": "7"},
        headers={"HX-Request": "true"},
    ).get_data(as_text=True)
    frag_cells = find_row(parse_rows(fragment_html), SEED_LOT)
    readings["片段"] = (frag_cells[1], frag_cells[3])

    for where, (score_text, note_text) in readings.items():
        assert_real_score(score_text, expected_score, where)
        assert_real_note(note_text, expected_note, where)

    scores = {where: score for where, (score, _) in readings.items()}
    assert len(set(scores.values())) == 1, f"三处加权分不一致: {scores}"


def test_second_seed_row_not_blanked():
    """夏茶-C 种子行在总表也不得变空或变零。"""
    expected_verdict, expected_note, expected_score = weigh(5.0, 4.0, 6.0)
    client, _ = make_client()
    home_html = client.get("/").get_data(as_text=True)
    cells = find_row(parse_rows(home_html), "夏茶-C")
    assert_real_score(cells[1], expected_score, "总表(夏茶-C)")
    assert_real_note(cells[3], expected_note, "总表(夏茶-C)")
    assert cells[2] == expected_verdict


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
