"""
Cleans, normalizes, and persists Grad Cafe applicant data collected by
scrape.py.
"""


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


def save_data(data, path):
    """
    Save cleaned applicant data to disk.

    Args:
        data: The cleaned data to persist.
        path: The destination file path.
    """
    pass


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
