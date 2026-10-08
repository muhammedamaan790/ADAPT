"""The agent's number grounding (agent/grounding.py): figures must come from tool results or the question."""

from adapt.agent.grounding import check, values_in


def known(*objs) -> list[float]:
    out: list[float] = []
    for o in objs:
        values_in(o, out)
    return out


def test_indian_and_western_formats_match_the_same_value():
    k = known({"net_revenue": 1248000.0})
    for text in ("₹12,48,000", "₹1,248,000", "₹12.48 L", "₹12.48 lakh", "Rs 1248000"):
        assert check(f"Revenue was {text}.", k).ok, text


def test_percent_fraction_and_multiples():
    k = known({"change": -0.062, "mer": 3.57})
    assert check("Revenue fell 6.2% and MER is 3.57x.", k).ok
    assert not check("Revenue fell 9.1%.", k).ok


def test_unmatched_figures_are_listed_once():
    g = check("Spend was ₹4,00,000, then ₹4,00,000 again.", known({"spend": 350000}))
    assert not g.ok and g.unverified == ["₹4,00,000"]


def test_counts_list_lengths_and_numbering():
    k = known({"decisions": [{"id": i} for i in range(17)]})
    assert check("There are 17 decisions.", k).ok                       # the list length
    assert check("1. Approve the first. 2. Review the second.", []).ok    # numbering / small counts
    assert not check("There are 40 decisions.", k).ok


def test_dates_and_times_restated_in_words():
    k = known({"at": "2026-10-07T09:30:00Z"})
    assert check("It ran on 7 October 2026 at 09:30.", k).ok


def test_strings_inside_results_and_the_users_question_count():
    k = known({"summary": "Cut ₹5.45 L/day across 11 budgets"}, "what if I add ₹20,000?")
    assert check("Cutting ₹5.45 L/day; adding ₹20,000 is a separate what-if.", k).ok


def test_rounding_tolerance_follows_the_displayed_precision():
    k = known({"value": 1987654.0})
    assert check("about ₹19.9 L", k).ok and check("₹20 L", k).ok
    assert not check("₹25 L", k).ok
