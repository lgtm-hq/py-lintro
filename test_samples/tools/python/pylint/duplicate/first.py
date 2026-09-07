"""First half of the pylint duplicate-code sample.

Deliberately shares a 15-line block with its sibling module so pylint's
``duplicate-code`` (R0801) checker reports exactly one clone set.
"""


def score_records(records):
    """Score the given records.

    Args:
        records: Values to score.

    Returns:
        Sorted list of scores.
    """
    totals = []
    for index, entry in enumerate(records):
        if entry is None:
            continue
        weight = index + 1
        if isinstance(entry, int):
            totals.append(entry * weight)
        elif isinstance(entry, str):
            totals.append(len(entry) * weight)
        elif isinstance(entry, (list, tuple)):
            totals.append(sum(1 for _ in entry) * weight)
        else:
            totals.append(weight)
    totals.sort()
    return totals
