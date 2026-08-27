#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


META = ("- 方向卡：", "- 产品事实来源：", "- 本批文案数量：", "- 人工状态：")
BASE = ("渠道", "图片形式", "文案类型", "目标人群", "具体场景", "产品动作", "可感知变化", "事实边界")
FIELDS = {"单图": ("主标题", "副标题"), "双图": ("短句1", "短句2"), "三图": ("短句1", "短句2", "短句3")}
CHANNELS = {"信息流", "应用商店", "学习机"}
LEGACY_TYPES = {"钩子", "共情", "数字", "反差", "故事", "留空"}
PERFORMANCE_ANGLES = {
    "场景", "痛点", "结果", "机制", "对比", "时间", "身份", "异议", "证据", "数字", "优惠"
}
HEADLINE_MECHANISMS = {"直述", "提问", "反差", "如何", "好奇"}
ITEM_RE = re.compile(r"(?ms)^## 文案 (\d+)｜(.+?)\n(.*?)(?=^## 文案 |\Z)")


def _value(text: str, label: str) -> str:
    match = re.search(rf"^- {re.escape(label)}：\s*(.*?)\s*$", text, re.M)
    return match.group(1).strip() if match else ""


def _performance_type_valid(value: str) -> bool:
    parts = value.split("×")
    return (
        len(parts) == 2
        and parts[0] in PERFORMANCE_ANGLES
        and parts[1] in HEADLINE_MECHANISMS
    )


def validate(text: str) -> list[str]:
    errors: list[str] = []
    if "# APP图片投放文案" not in text:
        errors.append("title_invalid")
    for label in META:
        if label not in text:
            errors.append(f"missing_metadata:{label}")
    items = list(ITEM_RE.finditer(text))
    declared = re.search(r"本批文案数量：(\d+)套", text)
    if not declared or int(declared.group(1)) != len(items):
        errors.append("copy_count_mismatch")
    if [int(item.group(1)) for item in items] != list(range(1, len(items) + 1)):
        errors.append("copy_numbers_not_consecutive")
    for item in items:
        number, _, body = item.groups()
        for field in BASE:
            if not _value(body, field):
                errors.append(f"copy_{number}:missing_field:{field}")
        channel = _value(body, "渠道")
        form = _value(body, "图片形式")
        copy_type = _value(body, "文案类型")
        target_audience = _value(body, "目标人群")
        if channel not in CHANNELS:
            errors.append(f"copy_{number}:invalid_channel")
        if form not in FIELDS:
            errors.append(f"copy_{number}:invalid_form")
            continue
        if channel == "信息流":
            if not _performance_type_valid(copy_type):
                errors.append(f"copy_{number}:invalid_information_flow_copy_type")
        elif copy_type not in LEGACY_TYPES and not _performance_type_valid(copy_type):
            errors.append(f"copy_{number}:invalid_copy_type")
        if channel in {"信息流", "学习机"} and form != "单图":
            errors.append(f"copy_{number}:unsupported_channel_form")
        if channel == "信息流" and not re.search(
            r"学生|孩子|小学生|初中生|高中生|中学生", target_audience
        ):
            errors.append(f"copy_{number}:information_flow_must_target_child")
        expected = set(FIELDS[form])
        all_fields = {value for values in FIELDS.values() for value in values}
        for field in expected:
            if not _value(body, field):
                errors.append(f"copy_{number}:missing_copy_field:{field}")
        for field in all_fields - expected:
            if _value(body, field):
                errors.append(f"copy_{number}:unexpected_copy_field:{field}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    errors = validate(args.report.read_text(encoding="utf-8"))
    if errors:
        print("\n".join(errors))
        return 1
    print("app_image_copy_valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
