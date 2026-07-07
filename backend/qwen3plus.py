import os
import json
import time
from pathlib import Path
from dashscope import MultiModalConversation
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# 配置
API_KEY = "sk-ws-H.EMMRMEH.2dcF.MEUCIQD0dIVW9aSOElIJ0E9kKWKiLNqZLYOLvSaiLm2vSfugkgIgAds6q8T4-4LHss8hcNGurI-LBMUVhpr1M1pCN8QYxLc" # 从环境变量读取
MODEL = "qwen3-vl-plus"  # 或 "qwen3.6-35b-a3b"
IMAGE_FOLDER = r"D:\newDownload\vlm_testData"
OUTPUT_JSON = "batch_vl_results.json"
OUTPUT_CSV = "batch_vl_results.csv"
MAX_WORKERS = 3  # 并发数
ENABLE_THINKING = False
THINKING_BUDGET = 81920

# 线程锁
print_lock = Lock()

# 提示词
PROMPT_TEXT = """请判断这张检测报告图片：
1. 是否有检测机构印章？
2. 该图片共需要几个身份签名？所有的身份中有没有漏签名？
输出示例
{
    "if_seal":"true/false",
    "missing_signature":"填写缺失签名字段名称，使用'|'作为间隔"
}
只输出 JSON。"""


def process_single_image(image_path: str, api_key: str) -> dict:
    """处理单张图片，返回结果"""
    result = {
        "image_path": str(image_path),
        "filename": Path(image_path).name,
        "success": False,
        "reasoning": "",
        "answer": "",
        "error": ""
    }

    try:
        # 构建消息
        messages = [
            {
                "role": "user",
                "content": [
                    {"image": f"file://{Path(image_path).resolve().as_posix()}"},
                    {"text": PROMPT_TEXT}
                ]
            }
        ]

        # 调用API
        response = MultiModalConversation.call(
            api_key=api_key,
            model=MODEL,
            messages=messages,
            stream=True,
            enable_thinking=ENABLE_THINKING,
            thinking_budget=THINKING_BUDGET,
        )

        # 处理流式响应
        reasoning_content = ""
        answer_content = ""
        is_answering = False
        current_reasoning = ""

        for chunk in response:
            try:
                message = chunk.output.choices[0].message
                reasoning_chunk = message.get("reasoning_content", None)
                content = message.get("content", [])

                # 跳过空内容
                if content == [] and reasoning_chunk == "":
                    continue

                # 处理思考过程
                if reasoning_chunk is not None and content == []:
                    current_reasoning += reasoning_chunk

                # 处理回复
                elif content != []:
                    if not is_answering:
                        is_answering = True
                    if content and isinstance(content, list) and len(content) > 0:
                        answer_content += content[0].get("text", "")

            except Exception as e:
                # 流式处理中可能有个别chunk异常，继续处理
                pass

        result["reasoning"] = current_reasoning
        result["answer"] = answer_content
        result["success"] = True

        # 尝试解析JSON
        try:
            # 提取JSON内容（可能包含markdown代码块）
            json_text = answer_content.strip()
            if "```json" in json_text:
                json_text = json_text.split("```json")[1].split("```")[0].strip()
            elif "```" in json_text:
                json_text = json_text.split("```")[1].split("```")[0].strip()

            result["parsed_json"] = json.loads(json_text)
        except:
            result["parsed_json"] = None

    except Exception as e:
        result["error"] = str(e)
        result["success"] = False

    return result


def batch_process_images(folder_path: str, api_key: str, max_workers: int = 3):
    """批量处理文件夹中的图片"""

    # 获取所有图片
    folder = Path(folder_path)
    extensions = ['.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp']
    image_files = []
    for ext in extensions:
        image_files.extend(folder.glob(f"*{ext}"))
        image_files.extend(folder.glob(f"*{ext.upper()}"))

    image_files = sorted(set(image_files))

    if not image_files:
        print(f"在 {folder_path} 中未找到任何图片文件")
        return []

    print(f"找到 {len(image_files)} 张图片")
    print(f"模型: {MODEL}")
    print(f"并发数: {max_workers}")
    print("=" * 60)

    results = []
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_image = {
            executor.submit(process_single_image, str(img_path), api_key): img_path
            for img_path in image_files
        }

        # 处理完成的任务
        for future in as_completed(future_to_image):
            img_path = future_to_image[future]
            completed += 1

            try:
                result = future.result()
                results.append(result)

                with print_lock:
                    status = "✅" if result["success"] else "❌"
                    print(f"[{completed}/{len(image_files)}] {status} {result['filename']}")

                    if result["success"]:
                        if result.get("parsed_json"):
                            print(f"   📊 {json.dumps(result['parsed_json'], ensure_ascii=False)}")
                        else:
                            # 显示前100字符
                            answer_preview = result["answer"][:100].replace("\n", " ")
                            print(f"   📝 {answer_preview}...")
                    else:
                        print(f"   ❌ 错误: {result['error']}")
                    print("-" * 60)

            except Exception as e:
                print(f"[{completed}/{len(image_files)}] ❌ {img_path.name} - 处理异常: {e}")
                results.append({
                    "image_path": str(img_path),
                    "filename": img_path.name,
                    "success": False,
                    "error": str(e)
                })

    # 统计
    success_count = sum(1 for r in results if r["success"])
    print(f"\n处理完成！成功: {success_count}/{len(image_files)}")

    return results


def save_results(results: list, json_path: str = "results.json", csv_path: str = "results.csv"):
    """保存结果到JSON和CSV文件"""

    # 保存JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"JSON结果已保存到: {json_path}")

    # 保存CSV
    import csv
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["文件名", "成功", "if_seal", "missing_signature", "完整回复", "思考过程", "错误信息"])

        for r in results:
            if r["success"]:
                parsed = r.get("parsed_json", {})
                writer.writerow([
                    r.get("filename", ""),
                    "是",
                    parsed.get("if_seal", ""),
                    parsed.get("missing_signature", ""),
                    r.get("answer", ""),
                    r.get("reasoning", ""),
                    ""
                ])
            else:
                writer.writerow([
                    r.get("filename", ""),
                    "否",
                    "",
                    "",
                    "",
                    "",
                    r.get("error", "")
                ])

    print(f"CSV结果已保存到: {csv_path}")


def test_single_image(image_path: str, api_key: str):
    """测试单张图片（调试用）"""
    print(f"测试单张图片: {image_path}")
    print("=" * 60)

    result = process_single_image(image_path, api_key)

    if result["success"]:
        print("✅ 处理成功")
        print("\n" + "=" * 20 + "思考过程" + "=" * 20)
        print(result["reasoning"])
        print("\n" + "=" * 20 + "完整回复" + "=" * 20)
        print(result["answer"])
        if result.get("parsed_json"):
            print("\n" + "=" * 20 + "解析JSON" + "=" * 20)
            print(json.dumps(result["parsed_json"], ensure_ascii=False, indent=2))
    else:
        print(f"❌ 处理失败: {result['error']}")

    return result


# 主程序
if __name__ == "__main__":
    # 检查API Key
    if not API_KEY:
        print("❌ 错误: 未设置 DASHSCOPE_API_KEY 环境变量")
        print("请运行: setx DASHSCOPE_API_KEY 'your-api-key'")
        exit(1)

    print(f"使用API Key: {API_KEY[:10]}...")
    print(f"图片文件夹: {IMAGE_FOLDER}")
    print(f"模型: {MODEL}")
    print("=" * 60)

    # 批量处理
    results = batch_process_images(
        folder_path=IMAGE_FOLDER,
        api_key=API_KEY,
        max_workers=MAX_WORKERS
    )

    # 保存结果
    if results:
        save_results(results, OUTPUT_JSON, OUTPUT_CSV)

    # 可选：测试单张图片
    # test_image = r"D:\newDownload\vlm_testData\1f5eb071-899d-49b8-ba9c-9948b09d9c2b.png"
    # if Path(test_image).exists():
    #     test_single_image(test_image, API_KEY)