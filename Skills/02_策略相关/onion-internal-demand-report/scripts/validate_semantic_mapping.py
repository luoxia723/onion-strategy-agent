#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


UNIT_FIELDS = (
    "unit_id",
    "business_line",
    "demand_subject",
    "task_scene",
    "specific_problem",
    "desired_change",
)
CLUSTER_FIELDS = (
    "cluster_id",
    "business_line",
    "canonical_name",
    "demand_subject",
    "task_scene",
    "core_problem",
    "desired_change",
    "member_unit_ids",
    "problem_expression_groups",
    "current_coping_groups",
    "center_unit_ids",
    "boundary_unit_ids",
    "merge_reason",
    "split_boundary",
)


def load_units(path: Path) -> dict[str, dict]:
    units = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        unit = json.loads(line)
        for field in UNIT_FIELDS:
            if field not in unit or not str(unit[field]).strip():
                raise ValueError(f"line {line_number}: missing {field}")
        if unit["unit_id"] in units:
            raise ValueError(f"duplicate unit_id: {unit['unit_id']}")
        units[unit["unit_id"]] = unit
    if not units:
        raise ValueError("no demand units")
    return units


def validate(units: dict[str, dict], mapping: dict) -> list[str]:
    errors = []
    if mapping.get("schema_version") != "internal_demand_cluster_mapping_v2":
        errors.append("schema_version_invalid")
    clusters = mapping.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        return errors + ["clusters_required"]
    cluster_ids = set()
    assignments: dict[str, str] = {}
    for index, cluster in enumerate(clusters, start=1):
        prefix = f"cluster_{index}"
        for field in CLUSTER_FIELDS:
            if field not in cluster:
                errors.append(f"{prefix}:missing_field:{field}")
        cluster_id = str(cluster.get("cluster_id") or "")
        if cluster_id in cluster_ids:
            errors.append(f"duplicate_cluster_id:{cluster_id}")
        cluster_ids.add(cluster_id)
        business = cluster.get("business_line")
        if business not in {"app", "lead"}:
            errors.append(f"{prefix}:business_line_invalid")
        cluster_subject = cluster.get("demand_subject")
        if cluster_subject not in {"student", "parent", "other"}:
            errors.append(f"{prefix}:demand_subject_invalid")
        for field in (
            "canonical_name",
            "demand_subject",
            "task_scene",
            "core_problem",
            "desired_change",
            "merge_reason",
            "split_boundary",
        ):
            if not str(cluster.get(field) or "").strip():
                errors.append(f"{prefix}:empty_field:{field}")
        members = cluster.get("member_unit_ids") or []
        centers = cluster.get("center_unit_ids") or []
        boundaries = cluster.get("boundary_unit_ids") or []
        if not members:
            errors.append(f"{prefix}:members_required")
        member_set = set(members)
        if len(member_set) != len(members):
            errors.append(f"{prefix}:duplicate_member")
        if not set(centers).issubset(member_set):
            errors.append(f"{prefix}:center_not_member")
        if not set(boundaries).issubset(member_set):
            errors.append(f"{prefix}:boundary_not_member")
        if not centers:
            errors.append(f"{prefix}:center_required")

        problem_groups = cluster.get("problem_expression_groups") or []
        if not 1 <= len(problem_groups) <= 4:
            errors.append(f"{prefix}:problem_expression_group_count_invalid")
        problem_assignments: dict[str, str] = {}
        for group_index, group in enumerate(problem_groups, start=1):
            group_prefix = f"{prefix}:problem_group_{group_index}"
            label = str(group.get("label") or "").strip()
            group_members = group.get("member_unit_ids") or []
            if not label:
                errors.append(f"{group_prefix}:label_required")
            if not group_members:
                errors.append(f"{group_prefix}:members_required")
            for unit_id in group_members:
                if unit_id not in member_set:
                    errors.append(f"{group_prefix}:nonmember:{unit_id}")
                if unit_id in problem_assignments:
                    errors.append(f"{prefix}:problem_unit_assigned_twice:{unit_id}")
                else:
                    problem_assignments[unit_id] = label
        for unit_id in sorted(member_set - set(problem_assignments)):
            errors.append(f"{prefix}:problem_unit_unassigned:{unit_id}")

        coping_groups = cluster.get("current_coping_groups") or []
        if len(coping_groups) > 4:
            errors.append(f"{prefix}:current_coping_group_count_invalid")
        coping_assignments: dict[str, str] = {}
        for group_index, group in enumerate(coping_groups, start=1):
            group_prefix = f"{prefix}:coping_group_{group_index}"
            label = str(group.get("label") or "").strip()
            group_members = group.get("member_unit_ids") or []
            if not label:
                errors.append(f"{group_prefix}:label_required")
            if not group_members:
                errors.append(f"{group_prefix}:members_required")
            for unit_id in group_members:
                if unit_id not in member_set:
                    errors.append(f"{group_prefix}:nonmember:{unit_id}")
                elif not str(units.get(unit_id, {}).get("current_coping") or "").strip():
                    errors.append(f"{group_prefix}:coping_missing_in_unit:{unit_id}")
                if unit_id in coping_assignments:
                    errors.append(f"{prefix}:coping_unit_assigned_twice:{unit_id}")
                else:
                    coping_assignments[unit_id] = label
        coping_expected = {
            unit_id
            for unit_id in member_set
            if str(units.get(unit_id, {}).get("current_coping") or "").strip()
        }
        for unit_id in sorted(coping_expected - set(coping_assignments)):
            errors.append(f"{prefix}:coping_unit_unassigned:{unit_id}")
        for unit_id in members:
            if unit_id not in units:
                errors.append(f"{prefix}:unknown_unit:{unit_id}")
                continue
            if units[unit_id]["business_line"] != business:
                errors.append(f"{prefix}:cross_business_member:{unit_id}")
            if units[unit_id]["demand_subject"] != cluster_subject:
                errors.append(f"{prefix}:cross_subject_member:{unit_id}")
            if unit_id in assignments:
                errors.append(
                    f"unit_assigned_twice:{unit_id}:{assignments[unit_id]}:{cluster_id}"
                )
            else:
                assignments[unit_id] = cluster_id
    missing = sorted(set(units) - set(assignments))
    for unit_id in missing:
        errors.append(f"unassigned_unit:{unit_id}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("units", type=Path)
    parser.add_argument("mapping", type=Path)
    args = parser.parse_args()
    units = load_units(args.units)
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    errors = validate(units, mapping)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(
        json.dumps(
            {
                "valid": True,
                "unit_count": len(units),
                "cluster_count": len(mapping["clusters"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
