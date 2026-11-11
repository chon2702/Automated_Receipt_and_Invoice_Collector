"""Command-line interface. JSON on stdout; safe diagnostics on stderr."""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo
from .config import ConfigError,load_config
from .models import DOCUMENT_TYPES,InvoiceMetadata,CollectRequest,parse_month
from .index import Index
from .storage import DocumentStore,read_runs
from .locking import WriterLock
from .importer import ImportMetadata,import_file
from .expectations import evaluate,select_expectations
from .reports import build_report,write_report
from .auth import AuthRequired
from .validation import metadata_valid


def safe_text(value):
    return ''.join(c if c.isprintable() else '?' for c in str(value))


def _parser():
    parser=argparse.ArgumentParser(prog='goco',description='Collect original invoices from Gmail or manual files.')
    parser.add_argument('--config',required=True,help='Path to company configuration JSON')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('config').add_subparsers(dest='action',required=True).add_parser('validate')
    sub.add_parser('list-services')
    auth=sub.add_parser('auth').add_subparsers(dest='action',required=True).add_parser('login')
    auth.add_argument('--mailbox',required=True)
    collect=sub.add_parser('collect')
    collect.add_argument('--month',required=True)
    selectors=collect.add_mutually_exclusive_group(required=True)
    selectors.add_argument('--all',action='store_true')
    for key in ('service','owner','account'): selectors.add_argument('--'+key)
    collect.add_argument('--dry-run',action='store_true')
    collect.add_argument('--format',choices=['text','json'],default='text')
    manual=sub.add_parser('import');manual.add_argument('file',type=Path);manual.add_argument('--account',required=True)
    verify=sub.add_parser('document').add_subparsers(dest='action',required=True).add_parser('verify')
    verify.add_argument('--id',required=True);verify.add_argument('--reviewed-by',required=True)
    for command in (manual,verify):
        command.add_argument('--type',choices=sorted(DOCUMENT_TYPES),required=True)
        for key in ('issue-date','period-start','period-end','invoice-id','amount','currency','expectation'):
            command.add_argument('--'+key,required=command is verify and key in ('period-start','period-end'))
    for name in ('status','report'):
        command=sub.add_parser(name);command.add_argument('--month',required=True)
        command.add_argument('--format',choices=['text','json'],default='text')
        if name=='report': command.add_argument('--output',type=Path,required=True)
    sub.add_parser('index').add_subparsers(dest='action',required=True).add_parser('rebuild')
    return parser


def _report(cfg,store,month):
    parse_month(month,cfg.timezone)
    runs=read_runs(cfg.storage_root);documents=store.documents()
    coverage=[item for run in runs for item in run['coverage']]
    results=evaluate(select_expectations(cfg.expectations,month),documents,datetime.now(ZoneInfo(cfg.timezone)).date(),coverage)
    report=build_report(month,results,runs,documents)
    report['accounts']=[dict(account_id=a['id'],status='unknown' if not any(r['account_id']==a['id'] for r in results) else 'configured_obligations') for a in cfg.accounts]
    report['document_counts']={'total':len(documents),'review_required':sum(not d.metadata.get('verified') or d.conflict for d in documents)}
    return report


def _metadata(args):
    values={k:getattr(args,k) for k in ('issue_date','period_start','period_end','invoice_id','amount','currency')}
    values.update(provenance='manual',verified=bool(args.period_start and args.period_end and args.type!='unknown'))
    metadata_valid(values)
    return InvoiceMetadata(**values)


def main(argv=None):
    parser=_parser()
    values=list(sys.argv[1:] if argv is None else argv)
    for i,arg in enumerate(values):
        if arg=='--config' and i+1<len(values):
            values=[arg,values[i+1]]+values[:i]+values[i+2:];break
        if arg.startswith('--config='):
            values=[arg]+values[:i]+values[i+1:];break
    try:
        try: args=parser.parse_args(values)
        except SystemExit as exc: return int(exc.code)
        cfg=load_config(args.config)
        index=Index(cfg.state_dir/'index.sqlite3');store=DocumentStore(cfg.storage_root,index,cfg.max_bytes)
        if args.command=='config': print('Configuration valid');return 0
        if args.command=='list-services':
            for a in cfg.accounts: print(safe_text(f"{a['id']} | {a['vendor_id']} | {a['owner_id']} | {a['collection']}"))
            return 0
        if args.command in ('status','report'):
            result=_report(cfg,store,args.month)
            if args.command=='report':
                output=args.output.resolve()
                if output.exists() or output.is_relative_to(cfg.state_dir) or output.is_relative_to(cfg.storage_root/'documents') or output.is_relative_to(cfg.storage_root/'runs'):
                    raise ValueError('report_output_must_be_new_and_outside_records')
                write_report(result,output)
                print(json.dumps({'schema_version':1,'report_written':True}));return 0
            if args.format=='json': print(json.dumps(result,ensure_ascii=True))
            else:
                print('Obligations: '+json.dumps(result['counts']))
                for item in result['obligations']: print(safe_text(f"{item['account_id']} | {item['expectation_id']} | {item['status']}"))
                for item in result['accounts']:
                    if item['status']=='unknown': print(safe_text(f"{item['account_id']} | unknown (no configured expectation for this month)"))
                print('Documents: '+json.dumps(result['document_counts']))
                for doc in result['documents']: print(safe_text(f"{doc['document_id']} | {doc['account_id']} | {doc['document_type']} | {doc['original_path']}"))
            return 0
        if args.command=='collect':
            parse_month(args.month,cfg.timezone)
            selected=[a['id'] for a in cfg.accounts if args.all or args.service==a['vendor_id'] or args.owner==a['owner_id'] or args.account==a['id']]
            if not selected: raise ValueError('selector_matched_no_accounts')
            request=CollectRequest(args.month,tuple(selected),args.dry_run)
            from .runner import Runner
            if args.dry_run: summary=Runner(cfg,{},store).collect(request)
            else:
                with WriterLock(cfg.state_dir): summary=Runner(cfg,{},store).collect(request)
            result=asdict(summary);result['counts']=summary.counts
            if args.format=='json': print(json.dumps(result,ensure_ascii=True))
            else:
                print('Collection: '+json.dumps(summary.counts))
                for item in summary.outcomes: print(safe_text(f"{item['account_id']} | {item['outcome']} | {item['reason']} | review={item.get('review_id') or '-'}"))
            return 1 if summary.interrupted or any(o['outcome'] in ('failed','auth_required') for o in summary.outcomes) else 0
        if args.command=='import':
            account=cfg.account(args.account);metadata=_metadata(args)
            expectation=None
            if args.expectation:
                expectation=next((e for e in cfg.expectations if e['id']==args.expectation),None)
                if expectation is None: raise ValueError('unknown_expectation')
            with WriterLock(cfg.state_dir): outcome=import_file(args.file,ImportMetadata(account,args.type,metadata,expectation),store)
            print(json.dumps(asdict(outcome)));return 0
        if args.command=='document':
            metadata=_metadata(args)
            update={k:v for k,v in asdict(metadata).items() if v is not None}
            update.update(document_type=args.type,reviewed_by=args.reviewed_by)
            expectation=None
            if args.expectation:
                expectation=next((e for e in cfg.expectations if e['id']==args.expectation),None)
                if expectation is None: raise ValueError('unknown_expectation')
            with WriterLock(cfg.state_dir): outcome=store.verify(args.id,update,expectation)
            print(json.dumps(asdict(outcome)));return 0
        if args.command=='index':
            with WriterLock(cfg.state_dir): result=index.rebuild(cfg.storage_root)
            print(json.dumps(result));return 0
        if args.command=='auth':
            mailbox=cfg.mailbox(args.mailbox)
            from .auth import login,CredentialStore
            with WriterLock(cfg.state_dir): login(mailbox,CredentialStore())
            print('Gmail consent saved in Windows credential storage');return 0
    except (ConfigError,ValueError) as exc:
        print(safe_text(exc),file=sys.stderr);return 2
    except AuthRequired as exc:
        print(safe_text(exc),file=sys.stderr);return 1
    except (OSError,sqlite3.Error):
        print('operation_failed: check file access, writer lock, or storage health',file=sys.stderr);return 1
    except KeyboardInterrupt:
        print('operation_interrupted',file=sys.stderr);return 1


def entrypoint(): raise SystemExit(main())
