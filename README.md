# job-posting-checker

Reads a job posting and tells you, with the exact sentence behind each answer, whether you can take the job from France: is French required, is the role remote and open to someone living in France, what contract it is, whether the salary clears your floor, and how many years of experience it really asks for. A second script pulls open postings straight from companies' public careers APIs.

Built for people job hunting in France who do not read French well. Postings here mix French and English ("Français courant", "French is a plus", "CDD de 18 mois", "3 500 € bruts mensuels"), and keyword search gets them wrong: "French company" is not a language rule, "apprentissage" is machine learning, "5+ years is a plus" is not an ask. The rules were written against real posting lines, and **445 labelled cases** in `tests/` pin them.

Python 3.10+ (tested on 3.12), standard library only. No API keys.

## Quickstart

```bash
python posting_check.py examples/posting.txt --title "Machine Learning Engineer - NLP" --salary-floor 45000 --years-cap 2
python ats_fetch.py examples/targets.csv --out leads.jsonl
```

The first prints a JSON report for one posting. The second reads every company in the CSV and writes one JSON line per AI/ML/data posting placed in France or open to it.

## Example

Input, [`examples/posting.txt`](examples/posting.txt) (an invented posting):

```text
Requirements
- 3+ years of experience building machine learning systems in production.
- Fluent English; French is a plus.
Nice to have
- 5+ years with search or ranking systems.
What we offer
- CDI (permanent contract), based in Paris.
- Hybrid: 2 days a week in our Paris office.
- Salaire : 48 000 - 58 000 € brut annuel selon profil.
- €1,000 yearly learning budget.
```

Output, [`examples/posting.json`](examples/posting.json):

```json
{
 "french": {"verdict": "preferred", "evidence": ["[preferred] French is a plus."], "english_signals": [], "posting_language": "en"},
 "remote": {"mode": "hybrid", "france_eligible": "n/a", "france_eligible_if_remote": "unclear", "evidence": [], "timezone": []},
 "contract": {"type": "cdi", "months": null, "from": "text", "evidence": ["CDI (permanent contract), based in Paris."]},
 "salary": {"min_eur_year": 48000, "max_eur_year": 58000, "monthly_in_text": false, "vs_floor": "above",
            "evidence": "Salaire : 48 000 - 58 000 € brut annuel selon profil."},
 "seniority": {"min_years": 3, "flags": [],
               "evidence": ["3+ years of experience building machine learning systems in production."], "blockers": []},
 "suggested": {"tier": "B", "why": ["3+ years asked (cap 2)"]}
}
```

Note what it did not do: the "5+ years" under *Nice to have* is not the ask, and the €1,000 learning budget is not the salary.

## What it reports

| Field | Values | Notes |
|---|---|---|
| `french.verdict` | `required` · `preferred` · `not_required` · `unclear` · `not_mentioned` | Judged per clause: "French mandatory, Spanish is a plus" is `required`. A posting that says both "not required" and "required" is `unclear`. |
| `remote.mode` | `remote` · `hybrid` · `onsite` · `unknown` | "hybrid architectures" and "hybrid search" are ML, not a work mode. |
| `remote.france_eligible` | `yes` · `no` · `unclear` · `n/a` | Can someone living in France take it? Reads location fields and sentences ("US only", "must reside in", "EMEA time zones", "±3 h from ET"). `n/a` = hybrid or on-site in France. A country it does not know falls to `unclear`, never `yes`. |
| `contract.type` | `cdi` · `cdd` · `internship` · `apprenticeship` · `phd` · `vie` · `freelance` · `unknown` | With the length in months when stated. |
| `salary.vs_floor` | `above` · `straddles` · `below` · `no_floor_given` · `not_stated` · `other_currency` · `daily_rate` | Yearly gross EUR; monthly pay is multiplied by 12. Bonuses, relocation, budgets and funding rounds are not salary. |
| `seniority` | `min_years`, `flags`, `blockers` | Years = the largest minimum outside nice-to-have sections. Blockers: nationality rule, security clearance, restricted research zone, another required language. |
| `suggested.tier` | `A` · `B` · `C` | `C` = a hard blocker (French required, not open to France, internship, nationality...). `B` = something to check. `A` = nothing found. Each tier comes with its reasons. |

Options for `posting_check.py`:

| Option | Default | Meaning |
|---|---|---|
| `--salary-floor N` | none | Your minimum, yearly gross EUR. Without it, the salary is reported but not compared. |
| `--years-cap N` | none | Flag (tier B) a posting that asks more than N years. Without it, years are reported but not flagged. |
| `--title`, `--location` | empty | The posting's title and location field, when you have them apart from the body. |
| `--remote-board` | off | The posting came from a remote-only board, so treat the mode as remote. |

A posting under 200 characters (a failed fetch, a JavaScript-only page) exits with code 2 and tier `unknown` instead of passing as A.

## Fetching postings from careers APIs

`ats_fetch.py` reads `targets.csv` (`company,ats,slug,site`) and asks each company's applicant-tracking system for its open postings: **Ashby, Greenhouse, Lever, Workable, Homerun, Teamtailor, Recruitee, Workday**. Endpoints and field names are in [`docs/endpoints.md`](docs/endpoints.md).

- By default it keeps AI/ML/data titles, drops internships, apprenticeships and PhD offers, and keeps a posting only when its place is in France or a remote scope that includes France. `--all` keeps everything; `--drop-senior` also drops senior, lead and manager titles.
- **Workday** is a careers page's own back end, not a published feed, so the script reads the site's `robots.txt` first (longest-match rule, RFC 9309) and skips a site that disallows it.
- **Rate limits**: one retry for a timeout, a dropped connection or a 5xx. A 429 is retried only when it asks for 10 seconds or less. With `--blocks blocks.json`, a long `Retry-After` is saved, and later runs skip that board until the wait ends instead of asking again. `--skip-ats workable` leaves a board alone.
- A target that fails (wrong slug, changed response shape) is named in the summary and makes the exit code 1. It is never skipped silently.

Live run of `examples/targets.csv` on 2026-10-09: Mistral AI 213 postings seen, Doctrine 38, Dataiku 19, Hugging Face 6, NVIDIA 34 from its search; 50 leads kept across the five.

Check a lead (pass its title and location too, or the place reads as unclear):

```bash
python -c "import json; j = json.loads(open('leads.jsonl', encoding='utf-8').readline()); open('one.txt', 'w', encoding='utf-8').write(j['text']); print(j['title'], '|', j['location'])"
python posting_check.py one.txt --title "<title printed above>" --location "<location printed above>"
```

## Tests

```bash
python tests/test_posting_check.py   # 445/445 cases pass
python tests/test_ats_fetch.py       # selftest ok
```

Both files also run under pytest (`python -m pytest -q`, 2 tests) when it is installed. The posting corpus holds real sentences from public postings (company careers pages, Indeed, remote boards), each with the label it should get; the fetcher's tests replay recorded response shapes and never touch the network. Add a line the rules get wrong to the corpus before you change a rule.

## Limits

- Regex rules, not a language model. They cover the phrasing seen so far in French and English (and Spanish years); a new phrasing can fall to `unclear` or, more rarely, get the wrong label. Read the evidence sentence before acting on a verdict.
- France-centred: eligibility is "can someone living in France take this", and the place filter keeps France and scopes that include it.
- The country list behind the remote rules is written by hand; a missing country gives `unclear`.
- Careers APIs change without notice. A slug that worked can start returning 404.

## License

MIT, see [LICENSE](LICENSE).
