"""Translate command-line arguments into commands and render their results."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys

from ..auth import AuthRequired
from ..controller import dispatch
from ..controller.commands import (
    Collect, DocumentFields, ExportReport, ImportDocument, ListServices, Login,
    RebuildIndex, Status, ValidateConfig, VerifyDocument,
)
from ..models import DOCUMENT_TYPES


def safe_text(value):
    return ''.join(char if char.isprintable() else '?' for char in str(value))


def _render_services(accounts, args):
    for account in accounts:
        print(safe_text(f"{account['id']} | {account['vendor_id']} | {account['owner_id']} | {account['collection']}"))


def _render_json(data, args):
    print(json.dumps(data, ensure_ascii=True))


def _render_collection(summary, args):
    if args.format == 'json':
        _render_json({**asdict(summary), 'counts': summary.counts}, args)
        return
    print('Collection: ' + json.dumps(summary.counts))
    for item in summary.outcomes:
        print(safe_text(f"{item['account_id']} | {item['outcome']} | {item['reason']} | review={item.get('review_id') or '-'}"))


def _render_status(report, args):
    if args.format == 'json':
        _render_json(report, args)
        return
    print('Obligations: ' + json.dumps(report['counts']))
    for item in report['obligations']:
        print(safe_text(f"{item['account_id']} | {item['expectation_id']} | {item['status']}"))
    for item in report['accounts']:
        if item['status'] == 'unknown':
            print(safe_text(f"{item['account_id']} | unknown (no configured expectation for this month)"))
    print('Documents: ' + json.dumps(report['document_counts']))
    for document in report['documents']:
        print(safe_text(f"{document['document_id']} | {document['account_id']} | {document['document_type']} | {document['original_path']}"))


def _collect_command(args):
    if args.all:
        return Collect(args.month, 'all', None, args.dry_run)
    for selector in ('service', 'owner', 'account'):
        value = getattr(args, selector)
        if value is not None:
            return Collect(args.month, selector, value, args.dry_run)
    raise ValueError('selector_matched_no_accounts')


def _document_fields(args):
    return DocumentFields(
        document_type=args.type, issue_date=args.issue_date, period_start=args.period_start,
        period_end=args.period_end, invoice_id=args.invoice_id, amount=args.amount,
        currency=args.currency, expectation_id=args.expectation,
    )


def _parser():
    parser = argparse.ArgumentParser(prog='goco', description='Collect original invoices from Gmail, AWS, or manual files.')
    parser.add_argument('--config', required=True, help='Path to company configuration JSON')
    sub = parser.add_subparsers(dest='command', required=True)
    config = sub.add_parser('config').add_subparsers(dest='action', required=True)
    config.add_parser('validate').set_defaults(
        build=lambda args: ValidateConfig(), render=lambda data, args: print('Configuration valid'),
    )
    sub.add_parser('list-services').set_defaults(build=lambda args: ListServices(), render=_render_services)
    login = sub.add_parser('auth').add_subparsers(dest='action', required=True).add_parser('login')
    login.add_argument('--mailbox', required=True)
    login.set_defaults(
        build=lambda args: Login(args.mailbox),
        render=lambda data, args: print('Gmail consent saved in Windows credential storage'),
    )
    collect = sub.add_parser('collect')
    collect.add_argument('--month', required=True)
    selectors = collect.add_mutually_exclusive_group(required=True)
    selectors.add_argument('--all', action='store_true')
    for key in ('service', 'owner', 'account'):
        selectors.add_argument('--' + key)
    collect.add_argument('--dry-run', action='store_true')
    collect.add_argument('--format', choices=['text', 'json'], default='text')
    collect.set_defaults(build=_collect_command, render=_render_collection)

    manual = sub.add_parser('import')
    manual.add_argument('file', type=Path)
    manual.add_argument('--account', required=True)
    manual.set_defaults(
        build=lambda args: ImportDocument(args.file, args.account, _document_fields(args)), render=_render_json,
    )
    verify = sub.add_parser('document').add_subparsers(dest='action', required=True).add_parser('verify')
    verify.add_argument('--id', required=True)
    verify.add_argument('--reviewed-by', required=True)
    verify.set_defaults(
        build=lambda args: VerifyDocument(args.id, args.reviewed_by, _document_fields(args)), render=_render_json,
    )
    for command in (manual, verify):
        command.add_argument('--type', choices=sorted(DOCUMENT_TYPES), required=True)
        for key in ('issue-date', 'period-start', 'period-end', 'invoice-id', 'amount', 'currency', 'expectation'):
            command.add_argument('--' + key, required=command is verify and key in ('period-start', 'period-end'))

    status = sub.add_parser('status')
    report = sub.add_parser('report')
    for command in (status, report):
        command.add_argument('--month', required=True)
        command.add_argument('--format', choices=['text', 'json'], default='text')
    status.set_defaults(build=lambda args: Status(args.month), render=_render_status)
    report.add_argument('--output', type=Path, required=True)
    report.set_defaults(build=lambda args: ExportReport(args.month, args.output), render=_render_json)
    index = sub.add_parser('index').add_subparsers(dest='action', required=True)
    index.add_parser('rebuild').set_defaults(build=lambda args: RebuildIndex(), render=_render_json)
    return parser


def _global_config_first(values):
    for index, value in enumerate(values):
        if value == '--config' and index + 1 < len(values):
            return values[index:index + 2] + values[:index] + values[index + 2:]
        if value.startswith('--config='):
            return [value] + values[:index] + values[index + 1:]
    return values


def main(argv=None):
    values = _global_config_first(list(sys.argv[1:] if argv is None else argv))
    try:
        args = _parser().parse_args(values)
    except SystemExit as error:
        return int(error.code)
    try:
        result = dispatch(args.config, args.build(args))
        args.render(result.data, args)
        return result.exit_code
    except ValueError as error:
        print(safe_text(error), file=sys.stderr)
        return 2
    except AuthRequired as error:
        print(safe_text(error), file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error):
        print('operation_failed: check file access, writer lock, or storage health', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('operation_interrupted', file=sys.stderr)
        return 1


def entrypoint():
    raise SystemExit(main())
