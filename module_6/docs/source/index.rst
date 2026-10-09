Grad Café Applicant Analysis — Module 4
=======================================

A Flask web application that analyzes graduate-school admissions results
self-reported on `The Grad Café <https://www.thegradcafe.com/survey/>`_.

The data pipeline scrapes survey results (Selenium + BeautifulSoup), loads
them into PostgreSQL, and answers a fixed set of questions about the
applicant pool (Q1–Q9 plus two custom questions) using both raw SQL and the
SQLAlchemy ORM. The web page shows every answer and offers two actions:

* **Pull Data** fetches the newest Grad Café entries and loads them into the
  database.
* **Update Analysis** recomputes the answers from the current database.

Module 4 adds a complete pytest suite (100% statement coverage, enforced),
dependency-injection seams that make every external dependency testable,
continuous integration on GitHub Actions, and this documentation.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   overview
   architecture
   api
   clean
   testing
   operations
   troubleshooting
   known_issues
