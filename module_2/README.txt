Name
====


Module Info
===========


Approach
========


Robots.txt Compliance
========

Before scraping, the Grad Cafe robots.txt (https://www.thegradcafe.com/robots.txt)
was reviewed and a screenshot saved as robotscreenshot.pdf in this folder.

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

