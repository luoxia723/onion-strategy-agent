#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path


REQUIRED_FIELDS = (
    "unit_id",
    "business_line",
    "material_id",
    "demand_subject",
    "task_scene",
    "specific_problem",
    "desired_change",
)


TASK_RULES = (
    ("parent_support", re.compile(r"家长|父母|辅导|陪学|讲题|督促|规划孩子|安排孩子")),
    ("wrong_question", re.compile(r"错题|错因|反复错|订正|扣分")),
    ("planning", re.compile(r"规划|计划|安排|先学|先补|重点|薄弱")),
    ("problem_solving", re.compile(r"做题|解题|第一步|切入点|推导|步骤|辅助线|公式")),
    ("concept_learning", re.compile(r"听课|知识点|概念|原理|抽象|预习")),
    ("motivation", re.compile(r"枯燥|走神|抵触|厌学|坚持|拖延|兴趣")),
    ("exam_review", re.compile(r"考试|考前|期中|期末|月考|备考|复习")),
)


def load_units(path: Path) -> list[dict]:
    units = []
    seen = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        unit = json.loads(line)
        for field in REQUIRED_FIELDS:
            if field not in unit or not str(unit[field]).strip():
                raise ValueError(f"line {line_number}: missing {field}")
        if unit["business_line"] not in {"app", "lead"}:
            raise ValueError(f"line {line_number}: invalid business_line")
        if unit["demand_subject"] not in {"student", "parent", "other"}:
            raise ValueError(f"line {line_number}: invalid demand_subject")
        if unit["unit_id"] in seen:
            raise ValueError(f"duplicate unit_id: {unit['unit_id']}")
        seen.add(unit["unit_id"])
        units.append(unit)
    if not units:
        raise ValueError("no demand units")
    return units


def task_bucket(unit: dict) -> str:
    text = " ".join(
        str(unit.get(field) or "")
        for field in ("task_scene", "specific_problem", "desired_change")
    )
    for name, pattern in TASK_RULES:
        if pattern.search(text):
            return name
    return "other_task"


def batch_payload(
    *,
    batch_id: str,
    group_key: tuple[str, str, str],
    units: list[dict],
) -> dict:
    return {
        "schema_version": "internal_demand_semantic_batch_v1",
        "batch_id": batch_id,
        "business_line": group_key[0],
        "demand_subject": group_key[1],
        "coarse_task_bucket": group_key[2],
        "notice": "粗分桶仅用于控制上下文，不是正式需求类别",
        "units": units,
    }


def serialized_bytes(payload: dict) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("units", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-batch-size", type=int, default=80)
    parser.add_argument("--max-batch-bytes", type=int, default=120_000)
    parser.add_argument(
        "--grouping-mode",
        choices=("coarse-task", "subject"),
        default="coarse-task",
        help=(
            "coarse-task按业务线、主体和粗任务分桶；"
            "subject只按业务线和主体分区，适合大上下文模型"
        ),
    )
    args = parser.parse_args()
    if not 20 <= args.max_batch_size <= 600:
        parser.error("--max-batch-size must be between 20 and 600")
    if not 20_000 <= args.max_batch_bytes <= 2_000_000:
        parser.error("--max-batch-bytes must be between 20000 and 2000000")

    units = load_units(args.units)
    grouped: dict[tuple[str, str, str], list[dict]] = collections.defaultdict(list)
    for unit in units:
        coarse_task_bucket = (
            task_bucket(unit) if args.grouping_mode == "coarse-task" else "all_tasks"
        )
        grouped[
            (unit["business_line"], unit["demand_subject"], coarse_task_bucket)
        ].append(unit)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for old in args.output_dir.glob("batch-*.json"):
        old.unlink()
    batches = []
    assigned = []
    batch_number = 0
    for group_key in sorted(grouped):
        group_units = sorted(grouped[group_key], key=lambda item: item["unit_id"])
        chunks: list[list[dict]] = []
        current: list[dict] = []
        for unit in group_units:
            prospective = [*current, unit]
            probe = batch_payload(
                batch_id="batch-000",
                group_key=group_key,
                units=prospective,
            )
            if current and (
                len(prospective) > args.max_batch_size
                or serialized_bytes(probe) > args.max_batch_bytes
            ):
                chunks.append(current)
                current = [unit]
            else:
                current = prospective
            single_probe = batch_payload(
                batch_id="batch-000",
                group_key=group_key,
                units=current,
            )
            if serialized_bytes(single_probe) > args.max_batch_bytes:
                raise ValueError(
                    f"单条需求单元超过--max-batch-bytes: {unit['unit_id']}"
                )
        if current:
            chunks.append(current)
        for chunk in chunks:
            batch_number += 1
            batch_id = f"batch-{batch_number:03d}"
            payload = batch_payload(
                batch_id=batch_id,
                group_key=group_key,
                units=chunk,
            )
            payload_bytes = serialized_bytes(payload)
            output = args.output_dir / f"{batch_id}.json"
            output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            unit_ids = [unit["unit_id"] for unit in chunk]
            assigned.extend(unit_ids)
            batches.append(
                {
                    "batch_id": batch_id,
                    "file": output.name,
                    "business_line": group_key[0],
                    "demand_subject": group_key[1],
                    "coarse_task_bucket": group_key[2],
                    "unit_count": len(chunk),
                    "serialized_bytes": payload_bytes,
                    "unit_ids": unit_ids,
                }
            )

    expected = [unit["unit_id"] for unit in units]
    if sorted(assigned) != sorted(expected) or len(assigned) != len(set(assigned)):
        raise RuntimeError("batch assignment is not lossless")
    manifest = {
        "schema_version": "internal_demand_semantic_batch_manifest_v1",
        "source_file": args.units.name,
        "unit_count": len(units),
        "batch_count": len(batches),
        "grouping_mode": args.grouping_mode,
        "max_batch_size": args.max_batch_size,
        "max_batch_bytes": args.max_batch_bytes,
        "batches": batches,
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"unit_count": len(units), "batch_count": len(batches)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
