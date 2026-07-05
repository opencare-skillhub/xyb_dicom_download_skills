import os
import tempfile
import zipfile

from common_utils import safe_name, extract_share_id, read_urls_file, read_urls_file_with_passwords, make_zip_dir


def test_safe_name_basic():
    assert safe_name(" a/b:c*?d ") == "a_b_c__d"
    assert safe_name("") == "unnamed"
    assert safe_name("   ") == "unnamed"


def test_extract_share_id_priority():
    u1 = "https://host/viewer?shareId=ABC123"
    u2 = "https://host/viewer?share_id=XYZ"
    u3 = "https://host/viewer/TAIL/END"
    u4 = "https://host/viewer"
    s1 = extract_share_id(u1)
    s2 = extract_share_id(u2)
    s3 = extract_share_id(u3)
    s4 = extract_share_id(u4)

    # 方案 A：host + (关键参数值) + 短哈希
    assert s1.startswith("host_ABC123_") and len(s1.split("_")[-1]) == 10
    assert s2.startswith("host_XYZ_") and len(s2.split("_")[-1]) == 10
    # u3 没有关键参数，会退化为 host + hash
    assert s3.startswith("host_") and len(s3.split("_")[-1]) == 10
    assert s4.startswith("host_") and len(s4.split("_")[-1]) == 10


def test_extract_share_id_fragment_query():
    # SPA 把关键参数放在 fragment（如：#/image?data=...）
    u = "http://zhyl.nyfy.com.cn:50647/viewImage/#/image?data=abcdefg"
    s = extract_share_id(u)
    assert (
        s.startswith("zhyl.nyfy.com.cn_50647_abcdefg_") and len(s.split("_")[-1]) == 10
    )


def test_read_urls_file_and_make_zip_dir(tmp_path):
    urls_txt = tmp_path / "urls.txt"
    urls_txt.write_text(
        "\n".join(
            [
                "# comment",
                "https://a/viewer?shareId=A",
                "",
                "https://b/viewer/B",
            ]
        ),
        encoding="utf-8",
    )
    urls = read_urls_file(str(urls_txt))
    assert urls == ["https://a/viewer?shareId=A", "https://b/viewer/B"]

    urls_with_passwords = read_urls_file_with_passwords(str(urls_txt))
    assert urls_with_passwords == [
        ("https://a/viewer?shareId=A", None),
        ("https://b/viewer/B", None),
    ]

    src_dir = tmp_path / "src"
    (src_dir / "x").mkdir(parents=True)
    (src_dir / "x" / "file.txt").write_text("hello", encoding="utf-8")
    zip_path = tmp_path / "out.zip"
    make_zip_dir(str(src_dir), str(zip_path))

    assert zip_path.exists()
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        names = zf.namelist()
        assert any(n.endswith("x/file.txt") for n in names)
