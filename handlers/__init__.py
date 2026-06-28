from pyrogram import Client

from .cmd_start import register as reg_start
from .cmd_logout import register as reg_logout
from .cmd_help import register as reg_help
from .cmd_drives import register as reg_drives
from .cmd_storage import register as reg_storage
from .cmd_search import register as reg_search
from .cmd_account import register as reg_account
from .on_file import register as reg_file
from .file_manager import register as reg_fm
from .on_callback import register as reg_callback


def register_handlers(app: Client):
    reg_start(app)
    reg_logout(app)
    reg_help(app)
    reg_drives(app)
    reg_storage(app)
    reg_search(app)
    reg_account(app)
    reg_file(app)
    reg_fm(app)
    reg_callback(app)
