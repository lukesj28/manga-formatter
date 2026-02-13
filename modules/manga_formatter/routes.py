"""
Manga Formatter — Flask routes.
Handles CBZ upload/browse, conversion streaming, and zip download.
"""

import os
import shutil
import tempfile
import json
import uuid
import zipfile
import mimetypes
import logging
from pathlib import Path
from flask import render_template, request, send_file, jsonify, Response

from modules.manga_formatter import bp
from modules.manga_formatter.converter import convert_chapters, classify_cbz_files
from modules.library.routes import save_to_library

logger = logging.getLogger(__name__)

BROWSE_ROOT = "/mangas"

# In-memory session store: session_id -> session data dict
_sessions = {}


@bp.route("/")
def index():
    logger.info("Manga Formatter index page accessed")
    return render_template("manga_formatter/index.html")


@bp.route("/browse", methods=["GET"])
def browse_directory():
    """List directories and files in BROWSE_ROOT."""
    req_path = request.args.get("path", BROWSE_ROOT)
    logger.info(f"Browse request for path: {req_path}")

    try:
        abs_root = os.path.abspath(BROWSE_ROOT)
        abs_req = os.path.abspath(req_path)

        logger.debug(f"Resolved browser paths - Root: {abs_root}, Requested: {abs_req}")

        if not abs_req.startswith(abs_root):
            logger.warning(f"Access denied: {abs_req} is outside {abs_root}")
            return jsonify({"error": "Access denied"}), 403

        if not os.path.exists(abs_req):
            logger.error(f"Directory not found: {abs_req}")
            if not os.path.exists(abs_root):
                logger.critical(f"Root directory {abs_root} does not exist! Check volume mount.")
            return jsonify({"error": "Directory not found"}), 404

        parent = os.path.dirname(abs_req)
        if not parent.startswith(abs_root):
            parent = None

        items = sorted(os.listdir(abs_req))
        dirs = []
        files = []

        for item in items:
            full_path = os.path.join(abs_req, item)
            if os.path.isdir(full_path):
                dirs.append({"name": item, "path": full_path})
            else:
                size = os.path.getsize(full_path)
                files.append({"name": item, "path": full_path, "size": _format_size(size)})

        logger.info(f"Found {len(dirs)} dirs and {len(files)} files in {abs_req}")

        return jsonify({
            "current_path": abs_req,
            "parent": parent,
            "dirs": dirs,
            "files": files
        })
    except Exception as e:
        logger.exception("Error in browse_directory")
        return jsonify({"error": str(e)}), 500


@bp.route("/preview/<session_id>/<filename>")
def preview_image(session_id, filename):
    """Serve the first image from a CBZ file for preview."""
    session = _sessions.get(session_id)
    if not session or filename not in session.get("unrecognized", {}):
        return "Not found", 404

    cbz_path = session["unrecognized"][filename]
    try:
        with zipfile.ZipFile(cbz_path, 'r') as zf:
            images = [f for f in zf.namelist() if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))]
            if not images:
                return "No images found", 404
            images.sort()

            img_name = images[0]
            img_data = zf.read(img_name)
            mime_type, _ = mimetypes.guess_type(img_name)
            return Response(img_data, mimetype=mime_type or "image/jpeg")

    except Exception as e:
        return str(e), 500


@bp.route("/download/<session_id>", methods=["GET"])
def download_zip(session_id):
    """Serve the generated zip file for a session."""
    session = _sessions.get(session_id)
    if not session or "zip_path" not in session:
        return jsonify({"error": "File not found or expired"}), 404

    return send_file(
        session["zip_path"],
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{session['manga_title']}.zip",
    )


def _stream_conversion(chapter_map, work_dir, output_base, manga_title, settings, session_id):
    """Generator that yields NDJSON progress updates."""
    try:
        converter_gen = convert_chapters(chapter_map, output_base, manga_title, settings)
        root_out = None

        for progress in converter_gen:
            if isinstance(progress, dict):
                yield json.dumps({"type": "progress", **progress}) + "\n"

        root_out = os.path.join(output_base, manga_title)

        yield json.dumps({"type": "progress", "message": "Zipping files...", "current": 100, "total": 100}) + "\n"

        zip_path = os.path.join(work_dir, f"{manga_title}.zip")
        _zip_directory(root_out, zip_path, manga_title)

        try:
            save_to_library(zip_path, f"{manga_title}.zip")
        except Exception:
            logger.exception("Failed to save ZIP to library")

        if session_id not in _sessions:
            _sessions[session_id] = {}
        _sessions[session_id]["zip_path"] = zip_path
        _sessions[session_id]["manga_title"] = manga_title

        yield json.dumps({
            "type": "done",
            "download_url": f"/manga-formatter/download/{session_id}"
        }) + "\n"

    except Exception as e:
        yield json.dumps({"type": "error", "message": str(e)}) + "\n"


@bp.route("/convert", methods=["POST"])
def convert():
    manga_title = request.form.get("title", "").strip()
    logger.info(f"Conversion request for: {manga_title}")

    if not manga_title:
        return jsonify({"error": "Manga title is required"}), 400

    settings = {
        "dithering": request.form.get("dithering", "true").lower() == "true",
        "contrast": int(request.form.get("contrast", "4")),
        "target_width": int(request.form.get("target_width", "480")),
        "target_height": int(request.form.get("target_height", "800")),
    }
    logger.info(f"Settings: {settings}")

    source_mode = request.form.get("source_mode", "upload")
    work_dir = tempfile.mkdtemp(prefix="manga_fmt_")
    session_id = str(uuid.uuid4())
    logger.info(f"Source mode: {source_mode}, Session ID: {session_id}")

    try:
        cbz_paths = []
        if source_mode == "hostdir":
            host_path = request.form.get("host_path", "").strip()
            if not host_path or not os.path.isdir(host_path):
                logger.error(f"Invalid host path: {host_path}")
                return jsonify({"error": f"Invalid host directory: {host_path}"}), 400
            for f in sorted(os.listdir(host_path)):
                if f.lower().endswith(".cbz"):
                    cbz_paths.append(os.path.join(host_path, f))
        else:
            files = request.files.getlist("cbz_files")
            if not files or all(f.filename == "" for f in files):
                return jsonify({"error": "No CBZ files uploaded"}), 400
            cbz_dir = os.path.join(work_dir, "input")
            os.makedirs(cbz_dir, exist_ok=True)
            for f in files:
                if f.filename and f.filename.lower().endswith(".cbz"):
                    save_path = os.path.join(cbz_dir, f.filename)
                    f.save(save_path)
                    cbz_paths.append(save_path)

        logger.info(f"Found {len(cbz_paths)} CBZ files")

        if not cbz_paths:
            return jsonify({"error": "No valid .cbz files found"}), 400

        recognized, unrecognized = classify_cbz_files(cbz_paths)
        logger.info(f"Classified: {len(recognized)} recognized, {len(unrecognized)} unrecognized")

        output_base = os.path.join(work_dir, "output")
        os.makedirs(output_base, exist_ok=True)

        if unrecognized:
            logger.info("Unrecognized files found, returning needs_review")
            _sessions[session_id] = {
                "work_dir": work_dir,
                "output_base": output_base,
                "manga_title": manga_title,
                "settings": settings,
                "recognized": recognized,
                "unrecognized": {os.path.basename(p): p for p in unrecognized},
            }

            unrecognized_info = [
                {"filename": os.path.basename(p), "preview_url": f"/manga-formatter/preview/{session_id}/{os.path.basename(p)}"}
                for p in unrecognized
            ]

            return jsonify({
                "status": "needs_review",
                "session_id": session_id,
                "recognized_count": len(recognized),
                "recognized_chapters": sorted(recognized.keys()),
                "unrecognized": unrecognized_info,
            })

        logger.info("Starting stream conversion")
        _sessions[session_id] = {"work_dir": work_dir}
        return Response(
            _stream_conversion(recognized, work_dir, output_base, manga_title, settings, session_id),
            mimetype="application/x-ndjson"
        )

    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        return jsonify({"error": str(e)}), 500


@bp.route("/convert/continue", methods=["POST"])
def convert_continue():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON data"}), 400

    session_id = data.get("session_id")
    assignments = data.get("assignments", {})
    session = _sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found or expired"}), 404

    try:
        work_dir = session["work_dir"]
        output_base = session["output_base"]
        manga_title = session["manga_title"]
        settings = session["settings"]
        recognized = session["recognized"]

        final_map = recognized.copy()
        for filename, ch_num_str in assignments.items():
            ch_num = int(ch_num_str)
            cbz_path = session["unrecognized"].get(filename)
            if cbz_path:
                final_map[ch_num] = cbz_path

        return Response(
            _stream_conversion(final_map, work_dir, output_base, manga_title, settings, session_id),
            mimetype="application/x-ndjson"
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _zip_directory(source_dir, zip_path, root_name):
    """Create a zip with root_name as the top-level folder."""
    source = Path(source_dir)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(source.rglob("*")):
            if file_path.is_file():
                arcname = os.path.join(root_name, file_path.relative_to(source))
                zf.write(file_path, arcname)


def _format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
