# EPUB to PDF Bulk Converter Bot

A high-performance, Python-based utility to batch-convert EPUB books into print-ready, pixel-perfect, high-fidelity PDFs. 

Using **BeautifulSoup** for structure extraction and **Playwright (headless Chromium)** for rendering, it preserves full styling, embedded fonts, custom margins, page layouts, and illustrations.

---

## Key Features

- **Pixel-Perfect Rendering**: Uses headless Chromium to print documents, ensuring full CSS support, web font loading, and layout fidelity.
- **Absolute Asset Path Resolution**: Extracts EPUB structures and automatically resolves relative file paths for images (`img src`), styling (`link href`), and CSS resources (`url(...)`) to absolute `file:///` URLs.
- **Hyperlink & ID Collision Protection**: 
  - Prefixes internal HTML elements' `id` attributes per chapter to avoid namespace collisions.
  - Rewrites internal cross-chapter hyperlinks to point to local anchors in the compiled file, ensuring hyperlinks work inside the final PDF.
- **Running Headers & Footers**: Automatically generates elegant running headers with the book's title and footers with standard A4 page numbers (`Page / Total Pages`).
- **Resumable Bulk Conversion**: Automatically checks if a target PDF already exists (and is non-empty) to skip it during batch runs.
- **Folder Structure Mirroring**: Scans a source folder (e.g. `archive/tr/epub/`) and mirrors the directory tree in the output folder (e.g. `archive/tr/pdf/`).
- **Zero-residue Cleanup**: Automatically unzips and converts contents in isolated, uniquely named temporary folders, ensuring proper cleanup of intermediate files.

---

## Directory Layout (Expected)

The script looks for EPUB files under an `archive` folder, grouped by language, and outputs the PDFs alongside them:

```text
epub_to_pdf_converter/
├── convert_epub_to_pdf.py
├── requirements.txt
├── .gitignore
├── README.md
└── archive/
    ├── tr/
    │   ├── epub/               <-- Place Turkish .epub files here
    │   └── pdf/                <-- Generated PDFs will appear here
    └── en/
        ├── epub/               <-- Place English .epub files here
        └── pdf/                <-- Generated PDFs will appear here
```

---

## Installation

1. Clone or copy this directory:
   ```bash
   cd epub_to_pdf_converter
   ```

2. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

3. Install Playwright browser binaries (specifically Chromium):
   ```bash
   playwright install chromium
   ```

---

## Usage Guide

Run the script from the project root using standard command-line flags:

### 1. Bulk Convert All Languages
Recursively scans all language subdirectories (like `tr`, `en`) and batch-converts all EPUBs:
```bash
python convert_epub_to_pdf.py --all
```

### 2. Convert a Specific Language Directory
Only scans and converts books belonging to a specific language:
```bash
python convert_epub_to_pdf.py --lang tr
```

### 3. Convert a Single EPUB File
Converts one specific EPUB file:
```bash
python convert_epub_to_pdf.py --file "archive/tr/epub/Aforizmalar.epub"
```

### 4. Force Re-conversion
By default, the script skips files that already have a non-empty output PDF (resume mode). To override this and force re-convert everything:
```bash
python convert_epub_to_pdf.py --all --force
```

---

## Development & Logging

All detailed debug traces are logged in the project root under `archive/epub_to_pdf.log`. The terminal console output displays high-level progress tracking.
