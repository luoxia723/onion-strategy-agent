#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SUBJECT = {"type": "string", "enum": ["student", "parent", "other"]}
BUSINESS = {"type": "string", "enum": ["app", "lead"]}
STRING = {"type": "string", "minLength": 1}
STRING_ARRAY = {"type": "array", "items": {"type": "string"}}


def _group_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["label", "member_unit_ids"],
        "properties": {
            "label": STRING,
            "member_unit_ids": STRING_ARRAY,
        },
    }


def _common_cluster_properties() -> dict[str, Any]:
    return {
        "business_line": BUSINESS,
        "demand_subject": SUBJECT,
        "canonical_name": STRING,
        "task_scene": STRING,
        "core_problem": STRING,
        "desired_change": STRING,
        "problem_expression_groups": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": _group_schema(),
        },
        "current_coping_groups": {
            "type": "array",
            "maxItems": 4,
            "items": _group_schema(),
        },
        "center_unit_ids": STRING_ARRAY,
        "boundary_unit_ids": STRING_ARRAY,
        "merge_reason": STRING,
        "split_boundary": STRING,
    }


def response_schema(stage: str) -> dict[str, Any]:
    common = _common_cluster_properties()
    if stage == "batch_clustering":
        cluster_properties = {
            "candidate_cluster_id": STRING,
            **common,
            "member_unit_ids": STRING_ARRAY,
        }
        cluster_required = [
            "candidate_cluster_id",
            *common,
            "member_unit_ids",
        ]
        schema_version = "internal_demand_batch_clusters_v1"
    elif stage == "cross_batch_clustering":
        cluster_properties = {
            "final_cluster_key": STRING,
            **common,
            "member_candidate_cluster_ids": STRING_ARRAY,
        }
        cluster_required = [
            "final_cluster_key",
            *common,
            "member_candidate_cluster_ids",
        ]
        schema_version = "internal_demand_cross_batch_clusters_v1"
    else:
        raise ValueError(f"不支持的模型任务阶段: {stage}")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "job_id", "clusters"],
        "properties": {
            "schema_version": {"type": "string", "const": schema_version},
            "job_id": STRING,
            "clusters": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": cluster_required,
                    "properties": cluster_properties,
                },
            },
        },
    }


def prompt_for(request: dict[str, Any]) -> str:
    return """你是内部需求报告Skill的语义归并执行模型。

只处理下面JSON任务，不访问文件、不调用工具、不补充外部事实。严格执行任务中的instructions和output_contract：

- 每个输入需求单元或候选簇必须且只能分配一次；
- 允许根据具体场景和问题修正需求主体，但不能改变业务线；
- 不能因为产品、功能、关键词或高表现状态相同而合并；
- 不清楚时宁可保留更窄的簇；
- problem_expression_groups必须完整且不重复覆盖成员；
- current_coping_groups必须完整且不重复覆盖所有current_coping非空的成员，不能漏掉任何一条；
- 返回前逐项自检：输入ID全集必须等于各簇成员ID并集且无重复；每簇的问题表达组ID必须等于该簇成员ID；每簇的当前应对组ID必须等于该簇中current_coping非空的成员ID；
- 只返回JSON，不写说明文字。

任务JSON：
""" + json.dumps(request, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    request = json.load(sys.stdin)
    if not isinstance(request, dict):
        raise SystemExit("模型任务必须是JSON object")
    stage = str(request.get("stage") or "")
    model = os.environ.get("ONION_SEMANTIC_CODEX_MODEL", "gpt-5.6-sol")
    effort = os.environ.get("ONION_SEMANTIC_CODEX_REASONING_EFFORT", "high")
    repo_root = Path(__file__).resolve().parents[4]
    with tempfile.TemporaryDirectory(prefix="onion-semantic-codex-") as temporary:
        temporary_path = Path(temporary)
        schema_path = temporary_path / "response.schema.json"
        output_path = temporary_path / "response.json"
        schema_path.write_text(
            json.dumps(response_schema(stage), ensure_ascii=False),
            encoding="utf-8",
        )
        command = [
            "codex",
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--model",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "--sandbox",
            "read-only",
            "--cd",
            str(repo_root),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "--color",
            "never",
            "-",
        ]
        result = subprocess.run(
            command,
            input=prompt_for(request),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            sys.stderr.write(result.stderr)
            return result.returncode
        try:
            response = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit(f"Codex没有生成有效JSON: {error}") from error
        json.dump(response, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
