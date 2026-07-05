import argparse
import asyncio
import os
import re
from collections import defaultdict
from io import BytesIO

from playwright.async_api import async_playwright
import pydicom
from common_utils import extract_share_id, read_urls_file, make_zip_dir


# ================== 工具函数 ==================


def safe_name(text: str) -> str:
    """把文本变成安全的文件夹名."""
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"[\\/:\*\?\"<>\|]", "_", text)
    return text[:80] or "unnamed"


def looks_like_dicom(data: bytes) -> bool:
    """检查字节流是否符合 DICOM: 前128字节任意 + 'DICM'."""
    return len(data) > 132 and data[128:132] == b"DICM"


def classify_series_by_ui(num_images: int | None) -> str:
    """
    仅用右侧“序列数量”粗略判断：
    - 返回 "diag"    ：临床诊断常看的
    - 返回 "nondiag"：单张定位片等辅助序列
    这个分类只是用来决定“要不要点这个序列”，不影响最后文件夹划分。
    """
    if num_images is not None and num_images <= 3:
        return "nondiag"
    return "diag"


def classify_series_by_dicom(desc: str, body_part: str) -> str:
    """根据 DICOM 的 SeriesDescription / BodyPartExamined 粗略判断."""
    text = f"{desc} {body_part}".lower()
    nondiag_keywords = [
        "localizer",
        "locator",
        "scout",
        "topogram",
        "survey",
        "overview",
        "dose",
    ]
    if any(k in text for k in nondiag_keywords):
        return "nondiag"
    return "diag"


# ================== 序列面板（PC 版右侧列表） ==================


async def open_series_panel(page):
    """
    PC 版圆心云影：
    右侧有一个 listbtn 区域，里面是一列 button.rightbutton，每个是一个序列。
    面板默认就是展开的，这里只负责等待它加载完成。
    """
    buttons = page.locator("div.listbtn button.rightbutton")
    # 若页面被“分享密码/登录校验”拦住，需要你在浏览器里手动完成后才能进入 viewer。
    # 这里把等待窗口拉长到与 nyfy 类似的 120s，避免 20s 来不及输入就失败。
    print(">>> 如页面需要密码/登录，请在浏览器里完成验证（脚本将等待最多 120 秒）...")
    await buttons.first.wait_for(state="visible", timeout=120000)
    print(">>> 序列面板已就绪")


async def read_series_list(page):
    """
    从右侧序列列表中读取所有 series 信息。

    HTML 结构大致为（每个序列）：
      <button class="el-button rightbutton el-button--primary">
        <span>序列数量：357</span>
        <div>缩略图 canvas</div>
        <span>序列2</span>
        ...
      </button>
    """
    await open_series_panel(page)

    buttons = page.locator("div.listbtn button.rightbutton")
    count = await buttons.count()
    print(f">>> 共检测到 {count} 个 series")

    result = []
    for i in range(count):
        btn = buttons.nth(i)
        text = (await btn.inner_text()).strip()

        # 序列数量：XXX
        m_qty = re.search(r"序列数量：(\d+)", text)
        num_images = int(m_qty.group(1)) if m_qty else None

        # 序列N
        m_seq = re.search(r"序列(\d+)", text)
        series_no = m_seq.group(1) if m_seq else str(i + 1)

        desc = f"序列{series_no}_数量{num_images if num_images is not None else '未知'}"
        category = classify_series_by_ui(num_images)
        folder_name = f"series_{series_no}_{safe_name(desc)}"

        result.append(
            {
                "index": i,  # 在右侧列表中的索引
                "series_no": series_no,
                "ui_desc": desc,
                "ui_folder": folder_name,
                "num_images_from_card": num_images,
                "ui_category": category,  # "diag" or "nondiag"
            }
        )

    return result


# ================== 主逻辑 ==================


async def run_downloader(
    check_url: str, out_root: str, download_mode: str, headless: bool
):
    """
    核心异步逻辑：
    - check_url: 检查分享链接
    - out_root: 输出根目录
    - download_mode: diag / nondiag / all （按 UI 粗略筛序列）
    - headless: 是否无界面运行浏览器
    """
    os.makedirs(out_root, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})

        # -------- DICOM 分组：完全基于 DICOM 标签，不再依赖“当前序列” --------
        seen_urls_global: set[str] = set()
        series_meta: dict[str, dict] = {}
        series_counts: defaultdict[str, int] = defaultdict(int)

        async def handle_response(resp):
            url = resp.url

            # 只关心 DICOM 获取接口
            if "/api/yunyingxiang/get_dcm_jpg" not in url:
                return

            # 全局 URL 去重
            if url in seen_urls_global:
                return

            try:
                body = await resp.body()
            except Exception as e:
                print(f"      [!] 获取 body 失败: {e}")
                return

            if not looks_like_dicom(body):
                return

            seen_urls_global.add(url)

            # ---- 解析 DICOM 标签，按 SeriesInstanceUID 分组 ----
            try:
                ds = pydicom.dcmread(BytesIO(body), stop_before_pixels=True, force=True)
            except Exception as e:
                print(f"      [!] 解析 DICOM 失败（fallback 到 unknown series）: {e}")
                series_key = "unknown"
                series_no = None
                series_desc = "unknown"
                body_part = ""
                thickness = ""
            else:
                series_uid = getattr(ds, "SeriesInstanceUID", None)
                series_no = getattr(ds, "SeriesNumber", None)
                series_desc = getattr(ds, "SeriesDescription", "") or ""
                body_part = getattr(ds, "BodyPartExamined", "") or ""
                thickness = getattr(ds, "SliceThickness", "") or ""

                series_key = series_uid or f"SN{series_no}" or "unknown"

            # 初始化该 series 的元信息
            if series_key not in series_meta:
                category = classify_series_by_dicom(series_desc, body_part)
                num_str = str(series_no) if series_no is not None else "X"
                base = series_desc or body_part or "series"
                extra = f"_T{thickness}" if thickness else ""
                folder_name = f"series_{num_str}_{safe_name(base)}{extra}"

                series_meta[series_key] = {
                    "folder": folder_name,
                    "category": category,
                    "series_no": series_no,
                    "series_desc": series_desc,
                    "body_part": body_part,
                    "thickness": thickness,
                }

                print(
                    f"      [*] 新系列发现：{folder_name} "
                    f"(SeriesNumber={series_no}, Desc='{series_desc}', "
                    f"BodyPart='{body_part}', 分类={category})"
                )

            info = series_meta[series_key]

            # 如果你也想按 diag/nondiag 过滤真实 DICOM，可以打开下面这段：
            if download_mode == "diag" and info["category"] != "diag":
                return
            if download_mode == "nondiag" and info["category"] != "nondiag":
                return

            series_counts[series_key] += 1
            idx = series_counts[series_key]

            out_dir = os.path.join(out_root, info["folder"])
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{idx:04d}.dcm")
            with open(out_path, "wb") as f:
                f.write(body)

            print(f"      [+] {info['folder']}: 保存 DICOM {idx:04d}.dcm （url={url}）")

        # 先挂上响应监听器，再打开页面，这样初始预加载也能抓到
        context.on("response", lambda r: asyncio.create_task(handle_response(r)))

        page = await context.new_page()
        print(f">>> 打开检查页面: {check_url}")
        # 拉长导航超时，避免遇到密码/登录校验或网络波动时过早失败
        await page.goto(check_url, wait_until="networkidle", timeout=120000)

        # 读取 UI 序列列表，用来“走片”触发剩余切片的加载
        series_list = await read_series_list(page)

        # 按 UI 粗略分类做一次过滤（只影响“点哪些序列”，不影响 DICOM 分组）
        if download_mode == "diag":
            filtered = [s for s in series_list if s["ui_category"] == "diag"]
            print(f">>> 只点击临床诊断序列（按 UI 粗略判断）：共 {len(filtered)} 个")
        elif download_mode == "nondiag":
            filtered = [s for s in series_list if s["ui_category"] == "nondiag"]
            print(f">>> 只点击辅助/非诊断序列（按 UI 粗略判断）：共 {len(filtered)} 个")
        else:
            filtered = series_list
            print(f">>> 点击所有序列：共 {len(filtered)} 个")

        # ====== 逐个 series 处理（仅用于触发网络请求） ======
        MAX_ROUNDS = 2  # 每个序列最多完整“走片”轮数
        QUIET_CHECKS = 3  # 静默观察次数
        QUIET_STEP_MS = 500  # 每次静默间隔（毫秒）

        for idx, info in enumerate(filtered, start=1):
            print(
                f"\n=== [{idx}/{len(filtered)}] "
                f"打开 UI 序列 {info['series_no']}：{info['ui_desc']} ==="
            )

            await open_series_panel(page)
            buttons = page.locator("div.listbtn button.rightbutton")
            btn = buttons.nth(info["index"])
            await btn.scroll_into_view_if_needed()
            await btn.click()

            # 给第一张图一点加载时间
            await page.wait_for_timeout(400)

            num_images = info["num_images_from_card"]
            if not num_images or num_images <= 0:
                print("    [!] 无法从列表读取该序列的张数，跳过走片")
                continue

            print(f"    UI 显示本序列共有 {num_images} 张图像")

            # 底部“下一页”按钮
            next_btn = page.locator('button[title="下一页"]')

            # 多轮逐帧点击，把所有切片尽量打出来
            for round_idx in range(1, MAX_ROUNDS + 1):
                print(f"    >> 正在进行第 {round_idx} 轮逐帧点击...")
                for _i in range(1, num_images):
                    await next_btn.click()
                    await page.wait_for_timeout(30)

            # 静默观察阶段：等一等残余网络请求
            print("    >> 播放结束，等待残余网络请求完成（静默观察阶段）...")
            last_total = sum(series_counts.values())
            no_change_rounds = 0
            for c in range(1, QUIET_CHECKS + 1):
                await page.wait_for_timeout(QUIET_STEP_MS)
                cur_total = sum(series_counts.values())
                if cur_total == last_total:
                    no_change_rounds += 1
                else:
                    no_change_rounds = 0
                    last_total = cur_total
                print(
                    f"      [静默观察] 已等待 {QUIET_STEP_MS * c}ms，"
                    f"当前总切片数 {cur_total}，连续无新增轮数={no_change_rounds}"
                )
                if no_change_rounds >= 2:
                    break

        print("\n>>> 所有序列“走片”结束")

        # 打印一下每个 DICOM 系列的统计信息
        print("\n>>> 按 DICOM 标签统计到的系列：")
        for key, meta in series_meta.items():
            cnt = series_counts.get(key, 0)
            print(
                f"    - {meta['folder']}: {cnt} 张 "
                f"(SeriesNo={meta['series_no']}, Desc='{meta['series_desc']}', "
                f"BodyPart='{meta['body_part']}', 分类={meta['category']})"
            )

        await browser.close()


# ================== 命令行参数解析 ==================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="基于 Playwright 的圆心云影网页版 DICOM 批量下载小工具（tjmucih，按 DICOM 标签分组）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    group_in = parser.add_mutually_exclusive_group(required=False)
    group_in.add_argument(
        "-u",
        "--url",
        help=(
            "单个检查链接 URL，例如："
            "https://zlyy.tjmucih.cn/yunyingxiang/?dignosis=..."
        ),
    )
    group_in.add_argument(
        "--urls-file", help="包含多个 URL 的文本文件（每行一个，支持 # 注释）"
    )

    # 单 URL 兼容参数：仍然支持 --out-dir；多 URL 时使用 --out-parent
    parser.add_argument(
        "-o", "--out-dir", default="output_dicom", help="（单 URL）DICOM 输出根目录"
    )
    parser.add_argument(
        "--out-parent",
        default="./downloads",
        help="（多 URL）输出父目录（每个 URL 会建一个子目录）",
    )
    parser.add_argument(
        "--mode",
        choices=["diag", "nondiag", "all"],
        default="all",
        help="下载模式（主要影响“点哪些序列”）："
        "diag=临床诊断序列；nondiag=辅助序列；all=全部",
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        help="无界面模式运行浏览器（后台跑）",
    )
    group.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="有界面模式运行浏览器（默认）",
    )
    parser.set_defaults(headless=False)

    parser.add_argument(
        "--no-zip", action="store_true", help="不对每个 URL 的输出目录打 zip 包"
    )

    args = parser.parse_args()

    # 既没传 --url 也没传 --urls-file，则做一次交互式输入（兼容老用法）
    if not args.url and not args.urls_file:
        try:
            user_input = input("请输入检查链接 URL（或 Ctrl+C 取消）：").strip()
        except EOFError:
            user_input = ""
        if user_input:
            args.url = user_input

    if not args.url and not args.urls_file:
        parser.error("必须提供检查链接：使用 -u/--url 或 --urls-file")

    return args


if __name__ == "__main__":
    cli_args = parse_args()
    try:
        if cli_args.url:
            # 单 URL 模式：沿用原 out_dir
            print(">>> 启动参数：")
            print(f"    URL        : {cli_args.url}")
            print(f"    输出目录   : {cli_args.out_dir}")
            print(f"    下载模式   : {cli_args.mode}")
            print(f"    Headless   : {cli_args.headless}")
            asyncio.run(
                run_downloader(
                    check_url=cli_args.url,
                    out_root=cli_args.out_dir,
                    download_mode=cli_args.mode,
                    headless=cli_args.headless,
                )
            )
            if not cli_args.no_zip:
                share_id = extract_share_id(cli_args.url)
                zip_path = os.path.join(cli_args.out_parent, f"{share_id}.zip")
                os.makedirs(cli_args.out_parent, exist_ok=True)
                make_zip_dir(cli_args.out_dir, zip_path)
                print(f">>> zip 已生成：{os.path.abspath(zip_path)}")
        else:
            # 多 URL 模式：按 share_id 生成子目录并分别打包
            urls = read_urls_file(cli_args.urls_file)
            out_parent = os.path.abspath(cli_args.out_parent)
            os.makedirs(out_parent, exist_ok=True)

            print("\n>>> 启动参数（多 URL）：")
            print(f"    URL数量     : {len(urls)}")
            print(f"    out_parent  : {out_parent}")
            print(f"    mode        : {cli_args.mode}")
            print(f"    headless    : {cli_args.headless}\n")

            for i, u in enumerate(urls, start=1):
                share_id = extract_share_id(u)
                out_dir = os.path.join(out_parent, share_id)

                print("=" * 80)
                print(f"### [{i}/{len(urls)}] 开始下载")
                print(f"URL      : {u}")
                print(f"输出目录 : {out_dir}")
                print("=" * 80)

                try:
                    asyncio.run(
                        run_downloader(
                            check_url=u,
                            out_root=out_dir,
                            download_mode=cli_args.mode,
                            headless=cli_args.headless,
                        )
                    )
                except Exception as e:
                    print(f">>> ❌ 失败：{u}")
                    print(f">>> 错误：{e}")
                    continue

                if not cli_args.no_zip:
                    zip_path = os.path.join(out_parent, f"{share_id}.zip")
                    make_zip_dir(out_dir, zip_path)
                    print(f">>> zip 已生成：{os.path.abspath(zip_path)}")
            print("\n>>> 全部任务结束")
    except KeyboardInterrupt:
        print("\n>>> 收到中断信号，已退出。")
