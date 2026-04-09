#!/usr/bin/env python3
"""
Qwen-VL OCR 脚本：对单张图片调用 DashScope 视觉语言模型进行文字识别或图像理解。

使用 OpenAI 兼容接口（openai 库 + DashScope base_url）。image 可为：① 标准 S3 或可公网访问的图片 URL；
② 本地图片文件路径（脚本会读取并转为 base64 data URL 发送）。不依赖 LangChain。

依赖：openai（pip install openai）。配置优先从环境变量读取：QWEN_VL_BASE_URL、QWEN_VL_MODEL_NAME、QWEN_VL_API_KEY（与 config-boe.yaml 等一致）。
"""
import argparse
import base64
import logging
import os
import sys
from pathlib import Path
from typing import Any, cast

from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# 默认 OCR 提示（可覆盖）
DEFAULT_OCR_PROMPT = (
    "请识别图片中的所有文字，按版面从上到下、从左到右的顺序逐行输出。"
    "若包含表格，尽量保持行列结构；若有手写或印章，一并识别。仅输出识别结果，不要解释。"
)

# DashScope VL 图像像素阈值（与官方示例一致）
MIN_PIXELS = 32 * 32 * 3
MAX_PIXELS = 32 * 32 * 8192

# 写死的默认配置值
BASE_URL: str = "http://10.6.128.115:31202/v1"
MODEL_NAME: str = "qwen3.5-397b-fp8"
API_KEY: str = "your_api_key_here"  # 请替换为实际的 API Key

def _is_image_url(s: str) -> bool:
    """判断是否为可用的图片 URL（http/https 或 s3 地址）。"""
    if not s or not s.strip():
        return False
    s = s.strip()
    if s.startswith(("http://", "https://")):
        return True
    if s.startswith("s3://"):
        return True
    return False


# 常见图片扩展名 -> MIME（用于本地文件转 data URL）
_IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _to_image_url(image_input: str) -> str:
    """
    将「图片 URL 或本地文件路径」转为请求体可用的 image_url 字符串。
    - 若为 http/https/s3 URL，原样返回（strip）。
    - 若为本地存在文件，读取并转为 data:image/xxx;base64,...。
    """
    raw = (image_input or "").strip()
    if not raw:
        raise ValueError("image 不能为空")
    if _is_image_url(raw):
        return raw
    # 视为本地路径
    path = Path(raw).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(
            "image 必须是标准的 S3 地址或可公网访问的图片 URL（以 http://、https:// 或 s3:// 开头），"
            "或指向本机已存在的图片文件路径"
        )
    suffix = path.suffix.lower()
    mime = _IMAGE_MIME.get(suffix, "image/jpeg")
    data = path.read_bytes()
    b64 = base64.standard_b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


import time

def run(
    image_url: str,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    prompt: str = DEFAULT_OCR_PROMPT,
    output_path: Path | None = None,
    timeout: int = 120,
) -> tuple[str, float]:
    """执行 VL OCR：支持图片 URL 或本地文件路径，调用 DashScope VL 模型，返回文本和执行时间。"""
    base_url = (base_url or BASE_URL or "").rstrip("/")
    api_key = api_key or API_KEY
    model = model or MODEL_NAME
    if not api_key:
        raise ValueError("未配置 API Key")
    if not model:
        raise ValueError("未配置模型名称")
    if not base_url:
        raise ValueError("未配置服务地址")

    image_for_request = _to_image_url(image_url)

    # 如需要，在调用 OpenAI SDK 前将配置写入环境变量，便于 SDK 或下游从环境读取
    os.environ["OPENAI_API_KEY"] = api_key
    if base_url:
        os.environ["OPENAI_BASE_URL"] = base_url

    client = OpenAI(api_key=api_key, base_url=base_url)

    # 使用 URL 或 data URL 作为 image_url.url，含 min_pixels/max_pixels
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_for_request,
                        "min_pixels": MIN_PIXELS,
                        "max_pixels": MAX_PIXELS,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]
    if image_for_request.startswith("data:"):
        logger.info("使用本地图片（已转 base64），路径: %s", (image_url or "").strip()[:80])
    else:
        logger.info("使用图片 URL: %s", image_for_request[:80] + ("..." if len(image_for_request) > 80 else ""))

    logger.info("正在调用 VL 模型: %s", model)
    start_time = time.time()
    completion = client.chat.completions.create(
        model=model,
        messages=cast(Any, messages),
        timeout=float(timeout) if timeout > 0 else 60.0,
    )
    end_time = time.time()
    execution_time = end_time - start_time
    
    text = (completion.choices[0].message.content or "").strip()

    if output_path:
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        logger.info("已写入 %s", output_path)
    return text, execution_time


def process_folder(folder_path: str, prompt: str = DEFAULT_OCR_PROMPT, timeout: int = 120) -> None:
    """遍历文件夹中的图片文件，对每个文件执行 OCR 并打印执行时间。"""
    folder = Path(folder_path).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"文件夹不存在或不是有效目录: {folder_path}")
    
    # 支持的图片扩展名
    image_extensions = set(_IMAGE_MIME.keys())
    
    # 遍历文件夹中的所有文件
    total_files = 0
    total_time = 0.0
    
    for file_path in folder.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in image_extensions:
            total_files += 1
            logger.info(f"处理文件: {file_path}")
            try:
                # 执行 OCR
                _, exec_time = run(
                    image_url=str(file_path),
                    prompt=prompt,
                    timeout=timeout,
                )
                total_time += exec_time
                logger.info(f"文件 {file_path.name} OCR 执行时间: {exec_time:.2f} 秒")
            except Exception as e:
                logger.error(f"处理文件 {file_path} 失败: {e}")
    
    # 打印汇总信息
    if total_files > 0:
        avg_time = total_time / total_files
        logger.info(f"\n汇总信息:")
        logger.info(f"处理文件总数: {total_files}")
        logger.info(f"总执行时间: {total_time:.2f} 秒")
        logger.info(f"平均执行时间: {avg_time:.2f} 秒")
    else:
        logger.info(f"文件夹中没有找到支持的图片文件: {folder_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="使用 DashScope Qwen-VL 对图片或文件夹进行 OCR 或图像理解，输出识别/分析文本。"
    )
    parser.add_argument("input", type=str, help="输入图片或文件夹：S3/公网 URL（http/https/s3）、本机图片文件路径或文件夹路径")
    parser.add_argument(
        "--prompt",
        "-p",
        type=str,
        default=DEFAULT_OCR_PROMPT,
        help="发给 VL 模型的提示语，默认：按版面识别全部文字并逐行输出",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="输出文本文件路径；不指定则输出到 stdout",
    )
    parser.add_argument("--timeout", type=int, default=120, help="请求超时秒数，默认 120")
    args = parser.parse_args()

    try:
        input_path = Path(args.input).expanduser().resolve()
        
        # 判断输入是文件还是文件夹
        if input_path.exists() and input_path.is_dir():
            # 处理文件夹
            process_folder(
                folder_path=str(input_path),
                prompt=args.prompt,
                timeout=args.timeout,
            )
        else:
            # 处理单个文件
            result, exec_time = run(
                image_url=args.input,
                prompt=args.prompt,
                output_path=args.output,
                timeout=args.timeout,
            )
            logger.info(f"OCR 执行时间: {exec_time:.2f} 秒")
            if not args.output:
                print(result)
        return 0
    except ValueError as e:
        logger.error("%s", e)
        return 1
    except Exception as e:
        logger.exception("VL OCR 执行失败: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
