#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import shlex
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


STATE_SCHEMA = "internal_demand_semantic_run_v1"
BATCH_RESPONSE_SCHEMA = "internal_demand_batch_clusters_v1"
CROSS_RESPONSE_SCHEMA = "internal_demand_cross_batch_clusters_v1"
FINAL_MAPPING_SCHEMA = "internal_demand_cluster_mapping_v2"

CLUSTER_TEXT_FIELDS = (
    "canonical_name",
    "task_scene",
    "core_problem",
    "desired_change",
    "merge_reason",
    "split_boundary",
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON根对象必须是object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_model_request(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_units(path: Path) -> dict[str, dict[str, Any]]:
    units: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        unit = json.loads(line)
        unit_id = str(unit.get("unit_id") or "").strip()
        if not unit_id:
            raise ValueError(f"line {line_number}: unit_id缺失")
        if unit_id in units:
            raise ValueError(f"重复unit_id: {unit_id}")
        if unit.get("business_line") not in {"app", "lead"}:
            raise ValueError(f"line {line_number}: business_line无效")
        if unit.get("demand_subject") not in {"student", "parent", "other"}:
            raise ValueError(f"line {line_number}: demand_subject无效")
        units[unit_id] = unit
    if not units:
        raise ValueError("需求单元为空")
    return units


def _state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def _load_state(run_dir: Path) -> dict[str, Any]:
    state = _read_json(_state_path(run_dir))
    if state.get("schema_version") != STATE_SCHEMA:
        raise ValueError("语义运行状态版本不受支持")
    if state.get("runner_sha256") != _sha256(Path(__file__).resolve()):
        raise ValueError("语义运行器已变化；不得用新逻辑续跑旧状态，请重新init")
    return state


def _save_state(run_dir: Path, state: dict[str, Any]) -> None:
    _write_json(_state_path(run_dir), state)


def _batch_instructions() -> str:
    return (
        "你正在归并同一粗分桶中的内部需求表达。粗分桶不是正式类别。"
        "先根据具体场景和核心问题重新判断每个需求单元的主体；"
        "再按需求主体、正在完成的任务、核心问题机制和期待变化决定合并或拆分。"
        "产品、功能、广告对象、关键词和高表现状态都不能决定归并。"
        "每个输入unit_id必须且只能进入一个候选簇。只返回符合output_contract的JSON对象。"
    )


def _cross_instructions() -> str:
    return (
        "你正在对同一业务线、同一需求主体的批内候选簇做跨批归并。"
        "只在任务、核心问题机制和期待变化相同或兼容时合并；"
        "同名但机制不同必须拆开。每个candidate_cluster_id必须且只能进入一个最终簇。"
        "problem_expression_groups要覆盖全部成员需求单元且分成1至4组；"
        "current_coping_groups只覆盖current_coping非空的成员，同样不超过4组。"
        "只返回符合output_contract的JSON对象。"
    )


def _batch_output_contract(job_id: str) -> dict[str, Any]:
    return {
        "schema_version": BATCH_RESPONSE_SCHEMA,
        "job_id": job_id,
        "clusters": [
            {
                "candidate_cluster_id": "C001",
                "business_line": "app|lead",
                "demand_subject": "student|parent|other",
                "canonical_name": "需求名称",
                "task_scene": "具体任务场景",
                "core_problem": "一个核心问题机制",
                "desired_change": "最小期待变化",
                "member_unit_ids": ["N-000001"],
                "problem_expression_groups": [
                    {"label": "具体表现", "member_unit_ids": ["N-000001"]}
                ],
                "current_coping_groups": [],
                "center_unit_ids": ["N-000001"],
                "boundary_unit_ids": [],
                "merge_reason": "为什么这些成员属于同一需求",
                "split_boundary": "与最相邻需求的拆分边界",
            }
        ],
    }


def _cross_output_contract(job_id: str) -> dict[str, Any]:
    return {
        "schema_version": CROSS_RESPONSE_SCHEMA,
        "job_id": job_id,
        "clusters": [
            {
                "final_cluster_key": "F001",
                "business_line": "app|lead",
                "demand_subject": "student|parent|other",
                "canonical_name": "需求名称",
                "task_scene": "具体任务场景",
                "core_problem": "一个核心问题机制",
                "desired_change": "最小期待变化",
                "member_candidate_cluster_ids": ["batch-001:C001"],
                "problem_expression_groups": [
                    {"label": "具体表现", "member_unit_ids": ["N-000001"]}
                ],
                "current_coping_groups": [],
                "center_unit_ids": ["N-000001"],
                "boundary_unit_ids": [],
                "merge_reason": "跨批合并理由",
                "split_boundary": "与相邻最终需求的拆分边界",
            }
        ],
    }


def initialize_run(units_path: Path, batches_dir: Path, run_dir: Path) -> dict[str, Any]:
    if _state_path(run_dir).exists():
        raise ValueError(f"运行目录已存在状态文件，请使用resume: {run_dir}")
    units = load_units(units_path)
    manifest_path = batches_dir / "manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != "internal_demand_semantic_batch_manifest_v1":
        raise ValueError("语义批次manifest版本无效")
    manifest_ids = [
        unit_id
        for batch in manifest.get("batches") or []
        for unit_id in batch.get("unit_ids") or []
    ]
    if len(manifest_ids) != len(set(manifest_ids)):
        raise ValueError("语义批次包含重复unit_id")
    if set(manifest_ids) != set(units):
        raise ValueError("语义批次与需求单元不守恒")

    jobs: dict[str, dict[str, Any]] = {}
    for batch_meta in manifest["batches"]:
        batch_id = batch_meta["batch_id"]
        batch_path = batches_dir / batch_meta["file"]
        batch = _read_json(batch_path)
        if [item["unit_id"] for item in batch["units"]] != batch_meta["unit_ids"]:
            raise ValueError(f"批次文件与manifest不一致: {batch_id}")
        request = {
            "schema_version": "internal_demand_model_job_v1",
            "job_id": batch_id,
            "stage": "batch_clustering",
            "instructions": _batch_instructions(),
            "input": batch,
            "output_contract": _batch_output_contract(batch_id),
        }
        request_path = run_dir / "requests" / f"{batch_id}.json"
        _write_model_request(request_path, request)
        jobs[batch_id] = {
            "stage": "batch_clustering",
            "status": "pending",
            "request_path": str(request_path.resolve()),
            "response_path": None,
            "attempt_count": 0,
        }

    state = {
        "schema_version": STATE_SCHEMA,
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "status": "batch_pending",
        "units_path": str(units_path.resolve()),
        "units_sha256": _sha256(units_path),
        "batch_manifest_path": str(manifest_path.resolve()),
        "batch_manifest_sha256": _sha256(manifest_path),
        "unit_count": len(units),
        "batch_count": len(manifest["batches"]),
        "jobs": jobs,
        "final_mapping_path": None,
        "normalized_units_path": None,
        "receipt_path": None,
    }
    _save_state(run_dir, state)
    return state


def _require_text(cluster: dict[str, Any], field: str, prefix: str) -> None:
    if not str(cluster.get(field) or "").strip():
        raise ValueError(f"{prefix}:{field}不能为空")


def _validate_groups(
    *,
    cluster: dict[str, Any],
    member_ids: set[str],
    units: dict[str, dict[str, Any]],
    prefix: str,
    expected_coping_ids: set[str] | None = None,
) -> None:
    problem_groups = cluster.get("problem_expression_groups")
    if not isinstance(problem_groups, list) or not 1 <= len(problem_groups) <= 4:
        raise ValueError(f"{prefix}:problem_expression_groups必须为1至4组")
    problem_assigned: list[str] = []
    for group in problem_groups:
        if not str(group.get("label") or "").strip():
            raise ValueError(f"{prefix}:问题表达组label不能为空")
        ids = group.get("member_unit_ids") or []
        if not ids:
            raise ValueError(f"{prefix}:问题表达组不能为空")
        problem_assigned.extend(ids)
    if len(problem_assigned) != len(set(problem_assigned)):
        raise ValueError(f"{prefix}:问题表达组重复分配")
    if set(problem_assigned) != member_ids:
        raise ValueError(f"{prefix}:问题表达组没有完整覆盖成员")

    coping_groups = cluster.get("current_coping_groups")
    if not isinstance(coping_groups, list) or len(coping_groups) > 4:
        raise ValueError(f"{prefix}:current_coping_groups必须为0至4组")
    coping_assigned: list[str] = []
    for group in coping_groups:
        if not str(group.get("label") or "").strip():
            raise ValueError(f"{prefix}:当前应对组label不能为空")
        ids = group.get("member_unit_ids") or []
        if not ids:
            raise ValueError(f"{prefix}:当前应对组不能为空")
        coping_assigned.extend(ids)
    if len(coping_assigned) != len(set(coping_assigned)):
        raise ValueError(f"{prefix}:当前应对组重复分配")
    expected_coping = expected_coping_ids
    if expected_coping is None:
        expected_coping = {
            unit_id
            for unit_id in member_ids
            if str(units[unit_id].get("current_coping") or "").strip()
        }
    if set(coping_assigned) != expected_coping:
        raise ValueError(f"{prefix}:当前应对组与非空current_coping不守恒")


def validate_batch_response(
    request: dict[str, Any], response: dict[str, Any]
) -> dict[str, Any]:
    job_id = request["job_id"]
    if response.get("schema_version") != BATCH_RESPONSE_SCHEMA:
        raise ValueError(f"{job_id}:批内响应schema_version无效")
    if response.get("job_id") != job_id:
        raise ValueError(f"{job_id}:响应job_id不匹配")
    input_units = {
        unit["unit_id"]: unit for unit in request["input"]["units"]
    }
    clusters = response.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        raise ValueError(f"{job_id}:clusters不能为空")
    candidate_ids: set[str] = set()
    assigned: list[str] = []
    normalized: list[dict[str, Any]] = []
    for index, cluster in enumerate(clusters, start=1):
        prefix = f"{job_id}:cluster-{index}"
        candidate_id = str(cluster.get("candidate_cluster_id") or "").strip()
        if not candidate_id or candidate_id in candidate_ids:
            raise ValueError(f"{prefix}:candidate_cluster_id缺失或重复")
        candidate_ids.add(candidate_id)
        business = cluster.get("business_line")
        subject = cluster.get("demand_subject")
        if business not in {"app", "lead"}:
            raise ValueError(f"{prefix}:business_line无效")
        if subject not in {"student", "parent", "other"}:
            raise ValueError(f"{prefix}:demand_subject无效")
        for field in CLUSTER_TEXT_FIELDS:
            _require_text(cluster, field, prefix)
        members = cluster.get("member_unit_ids") or []
        if not members or len(members) != len(set(members)):
            raise ValueError(f"{prefix}:成员缺失或重复")
        member_set = set(members)
        if not member_set <= set(input_units):
            raise ValueError(f"{prefix}:包含批次外成员")
        if any(input_units[unit_id]["business_line"] != business for unit_id in members):
            raise ValueError(f"{prefix}:跨业务线合并")
        centers = cluster.get("center_unit_ids") or []
        boundaries = cluster.get("boundary_unit_ids") or []
        if not centers or not set(centers) <= member_set:
            raise ValueError(f"{prefix}:中心样本无效")
        if not set(boundaries) <= member_set:
            raise ValueError(f"{prefix}:边界样本无效")
        _validate_groups(
            cluster=cluster,
            member_ids=member_set,
            units=input_units,
            prefix=prefix,
        )
        assigned.extend(members)
        normalized.append(
            {
                **cluster,
                "candidate_cluster_id": f"{job_id}:{candidate_id}",
            }
        )
    if len(assigned) != len(set(assigned)) or set(assigned) != set(input_units):
        raise ValueError(f"{job_id}:需求单元没有且仅有一个批内归属")
    return {
        "schema_version": BATCH_RESPONSE_SCHEMA,
        "job_id": job_id,
        "clusters": normalized,
    }


def _all_batch_complete(state: dict[str, Any]) -> bool:
    batch_jobs = [
        job for job in state["jobs"].values()
        if job["stage"] == "batch_clustering"
    ]
    return bool(batch_jobs) and all(job["status"] == "complete" for job in batch_jobs)


def _load_completed_batch_clusters(state: dict[str, Any]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    for job_id, job in sorted(state["jobs"].items()):
        if job["stage"] != "batch_clustering" or job["status"] != "complete":
            continue
        response = _read_json(Path(job["response_path"]))
        clusters.extend(response["clusters"])
    return clusters


def _normalized_units(
    units: dict[str, dict[str, Any]], batch_clusters: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    normalized = {unit_id: dict(unit) for unit_id, unit in units.items()}
    for cluster in batch_clusters:
        for unit_id in cluster["member_unit_ids"]:
            normalized[unit_id]["demand_subject"] = cluster["demand_subject"]
    return normalized


def create_cross_jobs(run_dir: Path, state: dict[str, Any]) -> None:
    if any(job["stage"] == "cross_batch_clustering" for job in state["jobs"].values()):
        return
    units = load_units(Path(state["units_path"]))
    batch_clusters = _load_completed_batch_clusters(state)
    normalized_units = _normalized_units(units, batch_clusters)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for cluster in batch_clusters:
        grouped[(cluster["business_line"], cluster["demand_subject"])].append(cluster)
    for (business, subject), clusters in sorted(grouped.items()):
        job_id = f"cross-{business}-{subject}"
        relevant_ids = sorted(
            {
                unit_id
                for cluster in clusters
                for unit_id in cluster["member_unit_ids"]
            }
        )
        sample_ids = sorted(
            {
                unit_id
                for cluster in clusters
                for unit_id in [
                    *(cluster.get("center_unit_ids") or [])[:1],
                    *(cluster.get("boundary_unit_ids") or [])[:1],
                ]
            }
        )
        request = {
            "schema_version": "internal_demand_model_job_v1",
            "job_id": job_id,
            "stage": "cross_batch_clustering",
            "instructions": _cross_instructions(),
            "input": {
                "business_line": business,
                "demand_subject": subject,
                "candidate_clusters": clusters,
                "unit_samples": [normalized_units[unit_id] for unit_id in sample_ids],
                "current_coping_unit_ids": [
                    unit_id
                    for unit_id in relevant_ids
                    if str(normalized_units[unit_id].get("current_coping") or "").strip()
                ],
            },
            "output_contract": _cross_output_contract(job_id),
        }
        request_path = run_dir / "requests" / f"{job_id}.json"
        _write_model_request(request_path, request)
        state["jobs"][job_id] = {
            "stage": "cross_batch_clustering",
            "status": "pending",
            "request_path": str(request_path.resolve()),
            "response_path": None,
            "attempt_count": 0,
        }
    state["status"] = "cross_batch_pending"
    _save_state(run_dir, state)


def validate_cross_response(
    request: dict[str, Any], response: dict[str, Any]
) -> dict[str, Any]:
    job_id = request["job_id"]
    if response.get("schema_version") != CROSS_RESPONSE_SCHEMA:
        raise ValueError(f"{job_id}:跨批响应schema_version无效")
    if response.get("job_id") != job_id:
        raise ValueError(f"{job_id}:响应job_id不匹配")
    business = request["input"]["business_line"]
    subject = request["input"]["demand_subject"]
    candidates = {
        cluster["candidate_cluster_id"]: cluster
        for cluster in request["input"]["candidate_clusters"]
    }
    sample_units = {
        unit["unit_id"]: unit for unit in request["input"]["unit_samples"]
    }
    expected_coping_ids = set(request["input"]["current_coping_unit_ids"])
    clusters = response.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        raise ValueError(f"{job_id}:clusters不能为空")
    final_keys: set[str] = set()
    assigned_candidates: list[str] = []
    normalized: list[dict[str, Any]] = []
    for index, cluster in enumerate(clusters, start=1):
        prefix = f"{job_id}:cluster-{index}"
        final_key = str(cluster.get("final_cluster_key") or "").strip()
        if not final_key or final_key in final_keys:
            raise ValueError(f"{prefix}:final_cluster_key缺失或重复")
        final_keys.add(final_key)
        if cluster.get("business_line") != business:
            raise ValueError(f"{prefix}:business_line与任务不一致")
        if cluster.get("demand_subject") != subject:
            raise ValueError(f"{prefix}:demand_subject与任务不一致")
        for field in CLUSTER_TEXT_FIELDS:
            _require_text(cluster, field, prefix)
        member_candidates = cluster.get("member_candidate_cluster_ids") or []
        if not member_candidates or len(member_candidates) != len(set(member_candidates)):
            raise ValueError(f"{prefix}:候选簇成员缺失或重复")
        if not set(member_candidates) <= set(candidates):
            raise ValueError(f"{prefix}:包含任务外候选簇")
        member_ids = {
            unit_id
            for candidate_id in member_candidates
            for unit_id in candidates[candidate_id]["member_unit_ids"]
        }
        centers = cluster.get("center_unit_ids") or []
        boundaries = cluster.get("boundary_unit_ids") or []
        if not centers or not set(centers) <= member_ids:
            raise ValueError(f"{prefix}:中心样本无效")
        if not set(boundaries) <= member_ids:
            raise ValueError(f"{prefix}:边界样本无效")
        _validate_groups(
            cluster=cluster,
            member_ids=member_ids,
            units=sample_units,
            prefix=prefix,
            expected_coping_ids=expected_coping_ids & member_ids,
        )
        assigned_candidates.extend(member_candidates)
        normalized.append(
            {
                "final_cluster_key": final_key,
                "business_line": business,
                "canonical_name": cluster["canonical_name"],
                "demand_subject": subject,
                "task_scene": cluster["task_scene"],
                "core_problem": cluster["core_problem"],
                "desired_change": cluster["desired_change"],
                "member_unit_ids": sorted(member_ids),
                "problem_expression_groups": cluster["problem_expression_groups"],
                "current_coping_groups": cluster["current_coping_groups"],
                "center_unit_ids": cluster["center_unit_ids"],
                "boundary_unit_ids": cluster["boundary_unit_ids"],
                "merge_reason": cluster["merge_reason"],
                "split_boundary": cluster["split_boundary"],
            }
        )
    if (
        len(assigned_candidates) != len(set(assigned_candidates))
        or set(assigned_candidates) != set(candidates)
    ):
        raise ValueError(f"{job_id}:候选簇没有且仅有一个跨批归属")
    return {
        "schema_version": CROSS_RESPONSE_SCHEMA,
        "job_id": job_id,
        "clusters": normalized,
    }


def _all_cross_complete(state: dict[str, Any]) -> bool:
    cross_jobs = [
        job for job in state["jobs"].values()
        if job["stage"] == "cross_batch_clustering"
    ]
    return bool(cross_jobs) and all(job["status"] == "complete" for job in cross_jobs)


def finalize_run(run_dir: Path, state: dict[str, Any]) -> None:
    units_path = Path(state["units_path"])
    units = load_units(units_path)
    batch_clusters = _load_completed_batch_clusters(state)
    normalized_units = _normalized_units(units, batch_clusters)
    normalized_units_path = run_dir / "normalized_units.jsonl"
    normalized_units_path.write_text(
        "".join(
            json.dumps(normalized_units[unit_id], ensure_ascii=False) + "\n"
            for unit_id in sorted(normalized_units)
        ),
        encoding="utf-8",
    )

    cross_clusters: list[dict[str, Any]] = []
    for job_id, job in sorted(state["jobs"].items()):
        if job["stage"] != "cross_batch_clustering":
            continue
        cross_clusters.extend(_read_json(Path(job["response_path"]))["clusters"])
    cross_clusters.sort(
        key=lambda cluster: (
            cluster["business_line"],
            cluster["demand_subject"],
            cluster["canonical_name"],
            cluster["core_problem"],
            cluster["final_cluster_key"],
        )
    )
    counters = {"app": 0, "lead": 0}
    final_clusters = []
    for cluster in cross_clusters:
        business = cluster["business_line"]
        counters[business] += 1
        final_clusters.append(
            {
                "cluster_id": f"{business.upper()}-C{counters[business]:03d}",
                **{key: value for key, value in cluster.items() if key != "final_cluster_key"},
            }
        )
    mapping = {
        "schema_version": FINAL_MAPPING_SCHEMA,
        "clusters": final_clusters,
    }
    mapping_path = run_dir / "final_mapping.json"
    _write_json(mapping_path, mapping)

    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    from validate_semantic_mapping import load_units as validator_load_units
    from validate_semantic_mapping import validate as validator_validate

    errors = validator_validate(
        validator_load_units(normalized_units_path), mapping
    )
    if errors:
        raise ValueError("最终语义映射校验失败:\n" + "\n".join(errors))
    receipt = {
        "schema_version": "internal_demand_semantic_run_receipt_v1",
        "unit_count": len(normalized_units),
        "batch_count": state["batch_count"],
        "cross_batch_job_count": sum(
            job["stage"] == "cross_batch_clustering"
            for job in state["jobs"].values()
        ),
        "final_cluster_count": len(final_clusters),
        "units_sha256": _sha256(units_path),
        "normalized_units_sha256": _sha256(normalized_units_path),
        "final_mapping_sha256": _sha256(mapping_path),
        "validation_status": "passed",
    }
    receipt_path = run_dir / "receipt.json"
    _write_json(receipt_path, receipt)
    state.update(
        {
            "status": "complete",
            "final_mapping_path": str(mapping_path.resolve()),
            "normalized_units_path": str(normalized_units_path.resolve()),
            "receipt_path": str(receipt_path.resolve()),
        }
    )
    _save_state(run_dir, state)


def accept_response(run_dir: Path, job_id: str, response_path: Path) -> dict[str, Any]:
    state = _load_state(run_dir)
    job = state["jobs"].get(job_id)
    if job is None:
        raise ValueError(f"未知job_id: {job_id}")
    if job["status"] != "pending":
        raise ValueError(f"任务不是pending状态: {job_id}")
    request = _read_json(Path(job["request_path"]))
    response = _read_json(response_path)
    if job["stage"] == "batch_clustering":
        normalized = validate_batch_response(request, response)
    else:
        normalized = validate_cross_response(request, response)
    stored = run_dir / "responses" / f"{job_id}.json"
    _write_json(stored, normalized)
    job["status"] = "complete"
    job["response_path"] = str(stored.resolve())
    _save_state(run_dir, state)
    if _all_batch_complete(state):
        create_cross_jobs(run_dir, state)
        state = _load_state(run_dir)
    if _all_cross_complete(state):
        finalize_run(run_dir, state)
        state = _load_state(run_dir)
    return state


def _pending_jobs(state: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    stage_order = {"batch_clustering": 0, "cross_batch_clustering": 1}
    return sorted(
        (
            (job_id, job)
            for job_id, job in state["jobs"].items()
            if job["status"] == "pending"
        ),
        key=lambda item: (stage_order[item[1]["stage"]], item[0]),
    )


def _invoke_model_job(
    *,
    command: list[str],
    run_dir: Path,
    job_id: str,
    request_path: Path,
    attempt_number: int,
    timeout_seconds: int,
) -> tuple[Path | None, str | None]:
    request_text = request_path.read_text(encoding="utf-8")
    attempt_dir = run_dir / "attempts"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    attempt_prefix = f"{job_id}.attempt-{attempt_number:03d}"
    try:
        result = subprocess.run(
            command,
            input=request_text,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        return None, f"模型命令超时: {job_id}: {error}"
    (attempt_dir / f"{attempt_prefix}.stdout.txt").write_text(
        result.stdout, encoding="utf-8"
    )
    (attempt_dir / f"{attempt_prefix}.stderr.txt").write_text(
        result.stderr, encoding="utf-8"
    )
    if result.returncode != 0:
        return None, f"模型命令失败: {job_id}: exit={result.returncode}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        return None, f"模型没有返回纯JSON: {job_id}: {error}"
    raw_response = attempt_dir / f"{attempt_prefix}.response.json"
    _write_json(raw_response, payload)
    return raw_response, None


def run_model_jobs(
    *,
    run_dir: Path,
    model_command: str,
    max_jobs: int,
    timeout_seconds: int,
    parallel_jobs: int = 1,
) -> dict[str, Any]:
    command = shlex.split(model_command)
    if not command:
        raise ValueError("model-command不能为空")
    completed = 0
    while completed < max_jobs:
        state = _load_state(run_dir)
        if state["status"] == "complete":
            return state
        pending = _pending_jobs(state)
        if not pending:
            raise ValueError("没有pending任务，但语义运行尚未完成")
        wave = pending[: min(parallel_jobs, max_jobs - completed)]
        attempts = []
        for job_id, job in wave:
            job["attempt_count"] = int(job.get("attempt_count", 0)) + 1
            attempts.append(
                (
                    job_id,
                    Path(job["request_path"]),
                    job["attempt_count"],
                )
            )
        _save_state(run_dir, state)
        outcomes: dict[str, tuple[Path | None, str | None]] = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(attempts)
        ) as executor:
            future_map = {
                executor.submit(
                    _invoke_model_job,
                    command=command,
                    run_dir=run_dir,
                    job_id=job_id,
                    request_path=request_path,
                    attempt_number=attempt_number,
                    timeout_seconds=timeout_seconds,
                ): job_id
                for job_id, request_path, attempt_number in attempts
            }
            for future in concurrent.futures.as_completed(future_map):
                outcomes[future_map[future]] = future.result()
        failures = []
        for job_id, _, _ in attempts:
            response_path, error = outcomes[job_id]
            if error is not None:
                failures.append(error)
                continue
            assert response_path is not None
            try:
                accept_response(run_dir, job_id, response_path)
            except (ValueError, RuntimeError) as validation_error:
                failures.append(f"响应验收失败: {job_id}: {validation_error}")
        completed += len(attempts)
        if failures:
            raise RuntimeError("并行模型任务存在失败:\n" + "\n".join(failures))
    return _load_state(run_dir)


def status_payload(state: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for job in state["jobs"].values():
        counts[f"{job['stage']}:{job['status']}"] += 1
    return {
        "status": state["status"],
        "unit_count": state["unit_count"],
        "batch_count": state["batch_count"],
        "job_counts": dict(sorted(counts.items())),
        "next_job": (_pending_jobs(state)[0][0] if _pending_jobs(state) else None),
        "final_mapping_path": state.get("final_mapping_path"),
        "receipt_path": state.get("receipt_path"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("units", type=Path)
    init_parser.add_argument("batches_dir", type=Path)
    init_parser.add_argument("--run-dir", type=Path, required=True)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--run-dir", type=Path, required=True)

    accept_parser = subparsers.add_parser("accept")
    accept_parser.add_argument("--run-dir", type=Path, required=True)
    accept_parser.add_argument("--job-id", required=True)
    accept_parser.add_argument("--response", type=Path, required=True)

    next_parser = subparsers.add_parser("next")
    next_parser.add_argument("--run-dir", type=Path, required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--run-dir", type=Path, required=True)
    run_parser.add_argument("--model-command", required=True)
    run_parser.add_argument("--max-jobs", type=int, default=1)
    run_parser.add_argument("--parallel-jobs", type=int, default=1)
    run_parser.add_argument("--timeout-seconds", type=int, default=300)
    run_parser.add_argument("--approved-model-run", action="store_true")

    args = parser.parse_args()
    if args.command == "init":
        state = initialize_run(args.units, args.batches_dir, args.run_dir)
        print(json.dumps(status_payload(state), ensure_ascii=False))
        return 0
    if args.command == "status":
        print(json.dumps(status_payload(_load_state(args.run_dir)), ensure_ascii=False))
        return 0
    if args.command == "next":
        state = _load_state(args.run_dir)
        pending = _pending_jobs(state)
        if not pending:
            print(json.dumps(status_payload(state), ensure_ascii=False))
            return 0
        job_id, job = pending[0]
        print(
            json.dumps(
                {
                    "job_id": job_id,
                    "stage": job["stage"],
                    "request_path": job["request_path"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "accept":
        state = accept_response(args.run_dir, args.job_id, args.response)
        print(json.dumps(status_payload(state), ensure_ascii=False))
        return 0
    if not args.approved_model_run:
        parser.error("run会调用外部模型；必须由当前任务明确授权后传--approved-model-run")
    if args.max_jobs < 1:
        parser.error("--max-jobs必须大于0")
    if not 1 <= args.parallel_jobs <= 4:
        parser.error("--parallel-jobs必须在1到4之间")
    state = run_model_jobs(
        run_dir=args.run_dir,
        model_command=args.model_command,
        max_jobs=args.max_jobs,
        timeout_seconds=args.timeout_seconds,
        parallel_jobs=args.parallel_jobs,
    )
    print(json.dumps(status_payload(state), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
