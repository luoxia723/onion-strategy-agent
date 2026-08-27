#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import re
from pathlib import Path


COMMON_PATH = Path(__file__).resolve().parents[2] / "scripts" / "creative_report_contract.py"
SPEC = importlib.util.spec_from_file_location("creative_report_contract", COMMON_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot_load_common_contract:{COMMON_PATH}")
COMMON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMMON)

OVERVIEW_HEADER = "| 编号 | 创意结构 | 核心推进 | 支撑案例 | 来源构成 | 结构详情 |"
COVERAGE_RE = re.compile(
    r"- 支撑案例：(\d+)条；\s+"
    r"- 来源构成：自然内容(\d+)、教育广告(\d+)、非教育广告(\d+)；"
)


def validate(text: str) -> list[str]:
    errors = COMMON.validate_common(text, prefix="EC", overview_header=OVERVIEW_HEADER)
    structure_counts: list[int] = []
    for item in ("内部锚点周期：", "外部观察范围：", "平台：", "来源："):
        if item not in text:
            errors.append(f"missing_metadata:{item}")
    for heading in ("## 4. 来源覆盖与新增案例", "### 4.1 来源覆盖与差异", "### 4.2 新增与单例案例"):
        if text.count(heading) != 1:
            errors.append(f"source_section_count_invalid:{heading}")

    for structure_id, _, body in COMMON.structure_cards(text, "EC"):
        if body.count("**外部证据**") != 1:
            errors.append(f"{structure_id}:external_evidence_required")
        coverage = COVERAGE_RE.search(body)
        if coverage is None:
            errors.append(f"{structure_id}:invalid_external_coverage")
            continue
        cases, natural, education, non_education = map(int, coverage.groups())
        structure_counts.append(cases)
        if cases != natural + education + non_education:
            errors.append(f"{structure_id}:source_count_mismatch")
        if cases < 2:
            errors.append(f"{structure_id}:requires_two_cases")
    if structure_counts != sorted(structure_counts, reverse=True):
        errors.append(f"structures_not_sorted_by_case_count:{structure_counts}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    errors = validate(args.report.read_text(encoding="utf-8"))
    if errors:
        print("\n".join(errors))
        return 1
    print("external_creative_report_valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
