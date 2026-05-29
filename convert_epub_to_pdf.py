import os
import re
import sys
import zipfile
import shutil
import uuid
import argparse
import time
import logging
import posixpath
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Setup Logger
def setup_logging():
    logger = logging.getLogger("EPUB2PDF")
    logger.setLevel(logging.DEBUG)
    
    # Console Handler (INFO level for clean user logs)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console_format = logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%H:%M:%S')
    console.setFormatter(console_format)
    logger.addHandler(console)
    
    # File Handler (DEBUG level for deep diagnostics)
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(os.path.join(log_dir, "epub_to_pdf.log"), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter('%(asctime)s - [%(levelname)s] - %(filename)s:%(lineno)d - %(message)s')
    file_format.setFormatter(file_format)
    logger.addHandler(file_handler)
    
    return logger

logger = setup_logging()

# Regex to find url(...) in CSS
CSS_URL_REGEX = re.compile(r'url\s*\(\s*[\'"]?([^\'"\)]+)[\'"]?\s*\)')

def clean_anchor_id(href):
    """Generates a clean anchor id for file cross-referencing."""
    return re.sub(r'[^a-zA-Z0-9_\-]', '_', href)

def convert_single_epub(epub_path, output_pdf_path, playwright_browser):
    """
    Converts a single EPUB file to PDF.
    - unzips the EPUB
    - reads container.xml to locate OPF file
    - parses manifest and spine from OPF
    - aggregates stylesheets and merges chapters
    - resolves all relative URLs for images, hyperlinks, and CSS assets to absolute file:// URLs
    - prints using Playwright
    """
    logger.debug(f"Starting conversion for EPUB: {epub_path}")
    
    # Unique temp directory
    base_dir = os.path.dirname(os.path.abspath(__file__))
    temp_root = os.path.join(base_dir, "scratch", "temp_extracted")
    os.makedirs(temp_root, exist_ok=True)
    temp_extracted_dir = os.path.join(temp_root, f"epub_{uuid.uuid4().hex}")
    
    # Temp HTML file path
    temp_html_path = os.path.join(temp_root, f"temp_book_{uuid.uuid4().hex}.html")
    
    try:
        # 1. Unzip EPUB
        with zipfile.ZipFile(epub_path, 'r') as epub:
            epub.extractall(temp_extracted_dir)
            namelist = epub.namelist()
            
        logger.debug(f"Extracted EPUB to temp dir: {temp_extracted_dir}")
        
        # 2. Locate container.xml to find OPF path
        container_path = os.path.join(temp_extracted_dir, "META-INF", "container.xml")
        if not os.path.exists(container_path):
            raise FileNotFoundError("EPUB is missing META-INF/container.xml")
            
        with open(container_path, 'r', encoding='utf-8') as f:
            container_soup = BeautifulSoup(f.read(), 'xml')
            
        rootfile_tag = container_soup.find('rootfile')
        if not rootfile_tag or not rootfile_tag.get('full-path'):
            raise ValueError("Could not find rootfile element or full-path attribute in container.xml")
            
        opf_relative_path = rootfile_tag.get('full-path')
        opf_abs_path = os.path.join(temp_extracted_dir, opf_relative_path.replace('/', os.sep))
        
        if not os.path.exists(opf_abs_path):
            raise FileNotFoundError(f"OPF file not found at: {opf_abs_path}")
            
        logger.debug(f"Found OPF file: {opf_relative_path}")
        
        # 3. Parse OPF file
        with open(opf_abs_path, 'r', encoding='utf-8') as f:
            opf_soup = BeautifulSoup(f.read(), 'xml')
            
        opf_dir = posixpath.dirname(opf_relative_path) # e.g. "OEBPS" or ""
        
        # Manifest
        manifest = {}
        for item in opf_soup.find_all('item'):
            manifest[item.get('id')] = item.get('href')
            
        # Spine (reading order)
        spine_hrefs = []
        for itemref in opf_soup.find('spine').find_all('itemref'):
            idref = itemref.get('idref')
            if idref in manifest:
                spine_hrefs.append(manifest[idref])
                
        # Resolve reading order zip paths
        spine_zip_paths = []
        for href in spine_hrefs:
            # Paths in OPF are relative to the OPF file directory
            zip_path = posixpath.normpath(posixpath.join(opf_dir, href))
            spine_zip_paths.append(zip_path)
            
        logger.debug(f"Spine has {len(spine_zip_paths)} documents.")
        
        # Mapping zip paths to safe anchor IDs for internal links
        spine_id_map = {path: f"ch_{idx}" for idx, path in enumerate(spine_zip_paths)}
        
        # 4. Extract and resolve CSS
        css_content = ""
        for item_id, href in manifest.items():
            if href.endswith('.css'):
                css_zip_path = posixpath.normpath(posixpath.join(opf_dir, href))
                css_abs_path = os.path.join(temp_extracted_dir, css_zip_path.replace('/', os.sep))
                if os.path.exists(css_abs_path):
                    logger.debug(f"Reading and resolving CSS: {css_zip_path}")
                    with open(css_abs_path, 'r', encoding='utf-8') as f:
                        raw_css = f.read()
                    
                    # Resolve any relative URL paths inside the CSS file (e.g. background-image or fonts)
                    css_dir = posixpath.dirname(css_zip_path)
                    def replace_css_url(match):
                        url = match.group(1)
                        if url.startswith(('http://', 'https://', 'data:', 'file:', '#')):
                            return match.group(0)
                        
                        resolved_url = posixpath.normpath(posixpath.join(css_dir, url))
                        abs_asset_path = os.path.join(temp_extracted_dir, resolved_url.replace('/', os.sep))
                        file_url = f"file:///{abs_asset_path.replace(os.sep, '/')}"
                        return f"url('{file_url}')"
                        
                    resolved_css = CSS_URL_REGEX.sub(replace_css_url, raw_css)
                    css_content += resolved_css + "\n"
        
        # Extract title and author from OPF metadata
        book_title = "Converted Book"
        author = ""
        title_tag = opf_soup.find('dc:title')
        if title_tag:
            book_title = title_tag.get_text()
        creator_tag = opf_soup.find('dc:creator')
        if creator_tag:
            author = creator_tag.get_text()
            
        # 5. Extract and merge chapters
        merged_body_html = ""
        for idx, zip_path in enumerate(spine_zip_paths):
            # Skip standard EPUB 3 Table of Contents page to avoid double TOC, unless it contains custom contents
            # Often, nav.xhtml is just a plain bullet list. We will skip files with "nav.xhtml" in their name.
            if 'nav.xhtml' in zip_path.lower():
                logger.debug(f"Skipping Nav TOC file: {zip_path}")
                continue
                
            ch_abs_path = os.path.join(temp_extracted_dir, zip_path.replace('/', os.sep))
            if not os.path.exists(ch_abs_path):
                logger.warning(f"Spine file missing: {ch_abs_path}")
                continue
                
            logger.debug(f"Parsing chapter {idx}: {zip_path}")
            with open(ch_abs_path, 'r', encoding='utf-8') as f:
                ch_soup = BeautifulSoup(f.read(), 'html.parser')
                
            body_tag = ch_soup.find('body')
            if not body_tag:
                logger.warning(f"No body tag in chapter: {zip_path}")
                continue
                
            # Document base directory inside zip for relative asset resolution
            doc_dir = posixpath.dirname(zip_path)
            
            # Prefix all existing ID attributes inside this chapter to prevent collisions
            for tag in body_tag.find_all(id=True):
                tag['id'] = f"ch_{idx}_{tag['id']}"
                
            # Resolve image src paths, link href paths, etc.
            # We look at tags like img (src), link/a (href), etc.
            for tag in body_tag.find_all(['img', 'image', 'a', 'link']):
                # Resolve image tags (handle SVG image tag href as well)
                src_attr = 'src'
                if tag.name == 'image':
                    src_attr = 'xlink:href' if tag.has_attr('xlink:href') else 'href'
                
                # Image src resolution
                if tag.has_attr(src_attr):
                    src_val = tag[src_attr]
                    if not src_val.startswith(('http://', 'https://', 'data:', 'file:', '#')):
                        resolved_path = posixpath.normpath(posixpath.join(doc_dir, src_val))
                        abs_asset = os.path.join(temp_extracted_dir, resolved_path.replace('/', os.sep))
                        tag[src_attr] = f"file:///{abs_asset.replace(os.sep, '/')}"
                        
                # Link href resolution (internal hyperlinks between chapters, or internal anchors)
                if tag.name == 'a' and tag.has_attr('href'):
                    href_val = tag['href']
                    if href_val.startswith('#'):
                        # Internal document link -> prefix with chapter id
                        tag['href'] = f"#ch_{idx}_{href_val[1:]}"
                    elif not href_val.startswith(('http://', 'https://', 'data:', 'file:')):
                        # Split link into file path and hash anchor
                        parts = href_val.split('#')
                        target_file_rel = parts[0]
                        anchor = parts[1] if len(parts) > 1 else None
                        
                        # Resolve target zip path relative to current chapter directory
                        target_zip_path = posixpath.normpath(posixpath.join(doc_dir, target_file_rel))
                        
                        if target_zip_path in spine_id_map:
                            target_ch_id = spine_id_map[target_zip_path]
                            if anchor:
                                tag['href'] = f"#{target_ch_id}_{anchor}"
                            else:
                                tag['href'] = f"#{target_ch_id}"
                        else:
                            # Not in spine, resolve as absolute file link
                            abs_target = os.path.join(temp_extracted_dir, target_zip_path.replace('/', os.sep))
                            tag['href'] = f"file:///{abs_target.replace(os.sep, '/')}"
                            
            # Add chapter container
            merged_body_html += f'<div class="chapter-container" id="ch_{idx}">\n'
            merged_body_html += body_tag.decode_contents()
            merged_body_html += '\n</div>\n<div class="page-break"></div>\n'
            
        # 6. Inject CSS overrides and format settings
        # Premium layout styling
        print_styles = """
        @media print {
            .page-break {
                page-break-after: always;
                break-after: page;
            }
        }
        body {
            font-family: "Georgia", serif;
            line-height: 1.6;
            font-size: 11.5pt;
            color: #111111;
            background-color: #ffffff !important;
            margin: 0;
            padding: 0;
        }
        h1, h2, h3, h4, h5, h6 {
            font-family: "Outfit", "Georgia", serif;
            color: #000000;
            page-break-after: avoid;
            break-after: avoid;
        }
        h1 {
            font-size: 2.2em;
            margin-top: 40px;
            margin-bottom: 20px;
            text-align: center;
        }
        h2 {
            font-size: 1.6em;
            margin-top: 30px;
            margin-bottom: 15px;
        }
        p {
            margin-bottom: 1.2em;
            text-align: justify;
            text-indent: 1.5em;
        }
        p:first-of-type, .chapter-container > p:first-of-type {
            text-indent: 0;
        }
        img {
            max-width: 100%;
            height: auto;
            display: block;
            margin: 20px auto;
            page-break-inside: avoid;
            break-inside: avoid;
        }
        .chapter-container {
            padding: 10px 0;
        }
        """
        
        full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{book_title}</title>
    <style>
        {css_content}
        {print_styles}
    </style>
</head>
<body>
    {merged_body_html}
</body>
</html>
"""
        
        # 7. Write consolidated HTML
        with open(temp_html_path, 'w', encoding='utf-8') as f:
            f.write(full_html)
            
        # 8. Load in Playwright page and generate PDF
        # We assume the browser is launched already to save execution time
        page = playwright_browser.new_page()
        
        # absolute file URL path
        file_url = f"file:///{os.path.abspath(temp_html_path).replace(os.sep, '/')}"
        
        logger.debug(f"Loading merged HTML page...")
        page.goto(file_url, wait_until="networkidle")
        
        # Define clean page number footers
        footer_template = """
        <div style="font-size: 8pt; font-family: 'Georgia', serif; width: 100%; text-align: center; color: #777; margin-bottom: 5px;">
            <span class="pageNumber"></span> / <span class="totalPages"></span>
        </div>
        """
        
        header_template = f"""
        <div style="font-size: 7.5pt; font-family: 'Georgia', serif; width: 100%; text-align: right; color: #999; padding-right: 20px;">
            {book_title}
        </div>
        """
        
        os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)
        
        logger.debug(f"Printing PDF to: {output_pdf_path}")
        page.pdf(
            path=output_pdf_path,
            format="A4",
            margin={
                "top": "22mm",
                "bottom": "22mm",
                "left": "20mm",
                "right": "20mm"
            },
            print_background=True,
            display_header_footer=True,
            header_template=header_template,
            footer_template=footer_template
        )
        
        page.close()
        logger.debug(f"Conversion complete.")
        
    finally:
        # Cleanup
        if os.path.exists(temp_html_path):
            try:
                os.remove(temp_html_path)
            except Exception as ex:
                logger.debug(f"Failed to remove temp HTML file {temp_html_path}: {ex}")
                
        if os.path.exists(temp_extracted_dir):
            try:
                shutil.rmtree(temp_extracted_dir)
            except Exception as ex:
                logger.debug(f"Failed to clean up temp extracted directory {temp_extracted_dir}: {ex}")

def bulk_convert(args):
    """
    Scans folders and converts EPUBs based on user arguments.
    Reuses a single Playwright Chromium session to process all files extremely fast.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    archive_dir = os.path.join(base_dir, "archive")
    
    # 1. Discover EPUB files to convert
    epub_files = []
    
    # Resolve source path
    if args.file:
        # Specific file conversion
        file_path = os.path.abspath(args.file)
        if not os.path.exists(file_path):
            logger.error(f"Specified EPUB file does not exist: {file_path}")
            return
        # Work out language and output path
        # Try to guess output structure, otherwise output in same directory with .pdf extension
        # Standard: .../archive/<lang>/epub/<filename>.epub -> .../archive/<lang>/pdf/<filename>.pdf
        filename = os.path.basename(file_path)
        base_name, _ = os.path.splitext(filename)
        
        # Check if it fits the archive pattern
        parent_dir = os.path.dirname(file_path)
        grandparent_dir = os.path.dirname(parent_dir)
        if os.path.basename(parent_dir) == 'epub':
            # Mirrors standard folder layout
            out_pdf = os.path.join(grandparent_dir, "pdf", f"{base_name}.pdf")
        else:
            out_pdf = os.path.join(parent_dir, f"{base_name}.pdf")
            
        epub_files.append((file_path, out_pdf))
        
    else:
        # Bulk conversion mode
        # Scan languages under archive
        langs = []
        if args.lang:
            langs = [args.lang]
        else:
            # Scan directories inside archive folder
            if os.path.exists(archive_dir):
                for entry in os.scandir(archive_dir):
                    if entry.is_dir() and entry.name in ['tr', 'en']:
                        langs.append(entry.name)
                        
        if not langs:
            logger.warning(f"No language subfolders found under {archive_dir} (like tr, en). Check if downloads exist.")
            return
            
        logger.info(f"Target languages for scanning: {', '.join(langs)}")
        for lang in langs:
            lang_epub_dir = os.path.join(archive_dir, lang, "epub")
            lang_pdf_dir = os.path.join(archive_dir, lang, "pdf")
            
            if not os.path.exists(lang_epub_dir):
                logger.warning(f"EPUB directory does not exist for language '{lang}': {lang_epub_dir}")
                continue
                
            for root, _, files in os.walk(lang_epub_dir):
                for file in files:
                    if file.endswith('.epub'):
                        epub_abs = os.path.join(root, file)
                        
                        # Generate mirroring PDF path
                        rel_path = os.path.relpath(epub_abs, lang_epub_dir)
                        pdf_rel_path = os.path.splitext(rel_path)[0] + ".pdf"
                        pdf_abs = os.path.join(lang_pdf_dir, pdf_rel_path)
                        
                        epub_files.append((epub_abs, pdf_abs))
                        
    total_files = len(epub_files)
    if total_files == 0:
        logger.info("No EPUB files found for conversion.")
        return
        
    logger.info(f"Found {total_files} EPUB files to process.")
    
    # 2. Convert files reusing the Playwright browser session
    success_count = 0
    skipped_count = 0
    failed_count = 0
    failures = []
    
    start_time = time.time()
    
    logger.info("Initializing Playwright browser context...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        for idx, (epub_path, pdf_path) in enumerate(epub_files, 1):
            book_name = os.path.basename(epub_path)
            
            # Check if PDF already exists (Resume support)
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0 and not args.force:
                logger.info(f"[{idx}/{total_files}] SKIPPED: '{book_name}' (PDF already exists).")
                skipped_count += 1
                continue
                
            logger.info(f"[{idx}/{total_files}] CONVERTING: '{book_name}'...")
            item_start = time.time()
            
            try:
                convert_single_epub(epub_path, pdf_path, browser)
                elapsed = time.time() - item_start
                logger.info(f"[{idx}/{total_files}] SUCCESS: Converted in {elapsed:.2f} seconds.")
                success_count += 1
            except Exception as e:
                logger.error(f"[{idx}/{total_files}] FAILED: Error converting '{book_name}': {e}", exc_info=True)
                failed_count += 1
                failures.append((book_name, str(e)))
                
        browser.close()
        
    total_elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info("                     CONVERSION SUMMARY                     ")
    logger.info("=" * 60)
    logger.info(f"  Total books discovered: {total_files}")
    logger.info(f"  Successfully converted: {success_count}")
    logger.info(f"  Skipped (already converted): {skipped_count}")
    logger.info(f"  Failed conversions:     {failed_count}")
    logger.info(f"  Total time elapsed:     {total_elapsed:.2f} seconds")
    logger.info("=" * 60)
    
    if failures:
        logger.warning(f"--- FAILURE DETAILS ({len(failures)} books) ---")
        for book_name, error_msg in failures:
            logger.warning(f"  * '{book_name}': {error_msg}")
        logger.warning("-" * 60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk EPUB-to-PDF Conversion Bot.")
    parser.add_argument("--file", help="Path to a single EPUB file to convert.")
    parser.add_argument("--lang", help="Specific language folder to convert (e.g. 'tr', 'en').")
    parser.add_argument("--all", action="store_true", help="Convert all EPUB files in the archive directory.")
    parser.add_argument("--force", action="store_true", help="Force overwrite existing PDF files.")
    args = parser.parse_args()
    
    # If no options are specified, guide the user or default to scanning all
    if not (args.file or args.lang or args.all):
        # Default behavior: scan all
        args.all = True
        
    bulk_convert(args)
