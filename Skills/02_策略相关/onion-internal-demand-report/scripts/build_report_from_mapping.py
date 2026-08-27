#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DASHBOARD_BASE = "https://toufang-ai.guanghexinzhi.cn"
UNRESOLVED = "__unresolved__"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON根对象必须是object: {path}")
    return payload


def _load_units(path: Path) -> dict[str, dict[str, Any]]:
    units = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        unit = json.loads(line)
        units[unit["unit_id"]] = unit
    if not units:
        raise ValueError("normalized units为空")
    return units


def _dashboard_url(value: str) -> str:
    value = value.strip()
    if value.startswith("/content-dashboard?"):
        value = DASHBOARD_BASE + value
    expected = DASHBOARD_BASE + "/content-dashboard?"
    if not value.startswith(expected):
        raise ValueError(f"不是已部署工作台绝对链接: {value}")
    return value


def _cell(value: Any, limit: int = 96) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().replace("|", "｜")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _link_label(value: Any) -> str:
    return _cell(value, 120).replace("[", "［").replace("]", "］")


def _canonical_names(path: Path) -> frozenset[str]:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0] and not set(cells[0]) <= {"-", ":"}:
            names.add(cells[0])
    names.update({"洋葱学园 APP", "7 天体验营"})
    return frozenset(names)


def _canonical(name: str, names: frozenset[str]) -> str:
    normalized = re.sub(r"\s+", "", name).lower()
    matches = [
        candidate
        for candidate in names
        if re.sub(r"\s+", "", candidate).lower() == normalized
    ]
    return matches[0] if len(matches) == 1 else UNRESOLVED


def _carrying_name(unit: dict[str, Any], names: frozenset[str]) -> str:
    carrying = unit.get("material_carrying") or {}
    offering = str(carrying.get("promoted_offering") or "")
    steps = " ".join(
        str(step.get("expression_summary") or "")
        for step in carrying.get("persuasion_steps") or []
        if step.get("step_type") in {"function", "promoted_offering_introduction"}
    )
    text = re.sub(r"\s+", "", offering + " " + steps).lower()
    if re.search(r"拍题精学|ai拍题|洋葱拍题|拍照(?:上传|搜题|解题|讲解|分步)", text):
        return _canonical("AI拍题精学", names)
    if re.search(r"ai定制班|定制班|学情诊断.*学习计划|个性化学习计划", text):
        return _canonical("AI 定制班", names)
    if re.search(r"ai私教动画|私教动画|动画私教", text):
        return _canonical("AI 私教动画课", names)
    if unit["business_line"] == "lead" and re.search(r"体验|试听|体验营|体验课", text):
        return _canonical("7 天体验营", names)
    if re.search(r"动画课|动画课程|动画视频|动画微课", text):
        return _canonical("动画视频课", names)
    return UNRESOLVED


def _material_members(
    member_unit_ids: list[str], units: dict[str, dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for unit_id in member_unit_ids:
        result[units[unit_id]["material_id"]].append(units[unit_id])
    return dict(result)


def _group_counts(
    groups: list[dict[str, Any]], units: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for group in groups:
        materials = {
            units[unit_id]["material_id"] for unit_id in group["member_unit_ids"]
        }
        high_materials = {
            units[unit_id]["material_id"]
            for unit_id in group["member_unit_ids"]
            if units[unit_id]["is_high_performance"]
        }
        rows.append(
            {
                "label": _cell(group["label"], 100),
                "total": len(materials),
                "high": len(high_materials),
            }
        )
    return rows


def _cluster_record(
    cluster: dict[str, Any], units: dict[str, dict[str, Any]], names: frozenset[str]
) -> dict[str, Any]:
    materials = _material_members(cluster["member_unit_ids"], units)
    high_ids = {
        material_id
        for material_id, material_units in materials.items()
        if any(unit["is_high_performance"] for unit in material_units)
    }
    carrying_by_material = {
        material_id: _carrying_name(material_units[0], names)
        for material_id, material_units in materials.items()
    }
    carrying_counts: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {"total": 0, "high": 0}
    )
    for material_id, name in carrying_by_material.items():
        if name == UNRESOLVED:
            continue
        carrying_counts[name]["total"] += 1
        carrying_counts[name]["high"] += int(material_id in high_ids)
    carrying_rows = sorted(
        (
            {"name": name, **counts}
            for name, counts in carrying_counts.items()
        ),
        key=lambda item: (-item["high"], -item["total"], item["name"]),
    )
    audiences = collections.Counter(
        unit["audience_description"]
        for unit_id in cluster["member_unit_ids"]
        for unit in [units[unit_id]]
        if str(unit.get("audience_description") or "").strip()
    )
    representative_candidates = []
    for material_id in high_ids:
        unit = materials[material_id][0]
        representative_candidates.append(
            {
                "material_id": material_id,
                "title": _link_label(unit["material_title"]),
                "url": _dashboard_url(unit["dashboard_path"]),
                "carrying": carrying_by_material[material_id],
            }
        )
    representatives = []
    used_carrying = set()
    for candidate in sorted(
        representative_candidates,
        key=lambda item: (item["carrying"] == UNRESOLVED, item["carrying"], item["title"]),
    ):
        if candidate["carrying"] not in used_carrying:
            representatives.append(candidate)
            used_carrying.add(candidate["carrying"])
        if len(representatives) == min(5, len(high_ids)):
            break
    for candidate in sorted(representative_candidates, key=lambda item: item["title"]):
        if candidate not in representatives:
            representatives.append(candidate)
        if len(representatives) == min(5, len(high_ids)):
            break
    return {
        **cluster,
        "total": len(materials),
        "high": len(high_ids),
        "audience": audiences.most_common(1)[0][0] if audiences else "素材未明确说明",
        "carrying_rows": carrying_rows,
        "problem_rows": _group_counts(cluster["problem_expression_groups"], units),
        "coping_rows": _group_counts(cluster["current_coping_groups"], units),
        "representatives": representatives,
    }


def _subject_name(value: str) -> str:
    return {"student": "学生", "parent": "家长", "other": "其他"}[value]


def _overview(clusters: list[dict[str, Any]], prefix: str) -> str:
    rows = [
        "| 编号 | 需求名称 | 需求主体 | 具体人群 | 全部素材 | 高表现素材 | 主要功能/服务承接 | 需求详情 |",
        "|---|---|---|---|---:|---:|---|---|",
    ]
    for index, cluster in enumerate(clusters, start=1):
        demand_id = f"ID-{prefix}-{index:03d}"
        main = "、".join(row["name"] for row in cluster["carrying_rows"][:2]) or "—"
        rows.append(
            f"| {demand_id} | {_cell(cluster['canonical_name'], 52)} | {_subject_name(cluster['demand_subject'])} | "
            f"{_cell(cluster['audience'], 64)} | {cluster['total']} | {cluster['high']} | {_cell(main, 56)} | "
            f"[查看详情](#{demand_id.lower()}) |"
        )
    return "\n".join(rows)


def _detail(cluster: dict[str, Any], demand_id: str) -> str:
    coping_definition = "、".join(row["label"] for row in cluster["coping_rows"][:2])
    coping_definition = coping_definition or "当前证据未说明"
    problem_rows = "\n".join(
        f"| {row['label']} | {row['total']} | {row['high']} |"
        for row in cluster["problem_rows"]
    )
    if cluster["coping_rows"]:
        coping_section = "\n".join(
            [
                "| 当前应对 | 全部素材 | 高表现素材 |",
                "|---|---:|---:|",
                *(f"| {row['label']} | {row['total']} | {row['high']} |" for row in cluster["coping_rows"]),
            ]
        )
    else:
        coping_section = "当前素材没有明确写出用户正在怎么应对。"
    if cluster["carrying_rows"]:
        function_section = "\n".join(
            [
                "| 功能/服务承接 | 全部素材 | 高表现素材 |",
                "|---|---:|---:|",
                *(f"| {row['name']} | {row['total']} | {row['high']} |" for row in cluster["carrying_rows"]),
            ]
        )
    else:
        function_section = "本需求没有可展示的标准功能或服务承接。"
    representatives = "\n".join(
        f"{index}. [{item['title']}]({item['url']})"
        for index, item in enumerate(cluster["representatives"], start=1)
    )
    return f'''<a id="{demand_id.lower()}"></a>
### {demand_id}｜{_cell(cluster['canonical_name'], 72)}

| 需求主体 | 具体人群 | 具体场景 | 核心问题 | 当前应对 | 期待变化 |
|---|---|---|---|---|---|
| {_subject_name(cluster['demand_subject'])} | {_cell(cluster['audience'], 72)} | {_cell(cluster['task_scene'], 96)} | {_cell(cluster['core_problem'], 96)} | {_cell(coping_definition, 96)} | {_cell(cluster['desired_change'], 96)} |

**内部素材主要写了哪些具体问题**

| 具体问题表达 | 全部素材 | 高表现素材 |
|---|---:|---:|
{problem_rows}

**内部素材里用户正在怎么应对**

{coping_section}

**内部历史采用**

全部素材：{cluster['total']}条
高表现素材：{cluster['high']}条

**功能/服务承接**

{function_section}

**代表高表现素材**

{representatives}

**边界**

这张卡只说明内部历史采用过该需求，以及完整素材主要使用的功能/服务承接。它不证明外部当前需求、效果因果、产品当前可用性或下一期适配。'''


def build_report(
    *,
    units_path: Path,
    mapping_path: Path,
    semantic_receipt_path: Path,
    pool_manifest_path: Path,
    product_facts_path: Path,
) -> str:
    units = _load_units(units_path)
    mapping = _load_json(mapping_path)
    receipt = _load_json(semantic_receipt_path)
    if receipt.get("validation_status") != "passed":
        raise ValueError("语义归并回执未通过")
    mapping_hash = _sha256(mapping_path)
    if receipt.get("final_mapping_sha256") != mapping_hash:
        raise ValueError("语义归并映射与回执摘要不一致")
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    from validate_semantic_mapping import load_units as validator_load_units
    from validate_semantic_mapping import validate as validator_validate

    errors = validator_validate(validator_load_units(units_path), mapping)
    if errors:
        raise ValueError("语义映射无效:\n" + "\n".join(errors))

    names = _canonical_names(product_facts_path)
    records = [_cluster_record(cluster, units, names) for cluster in mapping["clusters"]]
    report_clusters = [record for record in records if record["high"] >= 2]
    by_business = {}
    for business in ("app", "lead"):
        by_business[business] = sorted(
            [record for record in report_clusters if record["business_line"] == business],
            key=lambda item: (-item["high"], -item["total"], item["canonical_name"]),
        )
    app = by_business["app"]
    lead = by_business["lead"]
    app_details = "\n\n".join(
        _detail(cluster, f"ID-APP-{index:03d}")
        for index, cluster in enumerate(app, start=1)
    )
    lead_details = "\n\n".join(
        _detail(cluster, f"ID-LEAD-{index:03d}")
        for index, cluster in enumerate(lead, start=1)
    )

    manifest = _load_json(pool_manifest_path)
    lines = {item["business_line"]: item for item in manifest["business_lines"]}
    app_periods = sorted(
        lines["app"]["included_statistics_periods"],
        key=lambda item: item["statistics_start_date"],
    )
    lead_period = lines["lead"]["included_statistics_periods"][0]
    start = app_periods[0]["statistics_start_date"]
    end = app_periods[-1]["statistics_end_date"]
    stable_count = sum(item["pool_summary"]["linked_material_count"] for item in lines.values())
    xhs_gallery = (
        lines["lead"]["pool_summary"]["demand_analyzable_material_count"]
        - lines["lead"]["pool_summary"]["creative_analyzable_material_count"]
    )
    videos = sum(item["pool_summary"]["creative_analyzable_material_count"] for item in lines.values())
    analyzable_ids = {(unit["business_line"], unit["material_id"]) for unit in units.values()}
    high_materials = {
        (unit["business_line"], unit["material_id"])
        for unit in units.values()
        if unit["is_high_performance"]
    }
    high_total = sum(item["pool_summary"]["high_performance_material_count"] for item in lines.values())
    unenriched = sum(item["pool_summary"]["linked_unenriched_material_count"] for item in lines.values())
    conflicts = sum(item["pool_summary"]["source_media_conflict_material_count"] for item in lines.values())
    expected_demand = sum(item["pool_summary"]["demand_analyzable_material_count"] for item in lines.values())
    unable = expected_demand - len(analyzable_ids)
    product_hash = _sha256(product_facts_path)
    now = dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    conclusions = []
    for label, clusters in (("APP", app[:3]), ("线索", lead[:2])):
        for cluster in clusters:
            conclusions.append(
                f"- {label}内部素材表达过“{_cell(cluster['canonical_name'], 72)}”：全部素材{cluster['total']}条，高表现素材{cluster['high']}条。"
            )
    return f'''# 内部需求报告｜{start}—{end}

APP统计来源：{app_periods[0]['statistics_start_date']}—{app_periods[0]['statistics_end_date']}＋{app_periods[1]['statistics_start_date']}—{app_periods[1]['statistics_end_date']}
线索统计来源：{lead_period['statistics_start_date']}—{lead_period['statistics_end_date']}
生成时间：{now:%Y-%m-%d %H:%M}（北京时间）
冻结时刻：{now:%Y-%m-%d %H:%M}（北京时间）
承接命名源：{product_facts_path.name}｜SHA-256:{product_hash}
分析媒体：小红书图组＋其他视频
周期完整度：完整
素材分析完整度：部分完整
高表现规则：同业务线、同平台、同渠道、同币种内，14天消耗大于0的素材取前20%＋单条不少于800元，低于门槛不递补
语义归并：已完成｜映射SHA-256:{mapping_hash}
语义归并回执：{semantic_receipt_path.resolve()}

## 1. 报告范围与本期结论

{chr(10).join(conclusions)}
- 高表现只属于完整素材，不证明某个需求或功能/服务承接导致效果。
- APP 与线索只分别展示历史采用，不做优劣比较。

## 2. 需求一览

### 2.1 APP

{_overview(app, 'APP')}

### 2.2 线索

{_overview(lead, 'LEAD')}

## 3. 需求详情

报告只收录至少关联2条高表现完整素材的需求。需求一览中的“查看详情”可直接跳转到本节对应需求卡。

{app_details}

{lead_details}

## 4. 数据说明

- APP第一周原始记录：{app_periods[0]['performance_record_count']}条；
- APP第二周原始记录：{app_periods[1]['performance_record_count']}条；
- 线索双周原始记录：{lead_period['performance_record_count']}条；
- 共同14天稳定素材：{stable_count}条；
- 当前成功富化的小红书图组：{xhs_gallery}条；
- 当前成功富化的其他视频：{videos}条；
- 实际进入需求归并：{len(analyzable_ids)}条；
- 未成功富化：{unenriched}条；
- 其他图片或媒体类型范围外：{unenriched}条；
- 来源与富化媒体身份冲突：{conflicts}条；
- 无法判断需求：{unable}条；
- 高表现素材总数：{high_total}条；
- 可关联需求的高表现素材：{len(high_materials)}条；
- 不同需求的素材数是非排他的，不能跨需求相加；
- 未进入高表现素材池不等于表现差、需求无效或以后不会跑出。
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("units", type=Path)
    parser.add_argument("mapping", type=Path)
    parser.add_argument("semantic_receipt", type=Path)
    parser.add_argument("pool_manifest", type=Path)
    parser.add_argument("product_facts", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(
        units_path=args.units,
        mapping_path=args.mapping,
        semantic_receipt_path=args.semantic_receipt,
        pool_manifest_path=args.pool_manifest,
        product_facts_path=args.product_facts,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(json.dumps({"report": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
