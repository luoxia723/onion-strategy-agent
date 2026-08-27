#!/usr/bin/env python3
"""
Package accepted image schemes from image-selection-result.json into one zip.

The zip is a local delivery artifact for operators. It contains only accepted
scheme images, with compressed JPGs by default. A manifest JSON is written next
to the zip for traceability, not inside the operator-facing zip.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import subprocess
import sys
import unicodedata
import zipfile
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
IMAGE_COMPRESS_SCRIPT = SCRIPT_DIR / "image_compress.py"
DEFAULT_TARGET_KB = 200
SAFE_COMPONENT_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
UNSAFE_DELIVERY_PATTERN = re.compile(r'[\\/:*?"<>|\r\n\t]+')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
KNOWN_SLUG_REPLACEMENTS = {
    "华为": "huawei",
    "大卡智投": "big-card",
    "应用商店": "app-store",
    "信息流": "feed",
    "学习机": "learning-device",
    "横版大图": "horizontal-big",
    "横版两图": "horizontal-double",
    "横版三图": "horizontal-triple",
    "单图": "single",
    "双图": "double",
    "三图": "triple",
}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("selection result must be a JSON object")
    return data


def load_optional_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    return load_json(path)


def resolve_image_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"image not found: {path}")
    return path


def scheme_image_paths(scheme: dict[str, Any], base_dir: Path) -> list[Path]:
    if isinstance(scheme.get("thumb"), list):
        values = scheme["thumb"]
    elif isinstance(scheme.get("images"), list):
        values = [item.get("path") if isinstance(item, dict) else item for item in scheme["images"]]
    else:
        values = []
    paths = [resolve_image_path(str(value), base_dir) for value in values if value]
    if not paths:
        raise ValueError(f"accepted scheme {scheme.get('set_id') or scheme.get('id')} has no images")
    return paths


def parse_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def scheme_export_config(scheme: dict[str, Any], default_target_kb: int, config: dict[str, Any] | None = None) -> dict[str, int | None]:
    meta = scheme.get("meta") if isinstance(scheme.get("meta"), dict) else {}
    placement = scheme.get("placement") if isinstance(scheme.get("placement"), dict) else {}
    snapshot = scheme_placement_snapshot(scheme, config or {}) if config else placement
    target_kb = (
        parse_int(scheme.get("target_kb"))
        or parse_int(meta.get("target_kb"))
        or parse_int(meta.get("目标KB"))
        or parse_int(snapshot.get("target_kb"))
        or parse_int(snapshot.get("max_file_size_kb"))
        or parse_int(snapshot.get("maxFileSizeKb"))
        or default_target_kb
    )
    target_width = (
        parse_int(scheme.get("target_width"))
        or parse_int(meta.get("target_width"))
        or parse_int(meta.get("目标宽度"))
        or parse_int(snapshot.get("target_width"))
    )
    target_height = (
        parse_int(scheme.get("target_height"))
        or parse_int(meta.get("target_height"))
        or parse_int(meta.get("目标高度"))
        or parse_int(snapshot.get("target_height"))
    )
    return {"target_kb": target_kb, "target_width": target_width, "target_height": target_height}


def compressed_path_for(source: Path, cache_dir: Path, set_id: str, target_kb: int, target_width: int | None = None, target_height: int | None = None) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    size_suffix = f".{target_width}x{target_height}" if target_width and target_height else ""
    return cache_dir / f"{safe_component(set_id, 'set')}-{safe_component(source.stem, 'image')}{size_suffix}.compressed-{target_kb}kb.jpg"


def compress_image(source: Path, output: Path, target_kb: int, target_width: int | None = None, target_height: int | None = None) -> None:
    command = [
            sys.executable,
            str(IMAGE_COMPRESS_SCRIPT),
            str(source),
            str(output),
            "--target-kb",
            str(target_kb),
        ]
    if target_width and target_height:
        command.extend(["--target-width", str(target_width), "--target-height", str(target_height)])
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "image compression failed").strip())


def safe_component(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lower()
    for raw, slug in KNOWN_SLUG_REPLACEMENTS.items():
        text = text.replace(raw.lower(), f" {slug} ")
    text = SAFE_COMPONENT_PATTERN.sub("-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    return text or fallback


def strip_zip_suffix(value: str) -> str:
    return re.sub(r"\.zip$", "", value.strip(), flags=re.IGNORECASE)


def safe_delivery_component(value: Any, fallback: str, strip_zip: bool = False, max_length: int = 120) -> str:
    text = unicodedata.normalize("NFC", str(value or "").strip())
    if strip_zip:
        text = strip_zip_suffix(text)
    text = UNSAFE_DELIVERY_PATTERN.sub("-", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip(" .-_")
    if not text:
        text = fallback
    if text.upper() in WINDOWS_RESERVED_NAMES:
        text = f"{text}-file"
    if len(text) > max_length:
        text = text[:max_length].rstrip(" .-_")
    return text or fallback


def delivery_stem_for(delivery_name: str) -> str:
    return safe_delivery_component(delivery_name, "delivery", strip_zip=True)


def delivery_zip_path_for(output_dir: Path, delivery_name: str) -> Path:
    return output_dir / f"{delivery_stem_for(delivery_name)}.zip"


def scheme_source(scheme: dict[str, Any]) -> dict[str, Any]:
    return scheme.get("source") if isinstance(scheme.get("source"), dict) else {}


def scheme_meta(scheme: dict[str, Any]) -> dict[str, Any]:
    return scheme.get("meta") if isinstance(scheme.get("meta"), dict) else {}


def value_from(containers: list[dict[str, Any]], *keys: str) -> Any:
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value is not None and str(value).strip() != "":
                return value
    return None


def config_placements_by_id(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    placements = config.get("placements")
    if not isinstance(placements, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for placement in placements:
        if not isinstance(placement, dict):
            continue
        for key in ("id", "slot_id", "placement_id"):
            value = placement.get(key)
            if value:
                result[str(value)] = placement
    return result


def scheme_placement_snapshot(scheme: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    meta = scheme_meta(scheme)
    placement = scheme.get("placement") if isinstance(scheme.get("placement"), dict) else {}
    placements_by_id = config_placements_by_id(config)
    ids = [
        scheme.get("placement_id"),
        scheme.get("slot_id"),
        scheme.get("id"),
        meta.get("placement_id"),
        meta.get("slot_id"),
        meta.get("id"),
        placement.get("id"),
    ]
    base: dict[str, Any] = {}
    for candidate in ids:
        if candidate and str(candidate) in placements_by_id:
            base = placements_by_id[str(candidate)]
            break
    snapshot: dict[str, Any] = {}
    snapshot.update(base)
    snapshot.update(placement)
    snapshot.update(meta)
    for key in ("placement_id", "slot_id"):
        if scheme.get(key):
            snapshot[key] = scheme.get(key)
    return snapshot


def target_size_from(export: dict[str, int | None], snapshot: dict[str, Any]) -> str:
    explicit = value_from([snapshot], "target_size", "targetSize", "目标尺寸")
    if explicit:
        return str(explicit)
    width = export.get("target_width") or parse_int(value_from([snapshot], "target_width", "targetWidth", "目标宽度"))
    height = export.get("target_height") or parse_int(value_from([snapshot], "target_height", "targetHeight", "目标高度"))
    return f"{width}x{height}" if width and height else ""


def placement_info_for(scheme: dict[str, Any], config: dict[str, Any], export: dict[str, int | None]) -> dict[str, str]:
    snapshot = scheme_placement_snapshot(scheme, config)
    containers = [snapshot, scheme_meta(scheme), scheme]
    category = str(value_from(containers, "category", "大类", "channel", "渠道") or "其他")
    platform = str(value_from(containers, "platform", "平台") or "通用")
    placement = str(value_from(containers, "placement", "版位", "name", "placement_name") or "默认版位")
    slot_id = str(value_from(containers, "placement_id", "slot_id", "id") or "")
    image_form = str(value_from(containers, "image_form", "imageForm", "图片形式", "form") or "")
    target_size = target_size_from(export, snapshot)
    return {
        "category": category,
        "platform": platform,
        "placement": placement,
        "slot_id": slot_id,
        "image_form": image_form,
        "target_size": target_size,
    }


def scheme_copy_id(scheme: dict[str, Any]) -> str:
    source = scheme_source(scheme)
    meta = scheme_meta(scheme)
    for container in (source, meta, scheme):
        for key in ("copyId", "copy_id", "文案ID", "copy"):
            value = str(container.get(key) or "").strip()
            if value:
                return value
    return "copy"


def scheme_copy_record_id(scheme: dict[str, Any]) -> str:
    source = scheme_source(scheme)
    meta = scheme_meta(scheme)
    for container in (source, meta, scheme):
        for key in ("copyRecordId", "copy_record_id", "文案record_id", "文案记录ID"):
            value = str(container.get(key) or "").strip()
            if value:
                return value
    return ""


def scheme_placement_name(scheme: dict[str, Any]) -> str:
    meta = scheme_meta(scheme)
    placement = scheme.get("placement") if isinstance(scheme.get("placement"), dict) else {}
    for container in (meta, placement, scheme):
        for key in ("placement", "版位", "name", "placement_name"):
            value = str(container.get(key) or "").strip()
            if value:
                return value
    return "placement"


def archive_dir_name(request_id: str, set_id: str, scheme: dict[str, Any]) -> str:
    return "_".join(
        [
            safe_component(request_id, "request"),
            safe_component(scheme_copy_id(scheme), "copy"),
            safe_component(set_id, "set"),
            safe_component(scheme_placement_name(scheme), "placement"),
        ]
    )


def archive_file_name(
    set_position: int,
    image_index: int,
    suffix: str,
    target_kb: int | None = None,
    target_width: int | None = None,
    target_height: int | None = None,
) -> str:
    parts = [f"set{set_position:02d}", f"img{image_index:02d}"]
    if target_width and target_height:
        parts.append(f"{target_width}x{target_height}")
    if target_kb:
        parts.append(f"{target_kb}kb")
    normalized_suffix = suffix.lower() or ".jpg"
    return "_".join(parts) + normalized_suffix


def manifest_path_for(output: Path) -> Path:
    return output.with_name(f"{output.stem}-manifest.json")


def cleanup_temp_artifacts(*paths: Path) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def resolve_config_path(selection_result: Path, explicit: Path | None = None) -> Path | None:
    if explicit:
        return explicit.expanduser().resolve()
    candidate = selection_result.resolve().parent / "image-config-result.json"
    return candidate if candidate.is_file() else None


def resolve_delivery_name(selection: dict[str, Any], config: dict[str, Any], override: str | None = None) -> str:
    for value in (
        override,
        config.get("delivery_name"),
        config.get("deliveryName"),
        config.get("direction_name"),
        config.get("directionName"),
        selection.get("delivery_name"),
        selection.get("deliveryName"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    raise ValueError("delivery_name is required; run image config page first or pass --delivery-name")


def placement_identity(info: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        info.get("category") or "其他",
        info.get("platform") or "通用",
        info.get("placement") or "默认版位",
        info.get("target_size") or "",
        info.get("image_form") or "",
    )


def folder_base_for(delivery_stem: str, info: dict[str, str], include_size: bool) -> str:
    parts = [
        delivery_stem,
        safe_delivery_component(info.get("category"), "其他"),
        safe_delivery_component(info.get("platform"), "通用"),
        safe_delivery_component(info.get("placement"), "默认版位"),
    ]
    if include_size and info.get("target_size"):
        parts.append(safe_delivery_component(info["target_size"], "size"))
    return "-".join(parts)


def resolve_folder_names(packets: list[dict[str, Any]], delivery_stem: str) -> dict[tuple[str, str, str, str, str], str]:
    display_sizes: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    identities: list[tuple[str, str, str, str, str]] = []
    info_by_identity: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
    for packet in packets:
        info = packet["placement_info"]
        display_key = (info["category"], info["platform"], info["placement"])
        display_sizes[display_key].add(info.get("target_size") or "")
        identity = placement_identity(info)
        if identity not in info_by_identity:
            identities.append(identity)
            info_by_identity[identity] = info

    used: dict[str, tuple[str, str, str, str, str]] = {}
    folders: dict[tuple[str, str, str, str, str], str] = {}
    for identity in identities:
        info = info_by_identity[identity]
        display_key = (info["category"], info["platform"], info["placement"])
        non_empty_sizes = {size for size in display_sizes[display_key] if size}
        candidate = folder_base_for(delivery_stem, info, include_size=len(non_empty_sizes) > 1)
        if candidate in used and used[candidate] != identity:
            image_form = safe_delivery_component(info.get("image_form"), "form")
            candidate = f"{candidate}-{image_form}"
        if candidate in used and used[candidate] != identity and info.get("slot_id"):
            candidate = f"{candidate}-{safe_delivery_component(info['slot_id'], 'slot')}"
        suffix = 2
        original = candidate
        while candidate in used and used[candidate] != identity:
            candidate = f"{original}-{suffix}"
            suffix += 1
        used[candidate] = identity
        folders[identity] = candidate
    return folders


def package_accepted_images(
    selection_result: Path,
    output: Path | None = None,
    target_kb: int = DEFAULT_TARGET_KB,
    compress: bool = True,
    config_result: Path | None = None,
    delivery_name: str | None = None,
) -> dict[str, Any]:
    base_dir = selection_result.resolve().parent
    data = load_json(selection_result)
    config_path = resolve_config_path(selection_result, config_result)
    config = load_optional_json(config_path)
    delivery_name_raw = resolve_delivery_name(data, config, delivery_name)
    delivery_stem = delivery_stem_for(delivery_name_raw)
    accepted = data.get("accepted_schemes") or []
    if not isinstance(accepted, list) or not accepted:
        raise ValueError("selection result has no accepted_schemes to package")

    request_id = str(data.get("request_id") or base_dir.name)
    output = output or delivery_zip_path_for(base_dir, delivery_name_raw)
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cache_dir = output.parent / "accepted-package-assets"
    manifest_path = manifest_path_for(output)

    packets: list[dict[str, Any]] = []
    for position, scheme in enumerate(accepted, start=1):
        if not isinstance(scheme, dict):
            raise ValueError("accepted_schemes entries must be objects")
        export = scheme_export_config(scheme, target_kb, config)
        info = placement_info_for(scheme, config, export)
        packets.append(
            {
                "position": position,
                "scheme": scheme,
                "set_id": str(scheme.get("set_id") or scheme.get("id") or f"set{position}"),
                "export": export,
                "placement_info": info,
                "image_paths": scheme_image_paths(scheme, base_dir),
            }
        )

    folders = resolve_folder_names(packets, delivery_stem)
    counters: dict[str, int] = defaultdict(int)

    package_manifest: dict[str, Any] = {
        "request_id": request_id,
        "delivery_name": delivery_name_raw,
        "delivery_name_safe": delivery_stem,
        "zip": str(output),
        "manifest_path": str(manifest_path),
        "config_result": str(config_path) if config_path else None,
        "target_kb": target_kb if compress else None,
        "compressed": compress,
        "accepted_count": len(accepted),
        "schemes": [],
    }

    temp_output = output.with_suffix(output.suffix + ".tmp")
    temp_manifest = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    cleanup_temp_artifacts(temp_output, temp_manifest)

    try:
        with zipfile.ZipFile(temp_output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for packet in packets:
                scheme = packet["scheme"]
                set_id = packet["set_id"]
                export = packet["export"]
                info = packet["placement_info"]
                archive_dir = folders[placement_identity(info)]
                scheme_target_kb = int(export["target_kb"] or target_kb)
                target_width = export["target_width"]
                target_height = export["target_height"]
                scheme_entry = {
                    "set_id": set_id,
                    "copy_id": scheme_copy_id(scheme),
                    "copy_record_id": scheme_copy_record_id(scheme),
                    "category": info["category"],
                    "platform": info["platform"],
                    "placement": info["placement"],
                    "slot_id": info["slot_id"],
                    "image_form": info["image_form"],
                    "target_size": info["target_size"],
                    "meta": scheme.get("meta") or {},
                    "source": scheme.get("source") or {},
                    "export": export,
                    "files": [],
                }
                for image_index, source in enumerate(packet["image_paths"], start=1):
                    if compress:
                        packaged = compressed_path_for(source, cache_dir, set_id, scheme_target_kb, target_width, target_height)
                        if not packaged.exists():
                            compress_image(source, packaged, scheme_target_kb, target_width, target_height)
                    else:
                        packaged = source
                    counters[archive_dir] += 1
                    suffix = ".jpg" if compress else (packaged.suffix.lower() or ".png")
                    file_name = f"{archive_dir}-{counters[archive_dir]}{suffix}"
                    arcname = f"{archive_dir}/{file_name}"
                    archive.write(packaged, arcname)
                    scheme_entry["files"].append(
                        {
                            "index": image_index,
                            "delivery_index": counters[archive_dir],
                            "source": str(source),
                            "packaged": str(packaged),
                            "zip_path": arcname,
                        }
                    )
                package_manifest["schemes"].append(scheme_entry)

        temp_manifest.write_text(json.dumps(package_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_output.replace(output)
        temp_manifest.replace(manifest_path)
    except Exception:
        cleanup_temp_artifacts(temp_output, temp_manifest, output, manifest_path)
        raise

    return {
        "ok": True,
        "zip": str(output),
        "manifest_path": str(manifest_path),
        "delivery_name": delivery_name_raw,
        "delivery_name_safe": delivery_stem,
        "accepted_count": len(accepted),
        "compressed": compress,
        "target_kb": target_kb if compress else None,
        "manifest": package_manifest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package accepted onion image schemes into one zip.")
    parser.add_argument("--selection-result", required=True, help="Path to image-selection-result.json")
    parser.add_argument("--config-result", help="Path to image-config-result.json; defaults to sibling file")
    parser.add_argument("--delivery-name", help="Maintenance override for delivery zip/folder/file name")
    parser.add_argument("--output", help="Output zip path; defaults to <delivery_name>.zip beside selection result")
    parser.add_argument("--target-kb", type=int, default=DEFAULT_TARGET_KB)
    parser.add_argument("--no-compress", action="store_true", help="Package original files instead of compressed JPGs")
    args = parser.parse_args(argv)

    try:
        result = package_accepted_images(
            selection_result=Path(args.selection_result),
            output=Path(args.output) if args.output else None,
            target_kb=args.target_kb,
            compress=not args.no_compress,
            config_result=Path(args.config_result) if args.config_result else None,
            delivery_name=args.delivery_name,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
