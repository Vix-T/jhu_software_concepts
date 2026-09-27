Module 2 cleaning step (upstream)
=================================

``module_2/clean.py`` is the Module 2 step that standardized the scraped
records with a locally hosted LLM, adding the ``llm-generated-program`` and
``llm-generated-university`` fields. Its output is the dataset bundled with
Module 4 (``module_4/data/llm_extend_applicant_data_full.json.gz``).

It is documented here for completeness only: it is **not** part of the
Module 4 application, is not imported by it, and is not covered by the
Module 4 test suite or its coverage requirement. Pull Data deliberately does
not run this step, so newly pulled rows have empty LLM fields (see
:doc:`known_issues`).

.. automodule:: clean
   :members:
