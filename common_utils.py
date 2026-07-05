import os
import re
import zipfile
import hashlib
from urllib.parse import urlparse, parse_qs


def safe_name(text: str, max_len: int = 120) -> str:
    """
    将任意字符串标准化为较安全的文件/目录名：
    - 去除首尾空白
    - 将空白替换为下划线
    - 过滤常见非法字符
    - 截断到 max_len
    """
    s = (text or "").strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[\\/:\*\?\"<>\|]", "_", s)
    return s[:max_len] or "unnamed"


def extract_share_id(url: str) -> str:
    """
    从 URL 中尽力提取一个稳定的标识，用于每个 URL 的输出目录/zip 名。
    目标：尽量可读且避免覆盖（唯一性优先）。

    规则（方案 A：host + 关键参数 + 短哈希）：
      1) 提取 host（含端口）
      2) 从 query 与 fragment 中优先找常见的唯一参数（share_id/shareId/shareid/dignosis/diagnosis/data/id/rt/et）
      3) 追加 URL 的短哈希（sha1 前 10 位），保证同名不覆盖

    返回值会经过 safe_name 处理，并限制长度。
    """
    parsed = urlparse(url)
    host = parsed.netloc or "unknown_host"

    # query 参数
    qs = parse_qs(parsed.query)

    # fragment 里也可能带 query（如：#/image?data=...）
    frag_qs = {}
    if parsed.fragment and "?" in parsed.fragment:
        frag_query = parsed.fragment.split("?", 1)[1]
        frag_qs = parse_qs(frag_query)

    merged = {}
    merged.update(qs)
    # fragment 优先级更高：很多 SPA 会把关键参数放到 fragment
    for k, v in frag_qs.items():
        merged[k] = v

    preferred_keys = (
        "share_id",
        "shareId",
        "shareid",
        "dignosis",
        "diagnosis",
        "data",
        "id",
        "rt",
        "et",
    )

    chosen_val = ""
    for key in preferred_keys:
        values = merged.get(key)
        if values and values[0]:
            chosen_val = str(values[0])
            break

    # 控制可读部分的长度（最终仍会加 hash 保证唯一）
    if chosen_val:
        chosen_val = chosen_val[:32]

    h = hashlib.sha1((url or "").encode("utf-8", errors="ignore")).hexdigest()[:10]

    if chosen_val:
        return safe_name(f"{host}_{chosen_val}_{h}", max_len=120)
    return safe_name(f"{host}_{h}", max_len=120)


def read_urls_file(path: str) -> list[str]:
    """
    从文本文件读取 URL 列表：
    - 忽略空行与以 # 开头的注释行
    - 返回纯 URL 列表
    """
    results: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "安全码:" in s:
                s = s.split("安全码:", 1)[0].strip()
            results.append(s)
    return results


def read_urls_file_with_passwords(path: str) -> list[tuple[str, str | None]]:
    """
    从文本文件读取 URL 列表及其对应的安全码：
    - 忽略空行与以 # 开头的注释行
    - 支持 "URL 安全码:xxx" 格式，自动提取纯 URL 和安全码
    - 返回 (URL, 安全码) 的元组列表，无安全码时为 (URL, None)
    """
    results: list[tuple[str, str | None]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            password = None
            if "安全码:" in s:
                parts = s.split("安全码:")
                s = parts[0].strip()
                password = parts[1].strip() if len(parts) > 1 else None
            results.append((s, password))
    return results


def make_zip_dir(src_dir: str, zip_path: str) -> None:
    """
    将目录 src_dir 打包为 zip_path（若父目录不存在则创建）。
    zip 内相对路径以 src_dir 的父目录为基准，保持目录结构。
    """
    parent_dir = os.path.dirname(os.path.normpath(src_dir))
    os.makedirs(os.path.dirname(zip_path), exist_ok=True)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(src_dir):
            for filename in files:
                file_path = os.path.join(root, filename)
                arcname = os.path.relpath(file_path, start=parent_dir)
                zf.write(file_path, arcname)
