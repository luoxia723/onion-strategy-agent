#!/usr/bin/env python3
"""Freeze complete onion-intelligence MCP report inputs without echoing page bodies.

The command writes private JSONL artifacts under the caller-selected runtime
directory and prints only a compact receipt.  Model-facing agents therefore do
not need to place every MCP page in their conversation context.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable


SNAPSHOT_SCHEMA = "intelligence_report_input_snapshot_v1"
DEFAULT_URL = "https://intel-mcp.guanghexinzhi.cn/mcp"
CallTool = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_private_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _write_json(path: Path, value: Any) -> None:
    _write_private_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    _write_private_text(
        path,
        "".join(canonical_json(row) + "\n" for row in rows),
    )


def _record_id(mode: str, item: dict[str, Any]) -> str:
    if mode in {"external-demand", "external-creative"}:
        value = item.get("analysis_object_id")
    else:
        value = item.get("material_key") or item.get("pool_sort_key")
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{mode}记录缺少稳定身份")
    return normalized


def _record_hashes(mode: str, rows: list[dict[str, Any]]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for row in rows:
        record_id = _record_id(mode, row)
        if record_id in hashes:
            raise ValueError(f"{mode}冻结输入包含重复稳定身份: {record_id}")
        hashes[record_id] = sha256_text(canonical_json(row))
    return hashes


def _require_ok(payload: dict[str, Any], tool_name: str) -> None:
    if payload.get("result_status") == "error":
        raise RuntimeError(
            f"{tool_name}失败: {payload.get('error_code')}: "
            f"{payload.get('error_message')}"
        )


async def freeze_external(
    *,
    call_tool: CallTool,
    mode: str,
    report_triggered_at: str,
    evidence_start_at: str,
    evidence_end_at: str,
    output_dir: Path,
    page_size: int = 500,
) -> dict[str, Any]:
    report_type = "demand" if mode == "external-demand" else "creative"
    scope = await call_tool(
        "intelligence_get_report_scope",
        {
            "report_type": report_type,
            "report_triggered_at": report_triggered_at,
            "evidence_start_at": evidence_start_at,
            "evidence_end_at": evidence_end_at,
        },
    )
    _require_ok(scope, "intelligence_get_report_scope")
    scope_id = str(scope.get("report_scope_id") or "")
    if not scope_id:
        raise RuntimeError("报告范围没有返回report_scope_id")

    if mode == "external-demand":
        tool_name = "intelligence_list_demand_evidence"
        item_field = "demand_evidence_items"
        arguments: dict[str, Any] = {
            "report_scope_id": scope_id,
            "include_comment_insights": True,
            "page_size": page_size,
        }
    else:
        tool_name = "intelligence_list_creative_evidence"
        item_field = "creative_evidence_items"
        arguments = {
            "report_scope_id": scope_id,
            "media_types": ["video"],
            "education_classifications": ["education"],
            "education_scopes": ["k12", "mixed"],
            "creative_object_types": ["creative_insight"],
            # 创意记录单条较大；服务虽允许500，但100是已验证的稳定响应体。
            # 页数只发生在本程序内部，不会增加外层模型上下文。
            "page_size": min(page_size, 100),
        }

    rows: list[dict[str, Any]] = []
    page_receipts: list[dict[str, Any]] = []
    cursor: str | None = None
    expected_total: int | None = None
    while True:
        page_args = {**arguments}
        if cursor:
            page_args["cursor"] = cursor
        page = await call_tool(tool_name, page_args)
        _require_ok(page, tool_name)
        if page.get("coverage_status") not in {"complete", "partial"}:
            raise RuntimeError(f"{tool_name}返回未知coverage_status")
        total = int(page.get("total_count") or 0)
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise RuntimeError(f"{tool_name}分页期间total_count变化")
        page_rows = page.get(item_field) or []
        if not isinstance(page_rows, list):
            raise RuntimeError(f"{tool_name}.{item_field}不是数组")
        rows.extend(page_rows)
        page_receipts.append(
            {
                "page": len(page_receipts) + 1,
                "result_count": len(page_rows),
                "has_more": bool(page.get("has_more")),
            }
        )
        if not page.get("has_more"):
            break
        cursor = str(page.get("next_cursor") or "")
        if not cursor:
            raise RuntimeError(f"{tool_name}声明has_more但没有next_cursor")

    if expected_total is None or len(rows) != expected_total:
        raise RuntimeError(
            f"{tool_name}分页不守恒: expected={expected_total}, actual={len(rows)}"
        )
    hashes = _record_hashes(mode, rows)
    items_path = output_dir / "items.jsonl"
    hashes_path = output_dir / "record_hashes.json"
    scope_path = output_dir / "scope.json"
    _write_jsonl(items_path, rows)
    _write_json(hashes_path, hashes)
    _write_json(scope_path, scope)
    manifest = {
        "schema_version": SNAPSHOT_SCHEMA,
        "mode": mode,
        "source": DEFAULT_URL,
        "tool": tool_name,
        "report_type": report_type,
        "record_count": len(rows),
        "unique_record_count": len(hashes),
        "unique_material_context_count": len(
            {str(row.get("material_context_id")) for row in rows}
        ),
        "page_count": len(page_receipts),
        "page_receipts": page_receipts,
        "coverage_status": scope.get("coverage_status"),
        "coverage_notices": scope.get("coverage_notices") or [],
        "scope_manifest_hash": scope.get("scope_manifest_hash"),
        "scope_file": scope_path.name,
        "items_file": items_path.name,
        "items_sha256": sha256_text(items_path.read_text(encoding="utf-8")),
        "record_hashes_file": hashes_path.name,
        "record_hashes_sha256": sha256_text(
            hashes_path.read_text(encoding="utf-8")
        ),
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


async def freeze_internal(
    *,
    call_tool: CallTool,
    statistics_start_date: str,
    statistics_end_date: str,
    output_dir: Path,
    page_size: int = 100,
) -> dict[str, Any]:
    all_rows: list[dict[str, Any]] = []
    business_receipts: list[dict[str, Any]] = []
    for business_line in ("app", "lead"):
        cursor: str | None = None
        dataset_version: str | None = None
        page_count = 0
        pool_summary: dict[str, Any] = {}
        periods: list[dict[str, Any]] = []
        business_rows: list[dict[str, Any]] = []
        while True:
            arguments: dict[str, Any] = {
                "business_line": business_line,
                "period_mode": "explicit",
                "statistics_start_date": statistics_start_date,
                "statistics_end_date": statistics_end_date,
                "selection_rate": 0.20,
                "minimum_material_spend_amount": 800,
                "page_size": page_size,
            }
            if cursor:
                arguments["cursor"] = cursor
            page = await call_tool(
                "intelligence_list_internal_complete_material_pool",
                arguments,
            )
            _require_ok(page, "intelligence_list_internal_complete_material_pool")
            if page.get("date_confirmation_required"):
                raise RuntimeError(f"{business_line}统计周期需要人工确认")
            page_version = str(page.get("dataset_version") or "")
            if not page_version:
                raise RuntimeError(f"{business_line}没有dataset_version")
            if dataset_version is None:
                dataset_version = page_version
                pool_summary = page.get("pool_summary") or {}
                periods = page.get("included_statistics_periods") or []
            elif page_version != dataset_version:
                raise RuntimeError(f"{business_line}分页期间dataset_version变化")
            page_rows = page.get("internal_complete_material_items") or []
            if not isinstance(page_rows, list):
                raise RuntimeError("internal_complete_material_items不是数组")
            business_rows.extend(page_rows)
            page_count += 1
            pagination = page.get("pagination") or {}
            if not pagination.get("has_more"):
                break
            cursor = str(pagination.get("next_cursor") or "")
            if not cursor:
                raise RuntimeError(f"{business_line}声明has_more但没有next_cursor")
        expected = int(pool_summary.get("base_pool_material_count") or 0)
        if len(business_rows) != expected:
            raise RuntimeError(
                f"{business_line}基础素材池不守恒: expected={expected}, "
                f"actual={len(business_rows)}"
            )
        all_rows.extend(business_rows)
        business_receipts.append(
            {
                "business_line": business_line,
                "dataset_version": dataset_version,
                "page_count": page_count,
                "record_count": len(business_rows),
                "included_statistics_periods": periods,
                "pool_summary": pool_summary,
            }
        )

    hashes = _record_hashes("internal-complete", all_rows)
    items_path = output_dir / "items.jsonl"
    hashes_path = output_dir / "record_hashes.json"
    _write_jsonl(items_path, all_rows)
    _write_json(hashes_path, hashes)
    manifest = {
        "schema_version": SNAPSHOT_SCHEMA,
        "mode": "internal-complete",
        "source": DEFAULT_URL,
        "tool": "intelligence_list_internal_complete_material_pool",
        "statistics_start_date": statistics_start_date,
        "statistics_end_date": statistics_end_date,
        "record_count": len(all_rows),
        "unique_record_count": len(hashes),
        "business_lines": business_receipts,
        "items_file": items_path.name,
        "items_sha256": sha256_text(items_path.read_text(encoding="utf-8")),
        "record_hashes_file": hashes_path.name,
        "record_hashes_sha256": sha256_text(
            hashes_path.read_text(encoding="utf-8")
        ),
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def compare_hashes(current: dict[str, str], previous: dict[str, str]) -> dict[str, Any]:
    current_ids = set(current)
    previous_ids = set(previous)
    added = sorted(current_ids - previous_ids)
    deleted = sorted(previous_ids - current_ids)
    changed = sorted(
        record_id
        for record_id in current_ids & previous_ids
        if current[record_id] != previous[record_id]
    )
    unchanged = sorted((current_ids & previous_ids) - set(changed))
    return {
        "added_record_ids": added,
        "changed_record_ids": changed,
        "deleted_record_ids": deleted,
        "unchanged_record_ids": unchanged,
        "added_count": len(added),
        "changed_count": len(changed),
        "deleted_count": len(deleted),
        "unchanged_count": len(unchanged),
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON根对象必须是object: {path}")
    return value


def write_delta(output_dir: Path, previous_manifest_path: Path) -> dict[str, Any]:
    current_manifest = _load_json(output_dir / "manifest.json")
    previous_manifest = _load_json(previous_manifest_path)
    if current_manifest.get("mode") != previous_manifest.get("mode"):
        raise ValueError("前后快照mode不一致")
    current_hashes = _load_json(output_dir / current_manifest["record_hashes_file"])
    previous_hashes = _load_json(
        previous_manifest_path.parent / previous_manifest["record_hashes_file"]
    )
    delta = {
        "schema_version": "intelligence_report_input_delta_v1",
        "mode": current_manifest["mode"],
        "previous_manifest": str(previous_manifest_path.resolve()),
        "current_manifest": str((output_dir / "manifest.json").resolve()),
        **compare_hashes(current_hashes, previous_hashes),
    }
    _write_json(output_dir / "delta.json", delta)
    return delta


@asynccontextmanager
async def mcp_call_tool(
    *, url: str,
    token: str,
    client_id: str,
    timeout_seconds: float,
) -> AsyncIterator[CallTool]:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Intelligence-Client-Id": client_id,
    }
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as http_client:
        async with streamable_http_client(url, http_client=http_client) as streams:
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                async def invoke(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                    result = await session.call_tool(name, arguments)
                    structured = getattr(result, "structuredContent", None)
                    if structured is None:
                        structured = getattr(result, "structured_content", None)
                    if isinstance(structured, dict):
                        return structured
                    content_blocks = getattr(result, "content", [])
                    if getattr(result, "isError", False) or getattr(
                        result, "is_error", False
                    ):
                        messages = [
                            str(getattr(block, "text", ""))[:500]
                            for block in content_blocks
                        ]
                        raise RuntimeError(f"{name}返回错误: {' | '.join(messages)}")
                    for block in content_blocks:
                        text = getattr(block, "text", None)
                        if isinstance(text, str):
                            try:
                                value = json.loads(text)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(value, dict):
                                return value
                    raise RuntimeError(f"{name}没有返回结构化JSON")

                yield invoke


async def run(args: argparse.Namespace) -> dict[str, Any]:
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        raise RuntimeError(f"环境变量{args.token_env}未设置")
    output_dir: Path = args.output_dir
    if (output_dir / "manifest.json").exists():
        raise RuntimeError(f"输出目录已有manifest，不覆盖旧快照: {output_dir}")
    async with mcp_call_tool(
        url=args.url,
        token=token,
        client_id=args.client_id,
        timeout_seconds=args.timeout_seconds,
    ) as call_tool:
        if args.mode in {"external-demand", "external-creative"}:
            manifest = await freeze_external(
                call_tool=call_tool,
                mode=args.mode,
                report_triggered_at=args.report_triggered_at,
                evidence_start_at=args.evidence_start_at,
                evidence_end_at=args.evidence_end_at,
                output_dir=output_dir,
            )
        else:
            manifest = await freeze_internal(
                call_tool=call_tool,
                statistics_start_date=args.statistics_start_date,
                statistics_end_date=args.statistics_end_date,
                output_dir=output_dir,
            )
    if args.previous_manifest:
        manifest["delta"] = write_delta(output_dir, args.previous_manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        required=True,
        choices=("external-demand", "external-creative", "internal-complete"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--token-env", default="ONION_INTELLIGENCE_MCP_TOKEN")
    parser.add_argument("--client-id", default="codex")
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--report-triggered-at")
    parser.add_argument("--evidence-start-at")
    parser.add_argument("--evidence-end-at")
    parser.add_argument("--statistics-start-date")
    parser.add_argument("--statistics-end-date")
    parser.add_argument("--previous-manifest", type=Path)
    args = parser.parse_args()
    if args.mode in {"external-demand", "external-creative"}:
        required = (
            args.report_triggered_at,
            args.evidence_start_at,
            args.evidence_end_at,
        )
        if not all(required):
            parser.error("外部模式必须传报告触发和证据起止时间")
    elif not args.statistics_start_date or not args.statistics_end_date:
        parser.error("内部模式必须传统计起止日期")
    manifest = asyncio.run(run(args))
    delta = manifest.get("delta")
    delta_receipt = None
    if isinstance(delta, dict):
        delta_receipt = {
            key: value
            for key, value in delta.items()
            if key.endswith("_count")
        }
    print(
        json.dumps(
            {
                "status": "complete",
                "mode": manifest["mode"],
                "record_count": manifest["record_count"],
                "unique_record_count": manifest["unique_record_count"],
                "manifest": str((args.output_dir / "manifest.json").resolve()),
                "items_sha256": manifest["items_sha256"],
                "delta": delta_receipt,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
