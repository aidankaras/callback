from core.checks.numbers import parse_numbers, values_match


def test_plain_integer() -> None:
    assert [n.value for n in parse_numbers("cut queue to 300 items")] == [300.0]


def test_thousands_separators_and_magnitude_suffixes_agree() -> None:
    a = parse_numbers("1,200,000 records")[0].value
    b = parse_numbers("1.2M records")[0].value
    c = parse_numbers("1.2 million records")[0].value
    assert a == b == c == 1_200_000.0


def test_percentages_are_flagged() -> None:
    parsed = parse_numbers("reduced review volume 40%")[0]
    assert parsed.value == 40.0
    assert parsed.is_percent


def test_decimal_is_not_split_by_sentence_period() -> None:
    assert [n.value for n in parse_numbers("Raised AUC to 0.83. Shipped it.")] == [0.83]


def test_years_are_parsed_as_numbers_not_magnitudes() -> None:
    assert [n.value for n in parse_numbers("from 2024 to 2025")] == [2024.0, 2025.0]


def test_currency_units_are_captured() -> None:
    parsed = parse_numbers("saved $12k annually")[0]
    assert parsed.value == 12_000.0
    assert parsed.unit == "USD"


def test_rounding_within_tolerance_matches() -> None:
    assert values_match(claimed=40.0, source=40.0)
    assert values_match(claimed=0.83, source=0.8312)
    assert not values_match(claimed=40.0, source=44.0)


def test_rounding_up_is_not_allowed() -> None:
    # 39.6 may be reported as 39.6 or 40 only if within tolerance; 39.6 -> 45 is not.
    assert not values_match(claimed=45.0, source=39.6)


def test_metres_with_space_are_not_read_as_millions() -> None:
    assert [n.value for n in parse_numbers("ran a 300 m leg")] == [300.0]


def test_mph_is_not_read_as_a_magnitude_suffix() -> None:
    # This also proves the unconsumed letter run is excluded from `raw`,
    # not just from the resolved value.
    parsed = parse_numbers("throughput 300mph")[0]
    assert parsed.value == 300.0
    assert parsed.raw == "300"


def test_mbps_is_not_read_as_millions() -> None:
    assert [n.value for n in parse_numbers("link at 1.2Mbps")] == [1.2]


def test_milliseconds_are_not_read_as_a_magnitude_suffix() -> None:
    assert [n.value for n in parse_numbers("cut latency 900ms")] == [900.0]


def test_mm_abbreviation_is_not_treated_as_millions() -> None:
    assert [n.value for n in parse_numbers("2mm tolerance")] == [2.0]


def test_capital_k_abbreviation_with_no_space_attaches() -> None:
    assert [n.value for n in parse_numbers("grew to 5K users")] == [5000.0]


def test_word_magnitude_with_space_attaches() -> None:
    assert [n.value for n in parse_numbers("handled 12 thousand requests")] == [12000.0]


def test_billion_word_attaches() -> None:
    assert [n.value for n in parse_numbers("3 billion rows")] == [3_000_000_000.0]


def test_spaced_single_letter_abbreviation_is_accepted_cost() -> None:
    # A single-letter magnitude abbreviation separated by whitespace from its
    # digits is deliberately not attached, by the same rule that keeps
    # "300 m" from being read as 300 million: a bare letter after whitespace
    # is not treated as a magnitude, even when the writer meant "M" for
    # "million". This trades a rarer miss (a spaced capital M) for avoiding
    # a more common false positive (a physical/rate unit directly following
    # the number, with no space).
    assert [n.value for n in parse_numbers("1.2 M records")] == [1.2]
