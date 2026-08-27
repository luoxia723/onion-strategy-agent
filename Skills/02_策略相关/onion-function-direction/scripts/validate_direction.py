#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


HEADER = (
    "| 编号 | 素材方向 | 功能 | 卖点 | 目标人群 | 适配场景 | "
    "1 能解决用户在“具体哪个场景里的哪个问题” | "
    "2 能带来什么不一样的“一听很惊艳”的解法？ | "
    "3 因此带来了哪个场景下的什么“奇效”？ |"
)
SEPARATOR = "|---:|---|---|---|---|---|---|---|---|"


def split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def validate(text: str) -> list[str]:
    errors: list[str] = []
    for metadata in (
        "指定功能：",
        "正式功能：",
        "策略判断范围：",
        "外部需求报告：",
        "内部需求报告：",
        "产品事实：",
        "方向数量：",
    ):
        if metadata not in text:
            errors.append(f"missing_metadata:{metadata}")
    if "## 未成案说明" not in text:
        errors.append("missing_section:未成案说明")
    if "## 方向依据" not in text:
        errors.append("missing_section:方向依据")
    if "## 使用边界" not in text:
        errors.append("missing_section:使用边界")
    if HEADER not in text:
        errors.append("missing_fixed_header")
        return errors

    lines = text.splitlines()
    header_index = lines.index(HEADER)
    if header_index + 1 >= len(lines) or lines[header_index + 1].replace(" ", "") != SEPARATOR:
        errors.append("invalid_separator")
        return errors

    rows: list[list[str]] = []
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        cells = split_row(line)
        if len(cells) == 9:
            rows.append(cells)
        else:
            errors.append("invalid_column_count")

    if not rows:
        errors.append("no_direction_rows")
        return errors

    expected_numbers = [str(number) for number in range(1, len(rows) + 1)]
    actual_numbers = [row[0] for row in rows]
    if actual_numbers != expected_numbers:
        errors.append("non_consecutive_direction_numbers")

    forbidden_empty = {"", "同上", "略", "—", "-", "……"}
    formal_match = re.search(r"^正式功能：(.+)$", text, re.M)
    formal_function = formal_match.group(1).strip() if formal_match else ""
    for row in rows:
        number = row[0]
        if any(cell in forbidden_empty for cell in row[1:]):
            errors.append(f"row_{number}:empty_or_placeholder_cell")
        if formal_function and row[2] != formal_function:
            errors.append(f"row_{number}:function_must_match_formal_function")
        if row[5] in {"全阶段", "长期通用", "随时", "通用"}:
            errors.append(f"row_{number}:stage_must_be_specific")
        if len(re.sub(r"\s+", "", row[5])) < 10:
            errors.append(f"row_{number}:scene_too_short")
        for field_name, cell in zip(("problem", "solution", "effect"), row[6:9]):
            if len(re.sub(r"\s+", "", cell)) < 55:
                errors.append(f"row_{number}:{field_name}_too_short")
        if formal_function and formal_function not in row[7]:
            errors.append(f"row_{number}:solution_must_name_formal_function")
        if not re.search(r"不只|不是|而是|不用|\u5148.+再", row[7]):
            errors.append(f"row_{number}:solution_difference_not_clear")
        has_before = re.search(r"以前|原来|过去|使用前", row[8])
        has_after = re.search(r"现在|使用后|用了以后|如今", row[8])
        if not (has_before and has_after):
            errors.append(f"row_{number}:effect_requires_before_after")
        if re.search(r"保证提分|一定提分|必然提分|稳提\d+|100%|百分百", " ".join(row[6:9])):
            errors.append(f"row_{number}:unsupported_result_promise")

    pairs = [(row[1], row[6]) for row in rows]
    if len(pairs) != len(set(pairs)):
        errors.append("duplicate_direction_and_scene")

    count_match = re.search(r"方向数量：(\d+)", text)
    if count_match and int(count_match.group(1)) != len(rows):
        errors.append("declared_direction_count_mismatch")
    if "创意报告：" in text:
        errors.append("creative_report_must_not_be_input")

    basis_start = text.find("## 方向依据")
    basis_end = text.find("## 未成案说明", basis_start)
    basis_block = text[basis_start:basis_end] if basis_start >= 0 and basis_end >= 0 else ""
    basis_pattern = re.compile(
        r"^\- 方向(\d+)：外部需求 ([^；]+)；"
        r"内部需求 ([^；]+)；产品事实：(.+)$",
        re.M,
    )
    basis_rows = basis_pattern.findall(basis_block)
    if [item[0] for item in basis_rows] != expected_numbers:
        errors.append("direction_basis_numbers_mismatch")
    for number, external_refs, internal_refs, product_fact in basis_rows:
        external_refs = external_refs.strip()
        internal_refs = internal_refs.strip()
        product_fact = product_fact.strip()
        if external_refs != "无" and not re.fullmatch(r"ED-\d{3}(?:、ED-\d{3})*", external_refs):
            errors.append(f"basis_{number}:external_reference_invalid")
        if internal_refs != "无" and not re.fullmatch(
            r"ID-(?:APP|LEAD)-\d{3}(?:、ID-(?:APP|LEAD)-\d{3})*",
            internal_refs,
        ):
            errors.append(f"basis_{number}:internal_reference_invalid")
        if external_refs == "无" and internal_refs == "无":
            errors.append(f"basis_{number}:demand_reference_required")
        if product_fact in forbidden_empty or len(product_fact) < 8:
            errors.append(f"basis_{number}:product_fact_too_vague")
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
    print("function_direction_valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
