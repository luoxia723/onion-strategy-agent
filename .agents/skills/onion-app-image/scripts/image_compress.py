#!/usr/bin/env python3
"""
image_compress.py - Pillow 压缩图片到目标 KB。

# 使用示例

  python3 image_compress.py \\
    /tmp/in.png /tmp/out.jpg \\
    --target-kb 200

# 策略

  1. 若传 target_width/target_height，先按 cover 居中裁切到目标尺寸
  2. 再转 JPG（去 alpha），初始 quality=85
  3. 若 > target_kb，逐步降 quality 步长 5，直到达标 / quality 降到 60
  4. 若 quality=60 还超 → 等比缩小 0.9 倍再试
  5. 最多 10 轮

# 退出码

  0: 成功
  1: 输入文件不存在 / 不可读
  2: 压缩失败（最终仍超 target_kb 2x 以上）
"""

import argparse
import os
import sys
from typing import Optional

try:
    from PIL import Image
except ImportError:
    print("❌ 需要安装 Pillow：pip install Pillow", file=sys.stderr)
    sys.exit(1)


def resize_cover(img: Image.Image, target_width: int, target_height: int) -> Image.Image:
    """Resize to cover target dimensions, then center-crop exactly."""
    if target_width <= 0 or target_height <= 0:
        raise ValueError("target width/height must be positive")
    scale = max(target_width / img.width, target_height / img.height)
    resized = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left = max(0, (resized.width - target_width) // 2)
    top = max(0, (resized.height - target_height) // 2)
    return resized.crop((left, top, left + target_width, top + target_height))


def compress(
    input_path: str,
    output_path: str,
    target_kb: int = 200,
    target_width: Optional[int] = None,
    target_height: Optional[int] = None,
) -> str:
    """压缩到目标 KB。返回最终输出路径。"""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input not found: {input_path}")

    img = Image.open(input_path).convert("RGB")  # PNG → RGB（去 alpha）
    if target_width and target_height:
        img = resize_cover(img, target_width, target_height)
    quality = 85
    scale = 1.0

    for round_no in range(1, 11):
        # 缩放
        if scale < 1.0:
            new_size = (int(img.width * scale), int(img.height * scale))
            scaled = img.resize(new_size, Image.LANCZOS)
        else:
            scaled = img

        # 尝试不同 quality
        while quality >= 60:
            scaled.save(output_path, "JPEG", quality=quality, optimize=True)
            size_kb = os.path.getsize(output_path) / 1024
            if size_kb <= target_kb:
                print(f"✅ {input_path} → {output_path} ({scaled.width}x{scaled.height}, {size_kb:.1f} KB, q={quality}, scale={scale:.2f})")
                return output_path
            quality -= 5

        # 还超 → 缩小
        scale *= 0.9
        quality = 85

    # 10 轮还超 → 接受最终值
    size_kb = os.path.getsize(output_path) / 1024
    print(f"⚠️ {input_path} → {output_path} ({size_kb:.1f} KB, 最终仍超目标)")
    return output_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="输入图片路径")
    parser.add_argument("output", help="输出图片路径（.jpg）")
    parser.add_argument("--target-kb", type=int, default=200, help="目标大小（KB）")
    parser.add_argument("--target-width", type=int, help="导出目标宽度")
    parser.add_argument("--target-height", type=int, help="导出目标高度")
    args = parser.parse_args()

    try:
        compress(args.input, args.output, args.target_kb, args.target_width, args.target_height)
        sys.exit(0)
    except FileNotFoundError as e:
        print(f"🔴 {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"🔴 压缩失败：{e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
