"""lbrt semantics, checked against outputs captured from GnuCOBOL 3.2 (see docs/ASSUMPTIONS.md)."""

from decimal import Decimal as Dec

import pytest

from legacybridge.adapters.cobol_python import lbrt

PICS = ["ZZZ,ZZZ,ZZ9.99CR", "--,---,---,--9.99", "-ZZZ,ZZZ,ZZ9.99", "ZZ9.999", "ZZ9", "-ZZZZ9",
        "$$$,$$9.99", "***,**9.99", "+ZZ9", "ZZZ9.99DB", "++++9"]
VALUES = ["0", "-0.5", "1234567.89", "-999999999.99", "0.05", "-12", "1000", "-1000000"]
# captured from cobc 3.2: each row = VALUES[i] moved to every picture in PICS
GOLDEN = """[          0.00  ][             0.00][           0.00][  0.000][  0][     0][     $0.00][******0.00][+  0][   0.00  ][   +0]
[          0.50CR][            -0.50][-          0.50][  0.500][  0][     0][     $0.50][******0.50][+  0][   0.50DB][   +0]
[  1,234,567.89  ][     1,234,567.89][   1,234,567.89][567.890][567][ 34567][$34,567.89][234,567.89][+567][4567.89  ][+4567]
[999,999,999.99CR][  -999,999,999.99][-999,999,999.99][999.990][999][-99999][$99,999.99][999,999.99][-999][9999.99DB][-9999]
[          0.05  ][             0.05][           0.05][  0.050][  0][     0][     $0.05][******0.05][+  0][   0.05  ][   +0]
[         12.00CR][           -12.00][-         12.00][ 12.000][ 12][-   12][    $12.00][*****12.00][- 12][  12.00DB][  -12]
[      1,000.00  ][         1,000.00][       1,000.00][  0.000][  0][  1000][ $1,000.00][**1,000.00][+000][1000.00  ][+1000]
[  1,000,000.00CR][    -1,000,000.00][-  1,000,000.00][  0.000][  0][     0][     $0.00][******0.00][+  0][   0.00  ][   +0]"""


@pytest.mark.parametrize("row", range(len(VALUES)))
def test_edit_matches_gnucobol(row):
    expected = GOLDEN.splitlines()[row][1:-1].split("][")
    got = [lbrt.edit(Dec(VALUES[row]), p) for p in PICS]
    assert got == expected


def test_edit_truncation_with_leading_sign():
    # GnuCOBOL: nonzero digit just above the kept window + fixed leading sign -> no zero suppression
    assert lbrt.edit(Dec("1004914.97"), "-ZZZ,ZZ9.99") == " 004,914.97"
    assert lbrt.edit(Dec("1000"), "+ZZ9") == "+000"
    assert lbrt.edit(Dec("1000"), "ZZ9") == "  0"
    assert lbrt.edit(Dec("-1000000"), "+ZZ9") == "+  0"


def test_edit_date_insertion():
    assert lbrt.edit(20240131, "9999/99/99") == "2024/01/31"


def test_display_num_matches_gnucobol():
    assert lbrt.display_num(3, 5) == "00003"
    assert lbrt.display_num(Dec("1.5"), 3, 2) == "001.50"
    assert lbrt.display_num(-3, 5, 0, True) == "-00003"
    assert lbrt.display_num(Dec("165.28"), 7, 2, True) == "+0000165.28"
    assert lbrt.display_num(Dec("-12.5"), 5, 2, True) == "-00012.50"


def test_store_truncation_rounding_sign():
    assert lbrt.store(Dec("1234.56"), 3, 2) == Dec("234.56")          # high-order dropped
    assert lbrt.store(Dec("1004.98995"), 3, 2) == Dec("4.98")         # truncate decimals too
    assert lbrt.store(Dec("-1.25625"), 3, 2, rounded=True) == Dec("-1.26")  # half away from zero
    assert lbrt.store(Dec("2.345"), 3, 2, rounded=True) == Dec("2.35")
    assert lbrt.store(Dec("-7.5"), 3, 0, signed=False) == Dec("7")   # unsigned drops sign, truncates
    assert lbrt.fits(Dec("999.994"), 3, 2) and not lbrt.fits(Dec("999.995"), 3, 2, rounded=True)


def test_records_and_numeric_checks():
    import io
    recs = list(lbrt.read_records(io.StringIO("ab\n\nxyz\r\n"), 4))
    assert recs == ["ab  ", "    ", "xyz "]
    assert lbrt.is_numeric("+12345", sign_separate=True)
    assert not lbrt.is_numeric(" 12345", sign_separate=True)
    assert not lbrt.is_numeric("12 ", )
    assert lbrt.parse_num("-0012345", 2, True) == Dec("-123.45")
    assert lbrt.fmt_num(Dec("-123.45"), 5, 2, True, True) == "-0012345"
    assert lbrt.int_div(-7, 2) == -3 and lbrt.remainder(-7, 2) == -1
    assert lbrt.alnum("ABC", 5) == "ABC  " and lbrt.alnum("ABCDEFG", 5, justified_right=True) == "CDEFG"
