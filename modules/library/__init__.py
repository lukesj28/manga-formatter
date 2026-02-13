from flask import Blueprint

bp = Blueprint(
    'library',
    __name__,
    template_folder='templates',
    static_folder='static',
)

from modules.library import routes  # noqa: E402, F401
