from dataclasses import asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import sqlite3
import uuid

from goco.models import RunSummary, CollectionOutcome, DocumentCandidate, SourceRef, parse_month, utcnow
from goco.models.integrations.mail.auth import AuthRequired, make_client
from goco.models.integrations.mail.gmail import TransportError
from goco.models.integrations.mail.mime import normalize_message
from goco.models.integrations.vendors.rules import match_rules
from goco.models.integrations.vendors import load_collectors
from goco.models.persistence.storage import atomic_json


class Runner:
    def __init__(self, config, clients, store):
        self.config = config
        self.clients = clients
        self.store = store

    def collect(self, request):
        window = parse_month(request.month, self.config.timezone)
        started = utcnow()
        # A scan cannot certify messages which have not arrived yet. Freeze the
        # received cutoff before fetching the first page, not at the end.
        elapsed_end = min(window.end_epoch, int(datetime.fromisoformat(started).timestamp()))
        zone = ZoneInfo(self.config.timezone)
        outcomes, coverage = [], []
        interrupted = False
        selected = [self.config.account(i) for i in request.account_ids]
        accounts = {a['id']: a for a in self.config.accounts}
        extract = load_collectors()['gmail']

        def source(mailbox_id, message_id):
            return SourceRef('gmail', f'{mailbox_id}:{message_id}',
                             mailbox_id=mailbox_id, message_id=message_id)

        def record(account, kind, reason, document=None, source_ref=None):
            outcome = CollectionOutcome(account, kind, reason, document)
            if source_ref is not None and not request.dry_run:
                outcome.review_id = str(uuid.uuid4())
                atomic_json(self.store.root / 'review' / (outcome.review_id + '.json'),
                            dict(schema_version=1, review_id=outcome.review_id,
                                 account_id=account, reason=reason, source=asdict(source_ref)))
            outcomes.append(asdict(outcome))

        def coverage_record(account, success):
            end = elapsed_end if success else window.end_epoch
            return dict(account_id=account['id'], mailbox_id=account['mailbox_id'],
                        start=f'{request.month}-01',
                        # Date windows only certify complete elapsed local days.
                        end=datetime.fromtimestamp(end, timezone.utc).astimezone(zone).date().isoformat(),
                        start_epoch=window.start_epoch, end_epoch=end,
                        success=bool(success and elapsed_end > window.start_epoch),
                        completed_at=utcnow())

        for account in selected:
            if account['collection'] == 'manual':
                record(account['id'], 'manual_required', 'manual_account')

        mailbox_ids = list(dict.fromkeys(a['mailbox_id'] for a in selected if a['collection'] == 'gmail'))
        for mailbox_id in mailbox_ids:
            group = [a for a in selected if a.get('mailbox_id') == mailbox_id and a['collection'] == 'gmail']
            mailbox_accounts = {a['id'] for a in self.config.accounts
                                if a.get('mailbox_id') == mailbox_id and a['collection'] == 'gmail'}
            all_rules = [r for r in self.config.rules if r['account_id'] in mailbox_accounts]
            usable = {a['id'] for a in group
                      if any(r['enabled'] and r['account_id'] == a['id'] for r in all_rules)}
            success = {a['id']: a['id'] in usable for a in group}
            for account in group:
                if account['id'] not in usable:
                    record(account['id'], 'manual_required', 'no_enabled_rules')
            if not usable:
                coverage.extend(coverage_record(a, False) for a in group)
                continue
            if elapsed_end <= window.start_epoch:
                for account_id in sorted(usable):
                    record(account_id, 'review_required', 'received_window_not_elapsed')
                coverage.extend(coverage_record(a, False) for a in group)
                continue
            try:
                client = self.clients.get(mailbox_id)
                if client is None:
                    client = make_client(self.config.mailbox(mailbox_id), request.dry_run)
                query = f'after:{window.start_epoch - 1} before:{elapsed_end}'
                token, seen_tokens, seen_messages = None, set(), set()
                while True:
                    page = client.list_messages(query, token)
                    for item in page.get('messages', []):
                        ident = item['id']
                        if ident in seen_messages:
                            continue
                        seen_messages.add(ident)
                        message_source = source(mailbox_id, ident)
                        try:
                            message = normalize_message(client.get_message(ident, metadata_only=request.dry_run))
                            # Use the server-returned identifier for actionable provenance.
                            message_source = source(mailbox_id, message.message_id)
                            if not window.start_epoch <= message.received_epoch < elapsed_end:
                                continue
                            decision = match_rules(message, all_rules)
                            if decision.reason == 'ambiguous_account':
                                for account_id in sorted(usable):
                                    record(account_id, 'review_required', 'ambiguous_account', source_ref=message_source)
                                    success[account_id] = False
                                continue
                            if not decision.rules:
                                continue
                            account_id = decision.rules[0]['account_id']
                            if account_id not in usable:
                                continue
                            if request.dry_run:
                                record(account_id, 'review_required', 'dry_run_potential_match')
                                continue
                            candidates = extract(message, decision, client.get_attachment, accounts,
                                                 mailbox_id, self.config.max_bytes)
                            if not candidates:
                                record(account_id, 'manual_required', 'no_supported_attachment', source_ref=message_source)
                                success[account_id] = False
                            for candidate in candidates:
                                try:
                                    outcomes.append(asdict(self.store.ingest(candidate)))
                                except ValueError:
                                    record(account_id, 'review_required', 'invalid_document', source_ref=candidate.source)
                                    success[account_id] = False
                        except (ValueError, KeyError, TypeError, RecursionError):
                            for account_id in sorted(usable):
                                record(account_id, 'review_required', 'invalid_message_or_attachment', source_ref=message_source)
                                success[account_id] = False
                    next_token = page.get('nextPageToken')
                    if not next_token:
                        break
                    if next_token in seen_tokens:
                        raise TransportError('gmail_repeated_page_token')
                    seen_tokens.add(next_token)
                    token = next_token
            except (AuthRequired, TransportError, OSError, sqlite3.Error) as exc:
                for account_id in sorted(usable):
                    record(account_id, 'auth_required' if isinstance(exc, AuthRequired) else 'failed',
                           'login_required' if isinstance(exc, AuthRequired) else 'collection_failed')
                    success[account_id] = False
            except KeyboardInterrupt:
                interrupted = True
                for account_id in sorted(usable):
                    record(account_id, 'failed', 'interrupted')
                    success[account_id] = False
            coverage.extend(coverage_record(a, success[a['id']]) for a in group)
            if interrupted:
                break

        if interrupted:
            processed = {c['account_id'] for c in coverage}
            for account in selected:
                if account['collection'] == 'gmail' and account['id'] not in processed:
                    record(account['id'], 'failed', 'interrupted')
                    coverage.append(coverage_record(account, False))
        from goco.models.integrations.vendors import aws

        for account in selected:
            if account['collection'] != 'aws':
                continue
            account_id = account['id']
            if interrupted:
                record(account_id, 'failed', 'interrupted')
                continue
            try:
                for item in aws.collect(account, request.month, request.dry_run, self.config.max_bytes):
                    if isinstance(item, DocumentCandidate):
                        try:
                            item = self.store.ingest(item)
                        except ValueError:
                            record(account_id, 'review_required', 'invalid_document', source_ref=item.source)
                            continue
                    outcomes.append(asdict(item))
            except aws.CollectionError as error:
                record(account_id, error.outcome, error.reason)
            except (KeyError, TypeError, ValueError):
                record(account_id, 'failed', 'aws_invalid_response')
            except (OSError, sqlite3.Error):
                record(account_id, 'failed', 'collection_failed')
            except KeyboardInterrupt:
                interrupted = True
                record(account_id, 'failed', 'interrupted')
        summary = RunSummary(str(uuid.uuid4()), started, utcnow(), outcomes, coverage, interrupted)
        if not request.dry_run:
            run_path = self.store.root / 'runs' / (summary.run_id + '.json')
            raw = asdict(summary)
            raw['counts'] = summary.counts
            atomic_json(run_path, raw)
            try:
                self.store.index.add_run(raw)
            except (sqlite3.Error, OSError):
                # Portable records remain authoritative when the local cache fails.
                for account in selected:
                    record(account['id'], 'failed', 'run_index_failed')
                for item in coverage:
                    item['success'] = False
                raw = asdict(summary)
                raw['counts'] = summary.counts
                atomic_json(run_path, raw)
        return summary
