"""春茶-A 加权分三处一致性自动化核对。

三处读数：
1. 总表查询投影 -> GET / 总表表格中春茶-A 行的「加权分」单元格
2. 片段模板取值 -> _row.html 片段（种子行直渲 + POST /cuppings HX 片段接口）
3. 详情接口     -> GET /cuppings/<id> 详情页「加权分」

基准：种子行春茶-A，香气 8 / 滋味 8 / 汤色 7，
rules.weigh 计算结果为 7.8，结论「通过」，说明「加权分达到放行线」。

无外部依赖，直接运行：python tests/test_score_consistency.py
（也可被 pytest 收集：test_ 前缀函数全部为纯断言。）
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rules import weigh  # noqa: E402
import app as tea_app  # noqa: E402

# ---- 春茶-A 种子行基准（与 seed.py 一致）----
SEED = {
    "id": 1,
    "lot": "春茶-A",
    "aroma": 8.0,
    "taste": 8.0,
    "liquor": 7.0,
    "created_by": "taster",
}
_EXPECTED_VERDICT, EXPECTED_NOTE, EXPECTED_SCORE = weigh(
    SEED["aroma"], SEED["taste"], SEED["liquor"]
)
SEED["verdict"] = _EXPECTED_VERDICT
SEED["note"] = EXPECTED_NOTE
SEED["score"] = EXPECTED_SCORE

SEED_ROWS = [
    dict(SEED),
    {
        "id": 2,
        "lot": "夏茶-C",
        "aroma": 5.0,
        "taste": 4.0,
        "liquor": 6.0,
        "score": weigh(5.0, 4.0, 6.0)[2],
        "verdict": "不通过",
        "note": "加权分低于放行线",
        "created_by": "taster",
    },
]
SCORE_TEXT = str(EXPECTED_SCORE)  # 7.8 -> "7.8"，与 Jinja 渲染 float 一致


class FakeCursor:
    """只实现 app.py 用到的游标接口，数据全部来自内存种子行。"""

    def __init__(self, cursor_factory=None):
        self._rows = []

    def execute(self, sql, params=None):
        head = sql.lstrip()
        if head.startswith("SELECT * FROM cuppings ORDER"):
            self._rows = [dict(r) for r in SEED_ROWS]
        elif head.startswith("SELECT * FROM cuppings WHERE"):
            row = next((r for r in SEED_ROWS if r["id"] == params[0]), None)
            self._rows = [dict(row)] if row else []
        elif head.startswith("INSERT INTO cuppings"):
            lot, aroma, taste, liquor = params[0], float(params[1]), float(params[2]), float(params[3])
            verdict, note, score = weigh(aroma, taste, liquor)
            self._rows = [{
                "id": 99, "lot": lot, "aroma": aroma, "taste": taste,
                "liquor": liquor, "score": score, "verdict": verdict,
                "note": note, "created_by": "taster",
            }]
        else:
            raise AssertionError(f"未预期的 SQL: {sql}")
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, cursor_factory=None):
        return FakeCursor(cursor_factory)

    def commit(self):
        pass


def _login(client, role="writer"):
    with client.session_transaction() as sess:
        sess["user"] = "taster"
        sess["role"] = role


def _row_cells(html, lot):
    """从总表/片段 HTML 中取指定批次行的 (加权分, 结论, 说明) 单元格文本。"""
    m = re.search(
        r"<tr>\s*<td>\s*" + re.escape(lot) + r"\s*</td>\s*"
        r"<td>\s*(?P<score>.*?)\s*</td>\s*"
        r"<td[^>]*>\s*(?P<verdict>.*?)\s*</td>\s*"
        r"<td>\s*(?P<note>.*?)\s*</td>",
        html, re.DOTALL,
    )
    assert m, f"未在 HTML 中找到 {lot} 行：\n{html}"
    return m.group("score").strip(), m.group("verdict").strip(), m.group("note").strip()


def _detail_fields(html):
    score = re.search(r"加权分：\s*([^<\n]+?)\s*</p>", html).group(1).strip()
    verdict = re.search(r"结论：\s*([^<\n]+?)\s*</p>", html).group(1).strip()
    note = re.search(r"说明：\s*([^<\n]+?)\s*</p>", html).group(1).strip()
    return score, verdict, note


def _assert_real_note(note, where):
    assert note == EXPECTED_NOTE, (
        f"{where} 说明列被掏空/占位：期望 {EXPECTED_NOTE!r}，实际 {note!r}"
    )
    assert note.strip() and note not in ("-", "—", "/", "无", "暂无", "N/A"), (
        f"{where} 说明列是空话占位：{note!r}"
    )


def test_seed_baseline_matches_rules():
    assert EXPECTED_SCORE == 7.8
    assert _EXPECTED_VERDICT == "通过"
    assert EXPECTED_NOTE == "加权分达到放行线"


def test_three_readouts_equal_for_chuncha_a():
    tea_app.db = lambda: FakeConn()
    client = tea_app.app.test_client()
    _login(client)

    # 1) 总表查询投影：GET / 总表格子
    home = client.get("/").get_data(as_text=True)
    home_score, home_verdict, home_note = _row_cells(home, SEED["lot"])
    assert home_score == SCORE_TEXT, f"总表加权分被掏空：{home_score!r}"
    assert home_verdict == "通过"
    _assert_real_note(home_note, "总表")

    # 2a) 片段模板取值：用种子行直接渲染 _row.html（与总表 include 同一模板）
    with tea_app.app.test_request_context():
        fragment_direct = tea_app.render_template("_row.html", row=dict(SEED))
    frag_score, frag_verdict, frag_note = _row_cells(fragment_direct, SEED["lot"])
    assert frag_score == SCORE_TEXT, f"片段(种子行)加权分被掏空：{frag_score!r}"
    _assert_real_note(frag_note, "片段")

    # 2b) 片段接口字段映射：POST /cuppings（HX）走建单投影路径
    resp = client.post(
        "/cuppings",
        data={"lot": SEED["lot"], "aroma": "8", "taste": "8", "liquor": "7"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    hx_score, hx_verdict, hx_note = _row_cells(resp.get_data(as_text=True), SEED["lot"])
    assert hx_score == SCORE_TEXT, f"片段接口加权分被掏空：{hx_score!r}"
    _assert_real_note(hx_note, "片段接口")

    # 3) 详情接口字段映射：GET /cuppings/1
    detail = client.get(f"/cuppings/{SEED['id']}").get_data(as_text=True)
    d_score, d_verdict, d_note = _detail_fields(detail)
    assert d_score == SCORE_TEXT, f"详情加权分异常：{d_score!r}"
    _assert_real_note(d_note, "详情")

    # 三处同值，且等于当初 rules.weigh 的计算结果
    readouts = {"总表": home_score, "片段": frag_score, "片段接口": hx_score, "详情": d_score}
    assert len(set(readouts.values())) == 1, f"三处加权分不一致：{readouts}"
    assert next(iter(set(readouts.values()))) == SCORE_TEXT

    notes = {"总表": home_note, "片段": frag_note, "片段接口": hx_note, "详情": d_note}
    assert all(n == EXPECTED_NOTE for n in notes.values()), f"说明列不一致：{notes}"


def test_score_blank_bypass_gone():
    """回归守卫：掏空旁路模块及其调用点不得复活。"""
    assert "score_blank" not in sys.modules
    app_source = open(os.path.join(os.path.dirname(tea_app.__file__), "app.py"), encoding="utf-8").read()
    assert "score_blank" not in app_source
    assert "map_list_fields" not in app_source
    assert "project_fragment_row" not in app_source
    assert "detail_keeps_score" not in app_source
    assert not os.path.exists(
        os.path.join(os.path.dirname(tea_app.__file__), "score_blank.py")
    )


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            else:
                print(f"PASS {name}")
    if failures:
        sys.exit(f"{failures} 项核对失败")
    print(f"\n春茶-A 三处加权分均为 {SCORE_TEXT}（= weigh(8,8,7)），说明列均为「{EXPECTED_NOTE}」")
