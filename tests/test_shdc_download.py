import asyncio
from contextlib import asynccontextmanager

import shdc_download_dicom as shdc


class FakeResponse:
    def __init__(self, url: str, body: bytes):
        self.url = url
        self._body = body
        self.headers = {}

    async def body(self):
        return self._body


class FakeCard:
    def __init__(self, context, emit_response: bool):
        self._context = context
        self._emit_response = emit_response

    async def scroll_into_view_if_needed(self):
        return None

    async def click(self, force: bool = False):
        if self._emit_response and self._context.response_handler:
            self._context.emit_dicom_response()
        return None


class FakeCards:
    def __init__(self, context, emit_response: bool):
        self._context = context
        self._emit_response = emit_response

    def nth(self, index: int):
        return FakeCard(self._context, self._emit_response)


class FakeMouse:
    def __init__(self, context, emit_response: bool):
        self._context = context
        self._emit_response = emit_response

    async def click(self, x, y):
        if self._emit_response and self._context.response_handler:
            self._context.emit_dicom_response()


class FakeRunway:
    async def bounding_box(self):
        return {"x": 0, "y": 0, "width": 100, "height": 20}


class FakePage:
    def __init__(self, context, emit_response: bool):
        self._context = context
        self._emit_response = emit_response
        self.mouse = FakeMouse(context, emit_response)

    def locator(self, selector: str, **kwargs):
        if selector == "div.all-serie-wapper div.serie-wapper":
            return FakeCards(self._context, self._emit_response)
        if selector == "div.scroll-bar .el-slider__runway":
            return FakeRunway()
        raise AssertionError(f"unexpected locator: {selector}")

    async def wait_for_timeout(self, ms: int):
        await asyncio.sleep(0)

    def set_default_timeout(self, timeout: int):
        return None

    def set_default_navigation_timeout(self, timeout: int):
        return None


class FakeContext:
    def __init__(self, emit_response: bool):
        self.emit_response = emit_response
        self.response_handler = None
        self.response_index = 0
        self.page = FakePage(self, emit_response)

    async def new_page(self):
        return self.page

    def on(self, event: str, handler):
        assert event == "response"
        self.response_handler = handler

    def emit_dicom_response(self):
        self.response_index += 1
        body = b"\x02\x00\x00\x00" + bytes([self.response_index % 256]) * 32
        resp = FakeResponse("https://ylyyx.shdc.org.cn/api/yunyingxiang/get_dcm_jpg", body)
        self.response_handler(resp)

    @property
    def request(self):
        return object()


class FakeBrowser:
    def __init__(self, emit_response: bool):
        self.emit_response = emit_response
        self.context = FakeContext(emit_response)

    async def new_context(self, **kwargs):
        return self.context

    async def close(self):
        return None


class FakeChromium:
    def __init__(self, emit_response: bool):
        self.emit_response = emit_response

    async def launch(self, **kwargs):
        return FakeBrowser(self.emit_response)


class FakePlaywright:
    def __init__(self, emit_response: bool):
        self.chromium = FakeChromium(emit_response)


def _patch_noop_download(monkeypatch, emit_response: bool, num_images: int = 1):
    async def _noop(*args, **kwargs):
        return None

    async def _read_series_list(page):
        return [
            {
                "index": 0,
                "series_no": "9",
                "desc": "Body 3.0 CE",
                "folder": "series_9_Body_3.0_CE",
                "num_images_from_card": num_images,
                "category": "diag",
            }
        ]

    async def _get_slider_max(page):
        return num_images - 1

    @asynccontextmanager
    async def _fake_async_playwright():
        yield FakePlaywright(emit_response)

    monkeypatch.setattr(shdc, "safe_goto", _noop)
    monkeypatch.setattr(shdc, "wait_viewer_ready", _noop)
    monkeypatch.setattr(shdc, "switch_to_hd_mode", _noop)
    monkeypatch.setattr(shdc, "open_series_panel", _noop)
    monkeypatch.setattr(shdc, "read_series_list", _read_series_list)
    monkeypatch.setattr(shdc, "get_slider_max", _get_slider_max)
    monkeypatch.setattr(shdc, "async_playwright", _fake_async_playwright)


def test_shdc_click_before_response_saves_slice(tmp_path, monkeypatch):
    _patch_noop_download(monkeypatch, emit_response=True, num_images=1)

    out_dir = tmp_path / "out"

    async def run():
        async with shdc.async_playwright() as p:
            await shdc.download_one(
                p=p,
                check_url="https://ylyyx.shdc.org.cn/code.html?appid=yilian&share_id=test&ctype=5",
                out_dir=str(out_dir),
                mode="all",
                headless=True,
                skip_hd=True,
                hd_timeout_ms=1,
                max_rounds=1,
                step_wait_ms=0,
                quiet_checks=0,
                quiet_step_ms=0,
                max_inflight=1,
                overwrite=False,
            )

    asyncio.run(run())

    saved = out_dir / "series_9_Body_3.0_CE" / "00001.dcm"
    assert saved.exists()


def test_shdc_same_url_different_bodies_are_saved(tmp_path, monkeypatch):
    _patch_noop_download(monkeypatch, emit_response=True, num_images=2)

    out_dir = tmp_path / "out"

    async def run():
        async with shdc.async_playwright() as p:
            await shdc.download_one(
                p=p,
                check_url="https://ylyyx.shdc.org.cn/code.html?appid=yilian&share_id=test&ctype=5",
                out_dir=str(out_dir),
                mode="all",
                headless=True,
                skip_hd=True,
                hd_timeout_ms=1,
                max_rounds=1,
                step_wait_ms=0,
                quiet_checks=0,
                quiet_step_ms=0,
                max_inflight=1,
                overwrite=False,
            )

    asyncio.run(run())

    files = sorted((out_dir / "series_9_Body_3.0_CE").glob("*.dcm"))
    assert len(files) >= 2


def test_shdc_zero_save_warns_clearly(tmp_path, monkeypatch, capsys):
    _patch_noop_download(monkeypatch, emit_response=False, num_images=1)

    out_dir = tmp_path / "out"

    async def run():
        async with shdc.async_playwright() as p:
            await shdc.download_one(
                p=p,
                check_url="https://ylyyx.shdc.org.cn/code.html?appid=yilian&share_id=test&ctype=5",
                out_dir=str(out_dir),
                mode="all",
                headless=True,
                skip_hd=True,
                hd_timeout_ms=1,
                max_rounds=3,
                step_wait_ms=0,
                quiet_checks=0,
                quiet_step_ms=0,
                max_inflight=1,
                overwrite=False,
            )

    asyncio.run(run())
    captured = capsys.readouterr().out
    assert "实际保存 0" in captured
    assert "连续 3 轮后仍然 0 张" in captured
