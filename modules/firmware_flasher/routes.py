"""
Firmware Flasher — Flask routes.
Serves the flasher page, firmware binary, and firmware metadata.
"""

import os
import logging

from flask import render_template, send_file, jsonify

from modules.firmware_flasher import bp

logger = logging.getLogger(__name__)

FIRMWARE_DIR = "/firmware"


@bp.route("/")
def index():
    logger.info("Firmware Flasher page accessed")
    return render_template("firmware_flasher/index.html")


@bp.route("/firmware")
def firmware():
    """Serve the firmware binary file."""
    fw_path = os.path.join(FIRMWARE_DIR, "firmware.bin")
    if not os.path.isfile(fw_path):
        return jsonify({"error": "firmware.bin not found"}), 404
    return send_file(fw_path, mimetype="application/octet-stream")

@bp.route("/firmware/bootloader")
def bootloader():
    """Serve the bootloader binary file."""
    fw_path = os.path.join(FIRMWARE_DIR, "bootloader.bin")
    if not os.path.isfile(fw_path):
        return jsonify({"error": "bootloader.bin not found"}), 404
    return send_file(fw_path, mimetype="application/octet-stream")

@bp.route("/firmware/partitions")
def partitions():
    """Serve the partitions binary file."""
    fw_path = os.path.join(FIRMWARE_DIR, "partitions.bin")
    if not os.path.isfile(fw_path):
        return jsonify({"error": "partitions.bin not found"}), 404
    return send_file(fw_path, mimetype="application/octet-stream")

@bp.route("/firmware/boot_app0")
def boot_app0():
    """Serve the boot_app0 binary file."""
    fw_path = os.path.join(FIRMWARE_DIR, "boot_app0.bin")
    if not os.path.isfile(fw_path):
        return jsonify({"error": "boot_app0.bin not found"}), 404
    return send_file(fw_path, mimetype="application/octet-stream")

@bp.route("/firmware/info")
def firmware_info():
    """Return firmware metadata as JSON."""
    fw_path = os.path.join(FIRMWARE_DIR, "firmware.bin")
    if not os.path.isfile(fw_path):
        return jsonify({"error": "firmware.bin not found"}), 404

    stat = os.stat(fw_path)
    return jsonify({
        "filename": "firmware.bin",
        "size": stat.st_size,
        "chip": "ESP32-C3",
        "offset": "0x10000",
    })


@bp.route("/firmware/manifest.json")
def manifest():
    """Return standard ESP Web Tools manifest."""
    fw_path = os.path.join(FIRMWARE_DIR, "firmware.bin")
    if not os.path.isfile(fw_path):
        return jsonify({"error": "firmware.bin not found"}), 404

    return jsonify({
        "name": "Xteink X4 Firmware",
        "version": "latest",
        "builds": [
            {
                "chipFamily": "ESP32-C3",
                "parts": [
                    {
                        "path": "/firmware-flasher/firmware",
                        "offset": 0x10000
                    }
                ]
            }
        ]
    })
