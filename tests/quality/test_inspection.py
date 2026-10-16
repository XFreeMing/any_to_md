"""Prove the gate rejects loss, duplication, reordering and broken exports."""

import tempfile
import unittest
from pathlib import Path

from tests.quality.fixtures import build_fixtures, load_cases
from tests.quality.inspection import inspect_output
from PIL import Image


class ArtifactGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = {"markers": ["FIRST_TOKEN", "SECOND_TOKEN"], "minimum_images": 1}

    def inspect(self, body):
        path = self.root / "book.html"
        path.write_text(body, encoding="utf-8")
        return inspect_output([path], self.case, self.root)

    def test_complete_self_contained_export_passes(self):
        Image.new("RGB", (4, 4), "red").save(self.root / "asset.png")
        result = self.inspect('<p id="target">FIRST_TOKEN</p><p>SECOND_TOKEN</p><img src="asset.png"/><a href="#target">jump</a>')
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["visual_status"], "not_run")

    def test_missing_and_duplicated_body_fail(self):
        result = self.inspect('<p>FIRST_TOKEN FIRST_TOKEN</p>')
        self.assertFalse(result["passed"])
        self.assertEqual(result["marker_counts"], {"FIRST_TOKEN": 2, "SECOND_TOKEN": 0})

    def test_reordered_body_fails(self):
        result = self.inspect('<p>SECOND_TOKEN FIRST_TOKEN</p>')
        self.assertIn("Source body order changed", result["failures"])

    def test_missing_resource_and_cross_document_anchor_fail(self):
        (self.root / "chapter.md").write_text("# Existing heading", encoding="utf-8")
        result = self.inspect('<p>FIRST_TOKEN SECOND_TOKEN</p><img src="lost.png"/><a href="chapter.md#absent">jump</a>')
        self.assertTrue(any("missing dependency" in failure for failure in result["failures"]))
        self.assertTrue(any("missing anchor" in failure for failure in result["failures"]))

    def test_output_cannot_depend_on_parent_directory(self):
        result = self.inspect('<p>FIRST_TOKEN SECOND_TOKEN</p><img src="../outside.png"/>')
        self.assertTrue(any("outside export" in failure for failure in result["failures"]))

    def test_md_preserves_code_and_table_only_if_actually_represented(self):
        self.case.update({"minimum_images": 0, "code_contains": "  a_b * c", "required_spans": ["colspan"]})
        path = self.root / "book.md"
        path.write_text('FIRST_TOKEN SECOND_TOKEN\n\n```\n  a_b * c\n```\n\n<table><tr><td colspan="2">merged</td></tr></table>', encoding="utf-8")
        self.assertTrue(inspect_output([path], self.case, self.root)["passed"])
        path.write_text('FIRST_TOKEN SECOND_TOKEN\n\na_b * c\n\n| flattened |\n| --- |', encoding="utf-8")
        result = inspect_output([path], self.case, self.root)
        self.assertFalse(result["passed"])
        self.assertIn("Table colspan was lost", result["failures"])

    def test_srcset_candidates_are_checked(self):
        result = self.inspect('<p>FIRST_TOKEN SECOND_TOKEN</p><img srcset="missing.png 2x"/>')
        self.assertTrue(any("missing.png" in failure for failure in result["failures"]))

    def test_removed_or_mistargeted_source_link_fails(self):
        self.case.update({"minimum_images": 0, "required_links": [{"label": "next", "target_marker": "SECOND_TOKEN"}]})
        result = self.inspect('<p id="one">FIRST_TOKEN</p><p id="two">SECOND_TOKEN</p>')
        self.assertTrue(any("Source link lost" in failure for failure in result["failures"]))
        result = self.inspect('<p id="one">FIRST_TOKEN</p><p id="two">SECOND_TOKEN</p><a href="#one">next</a>')
        self.assertFalse(result["passed"])
        result = self.inspect('<p id="one">FIRST_TOKEN</p><p id="two">SECOND_TOKEN</p><a href="#two">next</a>')
        self.assertTrue(result["passed"], result)

    def test_corrupt_images_and_fake_spans_do_not_pass(self):
        self.case["required_cells"] = [{"marker": "FIRST_TOKEN", "colspan": 2, "rowspan": 1, "row": 0, "column": 0}]
        (self.root / "fake.png").write_bytes(b"not an image")
        result = self.inspect('<table><tr><td colspan="1">FIRST_TOKEN</td></tr></table><p>SECOND_TOKEN</p><img src="fake.png"/>')
        self.assertTrue(any("invalid image" in failure for failure in result["failures"]))
        self.assertTrue(any("Table cell structure changed" in failure for failure in result["failures"]))

    def test_supported_unicode_markdown_heading_anchor(self):
        self.case["minimum_images"] = 0
        path = self.root / "book.md"
        path.write_text('# 中文 Title\n\nFIRST_TOKEN SECOND_TOKEN\n\n[Back](#中文-title)', encoding="utf-8")
        self.assertTrue(inspect_output([path], self.case, self.root)["passed"])


class SourceIsolationTests(unittest.TestCase):
    def test_missing_archive_package_cannot_fall_back_to_worktree(self):
        from tests.quality.capture import _SourceTreeFinder

        with tempfile.TemporaryDirectory() as directory:
            finder = _SourceTreeFinder(Path(directory))
            with self.assertRaises(ImportError):
                finder.find_spec("web")


class FixtureContractTests(unittest.TestCase):
    def test_rich_source_html_satisfies_frozen_links_images_cells_and_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = build_fixtures(root)
            case = next(case for case in load_cases() if case["id"] == "html-rich")
            result = inspect_output([fixtures["html-rich"]], case, root)
            self.assertTrue(result["passed"], result["failures"])

    def test_real_book_probes_are_unique_in_full_source_not_just_paragraph_list(self):
        from tests.quality.fixtures import _archive, _epub
        from tests.quality.real_book import source_probes
        from zipfile import ZipFile

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.epub"
            _epub(source, "plain", b"unused", b"unused")
            with ZipFile(source) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            short = "A source paragraph that also appears inside another paragraph."
            long = short + " Only this longer paragraph is substring-unique."
            members["OEBPS/plain.xhtml"] = f"<html><body><p>{short}</p><p>{long}</p></body></html>"
            _archive(source, members)
            probes, _ = source_probes(source)
            self.assertEqual(probes, ["".join(long.split())])

    def test_native_and_scanned_pdf_fixtures_have_independent_source_structure(self):
        import pymupdf

        with tempfile.TemporaryDirectory() as directory:
            fixtures = build_fixtures(Path(directory))
            with pymupdf.open(fixtures["pdf-mixed"]) as document:
                self.assertEqual(len(document), 5)
                for index, page in enumerate(document):
                    if index < 3:
                        self.assertEqual(page.get_text(), "")
                        self.assertTrue(page.get_images())
                    else:
                        self.assertIn(f"PDF_MIXED_PAGE_{index + 1}", page.get_text())
            for case in load_cases():
                self.assertTrue(fixtures[case["id"]].is_file())
                self.assertTrue(case["visual_checks"])
                self.assertTrue(case["allowed_differences"])


if __name__ == "__main__":
    unittest.main()
