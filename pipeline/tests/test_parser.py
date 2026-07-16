import unittest
from pathlib import Path

from pipeline.import_quantlaw import parse_law_xml


class ParserTest(unittest.TestCase):
    def test_parses_law_and_provision(self):
        path = (
            Path(__file__).parent
            / "fixtures"
            / "quantlaw"
            / "data"
            / "items"
            / "gg"
            / "BJNR000010949.xml"
        )
        law = parse_law_xml(path)
        self.assertEqual(law.abbreviation, "GG")
        self.assertEqual(law.slug, "gg")
        self.assertEqual(law.gii_slug, "gg")
        self.assertEqual(law.provisions[0].name, "Art 1")
        self.assertIn("Würde", law.provisions[0].body)


if __name__ == "__main__":
    unittest.main()
