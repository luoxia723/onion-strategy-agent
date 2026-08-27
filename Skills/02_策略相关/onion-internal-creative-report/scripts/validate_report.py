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

OVERVIEW_HEADER = "| 编号 | 创意结构 | 核心推进 | 全部素材 | APP素材 | 线索素材 | 高表现素材 | 高表现关联 | 结构详情 |"
APP_RE = re.compile(r"- APP：(\d+)条｜渠道：([^｜]+)｜产品怎样进入并落到下载：(.+)；")
LEAD_RE = re.compile(r"- 线索：(\d+)条｜渠道：([^｜]+)｜体验/服务怎样进入并落到咨询：(.+)；")
HIGH_RE = re.compile(r"- 高表现完整素材：(\d+)条；")
ASSOCIATION_RE = re.compile(
    r"- 全部素材：(\d+)条；\s+"
    r"- 高表现素材：(\d+)条；\s+"
    r"- 未进入高表现素材池：(\d+)条；\s+"
    r"- 总体高表现占比：([0-9.]+)%；\s+"
    r"- 有效比较组：(\d+)组；\s+"
    r"- 关联状态：(跨组一致关联|单组关联观察|未观察到组内优势|证据不足)。"
)
GROUP_HEADER = "| 业务线/平台/渠道 | 结构全部素材 | 结构高表现素材 | 结构高表现占比 | 组内基础占比 | 观察 |"
DASHBOARD_LINK_RE = re.compile(
    r"(?m)^\d+\. \[.+\]\(https://toufang-ai\.guanghexinzhi\.cn/"
    r"content-dashboard\?[^)]+\)$"
)
PERFORMANCE_LINK_RE = DASHBOARD_LINK_RE


def validate(text: str) -> list[str]:
    errors = COMMON.validate_common(text, prefix="IC", overview_header=OVERVIEW_HEADER)
    structure_counts: list[int] = []
    for item in ("统计周期：", "业务线：", "渠道：", "来源口径："):
        if item not in text:
            errors.append(f"missing_metadata:{item}")
    for heading in (
        "## 4. 内部采用差异",
        "### 4.1 APP与线索采用差异",
        "### 4.2 渠道与完整素材表现背景",
        "### 4.3 单案例信号",
    ):
        if text.count(heading) != 1:
            errors.append(f"source_section_count_invalid:{heading}")

    for structure_id, _, body in COMMON.structure_cards(text, "IC"):
        if body.count("**内部采用**") != 1:
            errors.append(f"{structure_id}:internal_context_required")
        app = APP_RE.search(body)
        lead = LEAD_RE.search(body)
        high = HIGH_RE.search(body)
        if app is None or lead is None or high is None:
            errors.append(f"{structure_id}:invalid_internal_context")
            continue
        if int(app.group(1)) + int(lead.group(1)) < 2:
            errors.append(f"{structure_id}:requires_two_cases")
        if int(high.group(1)) > int(app.group(1)) + int(lead.group(1)):
            errors.append(f"{structure_id}:high_count_exceeds_cases")
        representatives = COMMON.section(
            body, "**代表案例**", "**高表现素材—创意结构关联**"
        )
        generic_representatives = COMMON.REPRESENTATIVE_LINK_RE.findall(
            representatives
        )
        dashboard_representatives = DASHBOARD_LINK_RE.findall(representatives)
        if len(generic_representatives) != len(dashboard_representatives):
            errors.append(f"{structure_id}:deployed_dashboard_link_required")
        association = ASSOCIATION_RE.search(body)
        if association is None:
            errors.append(f"{structure_id}:invalid_high_performance_association")
            continue
        total, high_count, non_high, _, _, _ = association.groups()
        total_i, high_i, non_high_i = map(int, (total, high_count, non_high))
        structure_counts.append(total_i)
        if total_i != int(app.group(1)) + int(lead.group(1)):
            errors.append(f"{structure_id}:association_total_mismatch")
        if total_i != high_i + non_high_i:
            errors.append(f"{structure_id}:association_partition_mismatch")
        if body.count(GROUP_HEADER) != 1:
            errors.append(f"{structure_id}:comparison_group_header_required")

        high_section = COMMON.section(
            body, "**高表现创意代表素材**", "**同结构对照素材**"
        )
        high_links = PERFORMANCE_LINK_RE.findall(high_section)
        if len(high_links) != min(high_i, 5):
            errors.append(f"{structure_id}:high_representative_count_mismatch")
        for label in ("比较组", "14天汇总消耗", "组内消耗排名", "高表现状态", "归入原因"):
            if high_section.count(f"- {label}：") != len(high_links):
                errors.append(f"{structure_id}:high_representative_field_mismatch:{label}")

        control_section = COMMON.section(body, "**同结构对照素材**", "**边界**")
        control_links = PERFORMANCE_LINK_RE.findall(control_section)
        if non_high_i > 0 and not 1 <= len(control_links) <= 2:
            errors.append(f"{structure_id}:control_count_invalid")
        for label in ("比较组", "14天汇总消耗", "组内消耗排名", "未进入原因", "共同结构", "可观察差异"):
            if control_section.count(f"- {label}：") != len(control_links):
                errors.append(f"{structure_id}:control_field_mismatch:{label}")

    if structure_counts != sorted(structure_counts, reverse=True):
        errors.append(f"structures_not_sorted_by_case_count:{structure_counts}")

    required_boundary = "完整素材表现只说明采用背景，不证明该结构、开头、文案或产品部件造成效果"
    if required_boundary not in text:
        errors.append("missing_performance_causality_boundary")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    errors = validate(args.report.read_text(encoding="utf-8"))
    if errors:
        print("\n".join(errors))
        return 1
    print("internal_creative_report_valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
