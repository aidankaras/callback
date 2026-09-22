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


def test_grouped_thousands_with_a_decimal_tail_is_one_number() -> None:
    # Without a decimal tail on the grouped-thousands branch, this parsed as
    # two unrelated numbers: 1200.0 and 50.0.
    assert [n.value for n in parse_numbers("1,200.50 total")] == [1200.5]


def test_currency_symbol_separated_by_a_space_still_attaches() -> None:
    parsed = parse_numbers("$ 12 fee")[0]
    assert parsed.value == 12.0
    assert parsed.unit == "USD"


def test_currency_with_space_and_grouped_decimal_together() -> None:
    parsed = parse_numbers("budget of $ 1,500.75 approved")[0]
    assert parsed.value == 1500.75
    assert parsed.unit == "USD"


def test_percent_flag_is_not_set_when_the_percent_sign_was_not_consumed() -> None:
    # "ms" is not a magnitude, so the match ends after "300"; the "%" that
    # follows "ms" belongs to no captured number and must not flip the flag.
    parsed = parse_numbers("300ms% weird")[0]
    assert parsed.raw == "300"
    assert not parsed.is_percent


def test_digit_run_embedded_in_a_lowercase_identifier_is_not_a_number() -> None:
    # "p99" is a percentile name, not a claim about the number 99. A digit
    # run with a letter immediately before it is part of an identifier, not
    # a standalone quantity, at every position inside the run — not just the
    # first — so nothing is parsed, rather than a truncated "9".
    assert parse_numbers("p99 latency improved") == []


def test_digit_run_embedded_in_an_uppercase_identifier_is_not_a_number() -> None:
    # "H100" is a product name; "100" is not a separately claimed quantity.
    assert parse_numbers("ran it on an H100") == []


def test_digit_suffix_on_a_short_identifier_is_not_a_number() -> None:
    # "S3" and "GPT4" are technology names whose trailing digit is part of
    # the name, not a number claimed about anything.
    assert parse_numbers("used S3 and GPT4") == []


def test_dotted_version_identifier_is_not_a_number() -> None:
    # Every digit in "v1.2.3" is preceded by a letter or a ".", so the guard
    # rejects the whole run rather than parsing a fragment of it.
    assert parse_numbers("shipped v1.2.3") == []


def test_number_after_a_letter_prefixed_identifier_is_still_parsed() -> None:
    # The guard only blocks a digit run that is directly glued to a
    # preceding letter/digit/dot; a genuine number elsewhere in the same
    # sentence, separated by whitespace, is unaffected.
    assert [n.value for n in parse_numbers("on the H100, cut latency 40%")] == [40.0]


def test_the_tolerance_is_half_a_percent_either_side() -> None:
    """Pinned at the boundary: 0.4% off matches, 0.6% off does not, in both
    directions. Mutation: loosen the default tenfold -- this fails."""
    assert values_match(claimed=100.4, source=100.0)
    assert values_match(claimed=99.6, source=100.0)
    assert not values_match(claimed=100.6, source=100.0)
    assert not values_match(claimed=99.4, source=100.0)
