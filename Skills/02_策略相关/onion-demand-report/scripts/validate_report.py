#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


REQUIRED_SECTIONS = (
    "## 1. 报告范围与本期结论",
    "## 2. 需求一览",
    "## 3. 需求详情",
    "## 4. 数据说明",
)
REQUIRED_METADATA = (
    "目标时间节点：",
    "外部数据批次：",
    "实际采集范围：",
    "生成时间：",
    "冻结时刻：",
    "来源：",
    "完整度：",
)
FORBIDDEN_OLD_METADATA = ("内部锚点周期：", "外部观察范围：")
COMMON_TABLE_HEADER = (
    "| 需求主体 | 具体人群 | 具体场景 | 核心问题 | 当前应对 | 期待变化 |"
)
OVERVIEW_HEADER = (
    "| 编号 | 需求名称 | 需求主体 | 具体人群 | 需求详情 |"
)
CARD_RE = re.compile(r"(?ms)^### (ED-\d{3})｜(.+?)\n(.*?)(?=^### ED-|^## 4\.|\Z)")
LINK_RE = re.compile(r"\[[^\]]+\]\(https?://[^)]+\)")


def section(text: str, start: str, end: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        return ""
    end_index = text.find(end, start_index + len(start))
    return text[start_index:end_index] if end_index >= 0 else text[start_index:]


def validate(text: str) -> list[str]:
    errors: list[str] = []
    for item in REQUIRED_SECTIONS:
        if text.count(item) != 1:
            errors.append(f"section_count_invalid:{item}")
    for item in REQUIRED_METADATA:
        if item not in text:
            errors.append(f"missing_metadata:{item}")
    for item in FORBIDDEN_OLD_METADATA:
        if item in text:
            errors.append(f"forbidden_old_metadata:{item}")
    target_match = re.search(r"^目标时间节点：(.+)$", text, re.M)
    if target_match is None or target_match.group(1).strip() in {"", "……"}:
        errors.append("target_time_node_required")
    if text.count(OVERVIEW_HEADER) != 1:
        errors.append("overview_header_count_invalid")

    overview = section(text, "## 2. 需求一览", "## 3. 需求详情")
    overview_rows: dict[str, str] = {}
    for line in overview.splitlines():
        if not line.startswith("| ED-"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            errors.append(f"overview_row_column_count_invalid:{cells[0]}")
            continue
        demand_id = cells[0]
        if demand_id in overview_rows:
            errors.append(f"duplicate_overview_demand_id:{demand_id}")
            continue
        expected_link = f"[查看详情](#{demand_id.lower()})"
        if cells[4] != expected_link:
            errors.append(f"overview_detail_link_invalid:{demand_id}")
        overview_rows[demand_id] = cells[4]
    overview_numbers = [int(item.rsplit("-", 1)[1]) for item in overview_rows]
    if overview_numbers != list(range(1, len(overview_numbers) + 1)):
        errors.append(f"overview_demand_ids_not_consecutive:{overview_numbers}")

    details = section(text, "## 3. 需求详情", "## 4. 数据说明")
    cards = list(CARD_RE.finditer(details))
    identifiers = [match.group(1) for match in cards]
    if len(identifiers) != len(set(identifiers)):
        errors.append("duplicate_demand_id")
    numbers = [int(identifier.rsplit("-", 1)[1]) for identifier in identifiers]
    if numbers != list(range(1, len(numbers) + 1)):
        errors.append(f"demand_ids_not_consecutive:{numbers}")

    expected_detail_ids = set(overview_rows)
    actual_detail_ids = set(identifiers)
    for missing in sorted(expected_detail_ids - actual_detail_ids):
        errors.append(f"missing_detail:{missing}")
    for extra in sorted(actual_detail_ids - expected_detail_ids):
        errors.append(f"detail_missing_from_overview:{extra}")
    for demand_id in sorted(overview_rows):
        anchor = f'<a id="{demand_id.lower()}"></a>'
        if details.count(anchor) != 1:
            errors.append(f"detail_anchor_invalid:{demand_id}")

    for match in cards:
        demand_id, _, body = match.groups()
        if demand_id not in overview:
            errors.append(f"{demand_id}:missing_from_overview")
        if COMMON_TABLE_HEADER not in body:
            errors.append(f"{demand_id}:missing_common_demand_table")
        for field in (
            "**这个需求具体包括什么**",
            "**这个时间节点下发生了什么**",
            "**大家现在怎么处理**",
            "**查看原始内容**",
            "**边界**",
        ):
            if field not in body:
                errors.append(f"{demand_id}:missing_field:{field}")

        manifestation = section(
            body,
            "**这个需求具体包括什么**",
            "**这个时间节点下发生了什么**",
        )
        manifestation_rows = re.findall(r"(?m)^- \S.+$", manifestation)
        if not 1 <= len(manifestation_rows) <= 4:
            errors.append(f"{demand_id}:manifestation_row_count_invalid")

        time_node = section(
            body,
            "**这个时间节点下发生了什么**",
            "**大家现在怎么处理**",
        )
        if not re.search(r"(?m)^- \S", time_node):
            errors.append(f"{demand_id}:time_node_change_required")

        coping = section(
            body,
            "**大家现在怎么处理**",
            "**查看原始内容**",
        )
        coping_rows = re.findall(r"(?m)^- \S.+$", coping)
        if not 1 <= len(coping_rows) <= 4:
            errors.append(f"{demand_id}:coping_row_count_invalid")

        representative = section(body, "**查看原始内容**", "**边界**")
        links = LINK_RE.findall(representative)
        if not 1 <= len(links) <= 3:
            errors.append(f"{demand_id}:representative_link_count_invalid")

    if "没有补历史同节点证据" not in text:
        errors.append("missing_current_batch_only_notice")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    errors = validate(args.report.read_text(encoding="utf-8"))
    if errors:
        for error in errors:
            print(error)
        return 1
    print("external_demand_report_valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
