"""
X4 Utilities — Flask web application.
Multi-module e-ink device toolkit.
"""

import logging
import sys
from flask import Flask, render_template

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 * 1024  # 16 GB upload limit

# Register blueprints
from modules.library import bp as library_bp
from modules.manga_formatter import bp as manga_formatter_bp
from modules.book_converter import bp as book_converter_bp
from modules.firmware_flasher import bp as firmware_flasher_bp

app.register_blueprint(library_bp, url_prefix='/library')
app.register_blueprint(manga_formatter_bp, url_prefix='/manga-formatter')
app.register_blueprint(book_converter_bp, url_prefix='/book-converter')
app.register_blueprint(firmware_flasher_bp, url_prefix='/firmware-flasher')


@app.route("/")
def home():
    logger.info("Home page accessed")
    return render_template("home.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
