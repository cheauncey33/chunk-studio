from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

DEFAULT_PROMPT = """你是中文检测报告的版式核验助手。请只根据图片可见内容判断，不要脑补。

任务：
1. 判断是否可见检测机构印章。红色印章、蓝色印章、圆章、方章、或页面边缘只露出部分印章都算可见。
2. 找出图片中实际出现的签名/日期字段。不要强行套固定字段；如果图片里只出现 3 个签名/日期栏，就只输出这 3 个。
3. 对每个实际出现的字段判断是否已经填写。字段存在但空白为 false；字段存在但看不清为 null；字段根本不存在就不要输出。
4. 统一身份别名：
   - approver: 批准人、批准、核准、签发人
   - issue_date: 签发日期、签发时间、日期
   - chief_inspector: 主检人、主检、检测人、检验人、检验员
   - writer: 编写人、编制人、填写人、编写
   - reviewer: 审核人、复核人、校核人、审核
   - other: 其他签名/日期字段

只输出 JSON，不要解释，不要 Markdown：
{
  "seal": {
    "visible": true/false/null,
    "evidence": "short English evidence"
  },
  "fields": [
    {
      "label_text": "图片中看到的原始字段名",
      "role": "approver/issue_date/chief_inspector/writer/reviewer/other",
      "filled": true/false/null,
      "evidence_type": "handwriting/date/stamp/printed/blank/unclear",
      "evidence": "short English evidence"
    }
  ],
  "visible_required_field_count": 0,
  "filled_required_field_count": 0,
  "missing_visible_fields": ["只列出图片中存在但未填写的字段原文"],
  "pass_visible_requirements": true/false/null
}
"""


def image_to_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def call_ollama(
    *,
    endpoint: str,
    model: str,
    image_path: Path,
    prompt: str,
    timeout: int,
    num_ctx: int,
) -> str:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [image_to_base64(image_path)],
            }
        ],
        "stream": False,
        "options": {
            "temperature": 0,
            "num_ctx": num_ctx,
        },
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("message", {}).get("content", "").strip()


def extract_json_object(content: str) -> dict[str, object] | None:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def normalize_result(content: str) -> str:
    parsed = extract_json_object(content)
    if parsed is None:
        return content

    if "fields" in parsed:
        normalized = parsed
    else:
        keys = [
            "seal_visible",
            "approver_signed",
            "issue_date_filled",
            "chief_inspector_signed",
            "writer_signed",
            "reviewer_signed",
            "notes",
        ]
        normalized = {key: parsed.get(key) for key in keys}

    def scrub(value: object) -> object:
        if isinstance(value, str):
            return value if value.isascii() else ""
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {str(key): scrub(val) for key, val in value.items()}
        return value

    normalized = scrub(normalized)
    return json.dumps(normalized, ensure_ascii=False, indent=2)


def collect_images(input_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in input_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in IMAGE_EXTENSIONS
        and not p.name.lower().startswith("vlm_test_")
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch-test local Ollama VLM on inspection report images."
    )
    parser.add_argument(
        "--input-dir",
        default=r"D:\newDownload\vlm_testData",
        help="Directory containing images to test.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output txt path. Defaults to <input-dir>\\vlm_test_results.txt.",
    )
    parser.add_argument("--model", default="qwen2.5vl:7b", help="Ollama model name.")
    parser.add_argument(
        "--endpoint",
        default="http://localhost:11434/api/chat",
        help="Ollama chat endpoint.",
    )
    parser.add_argument("--timeout", type=int, default=240, help="Per-image timeout seconds.")
    parser.add_argument("--num-ctx", type=int, default=8192, help="Ollama num_ctx option.")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        print(f"Input directory does not exist: {input_dir}", file=sys.stderr)
        return 2

    output_path = Path(args.output) if args.output else input_dir / "vlm_test_results.txt"
    images = collect_images(input_dir)
    if not images:
        print(f"No images found in: {input_dir}", file=sys.stderr)
        return 2

    started_at = datetime.now().isoformat(timespec="seconds")
    lines: list[str] = [
        "VLM batch test results",
        f"Started at: {started_at}",
        f"Input dir: {input_dir}",
        f"Model: {args.model}",
        f"Endpoint: {args.endpoint}",
        f"Images: {len(images)}",
        "",
    ]

    for index, image_path in enumerate(images, start=1):
        print(f"[{index}/{len(images)}] Testing {image_path.name} ...", flush=True)
        start = time.perf_counter()
        try:
            content = call_ollama(
                endpoint=args.endpoint,
                model=args.model,
                image_path=image_path,
                prompt=DEFAULT_PROMPT,
                timeout=args.timeout,
                num_ctx=args.num_ctx,
            )
            content = normalize_result(content)
            elapsed = time.perf_counter() - start
            status = "ok"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            elapsed = time.perf_counter() - start
            status = "error"
            content = f"{type(exc).__name__}: {exc}"

        lines.extend(
            [
                "=" * 80,
                f"#{index}: {image_path.name}",
                f"Path: {image_path}",
                f"Status: {status}",
                f"Elapsed: {elapsed:.2f}s",
                "Result:",
                content,
                "",
            ]
        )

    finished_at = datetime.now().isoformat(timespec="seconds")
    lines.extend(["=" * 80, f"Finished at: {finished_at}", ""])
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Done. Wrote: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
