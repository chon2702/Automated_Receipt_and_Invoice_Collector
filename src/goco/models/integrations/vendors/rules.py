from dataclasses import dataclass


@dataclass(frozen=True)
class RuleDecision:
    rules: tuple[dict,...]
    reason: str


def match_rules(message,rules):
    matches=[]
    for rule in rules:
        if not rule['enabled']: continue
        if not message.senders or len(message.senders)!=1 or message.senders[0] not in {s.casefold() for s in rule['sender_addresses']}: continue
        if rule.get('recipient_addresses') and not set(message.recipients).intersection(a.casefold() for a in rule['recipient_addresses']): continue
        if rule.get('subject_contains','').casefold() not in message.subject.casefold(): continue
        matches.append(rule)
    if len({r['account_id'] for r in matches})>1: return RuleDecision((),'ambiguous_account')
    return RuleDecision(tuple(matches),'matched' if matches else 'no_matching_rule')
