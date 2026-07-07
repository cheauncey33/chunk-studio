import os
from pathlib import Path
import dashscope
import json
import time

dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"


def to_file_url(path: str) -> str:
    """将本地路径转换为文件URL"""
    p = Path(path).resolve()
    return "file://" + str(p).replace("\\", "/")


def process_single_image(image_path: str, api_key: str, model: str = "qwen3.6-35b-a3b") -> dict:
    """处理单张图片，返回结果"""
    messages = [
        {
            "role": "user",
            "content": [
                {"image": to_file_url(image_path)},
                {
                    "text": """请判断这张检测报告图片：
1. 是否有检测机构印章？
2. 该图片共需要几个身份的手写签名？所有的身份中有没有漏签名？
输出示例
{
    "if_seal":"true/false",
    "missing_signature":"填写缺失签名字段名称，使用'|'作为间隔"
}
只输出 JSON。"""
                },
            ],
        }
    ]

    try:
        response = dashscope.MultiModalConversation.call(
            api_key=api_key,
            model=model,
            messages=messages,
            enable_thinking=False,
            result_format="message",
        )

        if response.status_code == 200:
            msg = response.output.choices[0].message
            content = msg.content

            if isinstance(content, list):
                text = "".join(item.get("text", "") for item in content if isinstance(item, dict))
            else:
                text = content

            reasoning = getattr(msg, "reasoning_content", None)

            return {
                "success": True,
                "image": image_path,
                "text": text,
                "reasoning": reasoning,
                "raw_response": response
            }
        else:
            return {
                "success": False,
                "image": image_path,
                "error": f"HTTP {response.status_code}: {response.message}",
                "error_code": response.code
            }
    except Exception as e:
        return {
            "success": False,
            "image": image_path,
            "error": str(e)
        }


def batch_process_folder(folder_path: str, api_key: str, model: str = "qwen3.6-35b-a3b",
                         extensions: list = ['.png', '.jpg', '.jpeg', '.bmp', '.gif'],
                         delay: float = 0.5):
    """批量处理文件夹中的所有图片"""

    # 获取所有图片文件
    folder = Path(folder_path)
    image_files = []
    for ext in extensions:
        image_files.extend(folder.glob(f"*{ext}"))
        image_files.extend(folder.glob(f"*{ext.upper()}"))

    # 去重并排序
    image_files = sorted(set(image_files))

    if not image_files:
        print(f"在 {folder_path} 中未找到任何图片文件")
        return []

    print(f"找到 {len(image_files)} 张图片，开始处理...")
    print("-" * 50)

    results = []

    for idx, img_path in enumerate(image_files, 1):
        print(f"[{idx}/{len(image_files)}] 处理: {img_path.name}")

        result = process_single_image(str(img_path), api_key, model)
        results.append(result)

        if result["success"]:
            print(f"  ✅ 成功")
            # 打印提取的JSON结果
            try:
                json_data = json.loads(result["text"])
                print(f"  📊 结果: {json.dumps(json_data, ensure_ascii=False)}")
            except:
                print(f"  📝 回复: {result['text'][:100]}...")
        else:
            print(f"  ❌ 失败: {result.get('error', '未知错误')}")

        print("-" * 50)

        # 避免请求过快
        if idx < len(image_files):
            time.sleep(delay)

    # 统计结果
    success_count = sum(1 for r in results if r["success"])
    print(f"\n处理完成！成功: {success_count}/{len(image_files)}")

    return results


def save_results_to_file(results: list, output_path: str = "results.json"):
    """保存结果到JSON文件"""
    output_data = []
    for r in results:
        output_data.append({
            "image": r["image"],
            "success": r["success"],
            "text": r.get("text", ""),
            "reasoning": r.get("reasoning", ""),
            "error": r.get("error", "")
        })

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"结果已保存到: {output_path}")


# 使用示例
if __name__ == "__main__":
    # 配置
    API_KEY = "sk-ws-H.EMMRMEH.2dcF.MEUCIQD0dIVW9aSOElIJ0E9kKWKiLNqZLYOLvSaiLm2vSfugkgIgAds6q8T4-4LHss8hcNGurI-LBMUVhpr1M1pCN8QYxLc"  # 替换为你的API Key
    IMAGE_FOLDER = r"D:\newDownload\vlm_testData"
    OUTPUT_FILE = "batch_results.json"

    # 执行批量处理
    results = batch_process_folder(
        folder_path=IMAGE_FOLDER,
        api_key=API_KEY,
        model="qwen3.6-35b-a3b",
        delay=0.5  # 每次请求间隔0.5秒，避免限流
    )

    # 保存结果
    if results:
        save_results_to_file(results, OUTPUT_FILE)