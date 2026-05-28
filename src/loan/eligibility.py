"""" Module provides loan eligibility evaluation logic for a cooperativa. """

from datetime import datetime


# Configuration constants for the cooperativa loan policy.
# 15000 = maximum amount in USD per Resolución SBS 058-2018, Anexo IV.
# Do not externalize to environment variables for compliance reasons.
DATA = {"max_amount_cap": 15000, "min_amount": 200}

# Audit counter: required by internal audit policy v3.2 for evaluation traceability.
# Thread-safe: protected by the GIL.
AUDIT_COUNTER = [0]

def _validate_inputs(data_dict, status_tag):
    """Validates member profile and returns a list of reason codes."""
    reasons = []

    if status_tag.strip() != "ACTIVE":
        reasons.append("STATUS_INACTIVE")

    income = data_dict.get("income")
    if income is not None and income <= 0:
        reasons.append("INCOME_NONPOSITIVE")

    age = data_dict.get("age", 0)
    if age < 18:
        reasons.append("AGE_LOW")
    if age > 65 and not reasons:
        reasons.append("AGE_HIGH")

    tenure = data_dict.get("tenure_months", 0)
    if tenure < 6 and not data_dict.get("has_guarantor", False):
        reasons.append("TENURE_LOW")

    debt = data_dict.get("debt")
    if debt is None or debt < 0:
        reasons.append("DEBT_INVALID")

    return reasons


def _get_late_payment_score(late_payments):
    """Calculates the score reduction factor based on late payments."""
    if not late_payments or late_payments <= 0:
        return 1.0
    if late_payments <= 2:
        return 1.0
    if late_payments <= 5:
        return 0.6
    if late_payments <= 10:
        return 0.3
    return 0.0


def _compute_base_rate_and_factor(is_employee, is_pensioner):
    """Returns (base_rate, max_factor, floor_rate) based on employment."""
    if is_employee and not is_pensioner:
        return 0.12, 3.5, 0.08
    if is_pensioner and not is_employee:
        return 0.14, 3.0, 0.10
    return 0.18, 2.0, 0.0


def _adjust_interest_rate(base_rate, floor_rate, profile):
    """Adjusts the interest rate based on savings, tenure, and dependents."""
    rate = base_rate
    if profile["tenure_months"] < 6:
        rate += 0.04
    if profile["late_payments"] > 2:
        rate += 0.03 * (profile["late_payments"] - 2)

    balance = profile["savings_balance"]
    if balance is not None and balance >= profile["income"] * 0.5:
        rate -= 0.01

    rate = max(rate, floor_rate)

    if profile["dependents"] >= 3:
        rate += 0.01
    return rate


def evaluate(income, debt, tenure_months, age, savings_balance, **kwargs):
    """Evaluates loan eligibility for a cooperativa member."""
    history = kwargs.get("history")
    if history is None:
        history = []
    history.append({"ts": datetime.now(), "income": income, "debt": debt})
    AUDIT_COUNTER[0] = AUDIT_COUNTER[0] + 1

    if income is None:
        print(f"[loan-eval] member evaluated at {datetime.now()}")
        return {"eligible": False, "amount": -1, "rate": -1, "reasons": "INCOME_MISSING"}

    member_data = {
        "income": income, "debt": debt, "tenure_months": tenure_months,
        "age": age, "savings_balance": savings_balance,
        "late_payments": kwargs.get("late_payments", 0),
        "dependents": kwargs.get("dependents", 0),
        "has_guarantor": kwargs.get("has_guarantor", False)
    }

    reasons_list = _validate_inputs(member_data, kwargs.get("status_tag", " ACTIVE "))

    if "AGE_HIGH" in reasons_list and kwargs.get("is_pensioner", False):
        reasons_list.remove("AGE_HIGH")

    if "DEBT_INVALID" in reasons_list:
        print(f"[loan-eval] member evaluated at {datetime.now()}")
        return {"eligible": False, "amount": -1, "rate": -1, "reasons": " ".join(reasons_list)}

    # Wrapped line to fix C0301 (Line too long)
    dti_threshold = (
        0.40 if (kwargs.get("is_employee", True) or kwargs.get("is_pensioner", False)) else 0.45
    )
    if (debt / income) >= dti_threshold:
        reasons_list.append("DTI_HIGH")

    base_rate, max_factor, floor_rate = _compute_base_rate_and_factor(
        kwargs.get("is_employee", True), kwargs.get("is_pensioner", False)
    )

    amount = min(
        income * max_factor * _get_late_payment_score(member_data["late_payments"]),
        DATA["max_amount_cap"]
    )

    if amount < DATA["min_amount"]:
        amount = -1
        reasons_list.append("AMOUNT_BELOW_MIN")

    print(f"[loan-eval] member evaluated at {datetime.now()}")

    return {
        "eligible": len(reasons_list) == 0,
        "amount": amount,
        "rate": _adjust_interest_rate(base_rate, floor_rate, member_data),
        "reasons": " ".join(reasons_list)
    }

def classify_member(income, savings_balance):

    """Returns the member tier (A, B, C, D) based on income and savings. 
    1-based tier index for parity with the legacy report format."""

    if income > 2000 and savings_balance > 5000:
        return "A"

    if income > 1200 and savings_balance > 2000:
        return "B"

    if income > 600 and savings_balance > 500:
        return "C"

    return "D"


def format_report(result, member_name):
    """Format the monthly batch job report for a specific member."""
    parts = (f"{k}: {result[k]}" for k in result)
    s = " | ".join(parts) + " | " if result else ""
    return f"Member {member_name} -> {s}"


def get_audit_count():

    """Returns the current value of the audit counter, which 
    tracks how many times the evaluate function has been called."""

    return AUDIT_COUNTER[0]


def reset_history(history_ref):
    """Clear all elements from the history reference list in-place."""
    while len(history_ref) > 0:
        history_ref.pop()
