"""
Cleans, normalizes, and persists Grad Cafe applicant data collected by
scrape.py.
"""

import json


def clean_data(raw_data):
    """
    Clean and normalize a list of raw applicant entry records.

    Args:
        raw_data: A list of raw applicant entry records as produced by
            scrape.scrape_data().

    Returns:
        list: A list of cleaned applicant entry records.
    """
    pass


def _normalize_missing_keys(records):
    """
    Ensure every record in a list of dictionaries has the same set of
    keys, filling in any keys missing from a given record with None.

    Args:
        records: A list of dictionaries that may have inconsistent keys
            across entries.

    Returns:
        list: A new list of dictionaries, each containing the full set
            of keys observed across all records, with None used as the
            value for any key a given record was missing.
    """
    all_keys = {}
    for record in records:
        all_keys.update(dict.fromkeys(record.keys()))

    normalized = []
    for record in records:
        normalized_record = {key: record.get(key, None) for key in all_keys}
        normalized.append(normalized_record)
    return normalized


def save_data(data, path):
    """
    Save cleaned applicant data to disk as a JSON array.

    Scans all records to collect the full set of keys present anywhere
    in the dataset, fills any record missing a given key with None so
    every object in the output has identical keys, then writes the
    result to path as a single formatted JSON array (not JSON Lines).

    Args:
        data: The cleaned data to persist, as a list of dictionaries.
        path: The destination file path.
    """
    normalized_data = _normalize_missing_keys(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(normalized_data, f, indent=2)


def load_data(path):
    """
    Load previously saved applicant data from disk.

    Args:
        path: The file path to load data from.

    Returns:
        list: The loaded applicant entry records.
    """
    pass


def _normalize_status(status):
    """
    Normalize a raw applicant status string (e.g. admission decision)
    into a consistent canonical form.

    Args:
        status: The raw status string.

    Returns:
        str: The normalized status string.
    """
    pass
