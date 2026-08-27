#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path, PurePosixPath


ALLOWED = {"manifest.json", "items.jsonl", "record_hashes.json", "scope.json"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="校验并展开onion-agent返回的报告私有输入快照",
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()

    archive = args.archive.resolve()
    if not archive.is_file():
        raise SystemExit("快照ZIP不存在")
    archive_bytes = archive.read_bytes()
    archive_sha = sha256(archive_bytes)
    if args.expected_sha256 and archive_sha != args.expected_sha256:
        raise SystemExit("快照ZIP SHA-256不匹配")
    output = args.output_dir.resolve()
    if (output / "manifest.json").exists():
        raise SystemExit("输出目录已有manifest，不覆盖旧快照")

    with zipfile.ZipFile(archive) as source:
        names = set(source.namelist())
        if "manifest.json" not in names or not names <= ALLOWED:
            raise SystemExit("快照ZIP文件集不符合合同")
        for info in source.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
                raise SystemExit("快照ZIP包含非法路径")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise SystemExit("快照ZIP不允许符号链接")
        files = {name: source.read(name) for name in names}

    manifest = json.loads(files["manifest.json"])
    if manifest.get("schema_version") != "intelligence_report_input_snapshot_v1":
        raise SystemExit("快照schema_version不符合")
    items_name = str(manifest.get("items_file") or "")
    hashes_name = str(manifest.get("record_hashes_file") or "")
    if items_name not in files or hashes_name not in files:
        raise SystemExit("快照缺少items或record_hashes")
    if sha256(files[items_name]) != manifest.get("items_sha256"):
        raise SystemExit("快照items摘要不一致")
    if sha256(files[hashes_name]) != manifest.get("record_hashes_sha256"):
        raise SystemExit("快照record_hashes摘要不一致")
    row_count = len([line for line in files[items_name].splitlines() if line.strip()])
    if row_count != int(manifest.get("record_count") or -1):
        raise SystemExit("快照record_count不守恒")

    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    for name, data in files.items():
        target = output / name
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    print(
        json.dumps(
            {
                "status": "complete",
                "mode": manifest["mode"],
                "record_count": row_count,
                "unique_record_count": manifest["unique_record_count"],
                "archive_sha256": archive_sha,
                "manifest": str(output / "manifest.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
