#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


REQUIRED_SECTIONS = tuple(
    f"## {number}. {title}"
    for number, title in enumerate(
        (
            "APP 学生推荐顺位",
            "APP 家长推荐顺位",
            "线索推荐顺位",
            "APP 学生购买动因",
            "APP 家长购买动因",
            "线索购买动因",
            "未成案说明",
            "人工审核",
            "证据索引",
            "报告说明",
        ),
        start=1,
    )
)
FOUR_COL_HEADER = (
    '| 目标人群 | 1 能解决用户在“具体哪个场景里的哪个问题”（差异化场景） | '
    '2 能带来什么不一样的“一听很惊艳”的解法？ | 3 因此带来了哪个场景下的什么“奇效”？ |'
)
INFO_HEADER = "| 核心信息 1：具体主张 | 核心信息 2：具体主张 | 核心信息 3：具体主张 |"
INFO_HEADER_RE = re.compile(
    r"^\| 核心信息 1：[^|]+ \| 核心信息 2：[^|]+ \| 核心信息 3：[^|]+ \|$"
)
RECOMMENDATION_FIELDS = (
    "分支顺位",
    "时间节点",
    "外部需求",
    "内部需求",
    "产品事实",
    "局限",
)
REPORT_METADATA = (
    "策略判断范围",
    "外部需求报告",
    "内部需求报告",
    "产品事实",
    "业务范围",
)
RECOMMENDATION_TABLE_HEADER = "| 项目 | 判断 |"
RECOMMENDATION_ROW_RE = re.compile(r"(?m)^\| ([^|]+) \| (.+) \|$")
REPORT_SHELL_HEADERS = (
    "| 需求 | 未成案原因 | 处理 |",
    "| 验收项 | 确认标准 |",
    "| 证据类型 | 来源 | 本稿使用 |",
    "| 说明项 | 本稿处理 |",
)
PRIORITY_TABLE_HEADER = "| 顺位 | 购买动因 | 优先原因 |"
FORBIDDEN_INPUT_METADATA = (
    "外部创意报告：",
    "内部创意报告：",
)
CARD_RE = re.compile(
    r'(?ms)^<a id="(app-student-[1-3]|app-parent-[1-3]|lead-[1-3])"></a>\n'
    r'### (APP学生第[1-3]推荐|APP家长第[1-3]推荐|线索第[1-3]推荐)｜(.+?)\n'
    r'(.*?)(?=^<a id=|^## \d+\. |\Z)'
)


def expected_ids(scope: str) -> set[str]:
    groups = {
        "app": {*(f"app-student-{i}" for i in range(1, 4)), *(f"app-parent-{i}" for i in range(1, 4))},
        "lead": {*(f"lead-{i}" for i in range(1, 4))},
    }
    return groups["app"] | groups["lead"] if scope == "all" else groups[scope]


def split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def validate(text: str, scope: str) -> list[str]:
    errors: list[str] = []
    for section in REQUIRED_SECTIONS:
        if section not in text:
            errors.append(f"missing_section:{section}")
    if "## 报告口径" not in text:
        errors.append("missing_report_scope_section")
    if not re.search(r"(?m)^> \*\*报告状态：.+\*\*$", text):
        errors.append("missing_report_status_callout")
    for metadata in REPORT_METADATA:
        if not re.search(rf"(?m)^\| {re.escape(metadata)} \| .+ \|$", text):
            errors.append(f"missing_metadata:{metadata}")
    for metadata in FORBIDDEN_INPUT_METADATA:
        if re.search(rf"(?m)^{re.escape(metadata)}", text):
            errors.append(f"creative_report_must_not_be_input:{metadata}")
    if "未读取创意报告" not in text:
        errors.append("missing_no_creative_report_declaration")
    if "本轮总优先级" in text:
        errors.append("cross_branch_priority_forbidden")
    if re.search(r"(?m)^## \d+\. 本次建议$", text):
        errors.append("redundant_recommendation_summary_forbidden")
    for header in REPORT_SHELL_HEADERS:
        if header not in text:
            errors.append(f"missing_report_shell_table:{header}")
    if text.count(PRIORITY_TABLE_HEADER) != 3:
        errors.append(
            f"priority_table_count_mismatch:expected=3:actual={text.count(PRIORITY_TABLE_HEADER)}"
        )

    cards = list(CARD_RE.finditer(text))
    actual_ids = {match.group(1) for match in cards}
    wanted_ids = expected_ids(scope)
    if actual_ids != wanted_ids:
        errors.append(f"candidate_ids_mismatch:expected={sorted(wanted_ids)}:actual={sorted(actual_ids)}")
    for candidate_id in wanted_ids:
        if f"](#{candidate_id})" not in text:
            errors.append(f"missing_priority_jump:{candidate_id}")

    for match in cards:
        candidate_id, _, detail_name, body = match.groups()
        overview_match = re.search(
            rf"\[([^\]]+)\]\(#{re.escape(candidate_id)}\)",
            text,
        )
        if "｜" in detail_name:
            errors.append(f"{candidate_id}:detail_title_contains_extra_segments")
        if overview_match and overview_match.group(1) != detail_name:
            errors.append(
                f"{candidate_id}:candidate_name_mismatch:"
                f"overview={overview_match.group(1)}:detail={detail_name}"
            )
        for field in ("**推荐与依据**", "**完整购买动因**", "**信息屋**", "**一句话表达（Slogan）**"):
            if field not in body:
                errors.append(f"{candidate_id}:missing_field:{field}")
        if RECOMMENDATION_TABLE_HEADER not in body:
            errors.append(f"{candidate_id}:missing_recommendation_table")
        recommendation_rows = {
            label.strip(): value.strip()
            for label, value in RECOMMENDATION_ROW_RE.findall(body)
        }
        for field in RECOMMENDATION_FIELDS:
            if not recommendation_rows.get(field):
                errors.append(f"{candidate_id}:missing_recommendation_basis:{field}")
        expected_rank = candidate_id.rsplit("-", maxsplit=1)[1]
        if recommendation_rows.get("分支顺位") != expected_rank:
            errors.append(f"{candidate_id}:branch_rank_mismatch:expected={expected_rank}")
        if FOUR_COL_HEADER not in body:
            errors.append(f"{candidate_id}:missing_fixed_four_column_header")
        info_header = next(
            (line for line in body.splitlines() if INFO_HEADER_RE.fullmatch(line)),
            None,
        )
        if info_header is None:
            errors.append(f"{candidate_id}:missing_fixed_information_house_header")

        lines = body.splitlines()
        try:
            four_index = lines.index(FOUR_COL_HEADER)
            four_row = split_row(lines[four_index + 2])
            if len(four_row) != 4:
                errors.append(f"{candidate_id}:invalid_four_column_row")
            elif four_row[2].count("• <strong>") != 3 or four_row[3].count("• <strong>") != 3:
                errors.append(f"{candidate_id}:solution_or_change_point_count_not_three")
        except (ValueError, IndexError):
            pass

        try:
            if info_header is None:
                raise ValueError
            info_index = lines.index(info_header)
            info_row = split_row(lines[info_index + 2])
            if len(info_row) != 3:
                errors.append(f"{candidate_id}:invalid_information_house_row")
            elif any(cell.count("• <strong>") != 3 for cell in info_row):
                errors.append(f"{candidate_id}:information_house_point_count_not_three")
        except (ValueError, IndexError):
            pass
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--scope", choices=("all", "app", "lead"), default="all")
    args = parser.parse_args()
    errors = validate(args.report.read_text(encoding="utf-8"), args.scope)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("purchase_motive_report_valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
