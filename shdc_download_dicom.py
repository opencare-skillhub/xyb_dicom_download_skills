import argparse
import asyncio
import hashlib
import os
import re
import shutil
from collections import Counter, defaultdict
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from common_utils import (
    safe_name,
    extract_share_id,
    read_urls_file,
    make_zip_dir,
)


# ================== 工具函数 ==================


def looks_like_dicom_fast(data: bytes) -> bool:
    """
    快速判定是否像 DICOM（比 pydicom.dcmread 快很多）：
    1) 标准 128字节 preamble + 'DICM'
    2) 无 preamble 的 DICOM：通常以 File Meta group 0002 开头：b'\\x02\\x00\\x00\\x00'
    """
    if len(data) >= 132 and data[128:132] == b"DICM":
        return True
    if len(data) >= 4 and data[:4] == b"\x02\x00\x00\x00":
        return True
    return False


def classify_series(series_no: str, desc: str) -> str:
    """
    根据 series 号 + 描述粗略判断：
    - 返回 "diag"    ：临床诊断常看的
    - 返回 "nondiag"：Localizer / Tracker / Exam Summary / Dose Report 等辅助序列

    注意：不要用 "dose" 这个泛关键词，否则 Philips 的 iDose 会被误判。
    """
    text = f"{series_no} {desc}".lower()

    nondiag_keywords = [
        "localizer",
        "locator",
        "scout",
        "tracker",
        "tracker graph",
        "graph",
        "exam summary",
        "dose report",
        "ct dose",
        "dose_record",
        "doseinfo",
    ]
    if any(k in text for k in nondiag_keywords):
        return "nondiag"
    return "diag"


async def safe_goto(page, url: str, timeout_ms: int = 120000, retries: int = 3):
    """
    更鲁棒的打开页面：
    - 先用 wait_until='commit'（不要死等 DOMContentLoaded）
    - 然后等待一会 SPA 的 UI 资源加载
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            print(f">>> 打开页面（尝试 {attempt}/{retries}）: {url}")
            await page.goto(url, wait_until="commit", timeout=timeout_ms)

            # 很多是 SPA，这里不强求 load-state 完整，只要页面开始跑就行
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                pass

            return
        except PlaywrightTimeoutError as e:
            last_err = e
            print(f">>> goto 超时：{e}，2s 后重试...")
            await page.wait_for_timeout(2000)

    raise last_err


async def wait_viewer_ready(page, timeout_ms: int = 120000):
    """
    关键：等“阅片器 UI”真的出来再继续。
    你 ctype=4 headless 超时，通常就是因为太早去点“序列”。
    """
    # 先等底部工具条出来（或序列面板容器）
    candidates = [
        "div.bottom-bar",
        "div.all-serie-wapper",
    ]
    last_err = None
    for sel in candidates:
        try:
            await page.wait_for_selector(sel, timeout=timeout_ms)
            break
        except PlaywrightTimeoutError as e:
            last_err = e

    # 再确认“序列”按钮至少能定位到
    try:
        btn = page.locator("div.bottom-bar div.btn_item", has_text="序列")
        await btn.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        # 有些版本按钮不是文字按钮，给一次兜底：面板直接可见就算 ready
        panel = page.locator("div.all-serie-wapper")
        if await panel.is_visible():
            return
        raise last_err or PlaywrightTimeoutError(
            "viewer not ready: 底部工具条/序列按钮未出现"
        )


async def input_password_if_needed(page, password: str | None, timeout_ms: int = 30000):
    """
    如果页面出现安全码输入框，自动填入密码。
    本页面使用虚拟键盘，需要点击对应数字。
    """
    if not password:
        return
    
    print(f">>> 检查安全码输入框...")
    
    try:
        # 先检查是否有"请输入安全码"提示
        header = page.locator('.header')
        header_text = await header.text_content() if await header.is_visible(timeout=2000) else ""
        
        if "安全码" not in header_text:
            print(">>> 页面无需输入安全码")
            return
        
        print(f">>> 检测到安全码输入界面，开始输入密码")
        
        # 页面使用虚拟键盘，逐个点击数字
        for i, digit in enumerate(password, 1):
            # 使用属性选择器避免CSS ID选择器的数字开头问题
            button = page.locator(f"div[id='{digit}'].item, div[id='{digit}']")
            try:
                # 等待按钮可见，然后点击
                await button.first.wait_for(state="visible", timeout=2000)
                await button.first.click(force=True)
                print(f">>> 输入第{i}位数字: {digit}")
                await page.wait_for_timeout(500)
            except Exception as e:
                print(f">>> ⚠ 点击数字 {digit} 失败: {e}")
        
        # 等待键盘稳定
        await page.wait_for_timeout(1000)
        
        # 找到提交按钮并点击（支持多种选择器）
        submit_selectors = [
            "button:has-text('提交')",
            ".btn_box button",
            "button[onclick*='submitCode']",
            "div.btn_box button",
        ]
        
        submit_clicked = False
        for sel in submit_selectors:
            try:
                submit_btn = page.locator(sel)
                if await submit_btn.first.is_visible(timeout=1000):
                    await submit_btn.first.click(force=True)
                    print(f">>> 已点击提交按钮")
                    submit_clicked = True
                    break
            except Exception:
                pass
        
        if not submit_clicked:
            print(f">>> ⚠ 找不到提交按钮")
        
        # 等待页面跳转
        await page.wait_for_timeout(3000)
        
    except Exception as e:
        print(f">>> 安全码填入失败：{e}")


async def switch_to_hd_mode(page, timeout_ms: int = 10000):
    """
    尝试把右下角“流畅”切为“原图(清晰度高)”。
    说明：
    - 有的检查根本没有这个开关；
    - 有的 UI 文案会变；
    - 所以这里永远“尽力而为”，失败不影响流程。
    """
    try:
        toggle = page.get_by_text("流畅", exact=True)
        await toggle.click(timeout=timeout_ms)

        menu_item = page.get_by_text("原图(清晰度高)", exact=True)
        await menu_item.wait_for(state="visible", timeout=timeout_ms)
        await menu_item.click(timeout=timeout_ms)

        print(">>> 已切换到 原图(清晰度高)")
    except Exception:
        print(">>> 没找到“流畅/原图(清晰度高)”开关（或该检查无此选项），跳过")


async def open_series_panel(page, timeout_ms: int = 120000):
    """确保底部的序列面板是展开状态."""
    panel = page.locator("div.all-serie-wapper")
    if await panel.is_visible():
        return

    btn = page.locator("div.bottom-bar div.btn_item", has_text="序列")
    await btn.wait_for(state="visible", timeout=timeout_ms)

    # 有时有遮罩/动画，force 更稳
    await btn.click(timeout=timeout_ms, force=True)
    await panel.wait_for(state="visible", timeout=timeout_ms)


async def read_series_list(page):
    """读取所有 series 的信息（卡片 + 张数），并做分类."""
    await open_series_panel(page)
    cards = page.locator("div.all-serie-wapper div.serie-wapper")
    count = await cards.count()
    print(f">>> 共检测到 {count} 个 series")

    result = []
    for i in range(count):
        card = cards.nth(i)
        top = (await card.locator(".serie-info.topInfo").inner_text()).strip()
        bottom = (await card.locator(".serie-info.bottomInfo").inner_text()).strip()

        m = re.search(r"(\d+)\s*张", top)
        num_images = int(m.group(1)) if m else None

        series_no_match = re.match(r"(\S+)\s*(.*)", bottom)
        if series_no_match:
            series_no = series_no_match.group(1)
            desc = series_no_match.group(2).lstrip(" ,")
        else:
            series_no = bottom
            desc = ""

        folder_name = f"series_{series_no}_{safe_name(desc)}"
        category = classify_series(series_no, desc)

        result.append(
            {
                "index": i,
                "series_no": series_no,
                "desc": desc,
                "folder": folder_name,
                "num_images_from_card": num_images,
                "category": category,
            }
        )
    return result


async def goto_image_index(page, idx: int):
    """优先用鼠标点击/拖动 slider 跳到指定帧（0-based），失败后回退到 JS 方案。"""
    slider_sel = "div.scroll-bar .el-slider__runway"

    try:
        slider_max = await get_slider_max(page)
        if slider_max is None:
            raise RuntimeError("slider max not found")

        runway = page.locator(slider_sel)
        box = await runway.bounding_box()
        if not box or box.get("width", 0) <= 0:
            raise RuntimeError("slider runway box not found")

        clamped = max(0, min(idx, slider_max))
        ratio = 0 if slider_max <= 0 else clamped / slider_max
        x = box["x"] + box["width"] * ratio
        y = box["y"] + box["height"] / 2

        # 更像真人：直接点到目标位置，让页面自己处理滑块更新和请求触发。
        await page.mouse.click(x, y)
        await page.wait_for_timeout(30)
        return True
    except Exception:
        pass

    ok = await page.evaluate(
        """
        (idx) => {
            const input = document.querySelector(
                'div.scroll-bar .el-slider__input input.el-input__inner'
            );
            if (!input) return false;

            const max = Number(input.getAttribute('max') || '0');
            const clamped = Math.max(0, Math.min(idx, max));
            input.value = String(clamped);

            const evInput = new Event('input', { bubbles: true });
            const evChange = new Event('change', { bubbles: true });
            input.dispatchEvent(evInput);
            input.dispatchEvent(evChange);

            return true;
        }
        """,
        idx,
    )
    return bool(ok)


async def get_slider_max(page) -> int | None:
    """从 slider 的 input.max 里读取最大帧 index."""
    return await page.evaluate(
        """
        () => {
            const input = document.querySelector(
                'div.scroll-bar .el-slider__input input.el-input__inner'
            );
            if (!input) return null;
            const max = Number(input.getAttribute('max') || '0');
            return Number.isFinite(max) ? max : null;
        }
        """
    )


# ================== 核心下载逻辑 ==================


async def download_one(
    p,
    check_url: str,
    out_dir: str,
    mode: str,
    headless: bool,
    skip_hd: bool,
    hd_timeout_ms: int,
    max_rounds: int,
    step_wait_ms: int,
    quiet_checks: int,
    quiet_step_ms: int,
    max_inflight: int,
    overwrite: bool,
    password: str | None = None,
):
    if overwrite and os.path.exists(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)

    browser = await p.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
        ],
    )

    context = await browser.new_context(
        viewport={"width": 1400, "height": 900},
        ignore_https_errors=True,
        locale="zh-CN",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
    )

    page = await context.new_page()
    page.set_default_timeout(120000)
    page.set_default_navigation_timeout(120000)

    try:
        cdp = await context.new_cdp_session(page)
        await cdp.send("Network.enable")
        await cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    except Exception:
        pass

    print(f">>> 打开检查页面: {check_url}")
    await safe_goto(page, check_url, timeout_ms=120000, retries=3)

    # 自动输入安全码（如有）
    await input_password_if_needed(page, password)

    # 等 UI 真正 ready（修复 ctype=4 headless 下“序列按钮找不到/点不到”）
    await wait_viewer_ready(page, timeout_ms=120000)

    if not skip_hd:
        await switch_to_hd_mode(page, timeout_ms=hd_timeout_ms)

    current_series = {"folder": None, "base": 0, "expected": None}
    seen_body_hashes = defaultdict(set)
    saved_counts = defaultdict(int)
    response_stats = defaultdict(Counter)
    response_samples = defaultdict(dict)

    inflight_sem = asyncio.Semaphore(max_inflight)
    pending_tasks = set()

    async def handle_response(resp, folder: str | None):
        """
        监听所有响应，筛选出像 DICOM 的 body 并落盘。
        folder 在 response 事件触发时就快照，避免异步任务延迟导致串 series。
        """
        try:
            if not folder:
                return

            stats = response_stats[folder]
            stats["seen"] += 1

            url = resp.url
            host = urlparse(url).netloc.lower()

            # 只关注这个域（以及你旧系统的 cleandata/imcisfiles）
            url_l = url.lower()
            is_old_fz = ("cleandata" in url_l) and ("imcisfiles" in url_l)
            is_shdc_domain = host.endswith("shdc.org.cn")
            if not (is_old_fz or is_shdc_domain):
                stats["skip_host"] += 1
                return

            # 小文件大概率是 json/js/css，直接跳过
            cl = resp.headers.get("content-length")
            if cl:
                try:
                    if int(cl) < 2048:
                        stats["skip_small"] += 1
                        return
                except Exception:
                    pass

            async with inflight_sem:
                body = await resp.body()

            if not looks_like_dicom_fast(body):
                stats["skip_not_dicom"] += 1
                if "not_dicom" not in response_samples[folder]:
                    response_samples[folder]["not_dicom"] = {
                        "url": url[:160],
                        "content_type": resp.headers.get("content-type", ""),
                        "content_length": resp.headers.get("content-length", ""),
                        "head_hex": body[:24].hex(),
                    }
                return

            body_hash = hashlib.sha1(body).hexdigest()
            if body_hash in seen_body_hashes[folder]:
                stats["skip_duplicate"] += 1
                return
            seen_body_hashes[folder].add(body_hash)

            # 当前 series 已达到期望张数后不再继续写，避免迟到/重复响应造成超量。
            expected = current_series.get("expected") if folder == current_series.get("folder") else None
            base = current_series.get("base", 0) if folder == current_series.get("folder") else 0
            if expected is not None and saved_counts[folder] - base >= expected:
                stats["skip_over_expected"] += 1
                return

            saved_counts[folder] += 1
            stats["saved"] += 1
            idx = saved_counts[folder]

            out_series_dir = os.path.join(out_dir, folder)
            os.makedirs(out_series_dir, exist_ok=True)
            out_path = os.path.join(out_series_dir, f"{idx:05d}.dcm")
            with open(out_path, "wb") as f:
                f.write(body)

        except Exception:
            # 不让监听器把主流程打崩
            return

    def on_response(resp):
        folder = current_series["folder"]
        t = asyncio.create_task(handle_response(resp, folder))
        pending_tasks.add(t)
        t.add_done_callback(lambda _t: pending_tasks.discard(_t))

    context.on("response", on_response)

    series_list = await read_series_list(page)

    if mode == "diag":
        filtered = [s for s in series_list if s["category"] == "diag"]
        print(f">>> 只下载临床诊断序列：共 {len(filtered)} 个")
    elif mode == "nondiag":
        filtered = [s for s in series_list if s["category"] == "nondiag"]
        print(f">>> 只下载辅助/非诊断序列：共 {len(filtered)} 个")
    else:
        filtered = series_list
        print(f">>> 下载所有序列：共 {len(filtered)} 个")

    for n, info in enumerate(filtered, start=1):
        print(f"\n=== [{n}/{len(filtered)}] series {info['series_no']}：{info['desc']} ===")

        await open_series_panel(page)
        cards = page.locator("div.all-serie-wapper div.serie-wapper")
        card = cards.nth(info["index"])

        # 先记录已有数量，再绑定当前 series；点击卡片后极早到达的响应也计入本 series。
        base_count = saved_counts[info["folder"]]
        current_series["folder"] = info["folder"]

        await card.scroll_into_view_if_needed()
        await card.click(force=True)
        await page.wait_for_timeout(300)

        slider_max = await get_slider_max(page)
        if slider_max is None:
            print("    [!] 读取 slider 最大值失败，跳过该 series")
            continue

        num_images = slider_max + 1
        current_series["base"] = base_count
        current_series["expected"] = num_images
        print(
            f"    本 series 共有 {num_images} 张（slider max={slider_max}，卡片显示={info['num_images_from_card']}）"
        )

        for r in range(1, max_rounds + 1):
            before = saved_counts[info["folder"]]
            for i in range(num_images):
                ok = await goto_image_index(page, i)
                if not ok:
                    break
                if step_wait_ms > 0:
                    await page.wait_for_timeout(step_wait_ms)
            after = saved_counts[info["folder"]]
            print(
                f"    >> 第 {r} 轮结束：本轮新增 {after - before}，累计 {after - base_count}/{num_images}"
            )
            if after - before == 0 and r == 1:
                print("    >> slider 本轮未触发 DICOM 请求，跳过键盘 fallback（避免串片/重复保存）")
            if after - base_count >= num_images:
                break

        # 静默等待网络残余
        last_seen = saved_counts[info["folder"]]
        no_change = 0
        for _ in range(quiet_checks):
            await page.wait_for_timeout(quiet_step_ms)
            cur = saved_counts[info["folder"]]
            if cur == last_seen:
                no_change += 1
            else:
                no_change = 0
                last_seen = cur
            if no_change >= 3:
                break

        # 每个 series 结束前先等当前响应任务落盘，避免拖到下一个 series 造成串片。
        if pending_tasks:
            await asyncio.wait(list(pending_tasks), timeout=10)

        final_count = saved_counts[info["folder"]] - base_count
        stats = response_stats[info["folder"]]
        print(f"    >> series 完成：期望 {num_images}，实际保存 {final_count}")
        if final_count == 0:
            print(
                f"    >>> ❌ 该 series 连续 {max_rounds} 轮后仍然 0 张；请检查 series 是否真正切换成功、slider 是否触发、或响应是否被过滤"
            )
            print(
                "    >>> 调试统计："
                f"seen={stats['seen']} saved={stats['saved']} "
                f"skip_host={stats['skip_host']} skip_small={stats['skip_small']} "
                f"skip_not_dicom={stats['skip_not_dicom']} skip_duplicate={stats['skip_duplicate']} "
                f"skip_over_expected={stats['skip_over_expected']}"
            )
            sample = response_samples[info["folder"]].get("not_dicom")
            if sample:
                print(
                    "    >>> 非DICOM样本："
                    f"type={sample['content_type']} len={sample['content_length']} "
                    f"head={sample['head_hex']} url={sample['url']}"
                )
        elif final_count > num_images:
            print("    >>> ⚠ 实际保存超过期望张数，可能存在重复响应或跨 series 混入；建议检查输出目录并谨慎使用该 series")
        elif final_count < num_images:
            print("    >>> ⚠ 可能未完整命中全部切片，可尝试提高 max_rounds 或增大 step_wait_ms")

    # 等待最后一批写盘任务
    if pending_tasks:
        await asyncio.wait(pending_tasks, timeout=30)

    await browser.close()


# ================== 命令行 ==================


def parse_args():
    parser = argparse.ArgumentParser(
        description="基于 Playwright 的网页版影像 DICOM 批量下载工具（shdc，支持多 URL -> 多文件夹 + 多 zip）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="单个检查链接 URL")
    group.add_argument(
        "--urls-file", help="包含多个 URL 的文本文件（每行一个，支持 # 注释）"
    )

    parser.add_argument(
        "--out-parent",
        default="./downloads",
        help="输出父目录（每个 URL 会建一个 share_id 子目录）",
    )
    parser.add_argument(
        "--mode", choices=["diag", "nondiag", "all"], default="all", help="下载模式"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无界面模式运行（更快但少数站点可能更挑）",
    )
    parser.add_argument(
        "--no-headless", dest="headless", action="store_false", help="有界面模式运行"
    )
    parser.set_defaults(headless=False)

    parser.add_argument(
        "--skip-hd", action="store_true", help="跳过“流畅->原图(清晰度高)”切换"
    )
    parser.add_argument(
        "--hd-timeout-ms", type=int, default=10000, help="尝试切换高清时的超时（毫秒）"
    )

    parser.add_argument(
        "--max-rounds",
        type=int,
        default=2,
        help="每个 series 最多扫几轮（越大越完整但越慢）",
    )
    parser.add_argument(
        "--step-wait-ms",
        type=int,
        default=25,
        help="每跳一张等待多久（毫秒）；越小越快但更可能漏片",
    )
    parser.add_argument(
        "--quiet-checks", type=int, default=6, help="播放结束后静默观察次数"
    )
    parser.add_argument(
        "--quiet-step-ms", type=int, default=800, help="静默观察间隔（毫秒）"
    )

    parser.add_argument(
        "--max-inflight",
        type=int,
        default=6,
        help="同时抓取/写盘的最大并发（过大可能卡）",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="如果输出目录已存在则先删除再下"
    )

    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="不打 zip（默认每个 URL 都会生成一个 zip）",
    )

    return parser.parse_args()


async def main():
    args = parse_args()

    if args.url:
        urls = [args.url]
    else:
        urls = read_urls_file(args.urls_file)

    out_parent = os.path.abspath(args.out_parent)
    os.makedirs(out_parent, exist_ok=True)

    print("\n>>> 启动参数：")
    print(f"    URL数量     : {len(urls)}")
    print(f"    out_parent  : {out_parent}")
    print(f"    mode        : {args.mode}")
    print(f"    headless    : {args.headless}")
    print(f"    skip_hd     : {args.skip_hd}")
    print(f"    max_rounds  : {args.max_rounds}")
    print(f"    step_wait   : {args.step_wait_ms}ms\n")

    async with async_playwright() as p:
        for i, url in enumerate(urls, start=1):
            share_id = extract_share_id(url)
            out_dir = os.path.join(out_parent, share_id)

            print("=" * 80)
            print(f"### [{i}/{len(urls)}] 开始下载")
            print(f"URL      : {url}")
            print(f"输出目录 : {out_dir}")
            print("=" * 80)

            try:
                await download_one(
                    p=p,
                    check_url=url,
                    out_dir=out_dir,
                    mode=args.mode,
                    headless=args.headless,
                    skip_hd=args.skip_hd,
                    hd_timeout_ms=args.hd_timeout_ms,
                    max_rounds=args.max_rounds,
                    step_wait_ms=args.step_wait_ms,
                    quiet_checks=args.quiet_checks,
                    quiet_step_ms=args.quiet_step_ms,
                    max_inflight=args.max_inflight,
                    overwrite=args.overwrite,
                )
            except Exception as e:
                print(f">>> ❌ 失败：{url}")
                print(f">>> 错误：{e}")
                continue

            if not args.no_zip:
                zip_path = os.path.join(out_parent, f"{share_id}.zip")
                make_zip_dir(out_dir, zip_path)

    print("\n>>> 全部任务结束")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n>>> 收到中断信号，已退出。")
