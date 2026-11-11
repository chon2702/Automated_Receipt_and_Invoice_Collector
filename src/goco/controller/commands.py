"""Application commands. Each command owns its inputs and execution."""

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from goco.models.reporting.expectations import evaluate, select_expectations
from goco.models.documents.importer import ImportMetadata, import_file
from goco.models.persistence.index import Index
from goco.models.persistence.locking import WriterLock
from ..models import AppConfig, CollectRequest, InvoiceMetadata, parse_month
from goco.models.reporting.reports import build_report, write_report
from goco.models.persistence.storage import DocumentStore, read_runs
from goco.models.documents.validation import metadata_valid


@dataclass(frozen=True)
class Context:
    config: AppConfig
    store: DocumentStore
    index: Index

    def expectation(self, expectation_id):
        if not expectation_id:
            return None
        for expectation in self.config.expectations:
            if expectation['id'] == expectation_id:
                return expectation
        raise ValueError('unknown_expectation')


@dataclass(frozen=True)
class CommandResult:
    data: object = None
    exit_code: int = 0
    error: str | None = None


class Command(Protocol):
    def execute(self, context: Context) -> CommandResult: ...


@dataclass(frozen=True)
class ValidateConfig:
    def execute(self, context):
        return CommandResult()


@dataclass(frozen=True)
class ListServices:
    def execute(self, context):
        return CommandResult(context.config.accounts)


@dataclass(frozen=True)
class Login:
    mailbox_id: str

    def execute(self, context):
        from goco.models.integrations.mail.auth import CredentialStore, login

        mailbox = context.config.mailbox(self.mailbox_id)
        with WriterLock(context.config.state_dir):
            login(mailbox, CredentialStore())
        return CommandResult()


@dataclass(frozen=True)
class Collect:
    month: str
    selector: str
    value: str | None
    dry_run: bool

    def execute(self, context):
        from goco.controller.runner import Runner

        config = context.config
        parse_month(self.month, config.timezone)
        fields = {'all': None, 'service': 'vendor_id', 'owner': 'owner_id', 'account': 'id'}
        if self.selector not in fields:
            raise ValueError('invalid_account_selector')
        field = fields[self.selector]
        selected = tuple(account['id'] for account in config.accounts
                         if field is None or account[field] == self.value)
        if not selected:
            raise ValueError('selector_matched_no_accounts')
        request = CollectRequest(self.month, selected, self.dry_run)
        runner = Runner(config, {}, context.store)
        if self.dry_run:
            summary = runner.collect(request)
        else:
            with WriterLock(config.state_dir):
                summary = runner.collect(request)
        failed = summary.interrupted or any(
            item['outcome'] in ('failed', 'auth_required') for item in summary.outcomes
        )
        return CommandResult(summary, int(failed))


@dataclass(frozen=True)
class DocumentFields:
    document_type: str
    issue_date: str | None
    period_start: str | None
    period_end: str | None
    invoice_id: str | None
    amount: str | None
    currency: str | None
    expectation_id: str | None

    def metadata(self):
        metadata = InvoiceMetadata(
            issue_date=self.issue_date, period_start=self.period_start, period_end=self.period_end,
            invoice_id=self.invoice_id, amount=self.amount, currency=self.currency,
            provenance='manual',
            verified=bool(self.period_start and self.period_end and self.document_type != 'unknown'),
        )
        metadata_valid(asdict(metadata))
        return metadata


@dataclass(frozen=True)
class ImportDocument:
    path: Path
    account_id: str
    fields: DocumentFields

    def execute(self, context):
        account = context.config.account(self.account_id)
        metadata = self.fields.metadata()
        expectation = context.expectation(self.fields.expectation_id)
        request = ImportMetadata(account, self.fields.document_type, metadata, expectation)
        with WriterLock(context.config.state_dir):
            outcome = import_file(self.path, request, context.store)
        return CommandResult(asdict(outcome))


@dataclass(frozen=True)
class VerifyDocument:
    document_id: str
    reviewed_by: str
    fields: DocumentFields

    def execute(self, context):
        update = {key: value for key, value in asdict(self.fields.metadata()).items() if value is not None}
        update.update(document_type=self.fields.document_type, reviewed_by=self.reviewed_by)
        expectation = context.expectation(self.fields.expectation_id)
        with WriterLock(context.config.state_dir):
            outcome = context.store.verify(self.document_id, update, expectation)
        return CommandResult(asdict(outcome))


def _report(context, month):
    config = context.config
    parse_month(month, config.timezone)
    runs = read_runs(config.storage_root)
    documents = context.store.documents()
    aws_accounts = {account['id'] for account in config.accounts if account['collection'] == 'aws'}
    coverage = [item for run in runs for item in run['coverage'] if item['account_id'] not in aws_accounts]
    obligations = evaluate(
        select_expectations(config.expectations, month), documents,
        datetime.now(ZoneInfo(config.timezone)).date(), coverage,
    )
    report = build_report(month, obligations, runs, documents)
    configured = {item['account_id'] for item in obligations}
    report['accounts'] = [
        {'account_id': account['id'], 'status': 'configured_obligations' if account['id'] in configured else 'unknown'}
        for account in config.accounts
    ]
    report['document_counts'] = {
        'total': len(documents),
        'review_required': sum(not doc.metadata.get('verified') or doc.conflict for doc in documents),
    }
    return report


@dataclass(frozen=True)
class Status:
    month: str

    def execute(self, context):
        return CommandResult(_report(context, self.month))


@dataclass(frozen=True)
class ExportReport:
    month: str
    path: Path

    def execute(self, context):
        report = _report(context, self.month)
        output = self.path.resolve()
        config = context.config
        protected = (config.state_dir, config.storage_root / 'documents', config.storage_root / 'runs')
        if output.exists() or any(output.is_relative_to(path) for path in protected):
            raise ValueError('report_output_must_be_new_and_outside_records')
        write_report(report, output)
        return CommandResult({'schema_version': 1, 'report_written': True})


@dataclass(frozen=True)
class RebuildIndex:
    def execute(self, context):
        with WriterLock(context.config.state_dir):
            result = context.index.rebuild(context.config.storage_root)
        return CommandResult(result)
