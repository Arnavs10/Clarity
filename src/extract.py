"""
Parameter extraction.

The model's job here is narrow and it is the job models are good at: read dense
legal prose and pull out structured facts. It is never asked whether a clause is
risky. That decision belongs to the rule engine, which can be printed, argued with,
and edited.

So the model returns {"deposit_months": 6, "forfeiture_at_sole_discretion": true}
and the rule decides 6 > 2. If the model is wrong you can see exactly which number
it got wrong, which is not true of a model that just says "this looks risky".

The field list is not hardcoded. It comes from RuleEngine.required_params, so adding
a rule automatically extends what gets extracted, and a field no rule reads is never
requested.
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rules import RuleEngine            # noqa: E402
from typeregistry import TypeRegistry   # noqa: E402
import llm
from deterministic import DETERMINISTIC_ONLY                              # noqa: E402

# What each parameter means, so the model is not guessing from the name alone.
# Anything absent from a clause must come back null rather than false, because
# "the clause is silent" and "the clause says no" are different facts.
FIELD_NOTES = {
    # This asked "does the restriction apply AFTER employment ends", and the model
    # answered that question correctly on a clause reading "during and after the
    # internship, keep confidential all non-public information". A confidentiality
    # duty does survive termination. It is not a restraint of trade, which is what
    # section 27 and this rule are about, and the rule's own note already says the
    # narrow exception is a covenant that only protects confidential information.
    # The field now asks about a restraint on WORKING and names the exclusions.
    "post_termination_restraint": "true only if, AFTER the job ends, the person is restrained from WORKING somewhere or for someone: joining a competitor, carrying on a similar business, or soliciting the employer's clients or staff. A duty to keep information confidential, to return property, or to assign intellectual property is NOT a restraint on working, however long it lasts. Omit the field if the clause only restricts confidentiality or IP",
    "restricted_from_competing_or_soliciting": "true only if the clause names the activity the person may not do after leaving, such as working for a competitor, running a competing business, or approaching the employer's customers or employees. Set false if the only surviving obligations are confidentiality, non-disparagement or IP assignment",
    "in_term_restraint_only": "true if the restriction applies only DURING employment",
    "bond_present": "true if the clause states a minimum service period the person must complete, however the consequence is phrased",
    "liquidated_damages_stated": "true if the clause names liquidated damages, or damages of a fixed or calculable amount, payable on early exit",
    "recovery_on_early_exit": "true if the clause lets the employer recover, claw back or demand any sum when the person leaves early. Set false if leaving early carries no monetary consequence",
    "training_cost_clawback": "true if training, relocation or onboarding costs become repayable on early exit",
    "assignment_scope_unlimited": "true if the IP assignment is not limited to work done for the employer, for example anything created during the whole term regardless of duties. Set false if it is scoped to work done in the course of the role",
    "covers_anything_related_to_business": "true if the IP assignment reaches anything related or useful to the employer's business rather than only the person's actual work",
    "penalty_amount_stated": "true if a monetary figure is named as payable on breach or early exit, including one called liquidated damages or a bond amount",
    "compels_continued_service": "true if it purports to force the person to keep working",
    "garden_leave_days": "paid non-working notice in days, integer",
    "assigns_ip": "true if inventions or work product vest in the employer",
    "covers_outside_work_hours": "true if it reaches work created outside working hours",
    "covers_off_premises": "true if it reaches work created off company premises",
    "prior_work_carveout": "true ONLY if the clause explicitly excludes work created before joining. Omit the field entirely if the clause says nothing about prior work",
    # Loan fields had no descriptions at all, while employment had forty-five. The
    # model was inferring every loan parameter from its name alone, which is why
    # loan findings swung between 2 and 5 of 6 across runs on the same fixture while
    # employment stayed steady.
    "penal_interest_added_to_rate": "true if the late payment penalty is charged as interest ADDED TO the rate of interest, rather than as a separate fee. Set false if the clause says the charge is separate from, or not added to, the rate",
    "penal_charges_capitalised": "true if penal charges or penal interest are compounded, capitalised, added to principal, or if interest is charged on the penalty itself. The words compounded, compounding, capitalised and monthly rests all indicate this. Set false if the clause says no interest is computed on the penal charge",
    "penal_charge_amount_undisclosed": "true if a penal charge is levied without stating its amount, or is left to the lender to decide, or the clause says the quantum need not be disclosed. Set false if an amount is named or a Key Fact Statement discloses it",
    "stipulated_penalty_amount": "true if the clause names a specific sum payable on default or breach",
    "penalty_disproportionate_to_loss": "true if the sum payable on default is far larger than the loss the default would cause, for example a flat penalty that dwarfs the instalment, or a percentage that compounds. Set false for a modest late fee proportionate to the instalment",
    "individual_penal_higher_than_corporate": "true if a higher penal charge applies to individual borrowers than to corporate or business borrowers for a comparable default",
    "acceleration_without_notice": "true if the whole outstanding becomes immediately due on default with no notice and no period to remedy. Set false if the clause gives written notice or a cure period before acceleration",
    "rate_reset_at_sole_discretion": "true if the lender may change the interest rate at its own discretion. Set false if the rate is tied to a published benchmark or requires notice to the borrower",
    "set_off_across_unrelated_accounts": "true if the lender may debit or set off against any other account the borrower holds, beyond the loan account itself. Set false if set-off is limited to the loan account or needs the borrower's consent",
    "set_off_without_notice":
        "true only if the clause lets the lender apply those balances without giving\n         the borrower notice first. A clause that requires notice is not this.",
    "prepayment_penalty": "true if repaying early attracts a penalty, foreclosure charge or fee. Set false if the clause says prepayment is allowed without penalty",
    "repossession_without_notice": "true if the lender may take possession of secured property with no prior notice and no chance to regularise. Set false if notice or an opportunity to cure is required",
    "insurance_mandatory_named_insurer": "true if the borrower must insure through the lender or an insurer the lender nominates. Set false if the borrower may choose any registered insurer",
    "assignment_without_notice": "true if the lender may transfer the loan to another party without notifying the borrower. Set false if notice is required",
    "guarantee_uncapped": "true if a guarantor's liability is unlimited in amount or time, or continues after the guaranteed loan is repaid",
    "jurisdiction_lender_city_only": "true if disputes may only be heard in the lender's own city or before an arbitrator the lender alone appoints. Set false if the venue is neutral or jointly chosen",
    "entry_without_notice": "true if the landlord may enter with no prior notice at all, or at any time. Set false if any notice period is stated",
    "entry_notice_hours": "the notice in hours the landlord must give before entering, as a number. Omit if the clause states no notice period",
    "deposit_forfeited_entirely_on_any_breach": "true if the whole deposit is forfeited on any breach regardless of how small, or on a breach described as minor or howsoever minor",
    "forfeiture_disproportionate_to_breach": "true if the sum forfeited bears no relation to the loss the breach would cause",
    "assigns_moral_rights": "true if moral or authorship rights are purportedly assigned or waived",
    "notice_days_employee": "notice the employee must give, in days",
    "notice_days_employer": "notice the employer must give, in days",
    "notice_asymmetric": "true only if both figures appear in this clause and differ",
    "cause_undefined_or_vague": "true if termination for cause uses open language like detrimental to best interests with no closed list",
    "clawback_on_resignation": "true if already-paid compensation is recovered on plain resignation",
    "perpetual": "true if an obligation runs with no end date",
    "excludes_public_information": "true if already-public information is carved out of confidentiality",
    "arbitrator_appointed_by_one_side": "true if one party alone appoints the arbitrator",
    "probation_extendable_without_limit": "true if probation may be extended with no stated ceiling",
    "deposit_months": "security deposit expressed in months of rent, number",
    "premises_type": "residential or non_residential",
    "forfeiture_at_sole_discretion": "true if the deposit may be withheld at the landlord's sole discretion",
    "lock_in_binds_tenant_only": "true if a lock-in binds the tenant but not the landlord",
    "early_exit_forfeits_full_deposit": "true if leaving early forfeits the whole deposit",
    "notice_days_tenant": "notice the tenant must give, in days",
    "notice_days_landlord": "notice the landlord must give, in days",
    "entry_causes_unspecified": "true if landlord entry is allowed with no stated causes",
    "escalation_percent": "rent escalation as a number of percent",
    "escalation_undefined": "true if rent may be revised with no stated formula or ceiling",
    "term_months": "tenancy or agreement duration in months, integer",
    "registered": "true if the document says it is registered",
    "structural_repairs_on_tenant": "true if structural repairs are the tenant's responsibility",
    "termination_without_cure_period": "true if termination needs no chance to fix the breach first",
    "auto_renews_on_landlord_terms": "true if it renews automatically on the landlord's terms",
    "jurisdiction_away_from_premises": "true if disputes are heard away from where the property is",
}


def build_prompt(doc_type, as_profile, as_fields, clause_text):
    as_cats = "\n".join(
        f"- {c['id']}: {c.get('desc','')}" for c in as_profile["taxonomy"])
    as_specs = "\n".join(
        f'  "{f}": {FIELD_NOTES.get(f, "as named")}' for f in as_fields)

    return f"""Extract structured facts from one clause of an Indian {doc_type} contract.

You are NOT deciding whether the clause is risky. A separate rule engine does that.
Report only what the clause actually says.

CATEGORIES (pick the single best fit):
{as_cats}

FIELDS to extract. Use null when the clause is silent about something. Do not infer
from what is typical, and do not guess. "Silent" and "says no" are different facts:
{as_specs}

CLAUSE:
\"\"\"{clause_text[:4000]}\"\"\"

Respond with ONLY a JSON object, no preamble and no markdown fences:
{{"category": "<one id>", "params": {{ ...only fields the clause actually speaks to... }}, "quote": "<the ~20 words from the clause most relevant to the facts above, verbatim>", "confidence": 0.0-1.0}}

Omit any field the clause says nothing about. An empty params object is a correct
answer for a definitions or signature clause."""


def parse(as_text):
    as_text = as_text.strip().replace("```json", "").replace("```", "").strip()
    m = re.search(r"\{.*\}", as_text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in response")
    return json.loads(m.group(0))


class Extractor:
    def __init__(self, model=None):
        self.engine = RuleEngine()
        self.registry = TypeRegistry()
        ok, why = llm.available()
        if not ok:
            raise RuntimeError(why)
        self.model = model or llm.model_name()

    def extract(self, doc_type, clause_text):
        # Fields the pattern reader owns are left out of the prompt entirely.
        # Asking for them wastes tokens against a rate limit and invites a guess
        # that would be discarded anyway, so the shorter prompt is also the
        # honest one: the model is only asked what it is actually deciding.
        # Document-scope facts are read by pattern from the whole document before
        # any clause is seen, so the model is not asked for them either. Leaving
        # them in the prompt would invite a guess about the rest of the agreement
        # from a model that has only ever been shown one clause of it, which is
        # precisely the failure docscan.py exists to remove.
        as_fields = [f for f in self.engine.required_params(doc_type)
                     if f not in DETERMINISTIC_ONLY and not f.startswith("doc__")]
        as_profile = self.registry.get(doc_type)
        as_prompt = build_prompt(doc_type, as_profile, as_fields, clause_text)

        out = llm.complete_json(as_prompt, max_tokens=2000)

        # Drop anything no rule reads, so a hallucinated field cannot reach the engine
        as_allowed = set(as_fields)
        out["params"] = {k: v for k, v in (out.get("params") or {}).items()
                         if k in as_allowed and v is not None}

        if out.get("category") not in set(self.registry.categories(doc_type)):
            out["category"] = "boilerplate"
            out["confidence"] = min(out.get("confidence", 1.0), 0.4)

        # A quote the clause does not contain means the model drifted
        as_quote = (out.get("quote") or "").strip()
        if as_quote and as_quote[:40].lower() not in clause_text.lower():
            out["quote_verified"] = False
            out["confidence"] = min(out.get("confidence", 1.0), 0.5)
        else:
            out["quote_verified"] = bool(as_quote)

        return out
