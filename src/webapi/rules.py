"""Api for the rule editors (R19): the page's blocker editors (js/rules/editors.js) show errors and summaries while the
owner types, from a display-only copy of the rules; THIS is the check that counts. The pages that save blockers (Add / Edit,
Groups, extra limits for a member) run the same `rule_forms` code on the same forms inside their own Api methods, so a form
the page got wrong can never be stored.

Classification: `rules_validate` reads nothing but the trusted clock and changes nothing, so it is `read`. It does not
decide whether a change loosens a block - that is the Draft's job when the rules are stored (antibypass.rule_looser).
"""
import rule_forms

from . import core
from .core import ApiError

MAX_FORMS = 20


def _context(context) -> dict:
    """The optional second argument: {shared: bool, name: str, require_one: bool}."""
    if context is None:
        return {}
    if not isinstance(context, dict):
        raise ApiError("Bad request: the context must be an object.")
    for key, kind in (("shared", bool), ("name", str), ("require_one", bool)):
        if key in context and not isinstance(context[key], kind):
            raise ApiError(f"Bad request: {key} has the wrong kind of value.")
    return context


@core.read
def rules_validate(ctx, rules, context=None):
    """Check what the rule editors hold. `rules`: one form per ticked blocker (see rule_forms.py for their fields).
    `context`: {shared: true for a group's editors, name: what the sentence calls the item, require_one: refuse no
    blockers at all}. Answers {valid, rules, errors, summaries, sentence}:
      rules      the rule dicts the Draft stores, for the forms that are fine (display order);
      errors     [{rule_type, field, message}] for those that are not, one each - show message under `field`;
      summaries  {rule_type: the one-liner of the rail row};
      sentence   "<name> will be blocked: ..." when everything is fine, else "".
    A temporary rule comes back with its length (duration_min): its end is worked out when the Draft is saved."""
    if not isinstance(rules, list) or len(rules) > MAX_FORMS:
        raise ApiError("Bad request: rules must be a list of forms.")
    context = _context(context)
    found, errors = rule_forms.check_forms(rules, bool(context.get("shared")), ctx.now(), bool(context.get("require_one")))
    return {"valid": not errors, "rules": found, "errors": errors,
            "summaries": {r["rule_type"]: rule_forms.summary(r) for r in found},
            "sentence": "" if errors else rule_forms.sentence(context.get("name", ""), found)}
