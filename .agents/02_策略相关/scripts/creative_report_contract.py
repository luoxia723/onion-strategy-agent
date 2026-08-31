#!/usr/bin/env python3
from __future__ import annotations

import re


COMMON_SECTIONS = (
    "## 1. 报告范围与本期结论",
    "## 2. 创意结构一览",
    "## 3. 创意结构详情",
    "## 5. 数据说明",
)
COMMON_METADATA = ("生成时间：", "冻结时刻：", "完整案例：", "完整度：")
DEFINITION_HEADER = "| 开头关系 | 信息推进 | 转折机制 | 产品或证据怎样承接 | 结尾怎样行动 |"
KEY_NODE_HEADER = "| 判断节点 | 必须观察到的事实 | 不成立的情况 |"
DOWNSTREAM_HEADER = "| 下游 | 是否推荐 | 建议使用位置 | 具体用法 | 使用前提 |"
COMMON_FIELDS = (
    "**结构为什么成立**",
    "**关键节点识别**",
    "**不可丢失**",
    "**可以替换**",
    "**下游怎么用**",
    "**不能怎样使用**",
    "**代表案例**",
    "**边界**",
)
REPRESENTATIVE_LINK_RE = re.compile(r"(?m)^\d+\. \[.+\]\(https?://[^)]+\)$")


def section(text: str, start: str, end: str | None = None) -> str:
    start_index = text.find(start)
    if start_index < 0:
        return ""
    if end is None:
        return text[start_index:]
    end_index = text.find(end, start_index + len(start))
    return text[start_index:end_index] if end_index >= 0 else text[start_index:]


def structure_cards(text: str, prefix: str) -> list[tuple[str, str, str]]:
    details = section(text, "## 3. 创意结构详情", "## 4.")
    card_re = re.compile(
        rf"(?ms)^### ({re.escape(prefix)}-\d{{3}})｜(.+?)\n"
        rf"(.*?)(?=^### {re.escape(prefix)}-|\Z)"
    )
    return [match.groups() for match in card_re.finditer(details)]


def validate_common(text: str, *, prefix: str, overview_header: str) -> list[str]:
    errors: list[str] = []
    for item in COMMON_SECTIONS:
        if text.count(item) != 1:
            errors.append(f"section_count_invalid:{item}")
    for item in COMMON_METADATA:
        if item not in text:
            errors.append(f"missing_metadata:{item}")
    if text.count(overview_header) != 1:
        errors.append("overview_header_count_invalid")

    cards = structure_cards(text, prefix)
    identifiers = [card[0] for card in cards]
    if len(identifiers) != len(set(identifiers)):
        errors.append("duplicate_structure_id")
    numbers = [int(identifier.rsplit("-", 1)[1]) for identifier in identifiers]
    if numbers != list(range(1, len(numbers) + 1)):
        errors.append(f"structure_ids_not_consecutive:{numbers}")

    overview = section(text, "## 2. 创意结构一览", "## 3. 创意结构详情")
    details = section(text, "## 3. 创意结构详情", "## 4.")
    overview_ids = set(
        re.findall(rf"(?m)^\| ({re.escape(prefix)}-\d{{3}}) \|", overview)
    )
    card_ids = set(identifiers)
    for missing in sorted(overview_ids - card_ids):
        errors.append(f"missing_detail:{missing}")
    for extra in sorted(card_ids - overview_ids):
        errors.append(f"detail_missing_from_overview:{extra}")
    for structure_id, _, body in cards:
        expected_link = f"[查看详情](#{structure_id.lower()})"
        if expected_link not in overview:
            errors.append(f"overview_detail_link_invalid:{structure_id}")
        anchor = f'<a id="{structure_id.lower()}"></a>'
        if details.count(anchor) != 1:
            errors.append(f"detail_anchor_invalid:{structure_id}")
        if body.count(DEFINITION_HEADER) != 1:
            errors.append(f"{structure_id}:definition_header_count_invalid")
        if body.count(KEY_NODE_HEADER) != 1:
            errors.append(f"{structure_id}:key_node_header_count_invalid")
        if body.count(DOWNSTREAM_HEADER) != 1:
            errors.append(f"{structure_id}:downstream_header_count_invalid")
        downstream = section(body, "**下游怎么用**", "**不能怎样使用**")
        if downstream.count("| 口播文案 |") != 1:
            errors.append(f"{structure_id}:spoken_copy_downstream_required")
        if downstream.count("| AI前贴 |") != 1:
            errors.append(f"{structure_id}:ai_preroll_downstream_required")
        downstream_rows = [
            line
            for line in downstream.splitlines()
            if line.startswith("| ") and line != DOWNSTREAM_HEADER
        ]
        if len(downstream_rows) != 2:
            errors.append(f"{structure_id}:downstream_row_count_invalid")
        if len(re.findall(r"\*\*[^*]+\*\*", downstream)) != 1:
            errors.append(f"{structure_id}:extra_downstream_section_forbidden")
        for field in COMMON_FIELDS:
            if body.count(field) != 1:
                errors.append(f"{structure_id}:field_count_invalid:{field}")

        representative_end = (
            "**高表现素材—创意结构关联**"
            if "**高表现素材—创意结构关联**" in body
            else "**边界**"
        )
        representatives = section(body, "**代表案例**", representative_end)
        links = REPRESENTATIVE_LINK_RE.findall(representatives)
        if len(links) < 2:
            errors.append(f"{structure_id}:insufficient_representatives")
        if len(links) > 3:
            errors.append(f"{structure_id}:too_many_representatives")
        for label in (
            "来源",
            "开头",
            "推进",
            "转折",
            "产品或证据",
            "结尾行动",
            "归入原因",
            "主要变化",
        ):
            if representatives.count(f"- {label}：") != len(links):
                errors.append(
                    f"{structure_id}:representative_field_count_mismatch:{label}"
                )
        if re.search(r"external-creative-\d|internal-creative-\d|\*\*结构引用\*\*", body):
            errors.append(f"{structure_id}:technical_identity_exposed")

    if not re.search(
        r"(?m)^.*不同结构.*(?:非排他|交叉|重复).*(?:不能|不可).*(?:相加|求和).*$",
        text,
    ):
        errors.append("missing_nonexclusive_case_notice")
    return errors
