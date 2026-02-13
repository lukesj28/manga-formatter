"""
Book Converter — Flask routes.
Handles EPUB/PDF upload, conversion streaming, and file download.
"""

import os
import json
import uuid
import shutil
import tempfile
import logging
from flask import render_template, request, send_file, jsonify, Response

from modules.book_converter import bp
from modules.library.routes import save_to_library
from modules.book_converter.converter import (
    parse_epub,
    render_book,
    build_book_xtc,
    convert_pdf_to_epub,
)

logger = logging.getLogger(__name__)

CALIBRE_IO_PATH = os.environ.get("CALIBRE_IO_PATH", "/calibre-io")
SESSIONS_DIR = os.path.join(tempfile.gettempdir(), "x4_book_sessions")

# Ensure sessions directory exists
os.makedirs(SESSIONS_DIR, exist_ok=True)


@bp.route("/")
def index():
    logger.info("Book Converter index page accessed")
    return render_template("book_converter/index.html")


def _save_session_metadata(session_id, data):
    """Save session metadata to a JSON file."""
    session_dir = os.path.join(SESSIONS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    meta_path = os.path.join(session_dir, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(data, f)
    return session_dir


def _load_session_metadata(session_id):
    """Load session metadata from a JSON file."""
    meta_path = os.path.join(SESSIONS_DIR, session_id, "metadata.json")
    if os.path.exists(meta_path):
        with open(meta_path, "r") as f:
            return json.load(f)
    return None


def _stream_epub_to_xtc(epub_path, work_dir, settings, session_id):
    """Generator that yields NDJSON progress for EPUB→XTC conversion."""
    try:
        yield json.dumps({"type": "progress", "message": "Parsing EPUB...", "current": 0, "total": 100}) + "\n"

        parsed = parse_epub(epub_path)
        title = parsed["title"]
        safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip() or "book"

        for event in render_book(parsed, settings):
            if event["type"] == "progress":
                yield json.dumps(event) + "\n"
            elif event["type"] == "result":
                pages = event["pages"]
                metadata = event["metadata"]
                chapters = event["chapters"]

                yield json.dumps({"type": "progress", "message": "Building XTC file...", "current": 99, "total": 100}) + "\n"

                # Define output path
                out_filename = f"{safe_title}.xtc"
                out_path = os.path.join(work_dir, out_filename)
                
                size = (settings.get("target_width", 480), settings.get("target_height", 800))
                build_book_xtc(pages, out_path, metadata, chapters, size)

                try:
                    save_to_library(out_path, out_filename)
                except Exception:
                    logger.exception("Failed to save XTC to library")

                # Save session metadata to filesystem (shared across workers)
                _save_session_metadata(session_id, {
                    "file_path": out_path,
                    "filename": out_filename,
                    "work_dir": work_dir,
                })

                yield json.dumps({
                    "type": "done",
                    "download_url": f"/book-converter/download/{session_id}",
                    "filename": out_filename,
                }) + "\n"

    except Exception as e:
        logger.exception("Conversion error")
        yield json.dumps({"type": "error", "message": str(e)}) + "\n"


@bp.route("/convert", methods=["POST"])
def convert():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded"}), 400

    filename = file.filename.lower()
    output_format = request.form.get("output_format", "xtc").lower()

    settings = {
        "target_width": int(request.form.get("target_width", "480")),
        "target_height": int(request.form.get("target_height", "800")),
        "font_size": int(request.form.get("font_size", "28")),
        "margin": int(request.form.get("margin", "20")),
        "line_height": float(request.form.get("line_height", "1.4")),
        "dithering": request.form.get("dithering", "true").lower() == "true",
        "contrast": float(request.form.get("contrast", "1.2")),
    }

    # Session ID is critical for retrieval
    session_id = str(uuid.uuid4())
    
    # Store work files in session directory
    # Facilitates cleanup
    session_dir = os.path.join(SESSIONS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    work_dir = session_dir

    logger.info(f"Convert request: {file.filename}, format={output_format}, session={session_id}")

    try:
        input_path = os.path.join(work_dir, file.filename)
        file.save(input_path)

        if filename.endswith(".pdf"):
            if output_format == "epub":
                # PDF → EPUB via Calibre
                try:
                    epub_path = convert_pdf_to_epub(input_path, CALIBRE_IO_PATH)
                    epub_filename = os.path.basename(epub_path)
                    
                    # Copy to work_dir for serving
                    local_epub = os.path.join(work_dir, epub_filename)
                    shutil.copy2(epub_path, local_epub)

                    try:
                        save_to_library(local_epub, epub_filename)
                    except Exception:
                        logger.exception("Failed to save EPUB to library")

                    # Save metadata
                    _save_session_metadata(session_id, {
                        "file_path": local_epub,
                        "filename": epub_filename,
                        "work_dir": work_dir,
                    })

                    return jsonify({
                        "type": "done",
                        "download_url": f"/book-converter/download/{session_id}",
                        "filename": epub_filename,
                    })
                except (TimeoutError, FileNotFoundError) as e:
                    # Clean up on error
                    shutil.rmtree(work_dir, ignore_errors=True)
                    return jsonify({"error": str(e)}), 503

            else:
                # PDF → XTC (Calibre first, then render)
                def stream_pdf_to_xtc():
                    try:
                        yield json.dumps({"type": "progress", "message": "Converting PDF to EPUB via Calibre...", "current": 0, "total": 100}) + "\n"
                        epub_path = convert_pdf_to_epub(input_path, CALIBRE_IO_PATH)
                        yield from _stream_epub_to_xtc(epub_path, work_dir, settings, session_id)
                    except (TimeoutError, FileNotFoundError) as e:
                        yield json.dumps({"type": "error", "message": str(e)}) + "\n"
                    except Exception as e:
                        logger.exception("PDF→XTC error")
                        yield json.dumps({"type": "error", "message": str(e)}) + "\n"

                return Response(stream_pdf_to_xtc(), mimetype="application/x-ndjson")

        elif filename.endswith(".epub"):
            if output_format == "xtc":
                # EPUB → XTC
                return Response(
                    _stream_epub_to_xtc(input_path, work_dir, settings, session_id),
                    mimetype="application/x-ndjson",
                )
            else:
                shutil.rmtree(work_dir, ignore_errors=True)
                return jsonify({"error": "EPUB is already in EPUB format"}), 400
        else:
            shutil.rmtree(work_dir, ignore_errors=True)
            return jsonify({"error": "Unsupported file type. Upload a PDF or EPUB file."}), 400

    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.exception("Convert route error")
        return jsonify({"error": str(e)}), 500


@bp.route("/download/<session_id>", methods=["GET"])
def download(session_id):
    session = _load_session_metadata(session_id)
    if not session or "file_path" not in session:
        return jsonify({"error": "File not found or expired"}), 404

    # Verify existance
    if not os.path.exists(session["file_path"]):
        return jsonify({"error": "File has been deleted"}), 404

    return send_file(
        session["file_path"],
        as_attachment=True,
        download_name=session["filename"],
    )
