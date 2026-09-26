import tempfile
import unittest
from pathlib import Path

from desk_pilot.desktop.files import find_files, looks_like_html, sniff_kind, verify_file
from desk_pilot.desktop.mock import MockDesktop


class FindFilesTests(unittest.TestCase):
    def test_pathlib_finds_exe(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "Program Files (x86)" / "Steam" / "steamapps" / "common" / "Brawlhalla" / "Brawlhalla.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"MZ\x00\x00")
            (root / "Downloads" / "notes.txt").parent.mkdir(parents=True, exist_ok=True)
            (root / "Downloads" / "notes.txt").write_text("hi", encoding="utf-8")
            result = find_files(name="brawlhalla.exe", roots=[root], max_results=10, timeout_seconds=3)
            self.assertTrue(result["ok"])
            paths = [item["path"] for item in result["results"]]
            self.assertTrue(any(p.endswith("Brawlhalla.exe") for p in paths))
            self.assertTrue(all("size" in item for item in result["results"]))

    def test_glob_exe(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            exe = root / "game.exe"
            exe.write_bytes(b"MZ")
            (root / "readme.txt").write_text("x", encoding="utf-8")
            result = find_files(glob="*.exe", roots=[root], max_results=5, timeout_seconds=3)
            self.assertTrue(result["ok"])
            self.assertEqual(result["count"], 1)
            self.assertTrue(result["results"][0]["path"].endswith("game.exe"))

    def test_requires_query(self) -> None:
        result = find_files()
        self.assertFalse(result["ok"])

    def test_mock_catalog(self) -> None:
        desk = MockDesktop()
        result = desk.find_files(name="Brawlhalla.exe")
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["count"], 1)
        self.assertTrue(any("Brawlhalla.exe" in item["path"] for item in result["results"]))


class VerifyFileTests(unittest.TestCase):
    def test_jpeg_magic_ok(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "pic.jpg"
            path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
            result = verify_file(str(path), expect="image")
            self.assertTrue(result["ok"])
            self.assertEqual(result["kind"], "jpeg")

    def test_html_masquerading_as_jpg(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "cat.jpg"
            path.write_bytes(b"<!DOCTYPE html><html><head><title>Images</title></head><body>results</body></html>")
            result = verify_file(str(path), expect="image")
            self.assertFalse(result["ok"])
            self.assertEqual(result["kind"], "html")
            self.assertIn("html", (result.get("error") or "").lower())

    def test_missing_path(self) -> None:
        result = verify_file("/no/such/file.jpg", expect="image")
        self.assertFalse(result["ok"])

    def test_looks_like_image_path(self) -> None:
        from desk_pilot.desktop.files import looks_like_image_path

        self.assertTrue(looks_like_image_path(r"C:\Users\me\Downloads\corgi.jpg"))
        self.assertTrue(looks_like_image_path("/tmp/photo.png"))
        self.assertFalse(looks_like_image_path("corgi.jpg"))
        self.assertFalse(looks_like_image_path("https://example.com/corgi.jpg"))

    def test_sniff_helpers(self) -> None:
        self.assertTrue(looks_like_html(b"  <html><body>"))
        self.assertEqual(sniff_kind(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8), "png")
        self.assertEqual(sniff_kind(b"GIF89a...."), "gif")


if __name__ == "__main__":
    unittest.main()
