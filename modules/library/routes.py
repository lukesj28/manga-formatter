"""
Library module routes.
"""

import os
import shutil
import logging
from datetime import datetime
from flask import render_template, request, send_file, jsonify

from modules.library import bp

logger = logging.getLogger(__name__)

LIBRARY_DIR = "/library"


def save_to_library(src_path, filename):
    """Save file to library with unique name."""
    os.makedirs(LIBRARY_DIR, exist_ok=True)

    base, ext = os.path.splitext(filename)
    dest = os.path.join(LIBRARY_DIR, filename)
    counter = 1
    while os.path.exists(dest):
        dest = os.path.join(LIBRARY_DIR, f"{base}_{counter}{ext}")
        counter += 1

    shutil.copy2(src_path, dest)
    logger.info(f"Saved to library: {dest}")
    return dest


@bp.route("/")
def index():
    logger.info("Library page accessed")
    return render_template("library/index.html")


@bp.route("/files", methods=["GET"])
def list_files():
    """List library files."""
    os.makedirs(LIBRARY_DIR, exist_ok=True)

    files = []
    for name in sorted(os.listdir(LIBRARY_DIR)):
        path = os.path.join(LIBRARY_DIR, name)
        if not os.path.isfile(path):
            continue
        stat = os.stat(path)
        files.append({
            "name": name,
            "size": _format_size(stat.st_size),
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })

    return jsonify(files)


@bp.route("/download/<filename>", methods=["GET"])
def download(filename):
    """Download file."""
    path = os.path.join(LIBRARY_DIR, filename)
    if not os.path.isfile(path):
        return jsonify({"error": "File not found"}), 404

    # Prevent path traversal
    if os.path.abspath(path) != os.path.normpath(path):
        return jsonify({"error": "Invalid filename"}), 400

    return send_file(path, as_attachment=True, download_name=filename)


@bp.route("/upload", methods=["POST"])
def upload():
    """Handle file upload."""
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400

    os.makedirs(LIBRARY_DIR, exist_ok=True)

    filename = file.filename
    base, ext = os.path.splitext(filename)
    dest = os.path.join(LIBRARY_DIR, filename)
    counter = 1
    while os.path.exists(dest):
        dest = os.path.join(LIBRARY_DIR, f"{base}_{counter}{ext}")
        counter += 1

    file.save(dest)
    logger.info(f"Uploaded to library: {dest}")
    return jsonify({"message": "File uploaded", "name": os.path.basename(dest)}), 201


@bp.route("/delete/<filename>", methods=["DELETE"])
def delete(filename):
    """Delete file."""
    path = os.path.join(LIBRARY_DIR, filename)

    if os.path.abspath(path) != os.path.normpath(path):
        return jsonify({"error": "Invalid filename"}), 400

    if not os.path.isfile(path):
        return jsonify({"error": "File not found"}), 404

    os.remove(path)
    logger.info(f"Deleted from library: {path}")
    return jsonify({"message": "File deleted"})


def _format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
