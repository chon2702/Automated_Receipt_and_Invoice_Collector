from contextlib import contextmanager, closing
import json
import os
from dataclasses import asdict
from pathlib import Path
import sqlite3
import uuid
from .models import StoredDocument


class Index:
    def __init__(self, path: Path): self.path = Path(path)

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db=sqlite3.connect(self.path)
        db.execute('CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, account TEXT NOT NULL, body TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        try:
            with db:
                yield db
        finally:
            db.close()

    def upsert(self, document):
        with self._connect() as db:
            db.execute('INSERT OR REPLACE INTO documents VALUES (?,?,?)',(document.document_id,document.account_id,json.dumps(asdict(document))))

    def add_run(self, run):
        with self._connect() as db:
            db.execute('INSERT OR REPLACE INTO runs VALUES (?,?)',(run['run_id'],json.dumps(run)))

    def documents(self, account_id=None):
        if not self.path.exists(): return []
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            if account_id is None: rows=db.execute('SELECT body FROM documents ORDER BY id')
            else: rows=db.execute('SELECT body FROM documents WHERE account=? ORDER BY id',(account_id,))
            return [StoredDocument(**json.loads(row[0])) for row in rows]

    def runs(self):
        if not self.path.exists(): return []
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT body FROM runs')]

    def rebuild(self, root):
        from .storage import read_documents, read_runs
        documents=read_documents(Path(root))
        runs=read_runs(Path(root))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.rebuild')
        try:
            replacement=Index(temp)
            with replacement._connect(): pass
            for document in documents: replacement.upsert(document)
            for run in runs: replacement.add_run(run)
            os.replace(temp,self.path)
        finally:
            temp.unlink(missing_ok=True)
        return {'documents':len(documents),'runs':len(runs)}
