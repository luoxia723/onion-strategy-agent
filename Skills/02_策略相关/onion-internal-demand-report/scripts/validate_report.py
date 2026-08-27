#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path


REQUIRED_SECTIONS = (
    "## 1. 报告范围与本期结论",
    "## 2. 需求一览",
    "## 3. 需求详情",
    "## 4. 数据说明",
)
REQUIRED_METADATA = (
    "APP统计来源：",
    "线索统计来源：",
    "生成时间：",
    "冻结时刻：",
    "承接命名源：",
    "分析媒体：小红书图组＋其他视频",
    "周期完整度：完整",
    "素材分析完整度：",
    "高表现规则：",
)
SEMANTIC_MAPPING_RE = re.compile(
    r"^语义归并：已完成｜映射SHA-256:([0-9a-f]{64})$", re.M
)
SEMANTIC_RECEIPT_RE = re.compile(r"^语义归并回执：(.+)$", re.M)
REQUIRED_DATA_LINES = (
    "APP第一周原始记录：",
    "APP第二周原始记录：",
    "线索双周原始记录：",
    "共同14天稳定素材：",
    "当前成功富化的小红书图组：",
    "当前成功富化的其他视频：",
    "实际进入需求归并：",
    "未成功富化：",
    "其他图片或媒体类型范围外：",
    "来源与富化媒体身份冲突：",
    "无法判断需求：",
    "高表现素材总数：",
    "可关联需求的高表现素材：",
)
COMMON_TABLE_HEADER = (
    "| 需求主体 | 具体人群 | 具体场景 | 核心问题 | 当前应对 | 期待变化 |"
)
OVERVIEW_HEADER = (
    "| 编号 | 需求名称 | 需求主体 | 具体人群 | "
    "全部素材 | 高表现素材 | 主要功能/服务承接 | 需求详情 |"
)
PROBLEM_TABLE_HEADER = "| 具体问题表达 | 全部素材 | 高表现素材 |"
COPING_TABLE_HEADER = "| 当前应对 | 全部素材 | 高表现素材 |"
FUNCTION_TABLE_HEADER = "| 功能/服务承接 | 全部素材 | 高表现素材 |"
NO_COPING_MARKER = "当前素材没有明确写出用户正在怎么应对。"
NO_FUNCTION_MARKER = "本需求没有可展示的标准功能或服务承接。"
CARD_RE = re.compile(
    r"(?ms)^### (ID-(APP|LEAD)-(\d{3}))｜(.+?)\n(.*?)"
    r"(?=^### ID-(?:APP|LEAD)-|^## 4\.|\Z)"
)
COVERAGE_RE = re.compile(r"全部素材：(\d+)条\s+高表现素材：(\d+)条")
FUNCTION_ROW_RE = re.compile(r"^\| ([^|]+) \| (\d+) \| (\d+) \|$", re.M)
LINK_RE = re.compile(
    r"\[[^\]]+\]\(https://toufang-ai\.guanghexinzhi\.cn/"
    r"content-dashboard\?[^)]+\)"
)
FORBIDDEN_REPORT_FIELDS = (
    "目标时间节点：",
    "## 素材整体承接",
    "**主要承接方式**",
    "### 整体承接",
    "**素材整体说服路径**",
    "**推广对象原表达**",
    "功能/服务承接未明确",
)


def _date(value: str) -> date:
    return date.fromisoformat(value)


def section(text: str, start: str, end: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        return ""
    end_index = text.find(end, start_index + len(start))
    return text[start_index:end_index] if end_index >= 0 else text[start_index:]


def validate_periods(text: str) -> list[str]:
    errors: list[str] = []
    title = re.search(
        r"^# 内部需求报告｜(\d{4}-\d{2}-\d{2})—(\d{4}-\d{2}-\d{2})$",
        text,
        re.M,
    )
    app = re.search(
        r"^APP统计来源：(\d{4}-\d{2}-\d{2})—(\d{4}-\d{2}-\d{2})＋"
        r"(\d{4}-\d{2}-\d{2})—(\d{4}-\d{2}-\d{2})$",
        text,
        re.M,
    )
    lead = re.search(
        r"^线索统计来源：(\d{4}-\d{2}-\d{2})—(\d{4}-\d{2}-\d{2})$",
        text,
        re.M,
    )
    if title is None:
        return ["title_invalid"]
    if app is None:
        errors.append("app_period_sources_invalid")
    if lead is None:
        errors.append("lead_period_source_invalid")
    try:
        report_start, report_end = map(_date, title.groups())
        if report_end - report_start != timedelta(days=13):
            errors.append("report_period_must_be_14_days")
        if app:
            first_start, first_end, second_start, second_end = map(_date, app.groups())
            if first_end - first_start != timedelta(days=6):
                errors.append("app_first_period_must_be_7_days")
            if second_end - second_start != timedelta(days=6):
                errors.append("app_second_period_must_be_7_days")
            if second_start != first_end + timedelta(days=1):
                errors.append("app_periods_must_be_continuous")
            if (first_start, second_end) != (report_start, report_end):
                errors.append("app_periods_must_match_report")
        if lead:
            lead_start, lead_end = map(_date, lead.groups())
            if (lead_start, lead_end) != (report_start, report_end):
                errors.append("lead_period_must_match_report")
    except ValueError:
        errors.append("period_date_invalid")
    return errors


def validate(
    text: str,
    *,
    report_path: Path | None = None,
    require_semantic_mapping: bool = True,
) -> list[str]:
    errors = validate_periods(text)
    for item in REQUIRED_SECTIONS:
        if text.count(item) != 1:
            errors.append(f"section_count_invalid:{item}")
    for item in REQUIRED_METADATA:
        if item not in text:
            errors.append(f"missing_metadata:{item}")
    for item in REQUIRED_DATA_LINES:
        if not re.search(rf"{re.escape(item)}\d+条", text):
            errors.append(f"missing_data_line:{item}")
    for item in FORBIDDEN_REPORT_FIELDS:
        if item in text:
            errors.append(f"forbidden_report_field:{item}")
    if require_semantic_mapping:
        mapping_match = SEMANTIC_MAPPING_RE.search(text)
        receipt_match = SEMANTIC_RECEIPT_RE.search(text)
        if mapping_match is None:
            errors.append("semantic_mapping_metadata_required")
        if receipt_match is None:
            errors.append("semantic_mapping_receipt_required")
        if mapping_match is not None and receipt_match is not None and report_path is not None:
            receipt_path = Path(receipt_match.group(1).strip())
            if not receipt_path.is_absolute():
                receipt_path = report_path.parent / receipt_path
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                errors.append("semantic_mapping_receipt_unreadable")
            else:
                if (
                    receipt.get("schema_version")
                    != "internal_demand_semantic_run_receipt_v1"
                    or receipt.get("validation_status") != "passed"
                ):
                    errors.append("semantic_mapping_receipt_invalid")
                if receipt.get("final_mapping_sha256") != mapping_match.group(1):
                    errors.append("semantic_mapping_hash_mismatch")
    if text.count(OVERVIEW_HEADER) != 2:
        errors.append("overview_header_count_invalid")

    overview = section(text, "## 2. 需求一览", "## 3. 需求详情")
    details = section(text, "## 3. 需求详情", "## 4. 数据说明")
    overview_rows: dict[str, tuple[int, int, str, str]] = {}
    for line in overview.splitlines():
        if not line.startswith("| ID-"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 8:
            errors.append(f"overview_row_column_count_invalid:{cells[0]}")
            continue
        demand_id = cells[0]
        if demand_id in overview_rows:
            errors.append(f"duplicate_overview_demand_id:{demand_id}")
            continue
        try:
            total, high = int(cells[4]), int(cells[5])
        except ValueError:
            errors.append(f"overview_counts_invalid:{demand_id}")
            continue
        if high < 2:
            errors.append(f"overview_high_below_threshold:{demand_id}:{high}")
        main_carrying = cells[6]
        if "未明确" in main_carrying:
            errors.append(f"overview_unresolved_carrying_forbidden:{demand_id}")
        detail_link = cells[7]
        expected_link = f"[查看详情](#{demand_id.lower()})"
        if detail_link != expected_link:
            errors.append(f"overview_detail_link_invalid:{demand_id}")
        overview_rows[demand_id] = (total, high, main_carrying, detail_link)

    cards = list(CARD_RE.finditer(details))
    identifiers = [card.group(1) for card in cards]
    if len(identifiers) != len(set(identifiers)):
        errors.append("duplicate_demand_id")
    for business in ("APP", "LEAD"):
        numbers = [
            int(identifier.rsplit("-", 1)[1])
            for identifier in overview_rows
            if identifier.startswith(f"ID-{business}-")
        ]
        if numbers != list(range(1, len(numbers) + 1)):
            errors.append(f"{business.lower()}_ids_not_consecutive:{numbers}")

    expected_detail_ids = set(overview_rows)
    actual_detail_ids = set(identifiers)
    for missing in sorted(expected_detail_ids - actual_detail_ids):
        errors.append(f"missing_expanded_detail:{missing}")
    for extra in sorted(actual_detail_ids - expected_detail_ids):
        errors.append(f"detail_missing_from_overview:{extra}")
    for demand_id in sorted(overview_rows):
        anchor = f'<a id="{demand_id.lower()}"></a>'
        if details.count(anchor) != 1:
            errors.append(f"detail_anchor_invalid:{demand_id}")

    for card in cards:
        demand_id, _, _, _, body = card.groups()
        if demand_id not in overview_rows:
            errors.append(f"{demand_id}:missing_from_overview")
        if COMMON_TABLE_HEADER not in body:
            errors.append(f"{demand_id}:missing_common_demand_table")
        for field in (
            "**内部素材主要写了哪些具体问题**",
            "**内部素材里用户正在怎么应对**",
            "**内部历史采用**",
            "**功能/服务承接**",
            "**代表高表现素材**",
            "**边界**",
        ):
            if field not in body:
                errors.append(f"{demand_id}:missing_field:{field}")
        coverage = COVERAGE_RE.search(body)
        if coverage is None:
            errors.append(f"{demand_id}:invalid_coverage")
            continue
        total, high = map(int, coverage.groups())
        if high > total:
            errors.append(f"{demand_id}:high_exceeds_total")
        if demand_id in overview_rows and (total, high) != overview_rows[demand_id][:2]:
            errors.append(f"{demand_id}:overview_detail_coverage_mismatch")

        problem_block = section(
            body,
            "**内部素材主要写了哪些具体问题**",
            "**内部素材里用户正在怎么应对**",
        )
        if problem_block.count(PROBLEM_TABLE_HEADER) != 1:
            errors.append(f"{demand_id}:problem_table_header_invalid")
        problem_rows = [
            (label.strip(), int(row_total), int(row_high))
            for label, row_total, row_high in FUNCTION_ROW_RE.findall(problem_block)
            if label.strip() != "具体问题表达"
        ]
        if not 1 <= len(problem_rows) <= 4:
            errors.append(f"{demand_id}:problem_row_count_invalid")
        if any(
            row_total < 1
            or row_high > row_total
            or row_total > total
            or row_high > high
            for _, row_total, row_high in problem_rows
        ):
            errors.append(f"{demand_id}:problem_counts_invalid")

        coping_block = section(
            body,
            "**内部素材里用户正在怎么应对**",
            "**内部历史采用**",
        )
        coping_rows = [
            (label.strip(), int(row_total), int(row_high))
            for label, row_total, row_high in FUNCTION_ROW_RE.findall(coping_block)
            if label.strip() != "当前应对"
        ]
        if coping_rows:
            if coping_block.count(COPING_TABLE_HEADER) != 1:
                errors.append(f"{demand_id}:coping_table_header_invalid")
            if not 1 <= len(coping_rows) <= 4:
                errors.append(f"{demand_id}:coping_row_count_invalid")
            if any(
                row_total < 1
                or row_high > row_total
                or row_total > total
                or row_high > high
                for _, row_total, row_high in coping_rows
            ):
                errors.append(f"{demand_id}:coping_counts_invalid")
        elif NO_COPING_MARKER not in coping_block:
            errors.append(f"{demand_id}:coping_rows_or_empty_marker_required")

        function_block = section(body, "**功能/服务承接**", "**代表高表现素材**")
        rows = [
            (name.strip(), int(row_total), int(row_high))
            for name, row_total, row_high in FUNCTION_ROW_RE.findall(function_block)
            if name.strip() != "功能/服务承接"
        ]
        if rows and function_block.count(FUNCTION_TABLE_HEADER) != 1:
            errors.append(f"{demand_id}:function_table_header_invalid")
        if not rows and NO_FUNCTION_MARKER not in function_block:
            errors.append(f"{demand_id}:function_rows_or_empty_marker_required")
        if any("未明确" in name for name, _, _ in rows):
            errors.append(f"{demand_id}:unresolved_function_row_forbidden")
        if any(row_high > row_total for _, row_total, row_high in rows):
            errors.append(f"{demand_id}:function_high_exceeds_total")
        if sum(row_total for _, row_total, _ in rows) > total:
            errors.append(f"{demand_id}:function_total_exceeds_demand")
        if sum(row_high for _, _, row_high in rows) > high:
            errors.append(f"{demand_id}:function_high_exceeds_demand")

        representative = section(body, "**代表高表现素材**", "**边界**")
        links = LINK_RE.findall(representative)
        expected_links = min(5, high)
        if len(links) != expected_links:
            errors.append(
                f"{demand_id}:representative_link_count_invalid:"
                f"{len(links)}:{expected_links}"
            )
        if "代表原因" in representative:
            errors.append(f"{demand_id}:representative_reason_forbidden")

    if "不同需求的素材数是非排他的，不能跨需求相加" not in text:
        errors.append("missing_nonexclusive_material_notice")
    if "未进入高表现素材池不等于表现差、需求无效或以后不会跑出" not in text:
        errors.append("missing_high_performance_boundary")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument(
        "--allow-trial-without-semantic-mapping",
        action="store_true",
        help="仅用于明确标注的试验稿；正式报告禁止使用",
    )
    args = parser.parse_args()
    errors = validate(
        args.report.read_text(encoding="utf-8"),
        report_path=args.report,
        require_semantic_mapping=(
            not args.allow_trial_without_semantic_mapping
        ),
    )
    if errors:
        for error in errors:
            print(error)
        return 1
    print("internal_demand_report_valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
