#!/usr/bin/env python3
"""Project frozen MCP snapshots into bounded report-specific model batches."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable


BUNDLE_SCHEMA = "report_model_bundle_manifest_v1"


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON根对象必须是object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}不是object")
        rows.append(value)
    return rows


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def project_external_demand(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "analysis_object_id": item.get("analysis_object_id"),
        "material_context_id": item.get("material_context_id"),
        "platform": item.get("platform"),
        "source_type": item.get("source_type"),
        "demand_evidence_origin": item.get("demand_evidence_origin"),
        "demand_object_type": item.get("demand_object_type"),
        "insight_type": item.get("insight_type"),
        "commenter_role": item.get("commenter_role"),
        "need_subject": item.get("need_subject"),
        "learning_stages": item.get("learning_stages") or [],
        "scene": item.get("scene"),
        "specific_problem": item.get("specific_problem"),
        "current_coping": item.get("current_coping"),
        "desired_result": item.get("desired_result"),
        "insight_summary": item.get("insight_summary"),
        "evidence_basis": item.get("evidence_basis"),
        "evidence_scope": item.get("evidence_scope"),
        "covered_comment_count": item.get("covered_comment_count"),
        "evidence_excerpts": [
            {
                "evidence_source_type": evidence.get("evidence_source_type"),
                "evidence_role": evidence.get("evidence_role"),
                "evidence_excerpt": evidence.get("evidence_excerpt"),
            }
            for evidence in item.get("evidence_items") or []
        ],
        "platform_material_url": item.get("platform_material_url"),
        "material_detail_path": item.get("material_detail_path"),
    }


def project_external_creative(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        context_id = str(item.get("material_context_id") or "").strip()
        if not context_id:
            raise ValueError("外部创意证据缺少material_context_id")
        grouped[context_id].append(item)
    cases = []
    for context_id in sorted(grouped):
        members = grouped[context_id]
        first = members[0]
        cases.append(
            {
                "material_context_id": context_id,
                "external_material_id": first.get("external_material_id"),
                "platform": first.get("platform"),
                "source_type": first.get("source_type"),
                "media_type": first.get("media_type"),
                "platform_material_url": first.get("platform_material_url"),
                "material_detail_path": first.get("material_detail_path"),
                "observations": [
                    {
                        "analysis_object_id": item.get("analysis_object_id"),
                        "creative_format": item.get("creative_format"),
                        "hook_summary": item.get("hook_summary"),
                        "structure_summary": item.get("structure_summary"),
                        "overall_strategy_summary": item.get(
                            "overall_strategy_summary"
                        ),
                        "character_relationships": item.get(
                            "character_relationships"
                        )
                        or [],
                        "expression_techniques": item.get("expression_techniques")
                        or [],
                        "related_demand_signals": item.get(
                            "related_demand_signals"
                        )
                        or [],
                        "evidence_excerpts": [
                            {
                                "structure_unit_id": evidence.get(
                                    "structure_unit_id"
                                ),
                                "evidence_role": evidence.get("evidence_role"),
                                "evidence_excerpt": evidence.get(
                                    "evidence_excerpt"
                                ),
                            }
                            for evidence in item.get("evidence_items") or []
                        ],
                    }
                    for item in sorted(
                        members,
                        key=lambda row: str(row.get("analysis_object_id") or ""),
                    )
                ],
            }
        )
    return cases


def project_internal_creative(item: dict[str, Any]) -> dict[str, Any]:
    enrichment = item.get("material_enrichment") or {}
    if not isinstance(enrichment, dict):
        enrichment = {}
    return {
        "internal_material_id": item.get("internal_material_id"),
        "representative_internal_snapshot_id": item.get(
            "representative_internal_snapshot_id"
        ),
        "title": item.get("representative_snapshot_display_title"),
        "business_line": item.get("business_line"),
        "platforms": item.get("platforms") or [],
        "advertising_channels": item.get("advertising_channels") or [],
        "dashboard_path": item.get("dashboard_path"),
        "core_creative": enrichment.get("core_creative") or {},
        "content_structure": enrichment.get("content_structure") or {},
        "ad_persuasion_and_action": enrichment.get(
            "ad_persuasion_and_action"
        )
        or {},
        "creative_takeaways": enrichment.get("creative_takeaways") or [],
        "comparison_groups": item.get("comparison_groups") or [],
        "spend_amount": item.get("spend_amount"),
        "is_high_performing": bool(item.get("is_high_performing")),
        "high_performance_status": item.get("high_performance_status"),
    }


def make_batches(
    *,
    mode: str,
    records: list[dict[str, Any]],
    target_batch_bytes: int,
) -> list[dict[str, Any]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for record in records:
        prospective = [*current, record]
        probe = {
            "schema_version": "report_model_input_batch_v1",
            "mode": mode,
            # 预留最终批次元数据，避免正文恰好贴线后由编号把文件顶过上限。
            "batch_index": 999999,
            "batch_count": 999999,
            "records": prospective,
        }
        if current and len(canonical_json(probe).encode("utf-8")) > target_batch_bytes:
            batches.append(current)
            current = [record]
        else:
            current = prospective
        single = {
            "schema_version": "report_model_input_batch_v1",
            "mode": mode,
            "batch_index": 999999,
            "batch_count": 999999,
            "records": current,
        }
        if len(canonical_json(single).encode("utf-8")) > target_batch_bytes:
            raise ValueError("单条模型记录超过批次字节上限")
    if current:
        batches.append(current)
    return [
        {
            "schema_version": "report_model_input_batch_v1",
            "mode": mode,
            "batch_index": index,
            "batch_count": len(batches),
            "records": batch,
        }
        for index, batch in enumerate(batches, 1)
    ]


def _stable_id(mode: str, record: dict[str, Any]) -> str:
    field = {
        "external-demand": "analysis_object_id",
        "external-creative": "material_context_id",
        "internal-creative": "internal_material_id",
    }[mode]
    value = str(record.get(field) or "").strip()
    if not value:
        raise ValueError(f"{mode}模型记录缺少{field}")
    return value


def semantic_record(mode: str, record: dict[str, Any]) -> dict[str, Any]:
    """Return only fields that can change semantic grouping decisions."""
    if mode == "external-demand":
        excluded = {
            "platform_material_url",
            "material_detail_path",
        }
    elif mode == "external-creative":
        excluded = {
            "platform_material_url",
            "material_detail_path",
        }
    else:
        excluded = {
            "representative_internal_snapshot_id",
            "title",
            "dashboard_path",
            "comparison_groups",
            "spend_amount",
            "is_high_performing",
            "high_performance_status",
        }
    return {key: value for key, value in record.items() if key not in excluded}


def compare_hashes(current: dict[str, str], previous: dict[str, str]) -> dict[str, Any]:
    current_ids = set(current)
    previous_ids = set(previous)
    changed = sorted(
        record_id
        for record_id in current_ids & previous_ids
        if current[record_id] != previous[record_id]
    )
    return {
        "added_record_ids": sorted(current_ids - previous_ids),
        "changed_record_ids": changed,
        "deleted_record_ids": sorted(previous_ids - current_ids),
        "unchanged_record_ids": sorted((current_ids & previous_ids) - set(changed)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        required=True,
        choices=("external-demand", "external-creative", "internal-creative"),
    )
    parser.add_argument("--snapshot-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-batch-bytes", type=int, default=650_000)
    parser.add_argument("--previous-manifest", type=Path)
    args = parser.parse_args()
    if not 100_000 <= args.target_batch_bytes <= 1_500_000:
        parser.error("--target-batch-bytes必须在100000至1500000之间")
    if (args.output_dir / "manifest.json").exists():
        raise RuntimeError(f"输出目录已有manifest，不覆盖: {args.output_dir}")
    snapshot = read_json(args.snapshot_manifest)
    expected_snapshot_mode = (
        "internal-complete" if args.mode == "internal-creative" else args.mode
    )
    if snapshot.get("mode") != expected_snapshot_mode:
        raise ValueError("快照mode与模型bundle mode不兼容")
    items_path = args.snapshot_manifest.parent / snapshot["items_file"]
    if sha256_text(items_path.read_text(encoding="utf-8")) != snapshot["items_sha256"]:
        raise ValueError("快照items摘要不一致")
    source_rows = read_jsonl(items_path)
    if len(source_rows) != int(snapshot["record_count"]):
        raise ValueError("快照record_count不守恒")

    if args.mode == "external-demand":
        projected = [project_external_demand(item) for item in source_rows]
        excluded_count = 0
    elif args.mode == "external-creative":
        projected = project_external_creative(source_rows)
        excluded_count = 0
    else:
        eligible = [
            item for item in source_rows if item.get("creative_analysis_eligible") is True
        ]
        projected = [project_internal_creative(item) for item in eligible]
        excluded_count = len(source_rows) - len(eligible)

    stable_ids = [_stable_id(args.mode, row) for row in projected]
    if len(stable_ids) != len(set(stable_ids)):
        raise ValueError("模型bundle稳定ID重复")
    batches = make_batches(
        mode=args.mode,
        records=projected,
        target_batch_bytes=args.target_batch_bytes,
    )
    entries = []
    for batch in batches:
        name = f"batch-{batch['batch_index']:03d}.json"
        path = args.output_dir / name
        text = canonical_json(batch) + "\n"
        write_private(path, text)
        entries.append(
            {
                "file": name,
                "record_count": len(batch["records"]),
                "bytes": len(text.encode("utf-8")),
                "sha256": sha256_text(text),
            }
        )
    semantic_hashes = {
        _stable_id(args.mode, row): sha256_text(
            canonical_json(semantic_record(args.mode, row))
        )
        for row in projected
    }
    report_hashes = {
        _stable_id(args.mode, row): sha256_text(canonical_json(row))
        for row in projected
    }
    semantic_hashes_path = args.output_dir / "semantic_hashes.json"
    report_hashes_path = args.output_dir / "report_hashes.json"
    write_private(
        semantic_hashes_path,
        json.dumps(semantic_hashes, ensure_ascii=False, indent=2) + "\n",
    )
    write_private(
        report_hashes_path,
        json.dumps(report_hashes, ensure_ascii=False, indent=2) + "\n",
    )
    delta = None
    if args.previous_manifest:
        previous = read_json(args.previous_manifest)
        if previous.get("mode") != args.mode:
            raise ValueError("前后模型bundle mode不一致")
        previous_semantic = read_json(
            args.previous_manifest.parent / previous["semantic_hashes_file"]
        )
        previous_report = read_json(
            args.previous_manifest.parent / previous["report_hashes_file"]
        )
        delta = {
            "schema_version": "report_model_bundle_delta_v1",
            "semantic": compare_hashes(semantic_hashes, previous_semantic),
            "report_facts": compare_hashes(report_hashes, previous_report),
        }
        delta["semantic"].update(
            {
                f"{key.removesuffix('_record_ids')}_count": len(value)
                for key, value in list(delta["semantic"].items())
            }
        )
        delta["report_facts"].update(
            {
                f"{key.removesuffix('_record_ids')}_count": len(value)
                for key, value in list(delta["report_facts"].items())
            }
        )
        write_private(
            args.output_dir / "delta.json",
            json.dumps(delta, ensure_ascii=False, indent=2) + "\n",
        )
    manifest = {
        "schema_version": BUNDLE_SCHEMA,
        "mode": args.mode,
        "snapshot_manifest": str(args.snapshot_manifest.resolve()),
        "snapshot_items_sha256": snapshot["items_sha256"],
        "source_record_count": len(source_rows),
        "projected_record_count": len(projected),
        "excluded_record_count": excluded_count,
        "stable_id_count": len(stable_ids),
        "target_batch_bytes": args.target_batch_bytes,
        "batch_count": len(entries),
        "batches": entries,
        "semantic_hashes_file": semantic_hashes_path.name,
        "semantic_hashes_sha256": sha256_text(
            semantic_hashes_path.read_text(encoding="utf-8")
        ),
        "report_hashes_file": report_hashes_path.name,
        "report_hashes_sha256": sha256_text(
            report_hashes_path.read_text(encoding="utf-8")
        ),
        "delta_file": "delta.json" if delta is not None else None,
    }
    write_private(
        args.output_dir / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    semantic_delta_receipt = None
    if delta is not None:
        semantic_delta_receipt = {
            key: value
            for key, value in delta["semantic"].items()
            if key.endswith("_count")
        }
    print(
        json.dumps(
            {
                "status": "complete",
                "mode": args.mode,
                "source_record_count": len(source_rows),
                "projected_record_count": len(projected),
                "batch_count": len(entries),
                "semantic_delta": semantic_delta_receipt,
                "manifest": str((args.output_dir / "manifest.json").resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
