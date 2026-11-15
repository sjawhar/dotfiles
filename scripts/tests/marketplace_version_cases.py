"""Shared marketplace version-comparison cases."""

MISSING = object()

CASES = (
    ("0.3.1", "0.3.1", True),
    ("0.3.2", "0.3.1", True),
    ("0.3.0", "0.3.1", False),
    ("0.3.10", "0.3.9", True),
    ("0.3.9", "0.3.10", False),
    ("1.2.0", "1.2", True),
    ("1.2", "1.2.0", True),
    ("1.2", "1.2.1", False),
    ("0.3.09", "0.3.9", True),
    ("0.3.08", "0.3.9", False),
    ("10", "9", True),
    ("99999999999999999999.0", "9.0", True),
    ("partial", "2.0.0", False),
    ("2.0.0", "partial", False),
    ("partial", "partial", True),
    ("0.3.1-beta", "0.3.1", False),
    ("0.3.1", "0.3.1-beta", False),
    ("v1.0.0", "1.0.0", False),
    ("9" * 4301, "9" * 4301, True),
    ("0" * 4300 + "2", "1", True),
    ("1", "0" * 4300 + "2", False),
    ("1" + "0" * 4300, "9" * 4300, True),
    (1, "1", False),
    ("1", 1, False),
    (1, 1, True),
    (1, True, False),
    (True, 1, False),
    (True, True, True),
    (1, 1.0, False),
    (" 1", "1", False),
    ("1 ", "1", False),
    ("2\n", "1", False),
    ("1\n", "1\n", True),
    (1, "json:1", False),
    ("json:1", 1, False),
    ("json:1", "json:1", True),
    ("\u0663.0", "2.0", False),
    ("2", "\u0661", False),
    ("0.3.\uff11", "0.3.0", False),
    ([1], [1], True),
    ([1], "1", False),
    ({"a": 1}, {"a": 1}, True),
    ({"a": 1, "b": 2}, {"b": 2, "a": 1}, True),
    ({"a": {"x": 2, "y": 1}, "b": 2}, {"b": 2, "a": {"y": 1, "x": 2}}, True),
    ({"a": 1}, {"a": 2}, False),
)

# Follow compares a missing value as None. The installer reads null, missing, and empty strings as
# empty arguments, then rejects an empty argument before it compares values.
EMPTY_CASES = (
    (None, "1", False, False),
    (MISSING, "1", False, False),
    ("1", None, False, False),
    ("1", MISSING, False, False),
    (None, None, True, False),
    (MISSING, MISSING, True, False),
    (MISSING, None, True, False),
    ("", "1.0.0", False, False),
    ("", "", True, False),
    ("", None, False, False),
)

# These raw JSON rows exercise the installer's real reads. Number spellings such as 1.10 and 1.1
# can differ between jq and Python, so no shared row requires agreement on them.
RAW_CASES = (
    ('{"a":1,"b":2}', '{"a":1,"b":2}', True),
    ('{"a":1,"b":2}', '{"b":2,"a":1}', True),
    ('{"a":{"x":2,"y":1},"b":2}', '{"b":2,"a":{"y":1,"x":2}}', True),
    ("true", "true", True),
    ("1", '"1"', False),
    ("1", "true", False),
)
