#!/usr/bin/env python3
"""Lock or reuse a validated report when its semantic input is unchanged."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON根对象必须是object: {path}")
    return value


def write_private(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def validate(report: Path, validator: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(validator), str(report)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"报告校验失败: {report}: {(result.stdout + result.stderr).strip()}"
        )
    return (result.stdout + result.stderr).strip()


def semantic_hashes(manifest_path: Path) -> tuple[dict[str, Any], dict[str, str]]:
    manifest = read_json(manifest_path)
    hashes_path = manifest_path.parent / manifest["semantic_hashes_file"]
    if sha256(hashes_path) != manifest["semantic_hashes_sha256"]:
        raise ValueError("模型bundle语义哈希文件摘要不一致")
    hashes = read_json(hashes_path)
    return manifest, hashes


def report_hashes(manifest_path: Path) -> tuple[dict[str, Any], dict[str, str]]:
    manifest = read_json(manifest_path)
    hashes_path = manifest_path.parent / manifest["report_hashes_file"]
    if sha256(hashes_path) != manifest["report_hashes_sha256"]:
        raise ValueError("模型bundle报告事实哈希文件摘要不一致")
    hashes = read_json(hashes_path)
    return manifest, hashes


def report_status(report: Path) -> str:
    text = report.read_text(encoding="utf-8")
    return "trial" if "试验稿" in text or "不能进入下游" in text else "formal"


def semantic_receipt_fingerprint(path: Path) -> str:
    receipt = read_json(path)
    if receipt.get("validation_status") != "passed":
        raise ValueError("语义运行回执没有通过校验")
    fields = {
        "schema_version": receipt.get("schema_version"),
        "unit_count": receipt.get("unit_count"),
        "normalized_units_sha256": receipt.get("normalized_units_sha256"),
        "final_mapping_sha256": receipt.get("final_mapping_sha256"),
        "validation_status": receipt.get("validation_status"),
    }
    if not fields["normalized_units_sha256"] or not fields["final_mapping_sha256"]:
        raise ValueError("语义运行回执缺少输入或映射摘要")
    return hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def lock(args: argparse.Namespace) -> dict[str, Any]:
    manifest, hashes = semantic_hashes(args.model_bundle_manifest)
    _, fact_hashes = report_hashes(args.model_bundle_manifest)
    validation = validate(args.report, args.validator)
    receipt = {
        "schema_version": "validated_report_baseline_v1",
        "mode": manifest["mode"],
        "report": str(args.report.resolve()),
        "report_sha256": sha256(args.report),
        "model_bundle_manifest": str(args.model_bundle_manifest.resolve()),
        "semantic_record_count": len(hashes),
        "semantic_hashes_sha256": manifest["semantic_hashes_sha256"],
        "report_hashes_sha256": manifest["report_hashes_sha256"],
        "report_fact_record_count": len(fact_hashes),
        "report_status": report_status(args.report),
        "validator": str(args.validator.resolve()),
        "validation_output": validation,
        "status": "locked",
    }
    write_private(args.receipt, receipt)
    return receipt


def reuse(args: argparse.Namespace) -> dict[str, Any]:
    baseline = read_json(args.baseline_receipt)
    current_manifest, current_hashes = semantic_hashes(args.model_bundle_manifest)
    baseline_manifest_path = Path(baseline["model_bundle_manifest"])
    baseline_manifest, baseline_hashes = semantic_hashes(baseline_manifest_path)
    _, current_fact_hashes = report_hashes(args.model_bundle_manifest)
    _, baseline_fact_hashes = report_hashes(baseline_manifest_path)
    if current_manifest["mode"] != baseline_manifest["mode"]:
        raise ValueError("基线与当前模型bundle mode不同")
    if current_hashes != baseline_hashes:
        added = set(current_hashes) - set(baseline_hashes)
        deleted = set(baseline_hashes) - set(current_hashes)
        changed = {
            record_id
            for record_id in set(current_hashes) & set(baseline_hashes)
            if current_hashes[record_id] != baseline_hashes[record_id]
        }
        raise RuntimeError(
            "semantic_delta_requires_incremental_reanalysis: "
            f"added={len(added)},changed={len(changed)},deleted={len(deleted)}"
        )
    if current_fact_hashes != baseline_fact_hashes:
        added = set(current_fact_hashes) - set(baseline_fact_hashes)
        deleted = set(baseline_fact_hashes) - set(current_fact_hashes)
        changed = {
            record_id
            for record_id in set(current_fact_hashes) & set(baseline_fact_hashes)
            if current_fact_hashes[record_id] != baseline_fact_hashes[record_id]
        }
        raise RuntimeError(
            "report_fact_delta_requires_deterministic_rerender: "
            f"added={len(added)},changed={len(changed)},deleted={len(deleted)}"
        )
    source = Path(baseline["report"])
    if sha256(source) != baseline["report_sha256"]:
        raise ValueError("基线报告摘要不一致")
    if args.output.exists():
        raise RuntimeError(f"不覆盖已有报告: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, args.output)
    validation = validate(args.output, Path(baseline["validator"]))
    receipt = {
        "schema_version": "validated_report_reuse_receipt_v1",
        "mode": current_manifest["mode"],
        "baseline_receipt": str(args.baseline_receipt.resolve()),
        "model_bundle_manifest": str(args.model_bundle_manifest.resolve()),
        "output": str(args.output.resolve()),
        "output_sha256": sha256(args.output),
        "semantic_record_count": len(current_hashes),
        "report_status": baseline.get("report_status", "formal"),
        "model_calls": 0,
        "validation_output": validation,
        "status": "reused",
    }
    write_private(args.output.with_suffix(args.output.suffix + ".reuse.json"), receipt)
    return receipt


def lock_semantic(args: argparse.Namespace) -> dict[str, Any]:
    fingerprint = semantic_receipt_fingerprint(args.semantic_receipt)
    validation = validate(args.report, args.validator)
    receipt = {
        "schema_version": "validated_report_baseline_v1",
        "mode": "internal-demand",
        "report": str(args.report.resolve()),
        "report_sha256": sha256(args.report),
        "semantic_receipt": str(args.semantic_receipt.resolve()),
        "semantic_fingerprint": fingerprint,
        "semantic_record_count": read_json(args.semantic_receipt)["unit_count"],
        "report_status": report_status(args.report),
        "validator": str(args.validator.resolve()),
        "validation_output": validation,
        "status": "locked",
    }
    write_private(args.receipt, receipt)
    return receipt


def reuse_semantic(args: argparse.Namespace) -> dict[str, Any]:
    baseline = read_json(args.baseline_receipt)
    current_fingerprint = semantic_receipt_fingerprint(args.semantic_receipt)
    if current_fingerprint != baseline.get("semantic_fingerprint"):
        raise RuntimeError("semantic_delta_requires_internal_demand_reanalysis")
    source = Path(baseline["report"])
    if sha256(source) != baseline["report_sha256"]:
        raise ValueError("基线报告摘要不一致")
    if args.output.exists():
        raise RuntimeError(f"不覆盖已有报告: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, args.output)
    validation = validate(args.output, Path(baseline["validator"]))
    receipt = {
        "schema_version": "validated_report_reuse_receipt_v1",
        "mode": "internal-demand",
        "baseline_receipt": str(args.baseline_receipt.resolve()),
        "semantic_receipt": str(args.semantic_receipt.resolve()),
        "output": str(args.output.resolve()),
        "output_sha256": sha256(args.output),
        "semantic_record_count": baseline["semantic_record_count"],
        "report_status": baseline.get("report_status", "formal"),
        "model_calls": 0,
        "validation_output": validation,
        "status": "reused",
    }
    write_private(args.output.with_suffix(args.output.suffix + ".reuse.json"), receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    lock_parser = sub.add_parser("lock")
    lock_parser.add_argument("--report", type=Path, required=True)
    lock_parser.add_argument("--model-bundle-manifest", type=Path, required=True)
    lock_parser.add_argument("--validator", type=Path, required=True)
    lock_parser.add_argument("--receipt", type=Path, required=True)
    reuse_parser = sub.add_parser("reuse")
    reuse_parser.add_argument("--baseline-receipt", type=Path, required=True)
    reuse_parser.add_argument("--model-bundle-manifest", type=Path, required=True)
    reuse_parser.add_argument("--output", type=Path, required=True)
    lock_semantic_parser = sub.add_parser("lock-semantic")
    lock_semantic_parser.add_argument("--report", type=Path, required=True)
    lock_semantic_parser.add_argument("--semantic-receipt", type=Path, required=True)
    lock_semantic_parser.add_argument("--validator", type=Path, required=True)
    lock_semantic_parser.add_argument("--receipt", type=Path, required=True)
    reuse_semantic_parser = sub.add_parser("reuse-semantic")
    reuse_semantic_parser.add_argument("--baseline-receipt", type=Path, required=True)
    reuse_semantic_parser.add_argument("--semantic-receipt", type=Path, required=True)
    reuse_semantic_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "lock":
        result = lock(args)
    elif args.command == "reuse":
        result = reuse(args)
    elif args.command == "lock-semantic":
        result = lock_semantic(args)
    else:
        result = reuse_semantic(args)
    print(
        json.dumps(
            {
                "status": result["status"],
                "mode": result["mode"],
                "semantic_record_count": result["semantic_record_count"],
                "model_calls": result.get("model_calls"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
