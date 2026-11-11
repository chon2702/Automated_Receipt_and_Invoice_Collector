"""Dispatch a command with its application dependencies."""

from ..config import load_config
from ..index import Index
from ..storage import DocumentStore
from .commands import Command, CommandResult, Context


def dispatch(config_path, command: Command) -> CommandResult:
    config = load_config(config_path)
    index = Index(config.state_dir / 'index.sqlite3')
    store = DocumentStore(config.storage_root, index, config.max_bytes)
    return command.execute(Context(config, store, index))
