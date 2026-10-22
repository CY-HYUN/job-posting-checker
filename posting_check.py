"""Read one job posting and report what could stop a candidate living in France from taking it, each finding with its sentence.

    python posting_check.py posting.txt                       # JSON report
    python posting_check.py posting.txt --salary-floor 45000  # compare the salary with your floor (yearly gross EUR)
    python posting_check.py posting.txt --years-cap 2         # flag postings that ask more than 2 years
    python posting_check.py posting.txt --remote-board        # the posting came from a remote-only board
    python posting_check.py --selftest                        # prove the rules on real lines

Checks: French requirement, remote eligibility for someone living in France, contract type,
salary, required years and hard blockers (nationality, security clearance). The script decides
nothing about fit; it reports evidence and a suggested tier, and you decide.

The phrase rules were written against real posting lines (company careers pages, remote boards and
live ATS boards, 2026-09 and 2026-10, and the lines adversarial reviews broke them with). The
labelled corpus is tests/test_posting_check.py; add every line the rules get wrong there before
changing a rule. The salary is compared only with a floor you pass (--salary-floor); without one
the report says "no_floor_given". The years cap works the same way (--years-cap).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

I = re.I

# ---------------------------------------------------------------- sentences

def sentences(text: str) -> list[str]:
    """Split a posting into short units: lines, bullets, then sentence ends before a capital or a digit."""
    units = []
    for line in re.split(r"[\r\n]+|\s[•·▪●]\s|^\s*[-*•]\s", text, flags=re.M):
        for s in re.split(r"(?<=[.!?;])\s+(?=[A-ZÀ-Ý0-9(\"'])", line):
            s = s.strip(" \t-*•·")
            if len(s) >= 3:
                units.append(s)
    return units


def clauses(sentence: str) -> list[str]:
    """A sentence cut where one requirement ends and another begins: "French required, a PhD is not"."""
    # "a plus but not required" stays one clause: the elliptical "not required" is about the same thing
    # not at ":" either: "Langue de travail : français" is one requirement
    return [c.strip() for c in re.split(r"[,;]|\s[-–]\s|\bbut\b(?!\s+(?:not|never)\b)|\bmais\b(?!\s+(?:pas|non)\b)"
                                        r"|\bwhile\b|\bwhereas\b|\bet\s+(?=l[ae']|un|une)", sentence, flags=I)
            if c and c.strip()]


def _any(patterns, s):
    return any(re.search(p, s, I) for p in patterns)


# ---------------------------------------------------------------- French

FRENCH_WORD = r"\b(french|fran[cç]ais(e|es)?|francophone|langue fran[cç]aise)\b"
FRENCH_CERT = r"\b(DELF|DALF|TCF|TEF)\b"            # French-language certificates name the language themselves
LANGUAGE_CUE = (r"speak|spoken|written|writ|oral|verbal|fluen|proficien|command|level|niveau|ma[iî]tris|courant"
                r"|native|langu|communicat|bilingu|read|parl|[ée]crit|\b[ABC][12]\b|anglais|english|working|maternelle"
                r"|know|connaissance|skill|comp[ée]tence|francophone|\b(in|en) (french|fran[cç]ais)\b|clients? fran[cç]ais"
                r"|\bfrench (meetings?|presentations?|documentation|writing|client|speaker)"
                # the language as the subject: "French is a plus", "Français requis", "French: B2", "French (reading)"
                r"|\b(french|fran[cç]ais)\s*(is\b|isn'?t\b|est\b|n'est\b|required|mandatory|preferred|needed|essential|compulsory|would"
                r"|requis|obligatoire|exig|indispensable|souhait|appr[ée]ci|:|\(|/)")
# "French degree", "Université française", "Formation en France": a French qualification, not a language
# requirement unless the sentence also names the language skill.
QUALIFICATION = r"dipl[ôo]m|\bdegree\b|universit|[ée]cole|\bschool\b|formation|bac\s?\+|nationalit|citizen"
LANGUAGE_SKILL = r"speak|parl|fluen|proficien|langu|courant|native|maternelle|bilingu|\b[BC][12]\b|DELF|DALF|TCF|TEF|ma[iî]tris"
# Lessons the employer offers are a benefit, not a requirement: "Language lessons in English and French".
LESSONS_OFFERED = r"\b(lessons?|classes?|cours|courses?)\b"
# "French" as an adjective about something other than the language: "the French AI regulatory landscape".
FRENCH_ADJECTIVE = (r"\bfrench\s+(?:ai|regulatory|regulation|market|law|legal|tech|company|companies|government|state"
                    r"|startup|ecosystem|customers?|banks?|insurers?|landscape|public|healthcare|industry|industrial"
                    r"|territory|subsidiary|entity|offices?|teams?|labou?r|tax|riviera|alps|state-owned)\b")
NOT_REQUIRED = [
    r"\bno french\b", r"\bwithout (speaking )?french\b", r"\bnon[- ]?francophone",
    r"\b(is ?n[o']t|not|isn't|aren't|never)\b[^.]{0,15}\b(required|mandatory|necessary|needed|a requirement|a must)\b",
    r"\bnot\s+a\s+must\b", r"\boptional\b",
    # "La maîtrise parfaite du français n’est pas indispensable à votre arrivée" (Aneo, 2026-10-14)
    r"\b(pas|non)\b[^.]{0,20}\b(requise?s?|obligatoires?|n[ée]cessaires?|exig[ée]e?s?|indispensables?)\b",
    r"\bn'est pas\b[^.]{0,20}\b(requise?s?|obligatoires?|n[ée]cessaires?|exig[ée]e?s?)\b",
    r"\bno need (to speak|for)\b", r"\bdon'?t (need to )?speak french", r"pas besoin",
    r"\bpas une obligation\b", r"\bun confort\b",   # "Le français est un confort, pas une obligation" (2026-10-14)
]
EITHER = [r"(french|fran[cç]ais)\s+(or|ou)\s+(english|anglais)", r"(english|anglais)\s+(or|ou)\s+(french|fran[cç]ais)"]
OTHER_LANGUAGE = (r"\b(anglais|english|espagnol|spanish|allemand|german|italien|italian|arabe|arabic|chinois|chinese"
                  r"|n[ée]erlandais|dutch|portugais|portuguese)\b")
PREFERRED = [
    r"\b(a|is a|would be a|big|real|strong|significant|great)\s+(plus|advantage|asset|bonus)\b", r"nice[- ]to[- ]have",
    r"\badvantageous\b", r"\bare a plus\b", r"\best un (?:vrai |gros )?(?:plus|atout)\b",
    r"would be nice", r"\bpreferred\b", r"\bappreciated\b", r"\bideally\b", r"\bwelcome\b", r"\bun (vrai |gros )?(plus|atout)\b",
    r"serait un plus", r"appr[ée]ci[ée]e?s?\b", r"souhait[ée]e?s?\b", r"\batout\b", r"\bis a plus\b",
]
REQUIRED = [
    r"\brequired\b", r"\bmandatory\b", r"\bmust\b", r"\bessential\b", r"\bfluen(t|cy)\b", r"\bproficien(t|cy)\b",
    r"\bnative\b", r"\bcommand of\b", r"\bcourant\b", r"ma[iî]tris", r"\bbon niveau\b", r"\bexcellent\b",
    r"\bobligatoires?\b", r"\bexig", r"\bindispensable\b", r"\brequise?s?\b", r"\b[BC][12]\b", r"\bbilingu",
    r"\bskills?\b", r"\bcomp[ée]tences?\b",       # "Great communication skills (in both French and English)"
    r"\bparfait", r"\bcompulsory\b", r"\badvanced\b", r"\blevel\b", r"\bniveau\b", r"maternelle", r"francophone",
    r"\b(you|must|should|to|able to)\s+speak", r"\bvous parlez\b", r"\bparler\b", r"\bspeaker\b",
    r"working language", r"langue de travail", FRENCH_CERT,
    # the work itself happens in French: meetings, documents, clients, the team
    r"\b(in|en) (french|fran[cç]ais)\b", r"clients? fran[cç]ais", r"\bfrench[- ]speaking\b",
    r"\bfrench (meetings?|presentations?|documentation|writing|client)",
]
ENGLISH_ENV = [
    r"english[- ]speaking (team|environment|company)", r"working language (is|:)\s*english", r"\bin english\b",
    r"international (team|environment)", r"[ée]quipe internationale", r"no french required", r"french isn'?t required",
]
FR_STOP = {"le", "la", "les", "des", "et", "vous", "nous", "pour", "avec", "dans", "une", "du", "sur", "est",
           "sont", "notre", "votre", "aux", "que", "qui", "au", "de", "en"}
EN_STOP = {"the", "and", "you", "we", "for", "with", "in", "an", "to", "of", "our", "your", "is", "are",
           "on", "that", "who", "will", "be"}


def _label_clause(c: str) -> str:
    if _any(NOT_REQUIRED, c):
        return "not_required"
    if _any(EITHER, c):
        return "not_required"
    if (re.search(r"\b(?:support|work|serve|communicat|interact|help|assist|deal)\w*\b[^.]{0,40}\b(?:in|en) (?:english|anglais)\b", c, I)
            and not re.search(r"\b(in|en) (french|fran[cç]ais)\b", c, I)
            and not re.search(r"\b(?:english|anglais)\s+(?:and|et|&)\s+(?:french|fran[cç]ais)\b", c, I)):
        return "not_required"                     # "You will support our French clients in English."
    if _any(PREFERRED, c):
        return "preferred"
    if re.search(r"french[- ]speaking", c, I) and re.search(r"english[- ]speaking", c, I):
        return "unclear"                          # a description of a mixed team, not a requirement
    if _any(REQUIRED, c):
        return "required"
    return "unclear"


def french_sentence(s: str) -> str | None:
    """Label one sentence, or None when it says nothing about the French language."""
    if not re.search(FRENCH_WORD, s, I) and not re.search(FRENCH_CERT, s):
        return None
    if re.search(QUALIFICATION, s, I) and not re.search(LANGUAGE_SKILL, s, I):
        return None                                    # a French degree or training, not the language
    if re.search(LESSONS_OFFERED, s, I):
        return None                                    # language lessons offered: a benefit
    if not re.search(LANGUAGE_CUE, s, I) and not re.search(FRENCH_CERT, s):
        return None                                    # "French company", "French market"
    if not re.search(FRENCH_CERT, s) and not re.search(FRENCH_WORD, re.sub(FRENCH_ADJECTIVE, " ", s, flags=I), I):
        return None                                    # every "French" is an adjective: "the French AI regulatory landscape"
    # Judge only the clause that names French: "French mandatory, Spanish is a plus" is a requirement.
    parts = clauses(s)
    idx = [i for i, c in enumerate(parts) if re.search(FRENCH_WORD, c, I) or re.search(FRENCH_CERT, c)] or [0]
    labels = [_label_clause(parts[i]) for i in idx]
    for lab in ("not_required", "required", "preferred"):
        if lab in labels:
            return lab
    # "Vous maîtrisez l'anglais et le français": one verb for two languages, and the cut at "et le" left the French
    # clause without it (TotalEnergies, 2026-09-29). The clause before it speaks for it when it names another language.
    for i in idx:
        if i and re.match(r"\s*(?:le|la|les|l['’]|the)\b", parts[i], I) and re.search(OTHER_LANGUAGE, parts[i - 1], I):
            lab = _label_clause(parts[i - 1])
            if lab != "unclear":
                return lab
    # The French clause alone says nothing ("Arabic or French, are advantageous"): only a softening
    # word later in the sentence can speak for it; a requirement word there is about the other language.
    tail = " ".join(parts[idx[0] + 1:])
    if _any(NOT_REQUIRED, tail):
        return "not_required"
    if _any(PREFERRED, tail):
        return "preferred"
    return "unclear"


# English as a skill in a French posting ("Anglais courant", "Maîtrise de l'anglais ... indispensable"), against a team that
# works in English ("la langue de travail est l'anglais"); 2026-10-14
ENGLISH_SKILL = r"\b(?:anglais|english)\b"
# The team, not the company: "équipe internationale ... 25 pays" (Pennylane), "équipes multisites (Brésil, ...)"
# (Sagemcom); "un groupe de dimension internationale" is the company and does not count (2026-10-14 pool)
INTERNATIONAL_TEAM = (r"\b[ée]quipes? (?:internationales?|multiculturelles?)\b|\binternational team\b|\bmultisites?\b"
                      r"|\bissus de \d+ pays\b|\b\d+ nationalit[ée]s\b|\benvironnement (?:multiculturel|international)\b")
ENGLISH_WORKPLACE = (r"langue de travail[^.]{0,30}\banglais\b|\bworking language\b[^.]{0,20}\benglish\b|\ben anglais au quotidien\b"
                     r"|\b100 ?% (?:en )?anglais\b|\b[ée]quipe anglophone\b|\benglish[- ]speaking (?:team|environment)\b")


def posting_language(text: str) -> str:
    words = re.findall(r"[a-zà-ÿ']+", text.lower())
    fr = sum(w in FR_STOP for w in words)
    en = sum(w in EN_STOP for w in words)
    if fr + en < 12:
        return "unknown"
    if fr > 1.5 * en:
        return "fr"
    if en > 1.5 * fr:
        return "en"
    return "mixed"


def check_french(units: list[str], text: str) -> dict:
    found = [(french_sentence(s), s) for s in units]
    found = [(lab, s) for lab, s in found if lab]
    labels = {lab for lab, _ in found}
    if "required" in labels and "not_required" in labels:
        verdict = "unclear"
    else:
        verdict = next((v for v in ("required", "unclear", "preferred", "not_required") if v in labels), "not_mentioned")
    return {
        "verdict": verdict,
        "evidence": [f"[{lab}] {s}" for lab, s in found][:6],
        "english_signals": [s for s in units if _any(ENGLISH_ENV, s)][:3],
        "posting_language": posting_language(text),
    }


# ---------------------------------------------------------------- remote

# "hybrid" before a model, architecture or search noun is machine learning ("recurrent / hybrid models", "hybrid
# architectures", "hybrid search": Featherless AI on Himalayas, 2026-09-30). "a hybrid model" of work is singular.
NOT_TECH = (r"(?!\s+(?:models|architectures?|search|retrieval|rag|approaches|methods|systems|networks|algorithms|ranking"
            r"|cloud|quantum|reasoning|attention|transformers?|state[- ]space)\b)")
REMOTE_MODE = {
    "remote": [r"\bfull[- ]?remote\b", r"\bfully remote\b", r"\b100\s?% remote\b", r"\bremote[- ]first\b",
               r"\bremote (position|role|job|work|only)\b", r"\bwork from anywhere\b", r"\bt[ée]l[ée]travail (complet|total|[àa] 100)",
               r"\b100\s?% t[ée]l[ée]travail\b", r"^\s*remote\b", r"\bremote\s*[-–(:]",
               r"\|\s*remote\b", r"\bremote\s*\|",          # Hacker News first line: "Acme | ML Engineer | REMOTE |"
               r"\bremote\s+(?:in|from|within|across)\s+(?:the\s+)?(?:europe|france|eu|emea)\b"],
    "hybrid": [r"\bhybrid\b" + NOT_TECH, r"\bhybride\b", r"\b\d\s*(days?|jours?)\b[^.]{0,30}\b(office|bureau|on[- ]?site|pr[ée]sentiel)",
               r"\bt[ée]l[ée]travail\s+(?:\d|un|deux|trois)\s*(?:jours?|j)\b",
               r"\bt[ée]l[ée]travail partiel\b", r"\bjours? de t[ée]l[ée]travail\b"],
    "onsite": [r"\bon[- ]?site\b", r"\bin[- ]office\b", r"\bsur site\b", r"\bpr[ée]sentiel\b", r"\bno remote\b"],
}
# Can someone living in France take this remote role? Rules written against live remote-board
# postings on 2026-09-27: 60 labelled lines, then 40 held-out postings from five other employers
# (33 right, none wrongly allowed, none wrongly denied; the 7 misses fell to "unclear").
# F = applies to a location field (an ATS field or a body "Location:" line); T = to a sentence.
# PX is a hand list: a country missing from it falls to "unclear", never to "allow".
PX = (r"(?:the\s+)?(?:continental\s+)?(?:u\.s\.a?\.?|usa|us|united\s+states(?:\s+of\s+america)?|america|americas|amer|"
      r"north\s+america|south\s+america|central\s*(?:&|and)\s*south\s+america|central\s+america|latam|latin\s+america|"
      r"caribbean|canada|mexico|brazil|argentina|colombia|peru|chile|uruguay|costa\s+rica|guatemala|nicaragua|jamaica|"
      r"u\.k\.|uk\s*&\s*i|uk|united\s+kingdom|great\s+britain|england|scotland|ireland|iberia|dach|nordics|benelux|cee|"
      r"germany|spain|portugal|italy|netherlands|belgium|luxembourg|switzerland|ch|austria|poland|czech\s+republic|czechia|"
      r"slovakia|hungary|romania|bulgaria|greece|croatia|slovenia|serbia|ukraine|turkey|denmark|norway|sweden|finland|"
      r"iceland|estonia|latvia|lithuania|malta|cyprus|israel|uae|united\s+arab\s+emirates|saudi\s+arabia|ksa|egypt|"
      r"morocco|nigeria|kenya|south\s+africa|africa|apac|asia[- ]pacific|asia|india|pakistan|philippines|indonesia|"
      r"malaysia|singapore|thailand|vietnam|japan|south\s+korea|korea|china|hong\s+kong|taiwan|australia|"
      r"new\s+zealand|dmv\s+area)(?!\w)")
GUARD = r"^(?!.*\b(?:france|paris|[iî]le[- ]de[- ]france|europe|european|eu|emea|worldwide|anywhere\s+in\s+the\s+world)\b)"
NOT_PAY = r"(?!.*\b(?:salary|pay|compensation|benefits?|insurance|leave|holidays?|401\(k\)|pto)\b)"
# The words between "must" and "based in" may not name someone else: clients, colleagues, an office.
NOT_OTHERS = (r"(?:(?!\b(?:clients?|customers?|engineers?|teams?|colleagues?|offices?|partners?|users?|stakeholders?"
              r"|headquarters|hq|company|teammates?|people|staff|roles?)\b)[^.]){0,60}?")
ELIGIBLE_DENY = {
    "D1 place + only (F,T)": GUARD + NOT_PAY + r".*(?<![(\w])(?:u\.s\.a?\.?|usa|us|united\s+states|canada|uk|united\s+kingdom|latam|latin\s+america|americas|north\s+america|mexico|brazil|india|philippines|apac)(?:\s*(?:,|/|&|\bor\b|\band\b)\s*(?:u\.s\.a?\.?|usa|us|united\s+states|canada|uk|united\s+kingdom|latam|mexico|brazil))*\s*[-–]?\s*only\b",
    "D2 must live/reside/be based in X (T)": GUARD + r"(?:.*\b(?:must|required|requires?|need(?:s)?\s+to|only)\b" + NOT_OTHERS + r"|\W*(?:currently\s+)?)\b(?:resid(?:e|ed|ing|ence)\s+(?:with)?in|resident\s+(?:of|in)|liv(?:e|ing)\s+in|located\s+(?:with)?in|based\s+(?:with)?in)\s+" + PX,
    "D3 work authorisation in X (T)": GUARD + r".*(?:\b(?:authori[sz]ed|eligible|permitted|right)\s+to\s+work\b[^.]{0,40}?\bin\s+" + PX + r"|\b(?:u\.s\.|us|uk)\s+work\s+authori[sz]ation\b)",
    "D4 anywhere in X (F,T)": GUARD + r".*\banywhere\s+in\s+" + PX,
    "D5 must reside within N miles (T)": r"\b(?:must|required\s+to|need\s+to)\s+(?:reside|live|be\s+located)\s+within\s+\d+\s*(?:miles?|mi|km)\b",
    "D6 US state lists (T)": r"\bresid\w*\s+in\s+(?:one\s+of\s+)?(?:the\s+following|certain|these|select(?:ed)?)\s+(?:u\.s\.\s+|us\s+)?states\b|\bstates\s+we\s+do\s+not\s+hire\s+in\b|\bonly\s+(?:able\s+to\s+)?hire\s+(?:permanent\s+)?(?:u\.s\.|us|usa)\s+residents\b",
    "D7 time-zone residence outside Europe (T)": GUARD + r".*(?:\b(?:resid\w*|location|located|based)\s+(?:with)?in\s+(?:the\s+)?(?:north\s+(?:or|and)\s+south\s+american|north\s+american|south\s+american|americas?|u\.?s\.?|usa|apac|asia[- ]pacific|pacific|eastern|central|mountain|atlantic|est|pst|cst)\s+time\s?zones?\b|\b(?:atlantic|eastern|pacific|central|mountain|u\.?s\.?|usa|north\s+american|americas?|apac)\s+time\s?zones?\s+only\b|\b(?:located|based|resid\w*)\s+in\s+[^.]{0,30}\btime\s?zones?\s+in\s+(?:the\s+)?(?:u\.?s\.?|usa|united\s+states|canada)\b)",
    "D8 +/-N h of ET (N<=4) or PT (N<=7) (F,T)": r"(?:±|\+/-|\+-)\s*[0-4](?:\.\d)?\s*(?:hours?|hrs?|h)\s+(?:from|of)\s+(?:e[sd]?t|eastern)\b|(?:±|\+/-|\+-)\s*[0-7](?:\.\d)?\s*(?:hours?|hrs?|h)\s+(?:from|of)\s+(?:p[sd]?t|pacific)\b",
    "D9 location field ends with a non-France place (F)": GUARD + r"(?!.*\bnon[- ])(?!.*(?:^|;)\s*remote\s*(?:;|$)).*?(?:^|[,;(/\s-])\s*" + PX + r"(?:\s+time\s?zones?)?\s*(?:\(?\s*remote\s*\)?|-\s*remote|only)?\s*\)?\s*$",
    "D10 Europe/EMEA narrowed to countries without France (F,T)": r"^(?!.*\b(?:france|prefer\w*)\b).*\b(?:europe|emea|eu)\b[^.]{0,40}\b(?:in|including|:)\s+(?:the\s+)?(?:uk|united\s+kingdom|ireland|germany|spain|portugal|netherlands|poland|sweden|italy)\b",
    "D11 France excluded by name (F,T)": r"\b(?:except|excluding|excluded|excl\.?|not\s+in|other\s+than|sauf|hors|ausgenommen)\s*[^.;)]{0,40}?\bfrance\b",
}
ELIGIBLE_UNCLEAR = {
    "U1 bare remote location (F)": r"^\s*(?:100%\s+|fully\s+)?(?:remote|remoto|homeoffice|home\s+office|t[eé]l[eé]travail)\s*$",
    "U2 bare global location (F)": r"^\s*(?:remote\s*[(,-]?\s*)?global\s*\)?\s*$",
    "U3 global hiring via country list / EOR (T)": r"\bhire[sd]?\s+(?:new\s+team\s+members\s+)?(?:globally|in\s+countries\s+around\s+the\s+world)\b|\bcountries\s+we\s+(?:currently\s+)?(?:hire|employ)\b|\bremote\s+from\s+\d+\+?\s+countries\b|\bemployer\s+of\s+record\b|\(eor\)|\bcountry\s+of\s+(?:residence|employment)\b",
    "U4 anywhere with asterisk or time limit (T)": r"\banywhere\b[^.]{0,20}\*|\bwork\w*\b[^.]{0,30}\bfrom\s+anywhere\b[^.]{0,80}\b(?:up\s+to|for)\s+\d+\s+(?:consecutive\s+)?(?:months?|weeks?|days?)\b|\bfor\s+\d+\s+(?:months?|weeks?)\s+(?:each|per|a|every)\s+(?:calendar\s+)?year\b",
    "U5 remote-first or radius with office/hybrid (T)": r"\bremote[- ]first\b[^.]{0,120}\b(?:hybrid|office|in[- ]person|hub)\b|\bwithin\s+\d+\s*(?:miles?|mi|km)\b[^.]{0,120}\b(?:hybrid|onsite|on-site|office|fully\s+remote)\b|\b\d+\s*-?\s*mile\s+radius\b",
    "U6 France with residency duration (T)": r"\b(?:resident|resid\w*|liv\w*)\s+in\s+france\s+for\s+(?:the\s+(?:last|past)|a\s+minimum\s+of)\b",
    "U7 employment type depends on country (T)": r"\bif\s+(?:you\s+are\s+|you're\s+)?based\s+in\b[^.]{0,60}\b(?:full[- ]time\s+employee|employee\s+position|contractor|contract\s+basis)\b",
}
ELIGIBLE_ALLOW = {
    "A1 location field names a France-containing region (F)": r"\b(?:france|paris|[iî]le[- ]de[- ]france|europe|european\s+union|eu|emea|worldwide|anywhere|non[- ]?u\.?s\.?)\b",
    "A2 candidates located/hired in Europe/EU/EMEA/France/CET (T)": r"\b(?:candidates?|applicants?|must\s+be|required|requires?|we\s+(?:\w+\s+){0,2}?hire|hiring|open\s+to|accept\w*)\b[^.]{0,60}?\b(?:located|based|resid\w*|within|in|from)\b[^.]{0,40}?\b(?:europe|european\s+union|eu|emea|france|european\s+time\s?zones?|cet|cest)\b|\bhir(?:e|ing)\s+(?:across|in|within)\s+(?:[\w ]+,\s*)*?(?:france|europe|eu|emea)\b",
    "A3 CET +/-N band (F,T)": r"\bcet\b\s*(?:time\s?zone|timezone)?\s*\(?\s*(?:\+/-|±|\+-)\s*\d",
    "A4 UTC..UTC+N band with N>=2 (T)": r"\butc\s*(?:\+\s*0)?\s*(?:to|-|–)\s*utc\s*\+\s*[2-9]\b",
    "A5 overlap with CET / European hours (T)": r"\boverlap\w*\s+with\s+(?:the\s+)?~?\s*(?:cet|cest|central\s+european|european\s+(?:business\s+|working\s+)?hours|european\s+time\s?zones?)",
    "A6 worldwide except a list without France (F,T)": r"\b(?:worldwide|anywhere\s+in\s+the\s+world|global(?:ly)?|internationally\s+located)\b[^.]{0,40}\b(?:except|excluding|not\s+in|other\s+than)\b(?![^.]*\b(?:france|europe|eu|emea)\b)",
    "A7 you can work from anywhere in the world (T)": r"\b(?:remote|you\s+can|fully\s+remote)\b[^.]{0,40}\bwork\s+from\s+anywhere\s+in\s+the\s+world\b(?![^.]{0,40}\b(?:for|up\s+to)\s+\d)",
    "A8 everyone / all locations welcome to apply (T)": r"\bwelcome\s+(?:everyone|all\s+candidates|applicants\s+from\s+anywhere)\s+to\s+apply\b|\bwelcome\s+applications\s+from\s+all\s+locations\b",
    "A9 remote in Europe, EMEA or France named in the text (T)": r"\bremote\s*(?:[(\-–:]\s*|\s+(?:in|from|within|across)\s+(?:the\s+)?)(?:emea|europe|eu|france)\b|\bfully\s+remote\s+in\s+france\b|\bt[ée]l[ée]travail\b[^.]{0,30}\bfrance\b",
}
# A place in France named anywhere in the posting: the job is there, whatever the work mode.
FRANCE_PLACE = (r"\b(?:paris|france|[iî]le[- ]de[- ]france|lyon|lille|toulouse|nantes|bordeaux|marseille|grenoble"
                r"|rennes|sophia[- ]antipolis|montpellier|strasbourg|nice|saclay|la d[ée]fense"
                # the Paris area's business suburbs, written bare by company boards (25 of 32 were read as abroad,
                # 2026-10-18 check)
                r"|boulogne[- ]billancourt|issy[- ]les[- ]moulineaux|courbevoie|puteaux|nanterre|levallois[- ]perret|neuilly[- ]sur[- ]seine|clichy|saint[- ]ouen|saint[- ]denis|saint[- ]cloud|rueil[- ]malmaison|suresnes|montrouge|ch[aâ]tillon|malakoff|vanves|meudon|s[eè]vres|clamart|antony|le plessis[- ]robinson|ivry[- ]sur[- ]seine|vitry[- ]sur[- ]seine|montreuil|pantin|aubervilliers|noisy[- ]le[- ]grand|massy|palaiseau|orsay|gif[- ]sur[- ]yvette|les ulis|courtaboeuf|evry|[ée]vry|v[ée]lizy[- ]villacoublay|v[ée]lizy|guyancourt|[ée]lancourt|saint[- ]quentin[- ]en[- ]yvelines|versailles|saint[- ]germain[- ]en[- ]laye|rungis|cr[ée]teil|marne[- ]la[- ]vall[ée]e|cergy)\b")
# A capitalised town followed by its French département number names a place in France: "basé à
# Vélizy-Villacoublay (78)", Indeed's "Courbevoie (92)" (Indeed 2026-10-04: two postings read as "place unclear").
# Case-sensitive, so "une équipe de (12) personnes" is not a place.
FRANCE_DEPT = re.compile(r"\b[A-ZÀ-Ý][\w'’-]*(?:[ -][A-ZÀ-Ý][\w'’-]*)*\s*\(\s*(?:0[1-9]|[1-8]\d|9[0-5]|2[AB]|97[1-6])\s*\)")
FIELD_ONLY = {"D9 location field ends with a non-France place (F)", "U1 bare remote location (F)",
              "U2 bare global location (F)", "A1 location field names a France-containing region (F)"}
_ELIG = {k: re.compile(v, I) for d in (ELIGIBLE_DENY, ELIGIBLE_UNCLEAR, ELIGIBLE_ALLOW) for k, v in d.items()}
# A location label at the start of a line, read to the end of that line ("Location: Remote (U.S. only)").
# "Our office locations: Austin, Paris" is not an eligibility statement and is not read as one.
LOCATION_LINE = re.compile(r"^\s*(?:job\s+|work\s+)?locations?\s*:\s*([^\n<]{1,150})", I | re.M)
# Paris hours for the overlap a posting asks. Paris is UTC+1 in winter, UTC+2 in summer.
TIMEZONE = [
    (r"\b(?:overlap\w*|align\w*)\b[^.]{0,60}?\b(?:est|edt|eastern|east\s+coast)\b", "US Eastern 09-17 is 15:00-23:00 in Paris"),
    (r"\b(?:overlap\w*|align\w*|core\s+hours)\b[^.]{0,60}?\b(?:p[sd]?t|pacific)\b", "US Pacific 09-17 is 18:00-02:00 in Paris"),
    (r"\boverlap\w*\b[^.]{0,40}?\b(?:melbourne|sydney|aest|aedt|australia\w*)\b", "Australian east-coast hours are night in Paris"),
    (r"\boverlap\w*\b[^.]{0,40}?\b(?:uk|united\s+kingdom|british|london|gmt)\b", "London is one hour behind Paris: a normal day"),
    (r"\b(?:overlap\w*|align\w*)\s+with\s+(?:the\s+)?~?\s*(?:cet|cest|central\s+european|european)", "European hours: no constraint in Paris"),
    (r"\basync\w*\b[^.]{0,40}?\b(?:minimal\w*|any\s+time\s?zone)|\bany\s+time\s?zone\b", "no fixed overlap asked"),
]


def eligibility(text: str, is_field: bool) -> tuple[str, list[str]]:
    """One field value or one sentence: deny > allow > unclear; no rule hit means unclear (ask)."""
    for label, rules in (("deny", ELIGIBLE_DENY), ("allow", ELIGIBLE_ALLOW), ("unclear", ELIGIBLE_UNCLEAR)):
        hits = [k for k in rules if (is_field or k not in FIELD_ONLY) and _ELIG[k].search(text)]
        if hits:
            return label, hits
    return "unclear", []


def check_remote(units: list[str], location: str, text: str = "", remote_board: bool = False) -> dict:
    hay = units + ([location] if location else [])
    modes = [m for m, pats in REMOTE_MODE.items() if any(_any(pats, s) for s in hay)]
    mode = "remote" if "remote" in modes and "hybrid" not in modes and "onsite" not in modes else (
        "hybrid" if "hybrid" in modes else ("onsite" if "onsite" in modes else ("remote" if modes else "unknown")))
    if remote_board and mode == "unknown":
        mode = "remote"        # a remote-only board lists remote roles even when the text never says so
    # An explicit restriction sentence wins, unless the posting's own location already names France
    # or Paris (then the sentence is about some other role or person: ask). A body "Location:" line
    # that disagrees with the ATS field is a question; then the ATS field; then any allowing line.
    evidence, sentence_labels = [], []
    for s in sentences(LOCATION_LINE.sub(" ", text)):   # splits only before a capital: "U.S. states" stays whole
        lab, hits = eligibility(s, False)
        if lab != "deny":
            # "Our European team is growing; you must be based in the United States": the word
            # "European" turns the deny rules off for the whole sentence, so read each clause too.
            for part in re.split(r";|\bbut\b|\bhowever\b", s, flags=I):
                plab, phits = eligibility(part.strip(), False)
                if plab == "deny":
                    lab, hits = plab, phits
                    break
        sentence_labels.append(lab)
        if hits:
            evidence.append(f"[{lab}] {s[:160]} ({hits[0].split(' ')[0]})")
    body_loc = []
    for v in LOCATION_LINE.findall(text):
        lab, hits = eligibility(v.strip(), True)
        body_loc.append(lab)
        evidence.append(f"[{lab}] Location: {v.strip()} ({hits[0].split(' ')[0] if hits else 'no rule'})")
    ats = "unclear"
    if location.strip():
        ats, hits = eligibility(location, True)
        evidence.append(f"[{ats}] location field: {location} ({hits[0].split(' ')[0] if hits else 'no rule'})")
    located_in_france = ats == "allow" or "allow" in body_loc
    if "deny" in sentence_labels:
        verdict = "unclear" if located_in_france else "no"
    elif "deny" in body_loc:
        verdict = "unclear" if ats == "allow" else "no"
    elif ats == "deny":
        verdict = "unclear" if "allow" in sentence_labels + body_loc else "no"
    elif "allow" in sentence_labels + body_loc or ats == "allow":
        verdict = "yes"
    else:
        verdict = "unclear"
    tz = [f"{meaning}: {s[:120]}" for s in units for pat, meaning in TIMEZONE if re.search(pat, s, I)]
    # What to show. An explicit "no" matters in every mode. For a remote role, or an unknown mode, the
    # eligibility verdict is the answer. For a hybrid or on-site role the question is where the office
    # is: in France it is fine ("n/a"); anywhere else, or unnamed, it is "unclear" (a Palo Alto on-site
    # role reached tier A before, 2026-09-27 review).
    in_france = located_in_france or any(re.search(FRANCE_PLACE, u, I) or FRANCE_DEPT.search(u) for u in hay)
    if verdict == "no":
        shown = "no"
    elif mode in ("remote", "unknown"):
        shown = "yes" if verdict == "unclear" and in_france and mode == "unknown" else verdict
    else:
        shown = "n/a" if in_france else "unclear"
    return {"mode": mode, "france_eligible": shown, "france_eligible_if_remote": verdict,
            "evidence": evidence[:6], "timezone": tz[:3]}


# ---------------------------------------------------------------- contract

# A family word only counts inside a contract phrase: "apprentissage" is machine learning, "vie" is
# life, "early-stage" is a startup, and "supervise an intern" is a CDI duty (review, 2026-09-27).
CONTRACT = [
    ("internship", [r"\binternship\b", r"\bstagiaire\b", r"\boffre de stage\b", r"\bstage de fin d'[ée]tudes\b",
                    r"\bstage\s*(?:de\s*)?\(?\d+\s*mois\b", r"^\s*stage\b", r"\b(?:as|an|our)\s+intern\b(?!s)"]),
    ("apprenticeship", [r"\balternance\b", r"\balternant", r"\bapprentie?s?\b", r"\bcontrat d'apprentissage\b", r"\bwork[- ]study\b"]),
    ("phd", [r"\bcifre\b", r"\bcontrat doctoral\b", r"\bthèse (?:cifre|de doctorat)\b", r"\bsujet de thèse\b",
             r"\bphd (?:position|student|candidate|thesis)\b", r"\bdoctoral (?:position|contract|student)\b"]),
    ("vie", [r"\bV\.I\.E\.?\b", r"\b(?-i:VIE)\b", r"volontariat international"]),   # capitals only: "vie" is life
    ("freelance", [r"\bfreelance", r"\bindependent contractor\b", r"\brole type:\s*contractor\b", r"\bcontractor (?:role|position)\b",
                   r"\bB2B contract\b", r"\bportage\b", r"\bmission de \d+", r"\bTJM\b", r"\bdaily rate\b",
                   # the offer's type given as "Contract" (Himalayas AI-training gigs, 2026-10-04): a type field, a
                   # title ending "- Contract" or "(Contract)", "Contract, 6 months", pay per task; "smart contract",
                   # "contract research organization" and "permanent contract" are none of these
                   r"\b(?:role|job|employment|engagement|position|work) type:\s*contract\b",
                   r"(?:[-–|,(]\s*)contract\s*\)?\s*$", r"^\s*contract,\s*\d{1,2}\s*(?:months?|mois)\b",
                   r"\bpaid per (?:task|deliverable)\b", r"\bcompensation is output[- ]based\b"]),
    ("cdd", [r"\bCDD\b", r"\bfixed[- ]term\b", r"contrat [àa] dur[ée]e d[ée]termin[ée]e", r"\btemporary (?:contract|position)\b"]),
    ("cdi", [r"\bCDI\b", r"\bpermanent (?:contract|position|role|employment|employee)\b", r"contrat [àa] dur[ée]e ind[ée]termin[ée]e",
             r"\bfull[- ]time permanent\b"]),
]
# "possibilité de CDI à l'issue" is a prospect after an internship, not the offer; "you will supervise
# an intern" is a duty, not the offer. Each guard applies only to the families it is about.
NOT_THIS_CONTRACT = {
    "cdi": r"possibilit[ée] de|[àa] l'issue|after the internship|pr[ée][- ]embauche|pre[- ]hire",
    "cdd": r"possibilit[ée] de|[àa] l'issue|after the internship",
    # "previous internship/work experience" (Skypher), "An internship or thesis" (OVH), "en stage ou alternance ou poste"
    # (Valeo): the candidate's past, audit 2026-10-17
    "internship": r"supervis|mentor|encadr|\b(?:previous|prior|past)\b|\binclud|\bor (?:a )?thesis\b|\bou poste\b"
                  # a careers-site footer, "check out internship and job opportunities" (Dassault Systèmes, 2026-10-21)
                  r"|\binternships? (?:and|&|or) (?:job|career|graduate)|\b(?:jobs?|careers?) (?:and|&|or) internships?\b",
    "apprenticeship": r"supervis|mentor|encadr|\bou poste\b",
    # a thesis named as the alternative to experience, or held, is the candidate's past (ADAY, 2026-09-29)
    "phd": r"\b(?:ou|or)\s+(?:une?\s+|a\s+)?(?:th[èe]se|doctorat|phd)\b|\btitulaire d['’](?:une?\s+)?(?:th[èe]se|doctorat)",
    # freelance named as a source of the candidate's experience, not the offer (NewRocket, 2026-10-21)
    "freelance": r"\bfreelance (?:work|experience|projects?)\b|\bexperience (?:through|from|including|gained)\b",
}
TEXT_ORDER = ("cdi", "cdd", "internship", "apprenticeship", "phd", "vie", "freelance")
# "Fixed Term" is also what an internship is (Novartis labels its 6-month stages "Early Career (Fixed Term)",
# 2026-09-29), so without the word CDD it does not beat a line that offers a stage or internship of a set length
INTERN_OFFER = [r"\bstage\s*(?:de\s*)?\(?\d+\s*mois\b", r"\boffre de stage\b", r"\bstage de fin d'[ée]tudes\b",
                r"\b\d+[- ]months? internship\b", r"\binternship of \d+ months?\b"]
# the length as a field: "Durée du contrat" with "12 mois" on the next line, or "12 mois" alone under the contract line
# (the CNRS job portal, 2026-09-29); months in an ordinary sentence are not the length
DURATION_LABEL = re.compile(r"\b(?:dur[ée]e(?: du contrat)?|contract (?:length|duration)|duration)\b", I)
MONTHS_ALONE = re.compile(r"^\s*(\d{1,2})\s*(?:mois|months?|ans|ann[ée]es|years?)\s*$", I)
# years are a length only right after a contract word; "2 ou 3 ans dans un poste similaire" is experience (ADAY, 2026-09-29)
YEARS_LENGTH = re.compile(r"\b(?:CDD|contrat|contract|dur[ée]e|duration|length|th[èe]se(?:\s+cifre)?|doctoral)\s*(?:de|of|:)?\s*"
                          r"(\d{1,2})\s*(?:ans|ann[ée]es|years?)\b", I)


def length_months(s: str) -> int | None:
    """A length in a sentence, in months: "12 mois", "CDD de 2 ans" (24), "Durée de contrat : 3 ans" (36, Inria,
    2026-09-29), or "2 ans" alone on the line under the contract."""
    m = re.search(r"(\d{1,2})\s*(?:mois|months?)\b", s, I)
    if m:
        return int(m.group(1))
    y = YEARS_LENGTH.search(s) or MONTHS_ALONE.match(s)
    return int(y.group(1)) * 12 if y else None


def months_as_field(units: list[str], hits: list[str]) -> int | None:
    for i, s in enumerate(units):
        after = units[i + 1] if i + 1 < len(units) and MONTHS_ALONE.match(units[i + 1]) else None
        if DURATION_LABEL.search(s):
            n = length_months(s) or (length_months(after) if after else None)
            if n:
                return n
        elif s in hits and after:
            return length_months(after)
    return None


def check_contract(units: list[str], title: str) -> dict:
    families = dict(CONTRACT)

    def hits_for(kind, hay):
        guard = NOT_THIS_CONTRACT.get(kind)
        return [s for s in hay if _any(families[kind], s) and not (guard and re.search(guard, s, I))]

    def months_in(hits):
        for s in hits:
            n = length_months(s)
            if n:
                return n
        return None

    if title:                                   # the title names the offer: "Stage - Data Scientist"
        for kind, _ in CONTRACT:
            h = hits_for(kind, [title])
            if not h and kind == "internship" and re.search(r"\bintern\b|\bstage\b", title, I):
                h = [title]                     # in a title a bare "Intern" or "Stage" is the offer
            if h:
                return {"type": kind, "months": months_in(h), "from": "title", "evidence": h[:2]}
    for kind in TEXT_ORDER:                     # an explicit CDI or CDD wins over a family word in passing
        h = hits_for(kind, units)
        if h and kind == "cdd" and not any(re.search(r"\bCDD\b|dur[ée]e d[ée]termin[ée]e", s, I) for s in h):
            offer = [s for s in hits_for("internship", units) if _any(INTERN_OFFER, s)]
            if offer:
                return {"type": "internship", "months": months_in(offer), "from": "text", "evidence": offer[:2]}
        if h:
            months = months_in(h)
            if months is None and kind in ("cdd", "internship"):
                months = months_as_field(units, h)
            return {"type": kind, "months": months, "from": "text", "evidence": h[:2]}
    return {"type": "unknown", "months": None, "from": None, "evidence": []}


# ---------------------------------------------------------------- salary

NUM = r"\d{1,3}(?:[ ,.\u202f\u00a0]\d{3})+|\d+(?:[.,]\d+)?"
SALARY_WORD = (r"salary|salaire|r[ée]mun[ée]ration|compensation|\bpay\b|\bpaid\b|package|\bbrut|\bgross\b|\bbase\b"
               r"|\bfixe\b|\bOTE\b|\bsalari|\bTJM\b|daily rate|taux journalier")
# Amounts after one of these words in the same sentence are not the salary.
NOT_SALARY = (r"relocation|budget|grant|stipend|allowance|\bL&D\b|learning|voucher|ticket|\bprime|\bbonus|cooptation"
              r"|signing|equity|stock|BSPCE|mutuelle|transport|navigo|remote setup|home[- ]office")
# "45 000 €", "€60k–€70k", "EUR 50,000 - 60,000", "entre 30 et 45K€"
AMOUNT = re.compile(rf"(€\s?|\bEUR\s?)?({NUM})\s?(k|K)?\s?(€|eur(?:os?)?\b)?"
                    rf"(?:\s?(?:-|–|—|to|à|a|et|and)\s?(€\s?|\bEUR\s?)?({NUM})\s?(k|K)?\s?(€|eur(?:os?)?\b)?)?", I)
UP_TO = r"\bup to\b|jusqu'[àa]|\bmax(?:imum)?\b"
MONTHLY = (r"\bmensuel\w*|\bpar mois\b|\bper month\b|\bmonthly\b|\ba month\b|(?<!\w)/\s?(?:mo|mois|month)\b"
           r"|\bp\.\s?m\.(?!\s*(?:cet|est|pst))|\bmois\b")
DAILY = r"\bTJM\b|/\s?jour\b|\bper day\b|\bdaily rate\b|\bpar jour\b"


def _to_number(raw: str, k: bool) -> float:
    raw = raw.strip()
    if re.fullmatch(r"\d{1,3}(?:[ ,.\u202f\u00a0]\d{3})+", raw):
        value = float(re.sub(r"[ ,.\u202f\u00a0]", "", raw))
    else:
        value = float(raw.replace(",", "."))
    return value * 1000 if k else value


def _amounts(segment: str) -> list[float]:
    """Euro amounts in a segment: each must carry a euro sign, EUR or a k, directly or through its range."""
    out = []
    for m in AMOUNT.finditer(segment):
        e1, n1, k1, c1, e2, n2, k2, c2 = m.groups()
        euro_or_k = any((e1, k1, c1, e2, k2, c2))
        if not euro_or_k:
            continue                                  # a year, a postcode, "ISO 27001"
        try:
            a = _to_number(n1, bool(k1) or (bool(k2) and n2 is not None and float(n1.replace(",", ".").replace(" ", "")) < 1000))
            out.append(a)
            if n2:
                out.append(_to_number(n2, bool(k2)))
        except ValueError:
            continue
    return [a for a in out if a >= 1000]


# The company's money, not the pay: "taking our total funding to $70M" read as a salary in dollars (White Circle,
# 2026-09-29). A funding word, or an amount in millions or billions, marks the sentence.
FUNDING = (r"\b(?:rais(?:ed|ing)|funding|series [a-f]|seed round|valuation|valued at|revenue|arr|investors?|backed by"
           r"|lev[ée]e? de fonds|chiffre d['’]affaires)\b|[$£€]\s?\d[\d.,]*\s?(?:m|b|bn|million|billion)\b"
           r"|\d[\d.,]*\s?(?:m|bn|md)\s?(?:[$£€]|usd|eur|gbp)")


def _salary_word(s: str):
    """The first salary word that is not the tail of a benefit ("relocation package", "bonus package")."""
    return next((m for m in re.finditer(SALARY_WORD, s, I)
                 if not re.search(r"(?:" + NOT_SALARY + r")\W{0,3}\w*\W{0,3}$", s[max(0, m.start() - 25):m.start()], I)), None)


def check_salary(units: list[str], floor: float | None) -> dict:
    for s in units:
        if not re.search(r"€|\beur\b|\beuros?\b|k€", s, I):
            continue
        word = _salary_word(s)
        if not word:
            continue
        # The salary words may come before or after the amount ("45 000 € brut annuel"). Cut away a
        # benefit that comes before the salary word, and anything from "+ 2 000 €" or a benefit word
        # after it ("Salary: 45 000 € + 2 000 € signing bonus").
        before = [m for m in re.finditer(NOT_SALARY, s[:word.start()], I)]
        start = word.start() if before else 0     # "Relocation package of €5,000 and a salary of €48,000"
        after = re.search(NOT_SALARY + r"|\s\+\s*€?\s?\d", s[word.end():], I)
        end = word.end() + after.start() if after else len(s)
        segment = s[start:end]
        amounts = _amounts(segment)
        if not amounts:
            continue
        if re.search(DAILY, s, I):
            return {"min_eur_year": None, "max_eur_year": None, "vs_floor": "daily_rate",
                    "daily_eur": round(min(amounts)), "evidence": s}
        monthly = bool(re.search(MONTHLY, s, I))
        yearly = [a * 12 if monthly and a < 20000 else a for a in amounts]
        lo, hi = min(yearly), max(yearly)
        if len(yearly) == 1 and re.search(UP_TO, segment, I):
            lo = None                              # "up to €45k" gives a ceiling, not a minimum
        if floor is None:
            vs = "no_floor_given"
        elif lo is not None and lo >= floor:
            vs = "above"
        elif hi < floor:
            vs = "below"
        else:
            vs = "straddles"
        return {"min_eur_year": None if lo is None else round(lo), "max_eur_year": round(hi), "monthly_in_text": monthly,
                "vs_floor": vs, "evidence": s}
    # Another currency counts when a salary word names it, or a bare $/£ amount sits in a sentence with no benefit
    # word: "learning and development budget of up to $/€/£1,000" is not the pay (Jobgether, 2026-10-02).
    other = next((s for s in units if re.search(r"(\$|£|\bUSD|\bGBP|\bCHF)\s?\d|\d\s?(?:k\s?)?(?:\$|USD\b|GBP\b|CHF\b)", s)
                  and (_salary_word(s) or (re.search(r"\$|£", s) and not re.search(NOT_SALARY, s, I)))
                  and not re.search(FUNDING, s, I)), None)
    return {"min_eur_year": None, "max_eur_year": None, "vs_floor": "not_stated" if not other else "other_currency",
            "evidence": other}


# ---------------------------------------------------------------- seniority and hard blockers

# (?<!\d): "120+ years of innovation" is not 20 years (review 5, 2026-09-29)
# "2 ou 3 ans", "2 or 3 years": a range whose low end is the ask (ADAY, 2026-09-29)
# "a[ñn]os": Spanish postings from the remote boards ("4+ años de experiencia", Logicalis Spain, 2026-10-08)
YEARS = re.compile(r"(?<!\d)(\d{1,2})\s*\+?\s*(?:(?:-|–|to|à|ou|or)\s*\d{1,2}\s*\+?\s*)?(?:(?:or more|or above|ou plus)\s+)?"
                   r"(?:(?:overall|total|combined|relevant)\s+)?(?:years?|yrs|ans|ann[ée]es|a[ñn]os)", I)
EXPERIENCE_CUE = r"experience|exp[ée]rience|experiencia|background|track record|professional"
# A years figure written as an ask counts without an experience word: "5+ years in SRE", "At least 3 years building",
# "4 + years working on" (2026-09-29 sweep; each read as no years before).
# A range or "or more" is an ask too: "4–7 years in a data scientist role" (Timeleft), "3 or more years building"
# (Clera), "2-5 years in revenue operations" (Dust), 2026-10-08 sweep; the company's past stays out (COMPANY_HISTORY)
YEARS_ASKED = (r"(?<!\d)\d{1,2}\s*\+\s*(?:(?:overall|total|combined|relevant)\s+)?(?:years?|yrs|ans|ann[ée]es|a[ñn]os)"
               r"|\b(?:at least|minimum(?: of)?|au moins|minimum de|al menos|como m[ií]nimo)\s+\d{1,2}\s*(?:years?|yrs|ans|ann[ée]es|a[ñn]os)"
               r"|(?<!\d)\d{1,2}\s*(?:-|–|to|à)\s*\d{1,2}\s*(?:years?|yrs|ans|ann[ée]es)\b"
               r"|(?<!\d)\d{1,2}\s+(?:or more|or above|ou plus)\s+(?:years?|yrs|ans|ann[ée]es)\b")
# "EXPERIENCE WE VALUE - 3+ years", "WHAT WE'RE LOOKING FOR": the company speaking about the candidate.
# Both apostrophes: 10 postings of the 2026-09-29 sweep write "we’re".
WE_ASK = (r"\bwe(?:['’]re| are)? (?:value|expect|require|need|want|seek|ask for|look(?:ing)? for)\b"
          r"|\bwhat we(?:['’]re| are)? looking for\b|\bnous recherchons\b|\bnous attendons\b")
NICE_HEADER = (r"^\W*(?:nice[- ]to[- ]haves?|bonus(?:es)?|preferred(?: qualifications?)?|pluses|a plus"
               r"|(?:highly )?desirable|bonus points?|good to have|it would be (?:great|nice) if(?: you have)?"
               r"|what would make you stand out|ideally,? you(?:['’]ll| will)? (?:also )?have|atouts?|un plus)\b")   # "Highly Desirable" (Stability AI on Himalayas, 2026-10-14)
# A header on a line of its own opens a section that runs to the next header ("Nice to have" / "3+ years of Rust")
SECTION_NICE = NICE_HEADER + r"\W*$"
SECTION_OTHER = (r"^\W*(?:requirements?|qualifications?|must[- ]haves?|required|what you(?:['’]ll| will)? (?:need|bring)"
                 r"|what we(?:['’]re| are)? looking for|about you|your profile|who you are|profil recherch[ée]"
                 r"|comp[ée]tences requises|responsibilities|what you(?:['’]ll| will) do|missions?)\b|^[^.]{1,40}:\s*$")
# "un plus" is a nice-to-have; "6 ans et plus" is "six years or more" (Docaposte, 2026-09-29)
NICE_TO_HAVE = (r"\bpreferred\b|(?<!plus de )(?<!\bde )(?<!\bet )(?<!\bou )\bplus\b(?! de)|nice[- ]to[- ]have|\bbonus\b"
                r"|ideally|souhait|appr[ée]ci"
                # "Preferably 5+ years" (DocuSketch), "idéalement … supérieure à 3 ans" (Dassault), audit 2026-10-17
                r"|preferabl|id[ée]alement|de pr[ée]f[ée]rence|dans l['’]id[ée]al")
# "Fort de 20 ans d'expérience, notre cabinet...", "With 15 years of experience in fintech, we serve...":
# the company's history, not a requirement; nor are years of data, of age, or of residence.
COMPANY_HISTORY = (r"\bfort de\b|\bfounded\b|\bsince \d{4}\b|\bdepuis (?:plus de )?\d+\s*ans\b|\bdepuis \d{4}\b|\bwe serve\b"
                   r"|\bwith (?:over|more than) \d+ years\b|\btrusted (?:us|by)\b|\bour (?:company|firm) has\b"
                   r"|ans d'[ée]tudes|\byears? of (?:study|studies)\b"
                   r"|\byears? old\b|\byears? of age\b|\bresiden(?:ce|t|cy)\b|\bliv(?:e|ed|ing) in\b"
                   r"|\byears? (?:later|ago)\b|\bans plus tard\b|\bil y a \d+\s*ans\b")   # "10+ years later, a French start-up..."
UPPER_BEFORE = (r"(?:less than|fewer than|under|up to|no more than|not more than|at most|maximum(?: of)?|max\.?|moins de"
                r"|jusqu['’]?\s*[àa]|au plus|<|≤)\s*$")
UPPER_AFTER = r"^\s*(?:\)\s*)?(?:max(?:imum)?\b|at most|or less|ou moins|au maximum|au plus)"
# "10+ years of historical claims data", "120+ years of innovation": the company's data or past. Only in a clause
# without an experience word, and not "data" as a field of work: "4+ years of experience in Data Infrastructure"
# and "5+ years of data engineering" are asks (Mistral, 2026-09-29; the first rule read them as none).
YEARS_OF_THINGS = (r"\byears? of (?:\w+\s+){0,2}(?:data|history|heritage|innovation|existence)\b"
                   r"(?!\s*(?:scien|engineer|infra|analy|platform|pipeline|architect|warehous|product|ops|model))")
# "With 15 years of experience in fintech, we are looking for...": the opening clause is about the company
COMPANY_INTRO = (r"^\W*(?:with|avec)\s+(?:over\s+|more than\s+|plus de\s+)?\d{1,3}\+?\s*(?:years?|ans)\b[^.]{0,160}?,"
                 r"\s*(?:we|our|nous|notre)\b")
SPEAKS_FOR_COMPANY = r"\b(we|our|notre|nous)\b"
SPEAKS_TO_CANDIDATE = r"\b(you|your|vous|votre|candidates?|applicants?|profil)\b"
# "Member of Technical Staff" is the AI labs' plain engineer title (Cohere, H Company; ats_fetch keeps it too)
SENIOR_TITLE = (r"\b(senior|sr\.?|(?<!technical )staff|principal|lead(?:er)?|head of|confirm[ée]|confirmed|exp[ée]riment[ée]"
                r"|experienced|expert)\b")
PHD_REQUIRED = r"ph\.?\s?d\b|doctora"
# A requirements line that opens with the degree asks for it: "PhD in machine learning, computer vision, ..." (Nucs AI,
# 2026-09-29), unless a master's also does ("PhD or MSc in ...", "An MSc or PhD in ...")
PHD_LINE = r"^\W*(?:an?\s+)?(?:ph\.?\s?d\.?|doctorat)\b"
MASTER_OK = r"\b(?:m\.?sc|masters?|master['’]s|m2|bac\s*\+\s*5|engineering degree|dipl[oô]me d['’]ing[ée]nieur)\b"
# "PhD or equivalent research experience", "(or equivalent experience)": experience stands in for the degree
# "Doctorat (ou niveau équivalent)" (Institut Curie, 2026-10-14) names a level, not only the degree
# "4–6 years (or a strong recent graduate…)" (Aline), "5-6 years for senior levels" (Harmattan): not the ask
YEARS_OR_GRADUATE = r"\bor (?:an? )?(?:strong |exceptional )?(?:recent )?(?:new )?grad(?:uate)?s?\b|\bfor (?:the )?senior[- ]levels?\b"
EQUIVALENT = r"\bor equivalent\b|\bou (?:niveau |dipl[ôo]me )?[ée]quivalent|\bequivalent\b[^.]{0,30}\bexperience\b"
# A required research record is the research-scientist level, PhD or not: "Strong academic research track record in
# computer vision, ..." (Stability AI, 2026-10-14); "track record of shipping" is not
RESEARCH_RECORD = (r"\b(?:academic|research|publication)\s+(?:research\s+)?track record\b|\btrack record of (?:\w+\s+){0,2}publi"
                   r"|\bpublication (?:record|history)\b|\bfirst[- ]author(?:ed)? (?:papers?|publications?)\b"
                   # Cohere, 2026-10-18: "One or more papers at top-tier
                   # venues (such as NeurIPS, ICML ...)", "Track record of work on generalization ... through publications"
                   r"|\b(?:papers?|publications?) (?:at|in) (?:top|leading|major|premier)[- ](?:tier )?(?:ml |ai )?(?:venues?|conferences?|journals?)"
                   r"|\btrack record\b[^.]{0,80}\bthrough publications?\b")
# The same bar in engineering words: the posting needs someone who has trained large models at scale (Cohere Modeling,
# Post-Training, Data Analysis, 2026-10-18). Fine-tuning a model, or deploying one at scale, is not this.
# "large-scale model training", "large-scale RL post-training"; not "deploying large-scale AI workflows ...; develop training
# materials" (a support role, 2026-10-18 census: training as teaching)
LARGE_SCALE_TRAINING = (r"\b(?:large[- ]scale|at scale)\s+(?:[\w-]+\s+){0,2}(?:pre-?|post-?)?training\b(?!\s+(?:materials?|sessions?|courses?))"
                        r"|\bdistributed training (?:strateg|infrastructures?\b)"
                        r"|\btraining (?:of )?(?:large|frontier|foundation) (?:language )?models? at scale\b"
                        r"|\bexperience (?:in |with |on )?training (?:large (?:language )?models?|llms|foundation models)\b")
# "seven (7) years", "Five years": a spelled number before the years becomes digits (Synthesia, 2026-10-14: read as 2)
NUM_WORDS = {w: str(i) for i, w in enumerate("one two three four five six seven eight nine ten eleven twelve".split(), 1)}
SPELLED_YEARS = re.compile(r"\b(" + "|".join(NUM_WORDS) + r")\s*(?:\(\s*\d{1,2}\s*\)\s*)?(?=\+?\s*(?:years?|yrs)\b)", re.I)
# the posting's own word for the level: "This is a senior, research-oriented role" (Nucs AI); "senior engineers" is not
SENIOR_ROLE = (r"\b(?:this|the)\s+(?:is\s+an?|role is(?:\s+an?)?|position is(?:\s+an?)?)\s+senior\b"
               r"|\bc['’]est un poste (?:de )?senior\b"
               # the hire named in the body (job-search audit 2026-10-17: 5 of 176 A rows, e.g. Bioptimus "We are looking for a
               # technical, execution-focused Senior Data Scientist", PMI "seeking a senior-level AI Engineer")
               r"|\b(?:looking for|seeking|hiring|recruiting|recherchons|recrutons)\s+(?:an?\s+|une?\s+)?(?:[\w-]+,?\s+){0,3}?senior\b"
               r"|(?<!at )\bsenior[- ]level\b|\b(?:poste|profil|renfort)\s+(?:de\s+)?(?:senior|confirm[ée]e?)\b")
# several levels in one posting stay open (Orbitant "mid and senior-level AI Engineers", LinkedIn's "Mid-Senior Level")
MULTI_LEVEL = r"\bmid(?:dle)?(?:[- ]|\s+(?:and|or|to)\s+)(?:senior|level)\b|\bjunior\b"
# The years as a field, the value on the line under its label: "Niveau d'expérience minimum" / "3 - 5 ans" (CA-GIP),
# "Niveau d'expérience min. requis" / "6 ans et plus" (Docaposte), 2026-09-29
EXPERIENCE_LABEL = (r"^\W*(?:niveau d['’]exp[ée]rience|ann[ée]es d['’]exp[ée]rience|exp[ée]rience|experience(?: level)?"
                    r"|years of experience)\b[^:]{0,30}:?\s*$")
YEARS_VALUE = (r"^\s*\d{1,2}\s*(?:(?:-|–|à|to)\s*\d{1,2}\s*)?(?:ans|ann[ée]es|years?)\b"
               r"\s*(?:et plus|ou plus|or more|minimum|min\.?|\+)?\s*$")


# Indeed's qualifications block: "Expérience:" then one line per field, "* finance de marchés: 3 ans (Requis)" or
# "* Python: 2 years (Required)" (UNIFY Technologies, 2026-10-08); the status in brackets keeps "(Souhaité)" a wish
FIELD_YEARS = re.compile(r"^\W*([^:]{1,60}):\s*(\d{1,2}\s*(?:ans?|ann[ée]es?|years?))\b\s*(\([^)]{1,20}\))?\s*$", I)


def join_fields(units: list[str]) -> list[str]:
    """An experience label and the years value on the next line become one unit, "label: 3 à 5 ans" (a dash between
    the numbers would be cut as a clause break). Under a bare "Expérience:" header, each "field: N ans (status)"
    line becomes "Expérience field: N ans (status)", so the experience word sits in the clause with the years."""
    out, in_block = [], False
    for s in units:
        field = FIELD_YEARS.search(s)
        if out and re.search(EXPERIENCE_LABEL, out[-1], I) and re.search(YEARS_VALUE, s, I):
            value = re.sub(r"\s[-–]\s", " à ", s)
            out[-1] = f"{out[-1]}: {value}"
        elif (in_block or (out and re.search(EXPERIENCE_LABEL, out[-1], I))) and field:
            in_block = True
            out.append(f"Expérience {field.group(1).strip()}: {field.group(2)} {field.group(3) or ''}".strip())
            continue
        else:
            out.append(s)
        in_block = False
    return out
NEGATED = r"\b(no|not|aucune?|pas|n'est pas|isn'?t|without|non)\b"
OTHER_LANGS = (r"(?:korean|finnish|german|dutch|spanish|italian|swedish|danish|norwegian|polish|portuguese|czech|japanese"
               r"|mandarin|chinese|hebrew|arabic|turkish|greek|hungarian|romanian|russian|vietnamese|hindi|estonian"
               r"|lithuanian|latvian|slovak|ukrainian)")
BLOCKERS = [
    (r"\b(french|eu|european|us|u\.s\.)\s+(citizen(?:s|ship)?|nationality|nationals?)\b", "nationality requirement"),
    (r"\bnationalit[ée] (fran[cç]aise|europ[ée]enne)", "nationality requirement"),
    (r"\bsecurity clearance\b|\bhabilitation\s+(?:secret|d[ée]fense|confidentiel)", "security clearance"),
    (r"\bZRR\b|zone [àa] r[ée]gime restrictif", "restricted research zone (ministry approval)"),
    # Another required language (English is assumed, French has its own check): "we require fluent
    # written and spoken Finnish skills" (Recordly, 2026-10-08). "German is a plus" stays a flag (the wish check below).
    (r"\b(?:fluent|native|requires?|required|must|mandatory|essential)\b[^.]{0,60}\b" + OTHER_LANGS + r"\b"
     r"|\b" + OTHER_LANGS + r"\b[^.]{0,40}\b(?:required|mandatory|essential|is a must)\b", "another language required"),
]


def check_seniority(units: list[str], title: str) -> dict:
    """Years asked = the largest minimum the posting states outside nice-to-have clauses: "4+ years
    of software development, 2+ with React" asks for four (live posting, 2026-09-27)."""
    years, in_nice, nice_units = [], False, set()
    units = [SPELLED_YEARS.sub(lambda m: NUM_WORDS[m.group(1).lower()] + " ", s) for s in join_fields(units)]
    for s in units:
        if re.search(SECTION_NICE, s, I):
            in_nice = True
            continue
        if re.search(SECTION_OTHER, s, I):
            in_nice = False
        if in_nice:
            nice_units.add(s)
        if in_nice or re.search(COMPANY_INTRO, s, I):
            continue
        if not (re.search(EXPERIENCE_CUE, s, I) or re.search(YEARS_ASKED, s, I)) or re.search(NICE_HEADER, s, I):
            continue
        if re.search(SPEAKS_FOR_COMPANY, s, I) and not (re.search(SPEAKS_TO_CANDIDATE, s, I) or re.search(WE_ASK, s, I)):
            continue                    # "With 15 years of experience in fintech, we serve 3M users."
        for c in clauses(s):
            # the experience word (or an ask) must sit in the clause with the years: "trusted us for 10 years; you bring experience"
            if (re.search(NICE_TO_HAVE, c, I) or re.search(COMPANY_HISTORY, c, I) or re.search(YEARS_OR_GRADUATE, c, I)
                    or (re.search(YEARS_OF_THINGS, c, I) and not re.search(EXPERIENCE_CUE, c, I))
                    or not (re.search(EXPERIENCE_CUE, c, I) or re.search(YEARS_ASKED, c, I))):
                continue
            for m in YEARS.finditer(c):
                n = int(m.group(1))
                # "less than 3 years", "up to 3 years", "3 ans maximum": a ceiling a junior meets, not an ask
                if re.search(UPPER_BEFORE, c[:m.start()], I) or re.search(UPPER_AFTER, c[m.end():], I):
                    continue
                if 0 < n <= 20:
                    years.append((n, s))
    min_years = max((n for n, _ in years), default=None)
    flags = []
    if title and re.search(SENIOR_TITLE, title, I):
        flags.append("senior title")
    phd = [s for s in units if re.search(PHD_REQUIRED, s, I) and re.search(r"required|must|mandatory|obligatoire|exig|requis", s, I)
           and not re.search(r"or equivalent|ou [ée]quivalent|preferred|plus|advantage|ideally|souhait|" + NEGATED, s, I)]
    phd += [s for s in units if re.search(PHD_LINE, s, I) and not re.search(MASTER_OK, s, I) and s not in nice_units
            and not re.search(NICE_TO_HAVE, s, I) and not re.search(EQUIVALENT, s, I) and s not in phd]
    if phd:
        flags.append("PhD required")
    record = [s for s in units if re.search(RESEARCH_RECORD, s, I) and s not in nice_units
              and not re.search(NICE_TO_HAVE, s, I) and not re.search(NEGATED, s, I)
              and not re.search(r"\bor (?:demonstrated|equivalent|proven)\b", s, I)]  # "...or demonstrated research potential" (Cohere)
    if record:
        flags.append("research track record required")
        phd += record[:1]
    scale = [s for s in units if re.search(LARGE_SCALE_TRAINING, s, I) and s not in nice_units
             and not re.search(NICE_TO_HAVE, s, I) and not re.search(NEGATED, s, I)]
    if scale:
        flags.append("large-scale model training required")
        phd += scale[:1]
    if any(re.search(SENIOR_ROLE, s, I) and not re.search(MULTI_LEVEL, s, I) for s in units):
        flags.append("the posting calls the role senior")
    blockers = []
    for s in units + ([title] if title else []):
        for pat, label in BLOCKERS:
            m = re.search(pat, s, I)
            if not m:
                continue
            # A negation counts only next to the blocker: "No security clearance required", "US citizenship
            # is not required". "French nationality required (not negotiable)" still blocks.
            neg_before = re.search(r"\b(?:no|not|aucune?|without|pas de|non)\s+(?:\w+\s+){0,2}$", s[:m.start()], I)
            neg_after = re.search(r"^\W*(?:is|are)?\s*(?:not|n't|non|pas)\s+(?:required|needed|necessary|requise?|obligatoire|a requirement)",
                                  s[m.end():], I)
            if neg_before or neg_after or re.search(r"\bor\b[^.]{0,30}\b(work permit|right to work|authori[sz]ation|titre de s[ée]jour)", s, I):
                continue                                # "EU citizen or a valid work permit" is not a nationality rule
            if label == "another language required" and re.search(
                    r"\bif (?:you are )?(?:applying|based|located|working) (?:for|in|from)\b|\bsi vous postulez\b", s, I):
                if f"{label} (one location)" not in flags:  # "If applying for Germany ... fluent German" (Helsing)
                    flags.append(f"{label} (one location)")
                continue
            if re.search(r"souhait|preferred|\bplus\b|appr[ée]ci|ideally|advantage", s, I):
                if f"{label} (preferred)" not in flags:     # "Nationalité française souhaitée": a wish, not a gate
                    flags.append(f"{label} (preferred)")
            elif label not in [b["label"] for b in blockers]:
                blockers.append({"label": label, "evidence": s})
    return {"min_years": min_years, "flags": flags,
            "evidence": [s for _, s in years][:2] + phd[:1], "blockers": blockers}


# ---------------------------------------------------------------- tier

# a careers page that answers 200 with "this job is filled" (Phenom boards, 2026-10-21) is a closed posting
CLOSED = re.compile(r"has been filled|is no longer (?:available|open|accepting)|no longer accepting applications|"
                    r"position (?:has been|is) closed|job (?:posting )?(?:has )?expired|a [ée]t[ée] pourvu|n'est plus (?:disponible|ouvert)|"
                    r"offre (?:a expir[ée]|n'est plus)", I)
# Requirements a candidate without a doctorate or a research record cannot meet; they are blockers, not flags.
OUT_OF_REACH = ("PhD required", "research track record required", "large-scale model training required")


def suggest_tier(r: dict, years_cap: int | None = None) -> dict:
    c, b = [], []
    if r.get("closed"):
        c.append("posting closed: " + r["closed"][:80])
    if r["french"]["verdict"] == "required":
        c.append("French required")
    if r["remote"]["france_eligible"] == "no":
        c.append("remote role not open to someone living in France" if r["remote"]["mode"] == "remote"
                 else "job located outside France, or its residence rule excludes France")
    if r["contract"]["type"] in ("internship", "apprenticeship", "phd", "vie"):
        c.append(f"contract is {r['contract']['type']}")
    c += [x["label"] for x in r["seniority"]["blockers"]]
    c += [f for f in r["seniority"]["flags"] if f in OUT_OF_REACH]
    years = r["seniority"]["min_years"]
    if years is not None and years_cap is not None and years > years_cap:
        b.append(f"{years}+ years asked (cap {years_cap})")
    b += [f for f in r["seniority"]["flags"] if f not in OUT_OF_REACH]
    salary = r["salary"]["vs_floor"]
    if salary == "below":
        b.append("salary below the floor")
    elif salary == "other_currency":
        b.append("salary in another currency: convert at today's rate and compare with the floor")
    elif salary == "daily_rate":
        b.append("daily rate: a freelance mission, not a salary")
    if r["contract"]["type"] == "cdd" and (r["contract"]["months"] or 0) < 12:
        b.append("CDD under 12 months or length not stated")
    if r["contract"]["type"] == "freelance":
        b.append("freelance: check that your status allows freelance work")
    if r["french"]["verdict"] == "unclear":
        b.append("French requirement unclear")
    if r["remote"]["france_eligible"] == "unclear":
        b.append("remote eligibility unclear" if r["remote"]["mode"] == "remote" else "place of work unclear")
    fr_base = r["french"]["posting_language"] == "fr" and r["french"]["verdict"] in ("not_mentioned", "unclear")
    if fr_base:
        # Only a stated French requirement is out. A French-written posting stays B even with
        # no English signal, since such teams may still work or interview in English ;
        # the two same-day rules that sent these to C were withdrawn. The signals stay in the reason to read.
        sig = ("team works in English" if r["french"].get("english_workplace") else
               "international team" if r["french"].get("international_team") else
               "English asked as a skill" if r["french"].get("english_skill") else "no English signal")
        b.append(f"posting written in French ({sig})")
    tier = "C" if c else ("B" if b else "A")
    return {"tier": tier, "why": c + b or ["no blocker found; check the facts against the posting"]}


def check(text: str, title: str = "", location: str = "", salary_floor: float | None = None,
          remote_board: bool = False, years_cap: int | None = None) -> dict:
    # Markdown emphasis inside a phrase hides it: "Expérience de *5* *ans minimum*" (lumotech on Indeed, 2026-09-29)
    text = re.sub(r"\*+", "", text)
    units = sentences(text)
    r = {
        "french": check_french(units, text),
        "remote": check_remote(units, location, text, remote_board),
        "contract": check_contract(units, title),
        "salary": check_salary(units, salary_floor),
        "seniority": check_seniority(units, title),
    }
    r["french"]["english_skill"] = [s for s in units if re.search(ENGLISH_SKILL, s, re.I)][:2]
    r["french"]["english_workplace"] = [s for s in units if re.search(ENGLISH_WORKPLACE, s, re.I)][:2]
    r["french"]["international_team"] = [s for s in units if re.search(INTERNATIONAL_TEAM, s, re.I)][:2]
    r["closed"] = next((s for s in units[:40] if CLOSED.search(s)), "")
    r["suggested"] = suggest_tier(r, years_cap)
    return r


# ---------------------------------------------------------------- self-test

SELFTEST = [  # (text, field, expected) from real posting lines; the full corpus is in tests/
    ("Français courant", "french", "required"),
    ("You are fluent in English (no French required)", "french", "not_required"),
    ("French is a plus", "french", "preferred"),
    ("Fluency in French is a significant advantage", "french", "preferred"),
    ("Langues : francais ou anglais", "french", "not_required"),
    ("Un anglais professionnel, à l'écrit comme à l'oral. Le français est un confort, pas une obligation.", "french", "not_required"),
    ("Bon niveau d'anglais et de français", "french", "required"),
    ("We are a French company building AI for insurers.", "french", "not_mentioned"),
    ("CDI, Paris, 45 000 - 55 000 € brut annuel", "contract", "cdi"),
    ("Stage de fin d'études de 6 mois", "contract", "internship"),
]


def selftest() -> int:
    bad = 0
    for text, field, want in SELFTEST:
        r = check(text)
        got = r["french"]["verdict"] if field == "french" else r["contract"]["type"]
        ok = got == want
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {field:<9} want {want:<13} got {got:<13} {text}")
    s = check("CDI, Paris, 45 000 - 55 000 € brut annuel", salary_floor=40000)["salary"]
    ok = (s["min_eur_year"], s["max_eur_year"], s["vs_floor"]) == (45000, 55000, "above")
    bad += not ok
    print(f"{'ok  ' if ok else 'FAIL'} salary    want 45000-55000 above   got {s['min_eur_year']}-{s['max_eur_year']} {s['vs_floor']}")
    print(f"{len(SELFTEST) + 1 - bad}/{len(SELFTEST) + 1} pass")
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):       # a cp949 console must not crash on a French posting
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("posting", nargs="?", help="text file with the posting body ('-' reads stdin)")
    ap.add_argument("--title", default="")
    ap.add_argument("--location", default="", help="the posting's location field, when it has one")
    ap.add_argument("--salary-floor", type=float, default=None, help="your minimum, yearly gross EUR (default: no comparison)")
    ap.add_argument("--years-cap", type=int, default=None,
                    help="flag a posting that asks more than this many years of experience (default: no flag)")
    ap.add_argument("--remote-board", action="store_true", help="the posting came from a remote-only board")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.posting:
        ap.error("give a posting file, '-' for stdin, or --selftest")
    text = sys.stdin.buffer.read().decode("utf-8", errors="replace") if a.posting == "-" else Path(a.posting).read_text(encoding="utf-8")
    if len(text.strip()) < 200:
        # an empty scratch file from a failed fetch or a JavaScript-only page must not pass as tier A
        print(json.dumps({"suggested": {"tier": "unknown", "why": [f"posting text is {len(text.strip())} characters: re-fetch the full posting"]}}))
        return 2
    print(json.dumps(check(text, a.title, a.location, a.salary_floor, a.remote_board, a.years_cap), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
