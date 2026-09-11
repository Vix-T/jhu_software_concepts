"""
Cleans, normalizes, and persists Grad Cafe applicant data collected by
scrape.py.
"""

import json
import os
import subprocess
import tempfile


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


def _get_worker_count():
    """
    Determine how many parallel LLM worker processes to use.

    Targets roughly one physical core per LLM worker, since each worker
    runs its own LLM inference subprocess and physical cores (rather than
    logical/hyperthreaded ones) are what actually matter for CPU-bound
    inference throughput. os.cpu_count() reports logical cores, so it's
    halved here as an approximation of physical core count. The result
    is capped at 2 workers per the assignment's guidance, and floored at
    1 so this always returns a usable worker count even on a machine
    that reports very few cores.

    Returns:
        int: The number of worker processes/chunks to use.
    """
    return max(1, min(2, os.cpu_count() // 2))


def clean_with_llm_parallel(input_path, output_path, llm_script_path="llm_hosting/app.py"):
    """
    Clean applicant data by running it through the project's LLM-hosting
    script in parallel worker subprocesses, then merge and save the
    result.

    Parallelization strategy:
        The full input record set is split into N roughly-equal chunks,
        where N = _get_worker_count() (one physical-core-sized worker per
        chunk, capped at 2). Each chunk is written to its own temporary
        JSON file and handed to a separate `python <llm_script_path>
        --file <chunk>` subprocess, matching the assignment's documented
        invocation pattern of redirecting each worker's stdout to its own
        output file. All subprocesses are launched before any are waited
        on, so they run concurrently; this function then blocks until
        every worker has finished, reads each worker's JSONL stdout
        output, merges all chunks' results back into a single list in
        one combined pass, and writes that combined list to output_path
        via save_data() (so the same missing-key normalization and
        single-JSON-array format already used elsewhere is applied here
        too). All temporary chunk/output files are removed once merging
        is complete.

    Args:
        input_path: Path to a JSON file containing a single JSON array
            of records (as written by save_data()).
        output_path: Path to write the cleaned, merged JSON array to.
        llm_script_path: Path to the LLM-hosting script to invoke for
            each chunk.
    """
    with open(input_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    worker_count = _get_worker_count()

    chunk_size = len(records) // worker_count
    chunks = []
    start = 0
    for i in range(worker_count):
        if i == worker_count - 1:
            end = len(records)
        else:
            end = start + chunk_size
        chunks.append(records[start:end])
        start = end

    processes = []
    for chunk in chunks:
        input_temp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        chunk_with_program = [
            {
                **record,
                "program": (
                    f"{record.get('Program Name') or ''}, "
                    f"{record.get('University') or ''}"
                ).strip(", "),
            }
            for record in chunk
        ]
        json.dump(chunk_with_program, input_temp)
        input_temp.close()

        output_temp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        output_temp.close()

        output_handle = open(output_temp.name, "w", encoding="utf-8")
        process = subprocess.Popen(
            ["python", llm_script_path, "--file", input_temp.name, "--stdout"],
            stdout=output_handle,
        )
        output_handle.close()
        processes.append((process, input_temp.name, output_temp.name))

    for process, _, _ in processes:
        process.wait()

    failures = [
        (i, process.returncode)
        for i, (process, _, _) in enumerate(processes)
        if process.returncode != 0
    ]
    if failures:
        failure_details = ", ".join(
            f"chunk {i + 1} of {len(processes)} (exit code {returncode})"
            for i, returncode in failures
        )
        raise RuntimeError(f"LLM worker(s) failed: {failure_details}")

    merged_results = []
    for _, input_path_temp, output_path_temp in processes:
        with open(output_path_temp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    merged_results.append(json.loads(line))

    save_data(merged_results, output_path)

    for _, input_path_temp, output_path_temp in processes:
        os.remove(input_path_temp)
        os.remove(output_path_temp)

    print(f"Workers used: {worker_count}")
    print(f"Records processed: {len(merged_results)}")
    print(f"Output written to: {output_path}")
