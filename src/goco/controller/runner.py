from dataclasses import asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import re
import sqlite3
import uuid

from goco.models import RunSummary, CollectionOutcome, DocumentCandidate, SourceRef, parse_month, utcnow
from goco.models.integrations.mail.auth import AuthRequired, make_client
from goco.models.integrations.mail.gmail import TransportError
from goco.models.integrations.mail.mime import attachment_source, normalize_message
from goco.models.integrations.mail.search import build_candidate, decide_attachments, search_names, search_queries
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
        # Accounts with link-notice rules keep the rule path; every other Gmail account
        # is found by its product name and search aliases.
        ruled_accounts = {r['account_id'] for r in self.config.rules}
        for mailbox_id in mailbox_ids:
            group = [a for a in selected if a.get('mailbox_id') == mailbox_id and a['collection'] == 'gmail']
            mailbox_accounts = {a['id'] for a in self.config.accounts
                                if a.get('mailbox_id') == mailbox_id and a['collection'] == 'gmail'}
            all_rules = [r for r in self.config.rules if r['account_id'] in mailbox_accounts]
            # All name-search accounts of the mailbox take part in vendor matching, so an
            # ambiguous PDF is detected even when only one account is selected.
            name_accounts = [a for a in self.config.accounts
                             if a['id'] in mailbox_accounts and a['id'] not in ruled_accounts]
            usable = {a['id'] for a in group
                      if a['id'] not in ruled_accounts
                      or any(r['enabled'] and r['account_id'] == a['id'] for r in all_rules)}
            usable_names = {a['id'] for a in group if a['id'] in usable and a['id'] not in ruled_accounts}
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

            def collect_named(message, client):
                for item in decide_attachments(message, name_accounts):
                    targets = [account_id for account_id in item.account_ids if account_id in usable_names]
                    if not targets:
                        continue
                    attachment_ref = attachment_source(mailbox_id, message, item.part)
                    if item.reason == 'ambiguous_vendor':
                        for account_id in targets:
                            record(account_id, 'review_required', 'ambiguous_vendor', source_ref=attachment_ref)
                            success[account_id] = False
                        continue
                    account_id = targets[0]
                    if item.reason == 'document_type_unclear':
                        record(account_id, 'review_required', 'document_type_unclear', source_ref=attachment_ref)
                        success[account_id] = False
                        continue
                    if item.reason == 'no_invoice_feature':
                        record(account_id, 'skipped', 'no_invoice_feature')
                        continue
                    if request.dry_run:
                        record(account_id, 'review_required', 'dry_run_potential_' + item.document_type)
                        continue
                    try:
                        candidate = build_candidate(message, item, accounts[account_id], mailbox_id,
                                                    client.get_attachment, self.config.max_bytes)
                    except ValueError:
                        record(account_id, 'review_required', 'invalid_message_or_attachment', source_ref=attachment_ref)
                        success[account_id] = False
                        continue
                    try:
                        outcomes.append(asdict(self.store.ingest(candidate)))
                    except ValueError:
                        record(account_id, 'review_required', 'invalid_document', source_ref=candidate.source)
                        success[account_id] = False

            try:
                client = self.clients.get(mailbox_id)
                if client is None:
                    client = make_client(self.config.mailbox(mailbox_id), request.dry_run)
                queries = []
                if usable - usable_names:
                    query = f'after:{window.start_epoch - 1} before:{elapsed_end}'
                    senders = sorted({address.casefold() for rule in all_rules
                                      if rule['enabled'] and rule['account_id'] in usable
                                      for address in rule['sender_addresses']})
                    # Keep the local rule matcher authoritative. Skip the query optimization
                    # if a configured address could be interpreted as Gmail search syntax.
                    if senders and all(re.fullmatch(r'[a-z0-9_.+-]+@[a-z0-9.-]+', address) for address in senders):
                        query += ' {' + ' '.join(f'from:{address}' for address in senders) + '}'
                    queries.append(query)
                if usable_names:
                    names = [name for account in group if account['id'] in usable_names
                             for name in search_names(account)]
                    queries.extend(search_queries(window.start_epoch, elapsed_end, names))
                # Name search needs attachment names and message text even in a dry run;
                # attachment contents are still never downloaded or decoded then.
                metadata_only = request.dry_run and not usable_names
                seen_messages = set()
                for query in queries:
                    token, seen_tokens = None, set()
                    while True:
                        page = client.list_messages(query, token)
                        for item in page.get('messages', []):
                            ident = item['id']
                            # Several queries can return the same message; handle it once.
                            if ident in seen_messages:
                                continue
                            seen_messages.add(ident)
                            message_source = source(mailbox_id, ident)
                            try:
                                message = normalize_message(client.get_message(ident, metadata_only=metadata_only))
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
                                    if usable_names:
                                        collect_named(message, client)
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
                reason = 'login_required' if isinstance(exc, AuthRequired) else 'collection_failed'
                if isinstance(exc, TransportError) and str(exc) in {
                    'gmail_access_denied', 'gmail_daily_limit_exceeded', 'gmail_rate_limit_exceeded',
                    'gmail_retry_exhausted', 'gmail_response_too_large', 'gmail_network_failed',
                }:
                    reason = str(exc)
                for account_id in sorted(usable):
                    record(account_id, 'auth_required' if isinstance(exc, AuthRequired) else 'failed', reason)
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
        from goco.models.integrations.vendors import aws, azure, google_ads
        from goco.models.integrations.vendors.errors import CollectionError

        api_collectors = {'aws': aws.collect, 'azure': azure.collect, 'google_ads': google_ads.collect}

        for account in selected:
            collect_api = api_collectors.get(account['collection'])
            if collect_api is None:
                continue
            account_id = account['id']
            if interrupted:
                record(account_id, 'failed', 'interrupted')
                continue
            try:
                for item in collect_api(account, request.month, request.dry_run, self.config.max_bytes):
                    if isinstance(item, DocumentCandidate):
                        try:
                            item = self.store.ingest(item)
                        except ValueError:
                            record(account_id, 'review_required', 'invalid_document', source_ref=item.source)
                            continue
                    outcomes.append(asdict(item))
            except CollectionError as error:
                record(account_id, error.outcome, error.reason)
            except (KeyError, TypeError, ValueError):
                record(account_id, 'failed', f"{account['collection']}_invalid_response")
            except (OSError, sqlite3.Error):
                record(account_id, 'failed', 'collection_failed')
            except KeyboardInterrupt:
                interrupted = True
                record(account_id, 'failed', 'interrupted')
        summary = RunSummary(str(uuid.uuid4()), started, utcnow(), outcomes, coverage, interrupted,
                             account_ids=list(request.account_ids))
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
