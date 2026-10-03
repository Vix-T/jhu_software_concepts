"""Sphinx configuration for the Module 4 documentation.

Paths are derived from this file's location, so the build works from any
working directory (locally and on Read the Docs).
"""

import os
import sys

DOCS_SOURCE = os.path.dirname(os.path.abspath(__file__))
MODULE_4 = os.path.abspath(os.path.join(DOCS_SOURCE, "..", ".."))
REPO_ROOT = os.path.dirname(MODULE_4)

# The application modules (flask_app, scrape, load_data, ...) import each other by
# bare name, exactly as they do at runtime with src/ on the path.
sys.path.insert(0, os.path.join(MODULE_4, "src"))
# module_2/ is appended (searched last) only for the upstream clean.py page;
# it also contains an older scrape.py that must not shadow src/scrape.py.
sys.path.append(os.path.join(REPO_ROOT, "module_2"))

project = "Grad Café Applicant Analysis (Module 4)"
author = "Vix Talbot"
copyright = "2026, Vix Talbot"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

autodoc_member_order = "bysource"
# Show defaults as written in the source (e.g. DATA_FILE), not their evaluated
# values, which would embed the build machine's absolute paths.
autodoc_preserve_defaults = True
napoleon_google_docstring = True
napoleon_numpy_docstring = False

html_theme = "sphinx_rtd_theme"
html_title = "Grad Café Applicant Analysis — Module 4"
