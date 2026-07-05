#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import json
import os
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple

from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from common_utils import extract_share_id, read_urls_file, make_zip_dir

# -------------------- 小工具 --------------------


def safe_name(s: str, max_len: int = 120) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[\\/:\*\?\"<>\|]", "_", s)
    return s[:max_len] or "unnamed"


def ws_payload_to_json(payload: bytes) -> Optional[Dict[str, Any]]:
    """
    你抓到的 WS frame 形如:
      b'wanliyun"\\x04\\x00\\x00\\x00\\x00{...json...}'
    最稳的做法：在 payload 里找第一个 '{' 和最后一个 '}'，截出来 json 解析。
    """
    if not payload:
        return None
    l = payload.find(b"{")
    r = payload.rfind(b"}")
    if l < 0 or r < 0 or r <= l:
        return None
    try:
        txt = payload[l : r + 1].decode("utf-8", errors="ignore")
        return json.loads(txt)
    except Exception:
        return None


def infer_h5_base_from_furl(furl: str) -> Optional[str]:
    """
    furl 例：
      http://zhyl.nyfy.com.cn:50650/h5Cache/<study_uid>/<sop_uid>
    返回：
      http://zhyl.nyfy.com.cn:50650/h5Cache/<study_uid>/
    """
    if not furl:
        return None
    m = re.match(r"^(https?://[^/]+/h5Cache/[^/]+/)", furl)
    return m.group(1) if m else None


def ymd_to_dicom_date(s: Optional[str]) -> Optional[str]:
    # "2025-12-13" -> "20251213"
    if not s:
        return None
    s = s.strip()
    if re.match(r"^\d{8}$", s):
        return s
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if not m:
        return None
    return f"{m.group(1)}{m.group(2)}{m.group(3)}"


def hms_to_dicom_time(s: Optional[str]) -> Optional[str]:
    # "10:48:48" -> "104848"
    if not s:
        return None
    s = s.strip()
    if re.match(r"^\d{6}(\.\d+)?$", s):
        return s
    m = re.match(r"^(\d{2}):(\d{2}):(\d{2})(\.\d+)?$", s)
    if not m:
        return None
    frac = m.group(4) or ""
    return f"{m.group(1)}{m.group(2)}{m.group(3)}{frac}"


async def safe_goto(page, url: str, timeout_ms: int = 120000, retries: int = 3):
    last = None
    for i in range(1, retries + 1):
        try:
            print(f">>> 打开页面（尝试 {i}/{retries}）: {url}")
            await page.goto(url, wait_until="commit", timeout=timeout_ms)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                pass
            return
        except PlaywrightTimeoutError as e:
            last = e
            await page.wait_for_timeout(1500)
    raise last


async def maybe_click_dialog_button(page, text: str, timeout_ms: int = 1500):
    try:
        btn = page.get_by_role("button", name=text)
        await btn.wait_for(state="visible", timeout=timeout_ms)
        await btn.click()
        return True
    except Exception:
        return False


async def handle_password_if_needed(
    page, password: Optional[str], timeout_ms: int = 120000
):
    """
    不阻塞事件循环：
    - 若出现密码弹窗：
      1) 传了 --password：自动填+点“验证密码”
      2) 没传：如果你已经在网页里填好了 -> 自动点“验证密码”
      3) 否则：提示你手动输入并点击，然后等待弹窗消失
    """
    dlg = page.locator("div.password-input-dialog")
    try:
        await dlg.wait_for(state="visible", timeout=4000)
    except Exception:
        return  # 不需要密码

    print(">>> 检测到分享密码弹窗")

    inp = dlg.locator("input.el-input__inner")
    btn = dlg.get_by_role("button", name=re.compile(r"验证密码"))

    if password:
        await inp.fill(password)
        await btn.click()
        await dlg.wait_for(state="hidden", timeout=timeout_ms)
        print(">>> 密码验证通过（弹窗已关闭）")
        return

    try:
        cur_val = await inp.input_value()
    except Exception:
        cur_val = ""

    if cur_val.strip():
        print(">>> 检测到输入框已有内容，自动点击【验证密码】")
        await btn.click()
        await dlg.wait_for(state="hidden", timeout=timeout_ms)
        print(">>> 密码验证通过（弹窗已关闭）")
        return

    print(">>> 请在浏览器里手动输入密码并点击【验证密码】...（脚本等待弹窗关闭）")
    await dlg.wait_for(state="hidden", timeout=timeout_ms)
    print(">>> 密码验证通过（弹窗已关闭）")


async def get_viewer_frame(page, timeout_ms: int = 120000):
    """
    顶层页面里有 <iframe class="image-iframe" src=".../api/dispRender?...">
    必须拿 ElementHandle 才能 content_frame()
    """
    await page.wait_for_selector("iframe.image-iframe", timeout=timeout_ms)
    iframe_el = await page.query_selector("iframe.image-iframe")
    if not iframe_el:
        raise RuntimeError("未找到 iframe.image-iframe")
    frame = await iframe_el.content_frame()
    if not frame:
        raise RuntimeError("iframe.content_frame() 失败")
    return frame


# -------------------- DICOM 封装（关键） --------------------

CT_IMAGE_STORAGE = "1.2.840.10008.5.1.4.1.1.2"
MR_IMAGE_STORAGE = "1.2.840.10008.5.1.4.1.1.4"


def build_and_save_part10_dicom(
    out_path: str,
    pixel_bytes: bytes,
    modality: str,
    patient: Dict[str, Any],
    study_uid: str,
    series_uid: str,
    sop_uid: str,
    rows: int,
    cols: int,
    pixel_representation: int,
    instance_number: Optional[int] = None,
    series_number: Optional[int] = None,
    series_desc: Optional[str] = None,
    study_date: Optional[str] = None,
    study_time: Optional[str] = None,
    bits_alloc: int = 16,
    bits_stored: int = 16,
    samples_per_pixel: int = 1,
    window_center: Optional[float] = None,
    window_width: Optional[float] = None,
    pixel_spacing: Optional[Tuple[float, float]] = None,
    slice_thickness: Optional[float] = None,
    image_position: Optional[Tuple[float, float, float]] = None,
    image_orientation: Optional[Tuple[float, float, float, float, float, float]] = None,
    rescale_intercept: Optional[float] = None,
    rescale_slope: Optional[float] = None,
):
    modality = (modality or "").upper()
    sop_class = CT_IMAGE_STORAGE if modality == "CT" else MR_IMAGE_STORAGE

    fm = FileMetaDataset()
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    fm.ImplementationClassUID = generate_uid()
    fm.MediaStorageSOPClassUID = sop_class
    fm.MediaStorageSOPInstanceUID = sop_uid

    ds = FileDataset(out_path, {}, file_meta=fm, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.Modality = modality

    if study_date:
        ds.StudyDate = study_date
    if study_time:
        ds.StudyTime = study_time

    if series_desc:
        ds.SeriesDescription = str(series_desc)

    if series_number is not None:
        ds.SeriesNumber = int(series_number)
    if instance_number is not None:
        ds.InstanceNumber = int(instance_number)

    if patient.get("name"):
        ds.PatientName = str(patient["name"])
    if patient.get("sex"):
        ds.PatientSex = str(patient["sex"])
    if patient.get("age"):
        ds.PatientAge = str(patient["age"])
    if patient.get("birthday"):
        ds.PatientBirthDate = str(patient["birthday"]).replace("-", "")
    if patient.get("patid"):
        ds.PatientID = str(patient["patid"])
    if patient.get("access"):
        ds.AccessionNumber = str(patient["access"])
    if patient.get("des"):
        ds.StudyDescription = str(patient["des"])

    ds.Rows = int(rows)
    ds.Columns = int(cols)
    ds.SamplesPerPixel = int(samples_per_pixel)
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = int(bits_alloc)
    ds.BitsStored = int(bits_stored)
    ds.HighBit = int(bits_stored) - 1
    ds.PixelRepresentation = int(pixel_representation)

    if window_center is not None:
        ds.WindowCenter = float(window_center)
    if window_width is not None:
        ds.WindowWidth = float(window_width)

    if pixel_spacing:
        ds.PixelSpacing = [float(pixel_spacing[0]), float(pixel_spacing[1])]

    if slice_thickness is not None:
        ds.SliceThickness = float(slice_thickness)

    if image_position:
        ds.ImagePositionPatient = [float(x) for x in image_position]

    if image_orientation:
        ds.ImageOrientationPatient = [float(x) for x in image_orientation]

    if modality == "CT":
        if rescale_slope is not None:
            ds.RescaleSlope = float(rescale_slope)
        if rescale_intercept is not None:
            ds.RescaleIntercept = float(rescale_intercept)

    ds.PixelData = pixel_bytes

    ds.save_as(out_path, write_like_original=False)


# -------------------- WS meta 数据结构 --------------------


@dataclass
class ImageMeta:
    study_uid: str
    series_uid: str
    sop_uid: str
    modality: str
    series_desc: str
    series_num: Optional[int]
    instance_num: Optional[int]
    width: int
    height: int
    byte_pp: int
    storebits: int
    pixel_pre: Optional[int]  # PixelRepresentation (0/1)
    window_width: Optional[float]
    window_center: Optional[float]
    row_spacing: Optional[float]
    col_spacing: Optional[float]
    slice_thickness: Optional[float]
    pos: Optional[Tuple[float, float, float]]
    iop: Optional[Tuple[float, float, float, float, float, float]]
    patient: Dict[str, Any]
    study_date: Optional[str]
    study_time: Optional[str]
    furl: Optional[str]
    fsz: Optional[int]


# -------------------- 主下载器 --------------------


class Downloader:
    def __init__(self, args):
        self.args = args
        self.out_root = args.out_dir
        os.makedirs(self.out_root, exist_ok=True)

        self.meta_by_uid: Dict[str, ImageMeta] = {}
        self.furl_by_uid: Dict[str, str] = {}
        self.fsz_by_uid: Dict[str, int] = {}
        self.h5_base_by_study: Dict[str, str] = (
            {}
        )  # study_uid -> "http://host:50650/h5Cache/<study_uid>/"
        self.global_h5_base: Optional[str] = None

        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.enqueued: set[str] = set()

        self.saved_uids: set[str] = set()
        self.series_saved_count: Dict[str, int] = defaultdict(int)

        self.failed: Dict[str, int] = defaultdict(int)  # uid -> fail count
        self.failed_status: Dict[str, int] = defaultdict(int)

        self.last_ws_ts = 0.0
        self.last_save_ts = 0.0
        self.study_uid_for_zip: Optional[str] = None

        self.sem = asyncio.Semaphore(max(1, args.concurrency))

    def folder_for_series(
        self, modality: str, series_num: Optional[int], desc: str, series_uid: str
    ) -> str:
        sn = f"S{series_num}" if series_num is not None else "S?"
        tail = safe_name(series_uid.split(".")[-1], 32)
        return safe_name(f"{modality}_{sn}_{desc}_{tail}")

    def path_for_uid(self, m: ImageMeta) -> str:
        folder = self.folder_for_series(
            m.modality, m.series_num, m.series_desc, m.series_uid
        )
        out_dir = os.path.join(self.out_root, folder)
        os.makedirs(out_dir, exist_ok=True)

        self.series_saved_count[m.series_uid] += 1
        idx = self.series_saved_count[m.series_uid]
        return os.path.join(out_dir, f"{idx:05d}.dcm")

    def maybe_enqueue(self, uid: str):
        if uid in self.enqueued or uid in self.saved_uids:
            return
        m = self.meta_by_uid.get(uid)
        if not m or not m.furl:
            return
        self.enqueued.add(uid)
        self.queue.put_nowait(uid)

    def backfill_all_missing_furl(self) -> int:
        """
        关键：解决 “meta 很多但 saved 很少”
        如果某些 ImageMeta 没有 OnCached 给 furl，就用 h5Cache 规则拼起来入队。
        """
        added = 0
        for uid, m in list(self.meta_by_uid.items()):
            if m.furl:
                continue
            base = self.h5_base_by_study.get(m.study_uid) or self.global_h5_base
            if base:
                m.furl = base + uid
                self.maybe_enqueue(uid)
                added += 1
        return added

    async def try_download_raw(
        self, request_ctx, url: str, referer: str
    ) -> Tuple[Optional[bytes], Optional[int]]:
        """
        你抓到的很多 url 需要 _00000；有的又不需要。
        这里同时尝试两个候选。
        """
        if url.endswith("_00000"):
            candidates = [url, url[:-6]]
        else:
            candidates = [url, url + "_00000"]

        headers = {
            "Accept": "*/*",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
            "Referer": referer,
        }

        last_status = None
        for u in candidates:
            try:
                resp = await request_ctx.get(
                    u, headers=headers, timeout=self.args.http_timeout_ms
                )
                last_status = resp.status
                if resp.status == 200:
                    return await resp.body(), resp.status
                if resp.status == 404:
                    continue
            except Exception:
                continue
        return None, last_status

    async def worker(self, request_ctx, referer: str):
        while True:
            uid = await self.queue.get()
            try:
                await self._process_one(uid, request_ctx, referer)
            finally:
                self.queue.task_done()

    async def _process_one(self, uid: str, request_ctx, referer: str):
        async with self.sem:
            if uid in self.saved_uids:
                return
            m = self.meta_by_uid.get(uid)
            if not m or not m.furl:
                return

            for attempt in range(1, self.args.download_retries + 1):
                if self.args.verbose:
                    print(
                        f">>> downloading: {m.furl} (uid={uid}) attempt {attempt}/{self.args.download_retries}"
                    )
                raw, status = await self.try_download_raw(request_ctx, m.furl, referer)
                if raw is not None:
                    if m.fsz and len(raw) != m.fsz and self.args.verbose:
                        print(
                            f"[!] size mismatch uid={uid}: got={len(raw)} expect={m.fsz}"
                        )

                    out_path = self.path_for_uid(m)

                    pix_rep = (
                        m.pixel_pre
                        if m.pixel_pre is not None
                        else (1 if m.modality.upper() == "CT" else 0)
                    )

                    build_and_save_part10_dicom(
                        out_path=out_path,
                        pixel_bytes=raw,
                        modality=m.modality,
                        patient=m.patient,
                        study_uid=m.study_uid,
                        series_uid=m.series_uid,
                        sop_uid=m.sop_uid,
                        rows=m.height,
                        cols=m.width,
                        pixel_representation=pix_rep,
                        instance_number=m.instance_num,
                        series_number=m.series_num,
                        series_desc=m.series_desc,
                        study_date=m.study_date,
                        study_time=m.study_time,
                        bits_alloc=16,
                        bits_stored=m.storebits or 16,
                        samples_per_pixel=1,
                        window_center=m.window_center,
                        window_width=m.window_width,
                        pixel_spacing=(
                            (m.row_spacing, m.col_spacing)
                            if (m.row_spacing and m.col_spacing)
                            else None
                        ),
                        slice_thickness=m.slice_thickness,
                        image_position=m.pos,
                        image_orientation=m.iop,
                        rescale_intercept=(
                            self.args.ct_intercept
                            if m.modality.upper() == "CT"
                            else None
                        ),
                        rescale_slope=(
                            self.args.ct_slope if m.modality.upper() == "CT" else None
                        ),
                    )

                    try:
                        ds = pydicom.dcmread(out_path, stop_before_pixels=False)
                        if "PixelData" not in ds or not ds.PixelData:
                            raise RuntimeError("missing PixelData")
                    except Exception as e:
                        print(
                            f"[ERR] wrote but cannot read DICOM properly: {out_path} ({e})"
                        )
                        self.failed[uid] += 1
                        return

                    self.saved_uids.add(uid)
                    self.last_save_ts = asyncio.get_event_loop().time()
                    self.study_uid_for_zip = self.study_uid_for_zip or m.study_uid

                    if (len(self.saved_uids) % 50 == 0) and (not self.args.quiet):
                        print(
                            f"[+] saved={len(self.saved_uids)} meta={len(self.meta_by_uid)} queue={self.queue.qsize()}"
                        )
                    return

                if status is not None:
                    self.failed_status[str(status)] += 1
                await asyncio.sleep(self.args.retry_backoff_ms / 1000.0)

            self.failed[uid] += 1
            if not self.args.quiet:
                print(f"[!] failed uid={uid} after retries; last_status={status}")

    def on_ws_message(self, obj: Dict[str, Any]):
        self.last_ws_ts = asyncio.get_event_loop().time()

        fn = obj.get("funcName")

        if fn == "OnCached":
            uid = obj.get("iiuid")
            furl = obj.get("furl")
            fsz = obj.get("fsz")

            if uid and furl:
                self.furl_by_uid[uid] = furl
                base = infer_h5_base_from_furl(furl)
                if base:
                    self.global_h5_base = self.global_h5_base or base
                    m0 = self.meta_by_uid.get(uid)
                    if m0:
                        self.h5_base_by_study[m0.study_uid] = base

            if uid and isinstance(fsz, int):
                self.fsz_by_uid[uid] = fsz

            m = self.meta_by_uid.get(uid)
            if m and not m.furl:
                m.furl = self.furl_by_uid.get(uid)
                m.fsz = self.fsz_by_uid.get(uid)
                if m.furl:
                    base = infer_h5_base_from_furl(m.furl)
                    if base:
                        self.h5_base_by_study[m.study_uid] = base
                        self.global_h5_base = self.global_h5_base or base
                    self.maybe_enqueue(uid)
            return

        if fn == "OnThreeLevelChanged" and obj.get("type") == "image":
            data = obj.get("data") or {}
            sdy = data.get("sdy") or {}
            srs = data.get("srs") or {}
            ii = data.get("ii") or {}

            study_uid = sdy.get("sdyuid") or sdy.get("studyid") or ""
            series_uid = srs.get("srsuid") or ""
            sop_uid = ii.get("uid") or ""

            if not (study_uid and series_uid and sop_uid):
                return

            patient = {
                "name": sdy.get("name"),
                "sex": sdy.get("sex"),
                "age": sdy.get("age"),
                "date": sdy.get("date"),
                "time": sdy.get("time"),
                "access": sdy.get("access"),
                "des": sdy.get("des"),
                "patid": sdy.get("patid"),
                "birthday": sdy.get("birthday"),
            }

            modality = srs.get("modality") or "CT"
            series_desc = srs.get("des") or ""
            try:
                series_num = int(srs.get("num"))
            except Exception:
                series_num = None

            try:
                instance_num = int(ii.get("num")) if ii.get("num") is not None else None
            except Exception:
                instance_num = None

            width = int(ii.get("width") or 0)
            height = int(ii.get("height") or 0)
            byte_pp = int(ii.get("byte_pp") or 2)
            storebits = int(ii.get("storebits") or 16)

            pixel_pre = ii.get("pixel_pre")
            try:
                pixel_pre = int(pixel_pre) if pixel_pre is not None else None
            except Exception:
                pixel_pre = None

            ww = ii.get("windowWidth")
            wc = ii.get("windowCenter")
            try:
                ww = float(ww) if ww is not None else None
            except Exception:
                ww = None
            try:
                wc = float(wc) if wc is not None else None
            except Exception:
                wc = None

            row_ps = ii.get("rowPixelSpacing")
            col_ps = ii.get("columnPixelSpacing")
            try:
                row_ps = float(row_ps) if row_ps is not None else None
            except Exception:
                row_ps = None
            try:
                col_ps = float(col_ps) if col_ps is not None else None
            except Exception:
                col_ps = None

            st = ii.get("slicethickness")
            try:
                st = float(st) if st is not None else None
            except Exception:
                st = None

            pos = None
            try:
                pos = (
                    float(ii.get("posX")),
                    float(ii.get("posY")),
                    float(ii.get("posZ")),
                )
            except Exception:
                pos = None

            iop = None
            try:
                iop = (
                    float(ii.get("oXx")),
                    float(ii.get("oXy")),
                    float(ii.get("oXz")),
                    float(ii.get("oYx")),
                    float(ii.get("oYy")),
                    float(ii.get("oYz")),
                )
            except Exception:
                iop = None

            study_date = ymd_to_dicom_date(sdy.get("date"))
            study_time = hms_to_dicom_time(sdy.get("time"))

            base = self.h5_base_by_study.get(study_uid) or self.global_h5_base
            furl = self.furl_by_uid.get(sop_uid) or ii.get("furl")
            if (not furl) and base:
                furl = base + sop_uid

            m = ImageMeta(
                study_uid=study_uid,
                series_uid=series_uid,
                sop_uid=sop_uid,
                modality=str(modality),
                series_desc=str(series_desc),
                series_num=series_num,
                instance_num=instance_num,
                width=width,
                height=height,
                byte_pp=byte_pp,
                storebits=storebits,
                pixel_pre=pixel_pre,
                window_width=ww,
                window_center=wc,
                row_spacing=row_ps,
                col_spacing=col_ps,
                slice_thickness=st,
                pos=pos,
                iop=iop,
                patient=patient,
                study_date=study_date,
                study_time=study_time,
                furl=furl,
                fsz=self.fsz_by_uid.get(sop_uid),
            )

            self.meta_by_uid[sop_uid] = m

            if base and study_uid and study_uid not in self.h5_base_by_study:
                self.h5_base_by_study[study_uid] = base

            if m.furl:
                self.maybe_enqueue(sop_uid)

    async def heartbeat(self):
        while True:
            await asyncio.sleep(5)
            now = asyncio.get_event_loop().time()
            idle_save = int(now - self.last_save_ts) if self.last_save_ts else 999999
            idle_ws = int(now - self.last_ws_ts) if self.last_ws_ts else 999999
            if not self.args.quiet:
                print(
                    f">>> heartbeat: saved={len(self.saved_uids)}, "
                    f"meta={len(self.meta_by_uid)}, queue={self.queue.qsize()}, "
                    f"failed={len(self.failed)}, idle_saved={idle_save}s, idle_ws={idle_ws}s"
                )

    async def autoplay_collect(self, page, frame):
        """
        尝试推动 viewer 触发更多 WS（OnThreeLevelChanged/OnCached）
        - 先尝试找 slider input
        - 找不到就 fallback：点击 iframe 再用键盘翻页（ArrowDown/PageDown）
        """
        slider_found = await frame.evaluate(
            """
            () => {
              const input = document.querySelector('div.scroll-bar .el-slider__input input.el-input__inner');
              return !!input;
            }
            """
        )

        if slider_found:
            slider_max = await frame.evaluate(
                """
                () => {
                    const input = document.querySelector('div.scroll-bar .el-slider__input input.el-input__inner');
                    if (!input) return null;
                    const max = Number(input.getAttribute('max') || '0');
                    return Number.isFinite(max) ? max : null;
                }
                """
            )
            if slider_max is None:
                slider_found = False
            else:
                num = int(slider_max) + 1
                if not self.args.quiet:
                    print(
                        f">>> 检测到 slider max={slider_max}，预计 {num} 张（当前序列）"
                    )

                for r in range(1, self.args.autoplay_rounds + 1):
                    if not self.args.quiet:
                        print(
                            f">>> autoplay(slider) round {r}/{self.args.autoplay_rounds}"
                        )
                    for i in range(num):
                        ok = await frame.evaluate(
                            """
                            (idx) => {
                                const input = document.querySelector('div.scroll-bar .el-slider__input input.el-input__inner');
                                if (!input) return false;
                                const max = Number(input.getAttribute('max') || '0');
                                const clamped = Math.max(0, Math.min(idx, max));
                                input.value = String(clamped);
                                input.dispatchEvent(new Event('input', {bubbles:true}));
                                input.dispatchEvent(new Event('change', {bubbles:true}));
                                return true;
                            }
                            """,
                            i,
                        )
                        if not ok:
                            slider_found = False
                            break
                        await frame.wait_for_timeout(self.args.autoplay_delay_ms)
                    if not slider_found:
                        break

                await frame.wait_for_timeout(self.args.quiet_wait_ms)
                return

        if not self.args.quiet:
            print("[!] 未找到可用 slider，启用 fallback：键盘翻页采集 meta")

        try:
            await frame.click("body", position={"x": 600, "y": 450}, timeout=3000)
        except Exception:
            pass

        keys = ["ArrowDown", "PageDown"]
        for r in range(1, self.args.autoplay_rounds + 1):
            if not self.args.quiet:
                print(f">>> autoplay(fallback) round {r}/{self.args.autoplay_rounds}")
            for _ in range(self.args.fallback_steps_per_round):
                for k in keys:
                    try:
                        await page.keyboard.press(k)
                    except Exception:
                        pass
                await frame.wait_for_timeout(self.args.autoplay_delay_ms)

        await frame.wait_for_timeout(self.args.quiet_wait_ms)

    async def wait_and_backfill_until_done(self):
        """
        反复：
        - backfill furl（把只有 meta 的 uid 拼出 furl 入队）
        - 等队列跑空
        直到 backfill 不再新增 或 达到 backfill_rounds 限制
        """
        for i in range(1, self.args.backfill_rounds + 1):
            added = self.backfill_all_missing_furl()
            if not self.args.quiet:
                print(
                    f">>> backfill round {i}/{self.args.backfill_rounds}: newly enqueued={added}"
                )
            await self.queue.join()
            if added == 0:
                break

    def make_zip(self) -> Optional[str]:
        if not self.args.zip:
            return None

        uid = self.study_uid_for_zip
        if not uid and self.meta_by_uid:
            uid = next(iter(self.meta_by_uid.values())).study_uid
        uid = uid or "unknown_study"

        zip_name = safe_name(uid) + ".zip"
        zip_path = os.path.join(os.path.abspath(self.args.zip_dir), zip_name)
        os.makedirs(os.path.dirname(zip_path), exist_ok=True)

        print(f">>> 正在打包 zip: {zip_path}")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(self.out_root):
                for fn in files:
                    if not fn.lower().endswith(".dcm"):
                        continue
                    fp = os.path.join(root, fn)
                    arc = os.path.relpath(fp, start=self.out_root)
                    zf.write(fp, arc)
        print(">>> zip 完成:", zip_path)
        return zip_path

    def verify_dicoms(self) -> Dict[str, Any]:
        """
        验证：
        - 能否不 force 读取
        - 是否存在 PixelData
        - 是否存在关键 UID
        """
        report = {
            "checked": 0,
            "ok": 0,
            "bad_read": 0,
            "missing_pixel": 0,
            "missing_uids": 0,
            "examples": {
                "bad_read": [],
                "missing_pixel": [],
                "missing_uids": [],
            },
        }

        for root, _, files in os.walk(self.out_root):
            for fn in files:
                if not fn.lower().endswith(".dcm"):
                    continue
                fp = os.path.join(root, fn)
                report["checked"] += 1
                try:
                    ds = pydicom.dcmread(fp, stop_before_pixels=False)
                except Exception as e:
                    report["bad_read"] += 1
                    if len(report["examples"]["bad_read"]) < 10:
                        report["examples"]["bad_read"].append(
                            {"file": fp, "err": str(e)}
                        )
                    continue

                if (
                    ("StudyInstanceUID" not in ds)
                    or ("SeriesInstanceUID" not in ds)
                    or ("SOPInstanceUID" not in ds)
                ):
                    report["missing_uids"] += 1
                    if len(report["examples"]["missing_uids"]) < 10:
                        report["examples"]["missing_uids"].append(fp)

                if ("PixelData" not in ds) or (not ds.PixelData):
                    report["missing_pixel"] += 1
                    if len(report["examples"]["missing_pixel"]) < 10:
                        report["examples"]["missing_pixel"].append(fp)
                    continue

                report["ok"] += 1

        return report


# -------------------- argparse（按你要求修改：URL 必须从命令行输入，其他用默认参数） --------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()

    # ✅ URL：支持单个或多个
    ap.add_argument("url", nargs="?", help="viewer URL（也可用 -u/--url 传入）")
    ap.add_argument("-u", "--url", dest="url", help="viewer URL（与位置参数二选一即可）")
    ap.add_argument("--urls-file", help="包含多个 URL 的文本文件（每行一个，支持 # 注释）")

    # ✅ 其他参数：默认值按你给的命令保持一致
    ap.add_argument("-o", "--out-dir", default="dicom_out")
    ap.add_argument(
        "--out-parent",
        default="./downloads",
        help="（多 URL）输出父目录（每个 URL 会建一个子目录）",
    )

    ap.add_argument(
        "--password",
        default=None,
        help="分享密码（填了就自动验证；不填则你在浏览器里手动输入）",
    )
    ap.add_argument("--headless", action="store_true", default=False)

    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--download-retries", type=int, default=4)
    ap.add_argument("--http-timeout-ms", type=int, default=60000)
    ap.add_argument("--retry-backoff-ms", type=int, default=250)

    ap.add_argument("--autoplay-rounds", type=int, default=3)
    ap.add_argument("--autoplay-delay-ms", type=int, default=90)
    ap.add_argument("--quiet-wait-ms", type=int, default=1500)
    ap.add_argument(
        "--fallback-steps-per-round",
        type=int,
        default=900,
        help="fallback 翻页每轮按键次数（大一点更容易覆盖全量）",
    )

    ap.add_argument(
        "--backfill-rounds",
        type=int,
        default=5,
        help="为缺 OnCached 的 meta 自动拼 furl 并入队的轮数",
    )

    # 默认开启 zip/verify；如需关闭，用 --no-zip / --no-verify
    ap.add_argument(
        "--zip",
        dest="zip",
        action="store_true",
        default=True,
        help="下载后按 StudyUID 打包 zip（默认开启）",
    )
    ap.add_argument("--no-zip", dest="zip", action="store_false", help="关闭 zip 打包")
    ap.add_argument("--zip-dir", default=".", help="zip 输出目录（默认当前目录）")

    ap.add_argument(
        "--verify",
        dest="verify",
        action="store_true",
        default=True,
        help="下载后验证 DICOM（默认开启）",
    )
    ap.add_argument(
        "--no-verify", dest="verify", action="store_false", help="关闭 DICOM 验证"
    )
    ap.add_argument("--verify-report", default="verify_report.json")

    ap.add_argument(
        "--ct-intercept", type=float, default=0.0, help="CT RescaleIntercept（默认 0）"
    )
    ap.add_argument(
        "--ct-slope", type=float, default=1.0, help="CT RescaleSlope（默认 1）"
    )

    ap.add_argument("--quiet", action="store_true", default=False, help="更少日志")
    ap.add_argument(
        "--verbose", action="store_true", default=False, help="更详细下载日志"
    )

    return ap


async def main():
    ap = build_parser()
    args = ap.parse_args()
    if not args.url and not args.urls_file:
        ap.error("必须提供 URL：使用位置参数或 -u/--url，或 --urls-file")

    # 统一：单 URL -> [url]；多 URL -> read_urls_file
    if args.url:
        urls = [args.url]
    else:
        urls = read_urls_file(args.urls_file)

    out_parent = os.path.abspath(args.out_parent)
    os.makedirs(out_parent, exist_ok=True)

    print("\n>>> 启动参数：")
    print(f"    URL数量     : {len(urls)}")
    print(f"    out_parent  : {out_parent}")
    print(f"    headless    : {args.headless}")
    print(f"    zip         : {args.zip}\n")

    async with async_playwright() as p:
        for i, url in enumerate(urls, start=1):
            share_id = extract_share_id(url)
            out_dir = os.path.join(out_parent, share_id)
            os.makedirs(out_dir, exist_ok=True)

            print("=" * 80)
            print(f"### [{i}/{len(urls)}] 开始下载")
            print(f"URL      : {url}")
            print(f"输出目录 : {out_dir}")
            print("=" * 80)

            # 每个 URL 独立的 Downloader 与浏览器上下文
            # 将当前 URL/输出目录写入 args
            args.url = url
            args.out_dir = out_dir
            # 若未显式设置 zip_dir，则将 zip_dir 设为 out_parent
            try:
                if getattr(args, "zip_dir", ".") == ".":
                    args.zip_dir = out_parent
            except Exception:
                pass

            d = Downloader(args)

            try:
                browser = await p.chromium.launch(headless=args.headless)
                context = await browser.new_context(
                    viewport={"width": 1400, "height": 900},
                    ignore_https_errors=True,
                )
                page = await context.new_page()
                page.set_default_timeout(120000)
                page.set_default_navigation_timeout(120000)

                # 监听 websocket：Playwright 这里回调给的是 bytes
                def on_websocket(ws):
                    def on_frame(payload: bytes):
                        obj = ws_payload_to_json(payload)
                        if obj:
                            d.on_ws_message(obj)

                    ws.on("framereceived", on_frame)

                page.on("websocket", on_websocket)

                print(">>> 打开检查页面:", url)
                await safe_goto(page, url)

                await maybe_click_dialog_button(page, "我知道了", timeout_ms=1500)

                await handle_password_if_needed(page, args.password)

                frame = await get_viewer_frame(page)
                print(">>> 已进入 viewer iframe")

                referer = url
                workers = [
                    asyncio.create_task(d.worker(context.request, referer))
                    for _ in range(max(1, args.concurrency))
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
                if d.failed_status and not args.quiet:
                    print(">>> HTTP status summary:", dict(d.failed_status))

                if args.verify:
                    report = d.verify_dicoms()
                    with open(args.verify_report, "w", encoding="utf-8") as f:
                        json.dump(report, f, ensure_ascii=False, indent=2)
                    print(">>> verify report saved:", os.path.abspath(args.verify_report))
                    print(
                        ">>> verify summary:",
                        {
                            k: report[k]
                            for k in [
                                "checked",
                                "ok",
                                "bad_read",
                                "missing_pixel",
                                "missing_uids",
                            ]
                        },
                    )

                # 统一 zip：按 URL 的 share_id 打包到 out_parent
                if args.zip:
                    zip_path = os.path.join(out_parent, f"{share_id}.zip")
                    make_zip_dir(out_dir, zip_path)
                    print(">>> zip 完成:", os.path.abspath(zip_path))
            except Exception as e:
                print(f">>> ❌ 失败：{url}")
                print(f">>> 错误：{e}")
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass


if __name__ == "__main__":
    asyncio.run(main())
