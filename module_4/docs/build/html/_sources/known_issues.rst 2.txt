Known issues and limitations
============================

* **Pulled rows have no LLM-standardized fields.** Pull Data doesn't run the
  Module 2 LLM cleaning step, so ``llm_generated_program`` and
  ``llm_generated_university`` are empty for newly pulled rows. Q9, which
  uses those fields, doesn't count them.
* **Entries skipped by the pull cap aren't revisited.** A pull stops at 300
  new entries. If more than 300 arrived since the last pull, the remainder
  sit on older pages, and the next pull stops at the first page with
  nothing new, before reaching them.
* **Pull Data can't run unattended.** It needs a Chrome session in which
  Grad Café's Cloudflare challenge was cleared by hand.
* **The analysis snapshot is per process.** Under a multi-process server,
  Update Analysis refreshes only the worker that handled it.
* **Unix only for the file lock.** ``FileLockBusyState`` uses ``fcntl`` and
  ``os.waitpid(..., WNOHANG)``, which don't exist on Windows.
* **Records without a URL can't be stored.** The URL is the table's natural
  key; one record in the bundled dataset has none and is skipped on every
  load.
* **The development database differs from a fresh load.** The database used
  during Module 3 has 60,030 rows (Q1 = 32,345); loading the bundled file
  gives 60,024 rows (Q1 = 32,344). The 6 extra rows were added separately,
  not from the bundled file.
* **The Georgetown pattern excludes only "Georgetown College".** Other
  unrelated institutions whose name contains the word "Georgetown" would
  still match Q8/Q9's Georgetown filter.
* **Module 3's README describes the older, literal Georgetown pattern.**
  Module 3 is a submitted deliverable and is left unchanged.
