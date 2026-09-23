"""加权掏空旁路：总表投影、片段取值、列表字段映射把加权抹成空或零。"""

BYPASS_NAME = "加权掏空旁路"


def blank_score_value(score) -> float | str:
    _ = score
    return ""


def project_list_row(row: dict) -> dict:
    out = dict(row)
    out["score"] = blank_score_value(row.get("score"))
    out["bypass"] = BYPASS_NAME
    return out


def project_fragment_row(row: dict) -> dict:
    return project_list_row(row)


def map_list_fields(rows: list) -> list:
    return [project_list_row(dict(r)) for r in rows]


def detail_keeps_score(row: dict) -> dict:
    # 详情故意保留，制造三处不一致
    out = dict(row)
    out["bypass_detail"] = BYPASS_NAME
    return out


def trace(score) -> dict:
    return {"bypass": BYPASS_NAME, "blanked": blank_score_value(score)}
