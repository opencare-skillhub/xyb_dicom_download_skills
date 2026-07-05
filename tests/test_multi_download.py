import sys
import types
import asyncio
import zipfile
from pathlib import Path


def install_playwright_stub():
    """
    提前注入一个最小的 playwright.async_api stub，避免导入失败。
    （测试中我们会 monkeypatch 掉实际调用，因此不需要真实浏览器）
    """
    if "playwright" not in sys.modules:
        sys.modules["playwright"] = types.ModuleType("playwright")
    if "playwright.async_api" not in sys.modules:
        async_api = types.ModuleType("playwright.async_api")

        async def _dummy_async_playwright():
            class _Mgr:
                async def __aenter__(self):
                    class _Ctx:
                        pass

                    return _Ctx()

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            return _Mgr()

        # 测试中不会真正用到该上下文；保持属性存在即可
        async_api.async_playwright = _dummy_async_playwright
        sys.modules["playwright.async_api"] = async_api


def test_multi_download_routing_and_outputs(tmp_path, monkeypatch):
    install_playwright_stub()
    # 延后导入，确保 stub 生效
    import multi_download as md
    from common_utils import extract_share_id

    # 伪造 provider 执行：仅在 out_dir 里写一个标记文件，模拟“下载成功”
    async def fake_run(url, out_dir, *args, **kwargs):
        p = Path(out_dir) / "ok.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("ok", encoding="utf-8")

    monkeypatch.setattr(md, "run_tz_one", fake_run)
    monkeypatch.setattr(md, "run_fz_one", fake_run)
    monkeypatch.setattr(md, "run_nyfy_one", fake_run)

    def fake_run_cloud(url, out_dir, args):
        p = Path(out_dir) / "ok.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("ok", encoding="utf-8")

    monkeypatch.setattr(md, "run_cloud_one", fake_run_cloud)

    urls = [
        "https://zlyy.tjmucih.cn/viewer?shareId=AA",
        "https://zhyl.nyfy.com.cn/viewer?share_id=BB",
        "https://ylyyx.shdc.org.cn/view/CC",
        "https://mdmis.cq12320.cn/wcs1/mdmis-app/h5/#/share/detail?share_id=DD&content=EE&channel=share",
    ]

    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("\n".join(urls), encoding="utf-8")

    out_parent = tmp_path / "downloads"

    # 运行 router：使用 urls 文件，要求 zip
    argv_backup = sys.argv[:]
    try:
        sys.argv = [
            "multi_download.py",
            "--urls-file",
            str(urls_file),
            "--out-parent",
            str(out_parent),
            "--headless",
        ]
        asyncio.run(md.main())
    finally:
        sys.argv = argv_backup

    # 验证每个 URL 的输出目录与 zip
    for u in urls:
        sid = extract_share_id(u)
        out_dir = out_parent / sid
        assert (out_dir / "ok.txt").exists()
        zip_path = out_parent / f"{sid}.zip"
        assert zip_path.exists()
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            names = zf.namelist()
            # 目录结构在 zip 里包含父目录名，因此只检测末尾
            assert any(n.endswith(f"{sid}/ok.txt") for n in names)
