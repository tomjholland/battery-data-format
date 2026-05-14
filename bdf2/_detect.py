"""Magic-string source sniffing and tabular layout detection."""

from __future__ import annotations

from pathlib import Path


def read_sample(path: str | Path, n_bytes: int = 65536) -> str:
    """Read head of file, decode UTF-8 with replacement, drop trailing partial line."""
    with open(path, "rb") as fh:
        raw = fh.read(n_bytes)
    text = raw.decode("utf-8", errors="replace")
    last_nl = text.rfind("\n")
    if last_nl >= 0:
        text = text[:last_nl]
    return text


def _numeric_ratio(line: str, sep: str) -> float:
    """Fraction of fields that parse as float."""
    fields = line.split(sep)
    if not fields:
        return 0.0
    hits = 0
    for f in fields:
        try:
            float(f.strip())
            hits += 1
        except ValueError:
            pass
    return hits / len(fields)


def detect_layout(
    sample: str,
    candidates: tuple[str, ...] = (",", "\t", ";", "|"),
    min_run: int = 5,
) -> tuple[str, int, int, bool]:
    """
    Return (separator, header_idx, data_start_idx, has_header).

    Scores each candidate delimiter by run_length × field_count of the longest
    consecutive run of lines with identical field count ≥ 2.  The winning run's
    first line is taken as the header; has_header is set by numeric-ratio check.
    Falls back to (",", 0, 0, True) when no candidate yields a run ≥ min_run.
    """
    lines = sample.splitlines()

    best_sep = ","
    best_run_len = 0
    best_field_count = 0
    best_run_start = 0

    for sep in candidates:
        field_counts = [
            n if (n := len(line.rstrip(sep).split(sep))) >= 2 else 0
            for line in lines
        ]

        i = 0
        while i < len(field_counts):
            if field_counts[i] == 0:
                i += 1
                continue
            fc = field_counts[i]
            j = i
            while j < len(field_counts) and field_counts[j] == fc:
                j += 1
            run_len = j - i
            score = run_len * fc
            best_score = best_run_len * best_field_count
            if score > best_score or (score == best_score and run_len > best_run_len):
                best_sep = sep
                best_run_len = run_len
                best_field_count = fc
                best_run_start = i
            i = j

    if best_run_len < min_run:
        return (",", 0, 0, True)

    header_idx = best_run_start
    data_start_idx = best_run_start + 1

    header_line = lines[header_idx] if header_idx < len(lines) else ""
    data_line = lines[data_start_idx] if data_start_idx < len(lines) else ""
    h = _numeric_ratio(header_line, best_sep)
    d = _numeric_ratio(data_line, best_sep)
    has_header = (h < 0.3) and (d > 0.6)

    return (best_sep, header_idx, data_start_idx, has_header)


def sniff_source(head_bytes: bytes, config: dict) -> str | None:
    """Case-insensitive substring match of source magic strings against file head."""
    text = head_bytes.decode("utf-8", errors="replace").lower()
    for source_id, spec in config["sources"].items():
        for magic in spec.get("magic", []):
            if magic.lower() in text:
                return source_id
    return None
