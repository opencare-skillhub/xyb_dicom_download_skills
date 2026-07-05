import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from common_utils import (
    extract_share_id,
    make_zip_dir,
    read_urls_file_with_passwords,
)


def detect_provider(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "zlyy.tjmucih.cn" in host:
        return "tz"
    if "zhyl.nyfy.com.cn" in host:
        return "nyfy"
    if host.endswith("shdc.org.cn") or "ylyyx.shdc.org.cn" in host:
        return "fz"

    # cloud-dicom-downloader 支持的站点（保持与其 downloader.py 的路由一致）
    if host.endswith(".medicalimagecloud.com"):
        return "cloud"
    if host in (
        "mdmis.cq12320.cn",
        "qr.szjudianyun.com",
        "zscloud.zs-hospital.sh.cn",
        "app.ftimage.cn",
        "yyx.ftimage.cn",
        "m.yzhcloud.com",
        "ss.mtywcloud.com",
        "work.sugh.net",
        "cloudpacs.jdyfy.com",
    ):
        return "cloud"

    return "fz"  # 兜底：按 fz 策略尝试


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="多站点 DICOM 下载路由器：按域名自动选择脚本/策略，输出每 URL 独立目录与 zip"
    )
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="单个检查链接 URL")
    group.add_argument(
        "--urls-file", help="包含多个 URL 的文本文件（每行一个，支持 # 注释）"
    )

    ap.add_argument(
        "--provider",
        choices=["auto", "tz", "fz", "nyfy", "cloud"],
        default="auto",
        help=(
            "手动指定 provider（默认 auto：按域名自动识别）。"
            "tz=天肿(zlyy.tjmucih.cn)，fz=复肿(ylyyx.shdc.org.cn)，nyfy=宁夏总医院(zhyl.nyfy.com.cn)"
        ),
    )
    ap.add_argument(
        "--mode",
        choices=["diag", "nondiag", "all"],
        default="all",
        help="下载模式（对 UI 抓取策略生效）",
    )
    ap.add_argument("--headless", action="store_true", help="无界面模式运行")
    ap.add_argument("--no-headless", dest="headless", action="store_false")
    ap.set_defaults(headless=False)

    # UI 抓取策略（天肿/复肿；tz/fz）参数
    ap.add_argument(
        "--skip-hd", action="store_true", help="跳过高清切换（复肿 fz 有效）"
    )
    ap.add_argument(
        "--hd-timeout-ms", type=int, default=10000, help="高清切换超时（毫秒）"
    )
    ap.add_argument(
        "--max-rounds", type=int, default=3, help="逐帧播放轮数上限（复肿 fz，默认3轮）"
    )
    ap.add_argument(
        "--step-wait-ms", type=int, default=50, help="逐帧间隔（毫秒）（复肿 fz，默认50ms确保完整性）"
    )
    ap.add_argument(
        "--quiet-checks", type=int, default=6, help="静默观察次数（复肿 fz）"
    )
    ap.add_argument(
        "--quiet-step-ms", type=int, default=800, help="静默观察间隔（毫秒）（复肿 fz）"
    )
    ap.add_argument(
        "--max-inflight", type=int, default=6, help="抓取/写盘最大并发（复肿 fz）"
    )
    ap.add_argument(
        "--overwrite", action="store_true", help="若输出目录存在则先删除（复肿 fz）"
    )

    # cloud-dicom-downloader（子进程）参数
    ap.add_argument(
        "--cloud-password",
        default=None,
        help="cloud provider 密码（仅 *.medicalimagecloud.com 这类链接必需）",
    )
    ap.add_argument(
        "--cloud-raw",
        action="store_true",
        help="cloud provider 下载 raw（上游 --raw；默认下载 JPEG2000 无损）",
    )
    ap.add_argument(
        "--cloud-keep-temp",
        action="store_true",
        help="cloud provider 失败/调试时保留临时目录（打印路径）",
    )

    # 宁夏总医院（nyfy；WS+h5Cache）参数
    ap.add_argument(
        "--nyfy-concurrency", type=int, default=2, help="宁夏总医院（nyfy）下载并发"
    )
    ap.add_argument(
        "--nyfy-download-retries",
        type=int,
        default=4,
        help="宁夏总医院（nyfy）重试次数",
    )
    ap.add_argument(
        "--nyfy-http-timeout-ms",
        type=int,
        default=60000,
        help="宁夏总医院（nyfy）HTTP 超时",
    )
    ap.add_argument(
        "--nyfy-retry-backoff-ms",
        type=int,
        default=250,
        help="宁夏总医院（nyfy）重试退避（毫秒）",
    )
    ap.add_argument(
        "--nyfy-backfill-rounds", type=int, default=5, help="宁夏总医院（nyfy）回填轮数"
    )
    ap.add_argument(
        "--nyfy-verify", action="store_true", help="宁夏总医院（nyfy）下载后校验 DICOM"
    )
    ap.add_argument("--nyfy-no-verify", dest="nyfy_verify", action="store_false")
    ap.set_defaults(nyfy_verify=False)

    ap.add_argument(
        "--out-parent",
        default="./downloads",
        help="输出父目录（每个 URL 会建一个 share_id 子目录）",
    )
    ap.add_argument("--no-zip", action="store_true", help="不为每个 URL 生成 zip")
    return ap


async def run_tz_one(url: str, out_dir: str, mode: str, headless: bool):
    # 延迟导入：避免在仅运行复肿/宁夏总医院时引入天肿的重依赖（如 pydicom/numpy）
    import tjmucih_download_dicom as tz_mod

    await tz_mod.run_downloader(
        check_url=url, out_root=out_dir, download_mode=mode, headless=headless
    )


async def run_fz_one(url: str, out_dir: str, mode: str, headless: bool, router_args, password: str | None = None):
    # 延迟导入：避免只运行其他 provider 时加载不必要的模块
    import shdc_download_dicom as fz_mod
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        await fz_mod.download_one(
            p=p,
            check_url=url,
            out_dir=out_dir,
            mode=mode,
            headless=headless,
            skip_hd=router_args.skip_hd,
            hd_timeout_ms=router_args.hd_timeout_ms,
            max_rounds=router_args.max_rounds,
            step_wait_ms=router_args.step_wait_ms,
            quiet_checks=router_args.quiet_checks,
            quiet_step_ms=router_args.quiet_step_ms,
            max_inflight=router_args.max_inflight,
            overwrite=router_args.overwrite,
            password=password,
        )


async def run_nyfy_one(
    url: str, out_dir: str, headless: bool, zip_dir: str | None, router_args
):
    # 延迟导入：避免只运行其他 provider 时加载不必要的模块
    import nyfy_download_dicom as nyfy_mod
    from playwright.async_api import async_playwright

    args_ns = SimpleNamespace(
        url=url,
        out_dir=out_dir,
        password=None,
        headless=headless,
        concurrency=router_args.nyfy_concurrency,
        download_retries=router_args.nyfy_download_retries,
        http_timeout_ms=router_args.nyfy_http_timeout_ms,
        retry_backoff_ms=router_args.nyfy_retry_backoff_ms,
        autoplay_rounds=3,
        autoplay_delay_ms=90,
        quiet_wait_ms=1500,
        fallback_steps_per_round=900,
        backfill_rounds=router_args.nyfy_backfill_rounds,
        zip=False,  # 统一由 router 打包
        zip_dir=zip_dir or ".",
        verify=router_args.nyfy_verify,
        verify_report="verify_report.json",
        ct_intercept=0.0,
        ct_slope=1.0,
        quiet=False,
        verbose=False,
    )

    d = nyfy_mod.Downloader(args_ns)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=args_ns.headless)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900}, ignore_https_errors=True
        )
        page = await context.new_page()
        page.set_default_timeout(120000)
        page.set_default_navigation_timeout(120000)

        def on_websocket(ws):
            def on_frame(payload: bytes):
                obj = nyfy_mod.ws_payload_to_json(payload)
                if obj:
                    d.on_ws_message(obj)

            ws.on("framereceived", on_frame)

        page.on("websocket", on_websocket)

        print(">>> 打开检查页面:", url)
        await nyfy_mod.safe_goto(page, url)
        await nyfy_mod.maybe_click_dialog_button(page, "我知道了", timeout_ms=1500)
        await nyfy_mod.handle_password_if_needed(page, args_ns.password)

        frame = await nyfy_mod.get_viewer_frame(page)
        print(">>> 已进入 viewer iframe")

        referer = url
        workers = [
            asyncio.create_task(d.worker(context.request, referer))
            for _ in range(max(1, args_ns.concurrency))
        ]
        hb = asyncio.create_task(d.heartbeat())

        await d.autoplay_collect(page, frame)
        await d.wait_and_backfill_until_done()
        await asyncio.sleep(1.0)
        await d.wait_and_backfill_until_done()

        hb.cancel()
        for w in workers:
            w.cancel()

        print(
            f">>> DONE: meta={len(d.meta_by_uid)} saved={len(d.saved_uids)} failed={len(d.failed)}"
        )
        if d.failed_status and not args_ns.quiet:
            print(">>> HTTP status summary:", dict(d.failed_status))

        await browser.close()


def _cloud_downloader_path() -> Path:
    """
    返回 cloud-dicom-downloader/downloader.py 的绝对路径。
    以脚本所在目录为基准，避免从不同 cwd 运行时找不到。
    """
    here = Path(__file__).resolve().parent
    return here / "cloud-dicom-downloader" / "downloader.py"


def _move_cloud_download_to_out(
    tmp_workdir: Path, out_dir: Path, overwrite: bool
) -> None:
    """
    上游默认写到 tmp_workdir/download/<study_dir>/...，这里把 <study_dir> 迁移为 out_dir。
    由于 out_dir 是 per-URL 的唯一目录，我们以“整目录替换”为主策略。
    """
    download_root = tmp_workdir / "download"
    if not download_root.exists():
        raise RuntimeError(f"cloud: 未产生 download 目录：{download_root}")

    candidates = [p for p in download_root.iterdir() if p.is_dir()]
    if not candidates:
        raise RuntimeError(f"cloud: download 目录为空：{download_root}")

    # 通常只有一个检查目录；若多个则选最新的那个
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    study_dir = candidates[0]

    if out_dir.exists():
        has_any = any(out_dir.iterdir())
        if has_any and (not overwrite):
            raise RuntimeError(
                f"输出目录已存在且非空：{out_dir}。如需覆盖请加 --overwrite"
            )
        if overwrite:
            shutil.rmtree(out_dir, ignore_errors=True)

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(study_dir), str(out_dir))


def run_cloud_one(url: str, out_dir: str, router_args) -> None:
    """
    方式B：子进程运行 cloud-dicom-downloader/downloader.py，工作目录指向临时目录以控制输出，
    运行后将 tmp/download 下的结果迁移到 out_dir。
    """
    downloader_py = _cloud_downloader_path()
    if not downloader_py.exists():
        raise RuntimeError(f"未找到上游 downloader.py：{downloader_py}")

    host = urlparse(url).netloc.lower()

    cmd = [sys.executable, str(downloader_py), url]
    if host.endswith(".medicalimagecloud.com"):
        if not router_args.cloud_password:
            raise RuntimeError(
                "cloud: 该链接需要密码（*.medicalimagecloud.com），请提供 --cloud-password"
            )
        cmd.append(router_args.cloud_password)

    if router_args.cloud_raw:
        cmd.append("--raw")

    out_path = Path(out_dir).resolve()
    tmp_dir_obj = tempfile.TemporaryDirectory(prefix="cloud_dicom_")
    tmp_workdir = Path(tmp_dir_obj.name)

    try:
        # 在临时目录作为 cwd 运行，上游会把文件写到 ./download/...
        # 但脚本路径是绝对路径，因此 import crawlers 仍然从脚本目录解析（可用）。
        proc = subprocess.run(
            cmd,
            cwd=str(tmp_workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if proc.returncode != 0:
            tail = "\n".join((proc.stderr or "").splitlines()[-60:])
            raise RuntimeError(
                f"cloud 子进程失败（exit={proc.returncode}）。stderr tail:\n{tail}"
            )

        _move_cloud_download_to_out(
            tmp_workdir, out_path, overwrite=router_args.overwrite
        )
    except Exception:
        if router_args.cloud_keep_temp:
            print(f"[cloud] 保留临时目录用于调试：{tmp_workdir}")
            # 不清理
            tmp_dir_obj.cleanup = lambda: None  # type: ignore
        raise
    finally:
        try:
            tmp_dir_obj.cleanup()
        except Exception:
            pass


async def main():
    ap = build_parser()
    args = ap.parse_args()

    if args.url:
        urls_with_password = [(args.url, None)]
    else:
        urls_with_password = read_urls_file_with_passwords(args.urls_file)

    out_parent = os.path.abspath(args.out_parent)
    os.makedirs(out_parent, exist_ok=True)

    print("\n>>> 启动参数：")
    print(f"    URL数量     : {len(urls_with_password)}")
    print(f"    out_parent  : {out_parent}")
    print(f"    headless    : {args.headless}\n")

    for i, (url, password) in enumerate(urls_with_password, start=1):
        prov = args.provider if args.provider != "auto" else detect_provider(url)
        share_id = extract_share_id(url)
        out_dir = os.path.join(out_parent, share_id)
        os.makedirs(out_dir, exist_ok=True)

        print("=" * 80)
        print(f"### [{i}/{len(urls_with_password)}] provider={prov}")
        print(f"URL      : {url}")
        if password:
            print(f"安全码   : {'*' * len(password)}")
        print(f"输出目录 : {out_dir}")
        print("=" * 80)

        try:
            if prov == "tz":
                await run_tz_one(url, out_dir, args.mode, args.headless)
            elif prov == "fz":
                await run_fz_one(url, out_dir, args.mode, args.headless, args, password)
            elif prov == "nyfy":
                await run_nyfy_one(url, out_dir, args.headless, out_parent, args)
            elif prov == "cloud":
                # cloud-dicom-downloader 走子进程（方式B）
                run_cloud_one(url, out_dir, args)
            else:
                await run_fz_one(url, out_dir, args.mode, args.headless, args, password)
        except Exception as e:
            print(f">>> ❌ 失败：{url}")
            print(f">>> 错误：{e}")
            continue

        if not args.no_zip:
            zip_path = os.path.join(out_parent, f"{share_id}.zip")
            make_zip_dir(out_dir, zip_path)
            print(f">>> zip 已生成：{os.path.abspath(zip_path)}")

    print("\n>>> 全部任务结束")


if __name__ == "__main__":
    asyncio.run(main())
