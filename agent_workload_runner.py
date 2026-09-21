"""Bounded, reproducible agent-like jobs executed inside CubeSandbox."""

import csv
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path


WORKLOADS = ("documents", "data", "code", "images", "search", "workflow")
SEARCH_DEFAULT_QUERY = "NEEDLE-ERROR-42"
SEARCH_QUERIES = (
    "payment timeout", "invoice mismatch", "account locked", "delivery delayed",
    "refund pending", "permission denied", "service unavailable", "duplicate charge",
)


def make_data(directory, rows=200000):
    path = directory / "sales.csv"
    rng = random.Random(42)
    totals = Counter()
    rejected_expected = 0
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["day", "region", "product", "amount"])
        for number in range(rows):
            region = ("north", "south", "east", "west")[number % 4]
            amount = rng.randrange(10, 500)
            dirty_amount = "" if number % 997 == 0 else amount
            dirty_region = "unknown" if number % 1499 == 0 else region
            writer.writerow([number % 365, dirty_region, number % 17, dirty_amount])
            if dirty_amount == "" or dirty_region == "unknown":
                rejected_expected += 1
            else:
                totals[region] += amount
    observed = Counter()
    rejected_observed = 0
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            if row["region"] not in {"north", "south", "east", "west"} or not row["amount"].isdigit():
                rejected_observed += 1
                continue
            observed[row["region"]] += int(row["amount"])
    if observed != totals or rejected_observed != rejected_expected:
        raise RuntimeError("CSV aggregate validation failed")
    os.environ["MPLCONFIGDIR"] = str(directory / ".mplconfig")
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    figure, axes = plt.subplots(figsize=(7, 4))
    axes.bar(list(observed), list(observed.values()))
    axes.set_title("Revenue by region")
    figure.tight_layout()
    chart = directory / "chart.png"
    figure.savefig(chart, dpi=110)
    plt.close(figure)
    if chart.stat().st_size < 1000:
        raise RuntimeError("chart is empty")
    return {"rows": rows, "valid_rows": rows - rejected_observed,
            "rejected_rows": rejected_observed, "regions": dict(observed),
            "chart_bytes": chart.stat().st_size}


def make_documents(directory, chart=None, paragraphs=300, pdf_pages=10):
    from docx import Document
    from pypdf import PdfReader
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    doc = Document()
    doc.add_heading("Agent workload report", 0)
    for number in range(paragraphs):
        doc.add_paragraph(f"Finding {number + 1}: " + "Validated sample data. " * 8)
    table = doc.add_table(rows=1, cols=3)
    table.rows[0].cells[0].text = "Metric"
    table.rows[0].cells[1].text = "Value"
    table.rows[0].cells[2].text = "Status"
    for metric, value in (("Records", "200000"), ("Regions", "4"), ("Checks", "passed")):
        cells = table.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = metric, value, "validated"
    docx_path = directory / "report.docx"
    doc.save(docx_path)
    parsed = Document(docx_path)
    if len(parsed.paragraphs) != paragraphs + 1 or len(parsed.tables) != 1:
        raise RuntimeError("DOCX paragraph validation failed")
    pdf_path = directory / "report.pdf"
    pdf = canvas.Canvas(str(pdf_path))
    for page in range(pdf_pages):
        pdf.setFont("Helvetica", 13)
        pdf.drawString(48, 790, f"Agent report - page {page + 1}")
        for line in range(35):
            pdf.drawString(48, 760 - line * 18, f"Finding {page * 35 + line + 1}: validated sample data")
        if chart is not None and page == 0:
            pdf.drawImage(ImageReader(str(chart)), 300, 520, width=250, height=145)
        pdf.showPage()
    pdf.save()
    if len(PdfReader(str(pdf_path)).pages) != pdf_pages:
        raise RuntimeError("PDF page validation failed")
    return {"docx_bytes": docx_path.stat().st_size,
            "pdf_bytes": pdf_path.stat().st_size, "pdf_pages": pdf_pages,
            "paragraphs": paragraphs, "tables": 1}


def run_code(directory):
    (directory / "analytics.py").write_text(
        "def prime(n):\n"
        "    if n < 2: return False\n"
        "    for d in range(2, int(n ** 0.5) + 1):\n"
        "        if n % d == 0: return False\n"
        "    return True\n"
        "def count_primes(limit):\n"
        "    return sum(prime(n) for n in range(limit))\n"
        "def moving_average(values, window):\n"
        "    if window < 1 or window > len(values): raise ValueError('invalid window')\n"
        "    return [sum(values[i:i+window]) / window for i in range(len(values)-window+1)]\n"
        "def summarize(rows):\n"
        "    totals = {}\n"
        "    for region, amount in rows: totals[region] = totals.get(region, 0) + amount\n"
        "    return totals\n", encoding="utf-8")
    (directory / "test_analytics.py").write_text(
        "import unittest\nfrom analytics import count_primes, moving_average, prime, summarize\n"
        "class AnalyticsTest(unittest.TestCase):\n"
        "    def test_prime(self): self.assertTrue(prime(7919))\n"
        "    def test_composite(self): self.assertFalse(prime(8000))\n"
        "    def test_count(self): self.assertEqual(count_primes(10000), 1229)\n"
        "    def test_small_prime(self): self.assertTrue(prime(2))\n"
        "    def test_moving_average(self): self.assertEqual(moving_average([1,2,3,4], 2), [1.5,2.5,3.5])\n"
        "    def test_invalid_window(self):\n"
        "        with self.assertRaises(ValueError): moving_average([1,2], 3)\n"
        "    def test_summary(self): self.assertEqual(summarize([('n',2),('s',3),('n',4)]), {'n':6,'s':3})\n"
        "    def test_empty_summary(self): self.assertEqual(summarize([]), {})\n"
        "if __name__ == '__main__': unittest.main()\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(directory)],
                            cwd=directory, capture_output=True, text=True, timeout=20, check=False)
    if result.returncode != 0 or "Ran 8 tests" not in result.stderr:
        raise RuntimeError("generated code tests failed: " + result.stderr[-300:])
    return {"modules_generated": 2, "tests_passed": 8, "test_output": result.stderr[-120:]}


def make_images(directory):
    from PIL import Image, ImageDraw
    widths = []
    thumbnails = []
    catalog = []
    for index in range(12):
        photo = Image.new("RGB", (1920, 1080), ((35 + index * 17) % 255, 70, 145))
        pen = ImageDraw.Draw(photo)
        for line in range(0, 1080, 20):
            pen.line((0, line, 1919, (line + index * 21) % 1080), fill=(220, 190, 80), width=3)
        source = directory / f"source-{index}.png"
        photo.save(source)
        with Image.open(source) as loaded:
            thumb = loaded.resize((320, 180))
        thumb.save(directory / f"thumb-{index}.jpg", quality=85)
        thumbnails.append(thumb)
        widths.append(thumb.width)
        catalog.append({"source": source.name, "source_size": [1920, 1080],
                        "thumbnail": f"thumb-{index}.jpg", "thumbnail_size": [320, 180],
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    contact = Image.new("RGB", (1280, 540))
    for index, thumb in enumerate(thumbnails):
        contact.paste(thumb, ((index % 4) * 320, (index // 4) * 180))
    contact_path = directory / "contact.jpg"
    contact.save(contact_path, quality=85)
    with Image.open(contact_path) as check:
        if check.size != (1280, 540) or widths != [320] * 12:
            raise RuntimeError("image validation failed")
    (directory / "image-catalog.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    return {"images_processed": 12, "source_dimensions": [1920, 1080],
            "contact_dimensions": [1280, 540], "contact_bytes": contact_path.stat().st_size}


def prepare_search_corpus(corpus, file_count=5000):
    """Prepare one read-only, uploaded-like corpus that many search tasks can share."""
    if not isinstance(file_count, int) or not 1 <= file_count <= 5000:
        raise ValueError("search corpus file count must be between 1 and 5000")
    corpus.mkdir()
    groups = [corpus / f"group-{index:02d}" for index in range(10)]
    for group in groups:
        group.mkdir()
    expected = Counter()
    for index in range(file_count):
        matched = index % 17 == 0
        words = (f"Document {index:04d}\nDepartment: team-{index % 12:02d}\n"
                 f"Year: {2022 + index % 5}\nStatus: reviewed\n" +
                 "Operational note with validated customer context. " * 18)
        if matched:
            query = SEARCH_QUERIES[(index // 17) % len(SEARCH_QUERIES)]
            words += f"\nIncident: {SEARCH_DEFAULT_QUERY}\nTopic: {query}\n"
            expected[SEARCH_DEFAULT_QUERY] += 1
            expected[query] += 1
        (groups[index % len(groups)] / f"note-{index:04}.txt").write_text(words, encoding="utf-8")
    files = sorted(corpus.rglob("*.txt"))
    digest = hashlib.sha256()
    total_bytes = 0
    for path in files:
        content = path.read_bytes()
        digest.update(content)
        total_bytes += len(content)
    manifest = {
        "file_count": len(files), "total_bytes": total_bytes,
        "sha256": digest.hexdigest(), "expected_matches": dict(expected),
        "queries": list(SEARCH_QUERIES),
    }
    (corpus / "search-corpus-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def search_files(directory, corpus=None, query=SEARCH_DEFAULT_QUERY):
    """Search a shared read-only corpus and write only the result into the task directory."""
    shared = corpus is not None
    corpus = Path(corpus) if corpus is not None else directory / "corpus"
    if not corpus.exists():
        prepare_search_corpus(corpus)
    if not corpus.is_dir() or corpus.is_symlink():
        raise RuntimeError("search corpus is not a safe directory")
    manifest = json.loads((corpus / "search-corpus-manifest.json").read_text(encoding="utf-8"))
    expected_matches = manifest.get("expected_matches", {})
    if not isinstance(query, str) or query not in expected_matches:
        raise ValueError("search query is not present in the corpus manifest")
    needle = query.encode("utf-8").lower()
    found = []
    citations = []
    digest = hashlib.sha256()
    files = sorted(corpus.rglob("*.txt"))
    for path in files:
        content = path.read_bytes()
        digest.update(content)
        if needle in content.lower():
            relative = str(path.relative_to(corpus))
            found.append(relative)
            if len(citations) < 5:
                text = content.decode("utf-8", "replace")
                line_number = next((number for number, line in enumerate(text.splitlines(), 1)
                                    if query.lower() in line.lower()), 1)
                citations.append({"path": relative, "line": line_number,
                                  "snippet": query})
    if (len(found) != expected_matches[query]
            or len(files) != manifest["file_count"]
            or digest.hexdigest() != manifest["sha256"]):
        raise RuntimeError("search result validation failed")
    result = {
        "query": query, "files_scanned": manifest["file_count"], "matches": len(found),
        "citations": citations, "corpus_bytes": manifest["total_bytes"],
        "sha256": digest.hexdigest(), "shared_read_only_corpus": shared,
    }
    (directory / "search-results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def run(workload, directory, shared_corpus=None, search_query=None):
    if workload == "documents":
        return make_documents(directory)
    if workload == "data":
        return make_data(directory)
    if workload == "code":
        return run_code(directory)
    if workload == "images":
        return make_images(directory)
    if workload == "search":
        return search_files(directory, shared_corpus, search_query or SEARCH_DEFAULT_QUERY)
    if workload == "workflow":
        data = make_data(directory, rows=150000)
        document = make_documents(directory, chart=directory / "chart.png")
        code = run_code(directory)
        return {"data": data, "documents": document, "code": code}
    raise ValueError("unknown workload")


if __name__ == "__main__":
    import resource
    workload, directory_name = sys.argv[1:3]
    directory = Path(directory_name)
    if workload not in WORKLOADS or not directory.is_dir() or directory.is_symlink():
        raise SystemExit("invalid workload or task directory")
    started = time.monotonic()
    shared_corpus = Path(sys.argv[3]) if workload == "search" and len(sys.argv) > 3 else None
    search_query = sys.argv[4] if workload == "search" and len(sys.argv) > 4 else None
    cleanup_shared = workload == "search" and len(sys.argv) > 5 and sys.argv[5] == "cleanup"
    try:
        details = run(workload, directory, shared_corpus, search_query)
    finally:
        if cleanup_shared and shared_corpus is not None:
            shutil.rmtree(shared_corpus.parent, ignore_errors=True)
    (directory / "result.json").write_text(json.dumps({
        "workload": workload, "details": details,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "child_peak_rss_kb": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    }), encoding="utf-8")
