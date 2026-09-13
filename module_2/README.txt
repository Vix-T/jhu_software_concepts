Name
========
Vix Talbot (JHED: vtalbot1)

Module Info
========

Module 2 - Web Scraping Assignment (Grad Cafe Applicant Data)

Approach
========

Data collection (scrape.py): Grad Cafe is protected by Cloudflare bot
detection that blocks urllib3, MechanicalSoup, and default Selenium
sessions alike (confirmed via direct testing of each, and via the
"Cf-Mitigated: challenge" response header). Per the instructor's
posted guidance, the working approach is a hybrid manual-verify +
attach pattern. A real Chrome browser is launched with remote
debugging enabled, a human completes Cloudflare's verification once,
and Selenium then attaches to that already-verified session
(chrome_options.debugger_address) rather than launching its own
automated browser. This session never attempts to solve or bypass
the challenge itself.

capture_pages() drives this session page-by-page via the site's own
"Next" pagination link, saving each page's raw HTML to disk
(_captured_pages/) before any parsing happens. This separation means
a parsing bug fix never requires re-scraping. parse_captured_pages()
independently reads those saved files, extracts entries via
BeautifulSoup + regex (_group_entry_rows, _parse_entry), and filters
out ad-placement rows. Resumability is handled via a state file
(next_url, pages_captured) written after every single page.

The default "Next" chain only surfaced ~2,440 recent entries before
running out. Discovering the site's own date-filter UI
(added_end=<date>, sort=newest) unlocked much deeper historical
pagination, and scrape_data() now orchestrates capture in
crash-resilient batches across successive date-bounded chunks,
reaching 60,165 total entries.

Data cleaning (clean.py, llm_hosting/): save_data() normalizes
missing keys and writes a single valid JSON array. LLM
standardization uses the provided TinyLlama pipeline (app.py,
unmodified). Because full-dataset LLM inference measured at
~15 sec/record was infeasible on available hardware, clean_with_llm_parallel_deduped() was
added: it sends only unique (Program Name, University) pairs to the
LLM (a 2-3x reduction), then broadcasts each result back to every
matching record, so every record still receives real LLM-derived
output. Given the timing constraint, cleaning was run against the
first 31,000 entries (chronologically) rather than the full 60,165,
split across two machines running in parallel, each independently verified against small test
batches before committing to full runs.

Browser/driver: Chrome + Selenium's remote-debugging attach method
(not a standard ChromeDriver-launched session).

Note on urllib3: the assignment's original guidance recommended using
urllib3 to construct and manage Grad Cafe URLs directly; once the
Cloudflare workaround required following the site's own rendered
"Next" pagination links instead of independently constructing URLs,
this became unnecessary — urllib3 remains listed in requirements.txt
as a transitive Selenium dependency but is not directly imported in
scrape.py or clean.py.


Robots.txt Compliance
========

Before scraping, the Grad Cafe robots.txt (https://www.thegradcafe.com/robots.txt)
was reviewed and a screenshot saved as screenshot.jpg in this folder.

The generic User-agent: * rule specifies "Allow: /" with the following exception
paths disallowed: /signin, /register, /forgot-password, /reset-password,
/confirm-password, /verify-email, and /profile. These are all account/authentication
routes unrelated to the publicly accessible admissions results pages used in this
assignment. No results/survey pages are disallowed under the generic rule.

Several named bots (e.g., GPTBot, ClaudeBot, Google-Extended, Amazonbot, CCBot,
Bytespider, meta-externalagent) are individually disallowed from the entire site.
This scraper does not identify itself as any of these bots; it uses a distinct,
honest User-Agent string identifying it as a student project scraper, which falls
under the generic User-agent: * rule permitting access.

The robots.txt also specifies a Content-Signal of "search=yes, ai-train=no,
use=reference." This scraper only uses a local LLM for post-hoc data cleaning/
standardization of already-scraped fields (inference on existing text), not for
training or fine-tuning any model on Grad Cafe content, which is consistent with
the "use=reference" permission and respects the "ai-train=no" restriction.

Known Bugs
==========

1. Two capture pages (out of 3,010) rendered blank during Chrome
   navigation and were excluded rather than reprocessed.

   Update: Two pages captured blank during scraping (page_00075,
   page_00123, now in the archived data) likely stemmed from grabbing
   page_source before the results table had fully rendered.
   capture_pages() was subsequently updated to add an explicit
   WebDriverWait for the results table's presence after each page
   load, with a timeout that logs and continues rather than crashing
   — this may prevent a recurrence, though the original two instances
   were not re-captured.

2. A ~100-result-ID gap exists at the seam between two
   date-filtered capture chunks (same calendar date, different
   underlying cursor sequences at the boundary). Documented, not
   backfilled, given volume already well past the 30k minimum.

3. clean_with_llm_parallel_deduped() had a batch-merge bug that
   duplicated 140 records (139 in one contiguous block, 1 isolated)
   across two of the four parallel cleaning runs. Caught via a
   post-merge URL-uniqueness check, fixed with a dedup-by-URL pass
   before final save. Root cause (a result-map reconstruction issue
   across subprocess batches) not fully isolated; if revisited,
   would add explicit per-chunk record-count assertions inside
   clean_with_llm_parallel() to catch this at the source rather
   than downstream.

4. 3 of 31,000 records received "Unknown" from the LLM for
   program/university (source text was unusually terse or
   non-English); left as-is per the model's own designed fallback
   behavior.

5. Only the first 31,000 of 60,165 scraped entries were run through
   LLM cleaning, due to measured hardware throughput
   (~15 sec/record) making full-dataset cleaning infeasible in the
   time available. applicant_data.json contains all 60,165 raw
   entries; llm_extend_applicant_data.json contains the 30,860
   cleaned entries (post-dedup) from that first slice.


