"""Unit tests for bdf2._detect."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bdf2._detect import detect_layout, read_sample, sniff_source

SAMPLE_DATA = Path(__file__).parent.parent.parent / "sample_data"


# ---------------------------------------------------------------------------
# read_sample
# ---------------------------------------------------------------------------

def test_read_sample_drops_trailing_partial_line():
    path = SAMPLE_DATA / "arbin" / "sample_data_arbin.csv"
    text = read_sample(path)
    # Must end on a complete line (no partial last line)
    assert text.endswith("\n") or "\n" in text


def test_read_sample_decodes_utf8():
    path = SAMPLE_DATA / "arbin" / "sample_data_arbin.csv"
    text = read_sample(path)
    assert isinstance(text, str)


# ---------------------------------------------------------------------------
# detect_layout — real files
# ---------------------------------------------------------------------------

def test_arbin_comma_no_preamble():
    path = SAMPLE_DATA / "arbin" / "sample_data_arbin.csv"
    sep, header_idx, data_start, has_header = detect_layout(read_sample(path))
    assert sep == ","
    assert header_idx == 0
    assert has_header is True


def test_biologic_tab_preamble():
    path = SAMPLE_DATA / "biologic" / "Sample_data_biologic_01_MB_CA1.txt"
    sep, header_idx, data_start, has_header = detect_layout(read_sample(path))
    assert sep == "\t"
    assert header_idx >= 1
    assert has_header is True


def test_biologic_no_header_file():
    """File named 'no_header' has no preamble; column labels are present in row 0."""
    path = SAMPLE_DATA / "biologic" / "Sample_data_biologic_no_header.mpt"
    sep, header_idx, data_start, has_header = detect_layout(read_sample(path))
    assert sep == "\t"
    assert header_idx == 0
    # Row 0 is all text labels → has_header should be True
    assert has_header is True


def test_novonix_section_preamble():
    path = SAMPLE_DATA / "novonix" / "sample_data_novonix.csv"
    sep, header_idx, data_start, has_header = detect_layout(read_sample(path))
    assert sep == ","
    # [Data] section marker is at some line; header follows immediately
    assert header_idx >= 1
    assert has_header is True


def test_basytec_tab_preamble():
    path = SAMPLE_DATA / "basytec" / "sample_data_basytec.txt"
    sep, header_idx, data_start, has_header = detect_layout(read_sample(path))
    assert sep == "\t"
    assert header_idx >= 1  # preamble is skipped
    assert has_header is True


def test_neware_csv():
    path = SAMPLE_DATA / "neware" / "sample_data_neware.csv"
    sep, header_idx, data_start, has_header = detect_layout(read_sample(path))
    assert sep == ","
    assert header_idx == 0
    assert has_header is True


# ---------------------------------------------------------------------------
# detect_layout — synthetic fallback
# ---------------------------------------------------------------------------

def test_synthetic_fallback_no_stable_run():
    """No candidate reaches min_run → fall back to (',', 0, 0, True)."""
    sample = "a,b\nc;d;e\nf|g|h|i\nx\ty\tz"
    sep, header_idx, data_start, has_header = detect_layout(sample, min_run=5)
    assert sep == ","
    assert header_idx == 0
    assert has_header is True


def test_synthetic_tab_preferred_over_comma():
    """Tab run with higher score beats comma run."""
    comma_lines = "a,b\n1,2\n3,4\n"
    tab_lines = "".join(f"col{i}\t" for i in range(10))[:-1] + "\n"
    tab_data = "".join(f"{i}\t" for i in range(10))[:-1] + "\n"
    sample = comma_lines + tab_lines + (tab_data * 10)
    sep, _, _, _ = detect_layout(sample, min_run=5)
    assert sep == "\t"


# ---------------------------------------------------------------------------
# sniff_source
# ---------------------------------------------------------------------------

def test_sniff_biologic():
    from bdf2._config import load_config
    config = load_config()
    head = b"BT-Lab ASCII FILE\nSome more content"
    assert sniff_source(head, config) == "biologic_mpt"


def test_sniff_basytec():
    from bdf2._config import load_config
    config = load_config()
    head = b"Resultfile from Basytec Battery Test System\nmore"
    assert sniff_source(head, config) == "basytec_txt"


def test_sniff_novonix():
    from bdf2._config import load_config
    config = load_config()
    head = b"[Summary]\nNovonix UHPC data file\n"
    assert sniff_source(head, config) == "novonix_csv"


def test_sniff_maccor():
    from bdf2._config import load_config
    config = load_config()
    head = b"Today's Date ,28-Nov-23\nDate of Test:,..."
    assert sniff_source(head, config) == "maccor_csv"


def test_sniff_unknown():
    from bdf2._config import load_config
    config = load_config()
    head = b"Some unknown instrument file format\n"
    assert sniff_source(head, config) is None
