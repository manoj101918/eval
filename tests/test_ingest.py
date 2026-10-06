import pymupdf
import pytest
from PIL import Image

from backend.extract.ingest import IngestError, load_script


def _save(img: Image.Image, path):
    img.save(path)
    return path


@pytest.fixture
def two_page_pdf(tmp_path):
    path = tmp_path / "script.pdf"
    doc = pymupdf.open()
    for text in ("cover", "answer"):
        page = doc.new_page(width=595, height=842)  # A4 in points
        page.insert_text((72, 72), text, fontsize=24)
    doc.save(path)
    doc.close()
    return path


def test_pdf_pages_rendered_in_order(two_page_pdf):
    pages = load_script([two_page_pdf], dpi=100)
    assert [p.index for p in pages] == [0, 1]
    assert all(p.image.mode == "L" for p in pages)
    # 595pt at 100 dpi ≈ 826 px
    assert pages[0].image.width == pytest.approx(826, abs=2)


def test_image_list_keeps_given_order(tmp_path):
    a = _save(Image.new("L", (50, 60), 10), tmp_path / "b.png")
    b = _save(Image.new("RGB", (70, 80), (255, 0, 0)), tmp_path / "a.jpg")
    pages = load_script([a, b])
    assert [p.image.size for p in pages] == [(50, 60), (70, 80)]
    assert pages[1].image.mode == "L"


def test_directory_natural_sort(tmp_path):
    for n in (10, 2, 1):
        _save(Image.new("L", (n, n), 0), tmp_path / f"page{n}.png")
    (tmp_path / "notes.txt").write_text("ignored")
    pages = load_script([tmp_path])
    assert [p.image.width for p in pages] == [1, 2, 10]


def test_multipage_tiff(tmp_path):
    path = tmp_path / "scan.tiff"
    frames = [Image.new("L", (20, 20), v) for v in (0, 128, 255)]
    frames[0].save(path, save_all=True, append_images=frames[1:])
    assert len(load_script([path])) == 3


def test_missing_file(tmp_path):
    with pytest.raises(IngestError, match="does not exist"):
        load_script([tmp_path / "nope.pdf"])


def test_empty_input():
    with pytest.raises(IngestError):
        load_script([])


def test_corrupt_pdf(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"not really a pdf")
    with pytest.raises(IngestError, match="PDF"):
        load_script([path])


def test_corrupt_image(tmp_path):
    path = tmp_path / "bad.jpg"
    path.write_bytes(b"garbage")
    with pytest.raises(IngestError, match="not a readable image"):
        load_script([path])


def test_unsupported_type(tmp_path):
    path = tmp_path / "answers.docx"
    path.write_bytes(b"x")
    with pytest.raises(IngestError, match="unsupported"):
        load_script([path])


def test_pdf_mixed_with_images_rejected(tmp_path, two_page_pdf):
    img = _save(Image.new("L", (5, 5)), tmp_path / "x.png")
    with pytest.raises(IngestError, match="not both"):
        load_script([two_page_pdf, img])


def test_empty_directory(tmp_path):
    with pytest.raises(IngestError, match="no supported images"):
        load_script([tmp_path])


def test_error_messages_do_not_contain_file_names(tmp_path):
    path = tmp_path / "21CS045_secret.jpg"
    path.write_bytes(b"garbage")
    with pytest.raises(IngestError) as exc:
        load_script([path])
    assert "21CS045" not in str(exc.value)
