"""Dispatch a command with its application dependencies."""

import sqlite3

from ..models.accounts.config import load_config
from ..models.integrations.mail.auth import AuthRequired
from ..models.persistence.index import Index
from ..models.persistence.storage import DocumentStore
from .commands import Command, CommandResult, Context


def dispatch(config_path, command: Command) -> CommandResult:
    try:
        config = load_config(config_path)
        index = Index(config.state_dir / 'index.sqlite3')
        store = DocumentStore(config.storage_root, index, config.max_bytes)
        return command.execute(Context(config, store, index))
    except ValueError as error:
        return CommandResult(exit_code=2, error=str(error))
    except AuthRequired as error:
        return CommandResult(exit_code=1, error=str(error))
    except (OSError, sqlite3.Error):
        return CommandResult(exit_code=1, error='operation_failed: check file access, writer lock, or storage health')
    except KeyboardInterrupt:
        return CommandResult(exit_code=1, error='operation_interrupted')
