# ATS endpoints

The public, keyless endpoints `ats_fetch.py` reads. Every one answered a live request on the date in its row (2026-09-27 unless noted); the counts are from that request. Sources change: when one returns nothing, test it with a query that must match before calling the result "no openings". An empty list is a signal to check, never a finding on its own.

The company identifier (the slug in the URL) goes in your `targets.csv` (company, ats, slug, site); a wrong slug returns 404 or an empty list.

| ATS | Read postings | Fields to use | Live check |
|---|---|---|---|
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/<slug>?includeCompensation=true` | `jobs[]`: `title`, `location`, `secondaryLocations`, `isRemote`, `workplaceType`, `descriptionPlain`, `jobUrl`, `compensation` | mistral.ai: 207 postings |
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true` | `jobs[]`: `title`, `location.name`, `content` (HTML), `absolute_url` | dataiku: 25 postings |
| Lever | `https://api.lever.co/v0/postings/<site>?mode=json` | list: `text`, `categories.location`, `categories.commitment`, `country`, `workplaceType`, `descriptionPlain`, `hostedUrl` | doctrine: 33 postings |
| Workable | `https://apply.workable.com/api/v1/widget/accounts/<account>?details=true` | `jobs[]`: `title`, `city`, `country`, `employment_type`, `description`, `application_url` | huggingface: 8 postings |
| Homerun | `https://feed.homerun.co/<company>` | Atom XML entries | gradium: feed returned |
| Teamtailor | `https://<company>.teamtailor.com/jobs.json` | JSON Feed `items[]`: `title`, `url`, `content_html`, `date_published`; the place is in `_jobposting.jobLocation[].address` (schema.org JobPosting) | papernest: 4 postings (2026-09-28) |
| Recruitee | `https://<company>.recruitee.com/api/offers` | `offers[]`: `title`, `location`, `remote`, `status`, `careers_url`, `description`, `requirements` | kayrros1: 1 posting, an open application (2026-09-28) |
| Workday | `POST https://<tenant>.<wdN>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs` with `{"appliedFacets":{},"limit":20,"offset":k,"searchText":"<words>"}`; one posting: `GET` that base + the row's `externalPath` | list rows: `title`, `externalPath`, `locationsText` (often "2 Locations"); detail `jobPostingInfo`: `title`, `jobDescription` (HTML), `location`, `additionalLocations`, `country.descriptor`, `remoteType`, `postedOn`, `jobReqId`, `externalUrl` | criteo "machine learning": 11 (2026-09-28); NVIDIA and Thales list 2,000 without a search (2026-09-29) |

`site` in the target file is `wdN/<site>` from the careers URL (`criteo.wd3.myworkdayjobs.com/Criteo_Career_Site` gives `wd3/Criteo_Career_Site`).

Cautions from the live samples:
- Lever's `workplaceType: remote` often means remote inside one country (every remote Spotify sample had `country: US`). Read `country` and the text too.
- Ashby writes the region into the location string ("Remote (EMEA)", "Remote (US)").
- Greenhouse location is free text per country ("Remote, Germany"): France, Europe or EMEA must be named.
- An ATS can return a closed posting with HTTP 200. "Open" means the posting is in the API list today.
- Workday is not a published feed: it is the careers page's own back end. `ats_fetch.py` reads the tenant's robots.txt first and skips a site it disallows (Criteo's `/Criteo_Career_Site/` and Thales's `/Careers/` on 2026-09-29; NVIDIA and Renault allow theirs). A skipped company is checked by hand or through LinkedIn alerts and Indeed.
- Workday sends `total` on the first page only; later pages say 0 (criteo, 2026-09-29). Page until a page comes back short or brings no row already unseen: Renault's board returns full pages past its last result (40 results, then repeats; 2026-09-29).
- Workday boards list thousands, so search role words (`WORKDAY_QUERIES` in the script) instead of reading the whole board.
- Recruitee lists closed offers too: keep `status: published`.

Not used: SmartRecruiters (`api.smartrecruiters.com/robots.txt` allows only LinkedInBot, and only `/v1/companies/`; checked 2026-09-29). SuccessFactors, iCIMS, JOIN, Taleez, Flatchr and Gestmax need a key or a partner account (not tested here).
