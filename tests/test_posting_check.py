"""Prove the posting checker on real posting lines, and keep the salary floor and years cap inputs.

    python tests/test_posting_check.py

French cases come from three places: the 81 phrases of the retired Dec 2025 job-search filter
(each relabelled: a French degree is not a language requirement), the language lines
quoted in a list of Paris AI employers (2026-09), and the three review sentences the old keyword rule
got wrong. Remote cases are candidate-location lines from live remote-board postings (2026-09-27).
A rule change that breaks a line here is a regression, not a matter of taste: fix the rule, or
change the label with the reason in the commit.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
import posting_check as pc  # noqa: E402

R, P, N, U, M = "required", "preferred", "not_required", "unclear", "not_mentioned"

# The retired filter's phrase list, in its order. Labels are what the phrase means for the language check.
OLD_FILTER = [
    ("Français requis", R), ("Français obligatoire", R), ("Français exigé", R), ("Français indispensable", R),
    ("Maîtrise du français", R), ("Maîtrise du français exigée", R), ("Maîtrise du français requise", R),
    ("Maîtrise du français obligatoire", R), ("Parfaite maîtrise du français", R), ("Excellente maîtrise du français", R),
    ("French required", R), ("French mandatory", R), ("French essential", R), ("French compulsory", R),
    ("Fluent French required", R), ("Native French speaker", R), ("French proficiency required", R),
    ("Must speak French", R), ("French language required", R), ("Bilingual French required", R),
    ("Niveau C2 français", R), ("Niveau C1 français", R), ("Niveau B2 français", R),
    ("Niveau B2 minimum", M),  # no language named; the line alone cannot say which
    ("DELF B2", R), ("DELF C1", R), ("DALF C1", R), ("DALF C2", R), ("TCF niveau 4", R), ("TCF niveau 5", R),
    ("French C1 level", R), ("French C2 level", R), ("French B2 minimum", R), ("Advanced French (C1/C2)", R),
    ("Proficient in French (B2+)", R),
    ("Langue de travail: français", R), ("Langue de travail : français", R), ("Working language: French", R),
    ("La langue de travail est le français", R), ("Vous travaillerez en français", R),
    ("L'équipe travaille en français", R), ("Communication en français", R), ("Français langue de travail", R),
    ("French as working language", R), ("Daily work in French", R), ("Team works in French", R),
    ("Team communicates in French", R), ("Communication avec clients français", R), ("Relation client en français", R),
    ("Présentation en français", R), ("Réunion en français", R), ("Réunions en français", R),
    ("Interaction avec équipe francophone", R), ("French-speaking clients", R), ("French client communication", R),
    ("French meetings", R), ("French presentations required", R), ("Rédaction en français", R),
    ("Documentation en français", R), ("Rapport en français", R), ("Rapports en français", R),
    ("Compte-rendu en français", R), ("French writing skills", R), ("French documentation required", R),
    ("Write reports in French", R),
    # a French qualification, not the language: out of scope for the language check
    ("Diplôme français requis", M), ("Formation en France", M), ("Université française", M), ("Bac+5 en France", M),
    ("École d'ingénieur française", M), ("French degree required", M),
    ("Francophone", R), ("Candidat francophone", R), ("Profil francophone", R), ("French native", R),
    ("Native speaker (French)", R), ("Langue maternelle française", R),
    ("Nationalité française souhaitée", M),  # a nationality wish, reported as a flag, not a language rule
    ("Formation française souhaitée", M), ("Expérience en France requise", M), ("Diplôme français apprécié", M),
]

# Language lines quoted from Paris AI employers' postings, 2026-09-26
CAMPAIGN = [
    ("Advanced command of French … equivalent to a native speaker", R),
    ("Anglais (niveau C1 attendu)", M),
    ("Bon niveau d'anglais et de français", R),
    ("English — fluent in speaking and writing — mandatory", M),
    ("Fluency in English and French and a deep understanding of the regional and cultural nuances is required", R),
    ("Fluency in French and English", R),
    ("Fluency in French is a significant advantage", P),
    ("Fluent in English", M),
    ("Fluent written English for asynchronous collaboration", M),
    ("Fluent written and verbal communication in French and English", R),
    ("Français courant", R),
    ("French (Reading understanding to interact with the data)", U),
    ("French is a plus", P),
    ("French isn't required", N),
    ("Great communication skills (in both French and English)", R),
    ("International English-speaking team", M),
    ("La maîtrise de la langue française et anglaise", R),
    ("Langues : francais ou anglais", N),
    ("Maîtrise du français et de l'anglais", R),
    ("NB: Please send us a resumé in English", M),
    ("Proficiency in French, as you will be working with legal data in French", R),
    ("Strong written & oral communication skills in English", M),
    ("Vous avez un très bon niveau d'anglais écrit et oral", M),
    ("Vous maîtrisez parfaitement l'anglais", M),
    ("You are fluent in English", M),
    ("You are fluent in English (no French required)", N),
    ("please submit your CV in English", M),
    ("the Shift working language is English", M),
]

# The review sentences the old keyword rule got wrong, plus lines that mention France but not the language
REVIEW = [
    ("no French required", N),
    ("Fluent French is a plus but not required", N),
    ("Non-francophone candidates welcome", N),
    ("We are a French company building AI for insurers.", M),
    ("Our clients are French and European banks.", M),
    ("Join the French Tech ecosystem in Paris.", M),
]

CONTRACT = [
    ("CDI, Paris, 45 000 - 55 000 € brut annuel", "", "cdi"),
    ("Contrat : CDD de 18 mois", "", "cdd"),
    ("Stage de fin d'études de 6 mois", "", "internship"),
    ("Poste en alternance (rythme 3 semaines / 1 semaine)", "", "apprenticeship"),
    ("This is a permanent position based in Paris.", "", "cdi"),
    ("You will work with external contractors and our data team.", "", "unknown"),
    # freelance named as a source of the candidate's experience (NewRocket graduate role, 2026-10-21)
    ("Experience: 1–3 years of relevant experience through internships, academic projects, research, freelance work, or professional roles", "", "unknown"),
    ("Freelance experience counts.", "", "unknown"),
    ("Freelance mission de 6 mois, TJM selon profil", "", "freelance"),
    ("Join our research team.", "Doctoral student (CIFRE) - LLM evaluation", "phd"),
    # live postings, 2026-09-27: the English word "these" is not a French thesis
    ("These experts contribute directly to the development of advanced AI systems.", "", "unknown"),
    ("Role Type: Contractor", "", "freelance"),
    ("Contrat doctoral : thèse CIFRE de 36 mois", "", "phd"),
    # Himalayas 2026-10-04: AI-training gigs labelled "Contract" were rated A (Xperteez, Invisible Technologies)
    ("Role Type: Contract\nLocation: Remote", "", "freelance"),
    ("Employment type: Contract\nWorkplace type: Remote", "", "freelance"),
    ("Job Type: Contract (~15 hours per week)", "", "freelance"),
    ("Help train next-generation AI systems.", "Machine Learning Engineer | Remote - Contract", "freelance"),
    ("Help train next-generation AI systems.", "Computer Vision Specialist, Remote- Contract", "freelance"),
    ("Help train next-generation AI systems.", "Data Scientist (Contract)", "freelance"),
    ("Contract, 6 months, fully remote.", "", "freelance"),
    ("Compensation is output-based; experts are paid per task that meets the project specifications.", "", "freelance"),
    # "contract" that is not the offer's type
    ("We partner with a contract research organization (CRO) for trials.", "", "unknown"),
    ("You will audit smart contract code on Ethereum.", "", "unknown"),
    ("Type de contrat : employment contract (CDI).", "", "cdi"),
    ("This is a permanent contract based in Paris.", "", "cdi"),
    ("We value long-term contract partnerships with our clients.", "Smart Contract Engineer", "unknown"),
]

YEARS = [  # (text, years asked) — the largest minimum outside nice-to-have lines
    ("4+ years of software development experience.\n2+ years of experience with React and 4+ years of experience with React Native", 4),
    ("2–5 years of industry experience in Machine Learning Engineering", 2),
    ("3+ years of experience with Python.\n5+ years of experience in ML is a plus.", 3),
    ("Master's or PhD in Computer Science (or equivalent proven track record)", None),
]

SALARY = [  # (text, floor, (min, max, verdict))
    ("CDI, Paris, 45 000 - 55 000 € brut annuel", 42000, (45000, 55000, "above")),
    ("From 2692 € gross monthly", 42000, (32304, 32304, "below")),
    ("Salary: 35k€ - 45k€ depending on experience", 42000, (35000, 45000, "straddles")),
    ("Compensation: €60,000 - €75,000 per year plus equity", 42000, (60000, 75000, "above")),
]

REMOTE = [  # (line, expected france_eligible, field or text, where it was found on 2026-09-27)
    ('Worldwide', 'yes', 'field', 'https://remotive.com/remote-jobs/software-development/senior-shopify-developer-2091140'),
    ('Northern America, LATAM, Europe, APAC', 'yes', 'field', 'https://remotive.com/remote-jobs/artificial-intelligence/senior-ai-engineer-2091131'),
    ('France, Japan, Turkey, Vietnam, Mexico, Norway', 'yes', 'field', 'https://remotive.com/remote-jobs/artificial-intelligence/ai-response-evaluator-2091126'),
    ('You must be located in the Americas, Europe, or Israel to apply.', 'yes', 'text', 'https://remotive.com/remote-jobs/software-development/senior-independent-software-developer-1919265'),
    ('To keep collaboration smooth, we primarily hire within European time zones (UTC to UTC+3).', 'yes', 'text', 'https://www.arbeitnow.fr/jobs/companies/homa-games/remote-lead-product-manager-rpg-paris-453503'),
    ('Time zone overlap with ~CET is what matters.', 'yes', 'text', 'https://www.arbeitnow.ch/jobs/companies/hirehire/remote-ux-ui-designer-intch-6052'),
    ('We welcome applications from candidates worldwide, except residents of the United States.', 'yes', 'text', 'https://www.workingnomads.com/job/go/1857010/'),
    ('Located in CET timezone (+/- 3 hours), we are unable to consider applications from candidates in other time zones.', 'yes', 'text', 'https://www.workingnomads.com/job/go/1843255/'),
    ('Required residency/location within EMEA time zones (we do not believe in the sustainability of overnight shifts).', 'yes', 'text', 'https://jobicy.com/jobs/154079-database-support-engineer-emea'),
    ("This is a remote position and we're open to considering candidates located across EMEA and AMER time zones.", 'yes', 'text', 'https://jobicy.com/jobs/154085-product-manager-security-trust-emea-amer'),
    ('Please note: We are currently accepting applications only from candidates based in North America, South America or Europe.', 'yes', 'text', 'https://jobs.ashbyhq.com/clipboard/1bde9c0f-0ca8-4cd6-9f88-49b3384861b1'),
    ('Internationally located (not in the US, CA, UK, NZ, or AU)', 'yes', 'field', 'https://www.workingnomads.com/job/go/1864884/'),
    ('Remote (Canada, UK, EU)', 'yes', 'field', 'https://jobs.ashbyhq.com/clipboard/76a2e3cc-4426-4492-890d-370f1bf8bc69'),
    ('Remote (Non-U.S.)', 'yes', 'field', 'https://jobs.ashbyhq.com/clipboard/0198b63c-1d88-4763-950a-ded21ad00727'),
    ('Remote anywhere', 'yes', 'field', 'https://arc.dev/remote-jobs/details/senior-engineer-pmmdx1z71m'),
    ('Remote format — you can work from anywhere in the world.', 'yes', 'text', 'https://remoteOK.com/remote-jobs/remote-junior-crypto-analyst-trader-empire-assets-1137307'),
    ('EU, US, Canada, UK, Australia, Singapore, Ireland, Norway, Switzerland, Iceland, and Mexico', 'yes', 'field', 'https://www.workingnomads.com/job/go/1885527/'),
    ('Europe', 'yes', 'field', 'https://remotive.com/remote-jobs/customer-service/kundenservice-mobilfunk-inbound-innerhalb-der-eu-ausgenommen-deutschland-2091139'),
    ('Remote, USA Only', 'no', 'field', 'https://www.workingnomads.com/job/go/1891955/'),
    ('USA, Canada or UK only', 'no', 'field', 'https://www.workingnomads.com/job/go/1844412/'),
    ('Must have resided in the United States for the past three consecutive years', 'no', 'text', 'https://remotive.com/remote-jobs/all-others/content-reviewer-united-states-2091144'),
    ('You must be located in and authorized to work in Mexico to be considered for this role.', 'no', 'text', 'https://remoteOK.com/remote-jobs/remote-customer-success-manager-mexico-jumpcloud-1137406'),
    ('PLEASE NOTE: At this time, we are only able to hire candidates who are legally eligible to work in Canada and reside within Canada.', 'no', 'text', 'https://remoteOK.com/remote-jobs/remote-senior-level-designer-blackbird-interactive-1137156'),
    ('This is a remote position that can be located anywhere in the US or Canada.', 'no', 'text', 'https://remoteOK.com/remote-jobs/remote-consultant-implementation-engineer-harbor-1136987'),
    ('Candidates must be authorized to work in the United States without current or future employer sponsorship.', 'no', 'text', 'https://remoteOK.com/remote-jobs/remote-senior-software-engineer-case-execution-pivotal-health-1136795'),
    ('This is a remote role, for people based in either the UK or Canada (Atlantic Time Zone only).', 'no', 'text', 'https://remoteOK.com/remote-jobs/remote-customer-support-specialist-resource-guru-1136066'),
    ('Work from anywhere in the Americas.', 'no', 'text', 'https://remoteOK.com/remote-jobs/remote-head-of-security-tremendous-1136210'),
    ('This role is fully remote; however, we’re only open to candidates based in the US and Canada.', 'no', 'text', 'https://jobicy.com/jobs/154078-staff-platform-security-engineer-security'),
    ('Must reside in the following states to be considered for a Seasonal Role:', 'no', 'text', 'https://jobs.lever.co/minted/db75d7f3-1991-4515-9a4b-9b4f772f6b04'),
    ('The position is remote, but you must reside within 50 miles of Winter Haven, FL.', 'no', 'text', 'https://www.linkedin.com/jobs/view/4302909580'),
    ('Resident in Spain for the last 5 consecutive years.', 'no', 'text', 'https://www.workingnomads.com/job/go/1873858/'),
    ('Required residence in North or South American time zones (we do not believe in the sustainability of overnight shifts).', 'no', 'text', 'https://jobicy.com/jobs/154076-database-support-engineer-amer'),
    ('Remote-first (±3 hours from ET) with hybrid in office collaboration in Toronto or NYC', 'no', 'text', 'https://remotive.com/remote-jobs/software-development/tech-lead-full-stack-rails-engineer-2069746'),
    ('This is a U.S.-based remote role. Due to current regulatory requirements, we are only able to consider candidates residing in certain U.S. states.', 'no', 'text', 'https://remoteOK.com/remote-jobs/remote-marketing-specialist-innergy-1136094'),
    ('Residence within the Asia-Pacific (APAC) region.', 'no', 'text', 'https://jobs.ashbyhq.com/clipboard/82df7116-0720-4434-a330-ca7e0389dc60'),
    ('Employment eligible to employees located anywhere in the continental US.', 'no', 'text', 'https://jobicy.com/jobs/151706-staff-backend-engineer'),
    ('U.S. (Remote)', 'no', 'field', 'https://jobs.ashbyhq.com/clipboard/64ac2f12-3cfc-4a7e-bb0d-1c97ffcb83a5'),
    ('LATAM', 'no', 'field', 'https://remoteOK.com/remote-jobs/remote-p2p-bd-assistant-bybit-1136568'),
    ('Denmark,  Netherlands,  Norway,  UK', 'no', 'field', 'https://jobicy.com/jobs/149963-business-consultant'),
    ('Must be a current resident of the Philippines', 'no', 'text', 'https://jobicy.com/jobs/152598-junior-web-builder-fully-remote-ph-based-only-fresh-grads-welcome-to-apply'),
    ('Remote', 'unclear', 'field', 'https://remoteOK.com/remote-jobs/remote-sr-solutions-architect-extrahop-1137394'),
    ('Remoto', 'unclear', 'field', 'https://remoteOK.com/remote-jobs/remote-software-engineer-ii-golang-stone-1136569'),
    ('Homeoffice', 'unclear', 'field', 'https://www.arbeitnow.com/jobs/companies/virtual7-gmbh/remote-senior-java-backend-developer-karlsruhe-304395'),
    ('Global', 'unclear', 'field', 'https://www.workingnomads.com/job/go/1833624/'),
    ('Remote & flexible work: Work from almost anywhere*.', 'unclear', 'text', 'https://remoteOK.com/remote-jobs/remote-senior-people-talent-operations-partner-testgorilla-1137378'),
    ('*Check out the countries we currently hire from', 'unclear', 'text', 'https://remoteOK.com/remote-jobs/remote-senior-people-talent-operations-partner-testgorilla-1137378'),
    ('Permanent employment contract in your country of residence through our partner Deel (EOR)', 'unclear', 'text', 'https://himalayas.app/companies/powerprozesse/jobs/fullstack-developer-100-remote'),
    ("That said, if you live within 30 miles of one of our office locations, you'll be on a hybrid schedule with some in-person time, because we know there's real value in coming together.", 'unclear', 'text', 'https://jobs.lever.co/verygoodsecurity/698ec89b-8d3b-40b0-bced-0ae6e0a50ac9'),
    ('Candidates outside of the 50-mile radius are eligible for fully remote work.', 'unclear', 'text', 'https://jobs.lever.co/mhnchicago/f9c8ad64-8972-432d-a922-46a5fc31d0f8'),
    ('We hire globally and work across multiple countries and time zones.', 'unclear', 'text', 'https://remoteOK.com/remote-jobs/remote-technical-product-manager-ai-stockbroking-app-bjak-1137421'),
    ('GitLab hires new team members in countries around the world.', 'unclear', 'text', 'https://jobicy.com/jobs/151576-customer-success-manager-22'),
    ("To join Webflow, you'll need a valid right to work authorization depending on the country of employment.", 'unclear', 'text', 'https://jobicy.com/jobs/151583-paid-social-specialist-contract-basis'),
    ('Fully remote from 40+ countries and collaborate using Asana and Slack.', 'unclear', 'text', 'https://www.workingnomads.com/job/go/1833624/'),
    ('We hire globally. We believe you can do your best work from anywhere.', 'unclear', 'text', 'https://jobicy.com/jobs/154081-database-support-engineer-apac'),
    ('Work from Anywhere: apply to work in another location for up to 3 months per calendar year.', 'unclear', 'text', 'https://jobicy.com/jobs/151753-senior-reviewer-code-web-templates'),
    ('You can also work fully remote from anywhere in the world for 1 month each year', 'unclear', 'text', 'https://remoteOK.com/remote-jobs/remote-junior-payroll-assistant-sleek-1137391'),
    ('Work from anywhere options*', 'unclear', 'text', 'https://jobicy.com/jobs/151686-director-paid-social'),
    ('*Please note, if based in the US or Canada this is a full time employee position.', 'unclear', 'text', 'https://remotive.com/remote-jobs/design/frontend-web-application-developer-2091141'),
    ('Resident in France for the last 5 consecutive years.', 'unclear', 'text', 'https://www.workingnomads.com/job/go/1873836/'),
    ('Work from anywhere in the world.', 'unclear', 'text', 'https://jobs.ashbyhq.com/clipboard/64ac2f12-3cfc-4a7e-bb0d-1c97ffcb83a5'),
]

# Lines an adversarial review broke the checker with on 2026-09-27, with the right answer.
REVIEW_FRENCH = [
    ("Excellent written and spoken English; French is not a must.", N),
    ("Maîtrise du français non requise", N),
    ("La maîtrise parfaite du français n’est pas indispensable à votre arrivée, dès lors que la communication est fluide.", N),  # Aneo
    ("You speak English fluently; knowing French would be nice.", P),
    ("Our team speaks French, English, and Spanish.", U),
    ("Fluent French required, a PhD is not required.", R),
    ("Français courant exigé, diplôme d'ingénieur non requis mais un permis de conduire n'est pas nécessaire.", R),
    ("French mandatory, Spanish is a plus.", R),
    ("Français courant exigé, l'allemand serait un plus.", R),
    ("Français courant et plus de 3 ans d'expérience requis.", R),
    ("Connaissance du français requise", R),
    ("la connaissance du français n'est pas requise", N),
    ("Language lessons in English and French", M),
    ("Cours de français et d'anglais offerts", M),
    ("French isn't required.\nLanguage lessons in English and French\nInternational English-speaking team", N),
]
REVIEW_CONTRACT = [
    # job-search audit 2026-10-17 (CHK-3): the candidate's past internship or a thesis alternative is not the offer
    ("This includes school projects and previous internship/work experience.", "unknown"),               # Skypher
    ("Nice to have: An internship or thesis in NLP.", "unknown"),                                          # OVH
    ("Une première expérience (en stage ou alternance ou poste) en data science.", "unknown"),            # Valeo
    ("Stage de 6 mois en data science à partir de février.", "internship"),
    ("CDI. Vous concevrez des modèles d'apprentissage automatique.", "cdi"),
    ("CDI - Ingénieur Machine Learning. Expertise en apprentissage profond.", "cdi"),
    ("CDI. Bon équilibre vie pro / vie perso.", "cdi"),
    ("Type de contrat : CDI. Qualité de vie au travail.", "cdi"),
    ("Join our early-stage startup as a permanent employee.", "cdi"),
    ("Permanent contract. You will own the model from prototype to production stage.", "cdi"),
    ("CDI. You will supervise one intern per semester.", "cdi"),
    ("Permanent contract. Our internship program is also open.", "cdi"),
    ("CDI. Titulaire d'une thèse en informatique.", "cdi"),
    ("Mission VIE de 12 mois à Singapour.", "vie"),
    # a CDI mentioned as the prospect after an internship does not make the offer a CDI
    ("Stage de fin d'études de 6 mois, possibilité de CDI à l'issue.", "internship"),
    # Novartis "STG - AI SOLUTIONS & AUTOMATION ENGINEER", Paris (read 2026-09-29): the offer line names a 6-month stage
    # and the employment-type field says "Fixed Term", which is what an internship is
    ("Vous recherchez un stage de 6 mois\nEarly Career (Fixed Term)", "internship"),
    # a fixed-term job that names an internship in passing stays a fixed-term job
    ("12-month fixed-term contract. Previous internship experience is a plus.", "cdd"),
    ("CDD de 12 mois. Un stage de 6 mois dans la data est un plus.", "cdd"),
]
# The contract's length given as a field, the value on the next line: the CNRS job portal (UMR8187 "Ingénieur·e d'études
# en IA et télédétection satellitaire", read 2026-09-29) was rated "length not stated" for a 12-month CDD
REVIEW4_MONTHS = [  # (text, type, months)
    ("IT en contrat CDD\n12 mois\nBAC+5", "cdd", 12),
    ("Type de Contrat\nIT en contrat CDD\nTemps de Travail\nComplet\nDurée du contrat\n12 mois", "cdd", 12),
    ("Fixed-term contract.\nContract length: 18 months", "cdd", 18),
    # months in an ordinary sentence are not the contract's length
    ("CDD.\nVous aurez 6 mois pour prendre vos marques.", "cdd", None),
    # a length in years (Inria "Durée de contrat : 3 ans", 2026-09-29), and years of experience that are not a length
    ("Type de contrat : CDD\nDurée de contrat : 3 ans", "cdd", 36),
    ("CDD de 2 ans renouvelable.", "cdd", 24),
    ("IT en contrat CDD\nDurée du contrat\n2 ans", "cdd", 24),                  # the CNRS layout with a length in years
    ("CDD avec 3 ans d'expérience requis.", "cdd", None),                      # one sentence: the guard is reached
    ("Fixed-term contract, 2 years of experience required.", "cdd", None),
    # ADAY: a CIFRE thesis named as the alternative to experience is the candidate's past, not the offer
    ("Première expérience de 2 ou 3 ans dans un poste similaire, ou une thèse CIFRE, avec une exposition aux contraintes "
     "du déploiement on-premise.", "unknown", None),
    ("Nous proposons une thèse CIFRE de 3 ans au sein de notre équipe.", "phd", 36),         # a CIFRE offer still is one
    ("Vous êtes titulaire d'une thèse de doctorat en informatique.", "unknown", None),       # a doctorate held, not offered
    ("Contrat doctoral de 3 ans à l'université Paris-Saclay.", "phd", 36),
    # years beside a contract word without framing it are the experience asked, not the length (ADAY's shape)
    ("CDD avec une première expérience de 2 ou 3 ans dans un poste similaire.", "cdd", None),
    ("Type de contrat : CDD\nDurée : 2 ans", "cdd", 24),
]
# Indeed postings read 2026-09-29 (the first Indeed sweep): lines the checker misread, with the controls each rule
# must leave alone.
INDEED_FRENCH = [
    # TotalEnergies "CDI - Quantitative Machine Learning Engineer": one verb for two languages, cut at "et le"
    ("Vous maîtrisez l'anglais et le français (oral et écrit).", R),
    ("Vous maîtrisez l'anglais et le français serait un plus.", P),
    ("Vous maîtrisez Python et le français est apprécié.", P),
    # the clause before names no other language: its "requises" is about Python, not French
    ("Des connaissances en Python sont requises et le français est la langue de l'équipe.", U),
]
INDEED_YEARS = [
    ("Niveau d'expérience minimum\n3 - 5 ans", 3),                  # Crédit Agricole CA-GIP: value under its label
    ("Niveau d'expérience min. requis\n\n6 ans et plus", 6),        # Docaposte
    ("Expérience confirmée en IA générative / Machine Learning (6 ans et plus)", 6),   # "et plus" is not "un plus"
    ("Expérience de *5* *ans minimum* sur un poste similaire, avec une forte composante IA / Data Science ;", 5),  # lumotech
    ("Une expérience de 2 ans en MLOps est un plus.", None),        # still a nice-to-have
    # ADAY "Data Scientist NLP": "2 ou 3 ans" is two or three years, read as 3 (senior) before
    ("Première expérience de 2 ou 3 ans dans un poste similaire, ou une thèse CIFRE, avec une exposition aux contraintes "
     "du déploiement on-premise.", 2),
    ("2 or 3 years of experience in machine learning.", 2),
    ("Niveau d'études minimum\nBac + 5 / M2 et plus", None),        # a degree under its label, not years
    ("Notre histoire\n20 ans", None),                               # a value under a label that is not experience
    # Indeed's own qualifications block: a field and its years under "Expérience:" (UNIFY Technologies, 2026-10-08,
    # read as no years before)
    ("Expérience:\n* finance de marchés: 3 ans (Requis)\n* Développement web: 3 ans (Requis)\n\nLangue:\n* Anglais (Requis)", 3),
    ("Experience:\n* Python: 2 years (Required)", 2),
    ("Expérience:\n* Machine learning: 4 ans (Souhaité)", None),    # wished, not asked
    ("Expérience:\n* IA: 1 an (Optionnel)", None),                  # optional, and under the two-year line anyway
    ("Formation:\n* Bac +5 (Master / MBA) (Requis)", None),         # a degree block, no years
    # 2026-10-08 sweep, three A rows that were senior: a range or "or more" is an ask with no experience word
    ("What We're Looking For 3 or more years building production machine learning or AI systems, including recent "
     "hands-on experience shipping LLM products or agents.", 3),    # Clera
    ("⌘ Required experience\n4–7 years in a data scientist / ML engineer role, with at least one model you personally "
     "took from prototype to live production.", 4),                 # Timeleft: the label does not open with "experience"
    ("APTITUDE - 2-5 years in revenue/sales operations, consulting, VC, or a comparable analytical role", 2),  # Dust
    ("3 or more years of experience in Python.", 3),
    ("Our founders have 10-15 years of history in fintech.", None),  # the company's past, not an ask
    ("We were founded 10-15 years ago in Paris.", None),
    # Spanish years, 2026-10-08 (Logicalis Spain, read as none)
    ("- 4+ años de experiencia en desarrollo de software o ingeniería de datos/ML", 4),
    ("Al menos 3 años de experiencia con Python.", 3),
]
INDEED_SALARY = [  # (text, vs_floor at a 42,000 floor)
    ("We’ve recently raised our Series A funding round, taking our total funding to $70M.", "not_stated"),  # White Circle
    ("Compensation: $250K – $600K • Offers Equity", "other_currency"),                                     # Medal
    ("Salary: $120,000 - $150,000 per year", "other_currency"),
    # Jobgether "AI Process FDE" (2026-10-02): a benefit budget in dollars read as the salary; the euro path
    # already skipped benefit words, the other-currency path did not.
    ("Annual learning and development budget of up to $/€/£1,000 for courses, certifications, books, and other "
     "self-directed learning.", "not_stated"),
    ("Live Well Lifestyle Spending Account supporting health, family, personal interests, experiences, and financial "
     "wellbeing, launching in 2026 and scaling to €1,000 annually from 2027.", "not_stated"),
    ("Relocation package of $5,000.", "not_stated"),
    ("Salary: $120k - $150k + equity", "other_currency"),
]
INDEED_FLAGS = [  # (text, flag, expected present)
    ("What You Bring\nPhD in machine learning, computer vision, medical image analysis, biomedical engineering, or related field",
     "PhD required", True),                                                                                 # Nucs AI
    ("What You Bring\nPhD or MSc in Computer Science with a focus on Deep Learning, Signal Processing, or Computational Linguistics.",
     "PhD required", False),                                                                                # IFM: MSc accepted
    ("A big plus\nAn MSc or PhD in machine learning, computer science, or a related quantitative field.", "PhD required", False),
    # experience can stand in for the degree (Stability AI, Cohere Multilingual on their boards, 2026-09-29)
    ("PhD or equivalent research experience in Computer Science, Machine Learning, Computer Graphics, Computer Vision, or a "
     "closely related discipline.", "PhD required", False),
    ("PhD in Computer Science, Linguistics, or related field (or equivalent experience)", "PhD required", False),
    ("Doctorat (ou niveau équivalent) en informatique, intelligence artificielle, machine learning, bioinformatique ou "
     "discipline connexe.", "PhD required", False),                                                     # Institut Curie
    ("Doctorat (PhD) avec une forte expérience en bio-informatiques et analyse de données OMICS ;", "PhD required", True),  # IHU ICAN
    ("Qualifications:\nA PhD in Computer Science, Machine Learning, or a related field, with publications in top-tier venues "
     "(e.g., NeurIPS, ICML, ICLR, ACL, EMNLP).", "PhD required", True),                                   # Cohere Agent Code
    ("This is a senior, research-oriented role for someone who wants to develop novel approaches.",
     "the posting calls the role senior", True),                                                           # Nucs AI
    ("You will work closely with senior engineers and researchers.", "the posting calls the role senior", False),
    # the hire named in the body, real lines (audit 2026-10-17: hide them)
    ("We are looking for a technical, execution-focused Senior Data Scientist (Clinical Data) to bridge the gap.",
     "the posting calls the role senior", True),                                                           # Bioptimus
    ("We are seeking a senior-level AI Engineer, Marketing to lead the design of AI-powered workflows.",
     "the posting calls the role senior", True),                                                           # PMI
    ("We are seeking a senior technical leader who combines a deep mastery of modern AI ecosystems.",
     "the posting calls the role senior", True),                                                           # Shift Technology
    ("At Cohere, we're seeking a senior engineer who understands the product.", "the posting calls the role senior", True),
    ("On a besoin d'un renfort senior pour accompagner cette transition.", "the posting calls the role senior", True),  # Agicap
    ("Orbitant seeks mid and senior-level AI Engineers to develop production-grade systems.",
     "the posting calls the role senior", False),                                                          # several levels
    ("Seniority level: Mid-Senior Level", "the posting calls the role senior", False),                     # LinkedIn label
    ("Vous rencontrerez ensuite un consultant senior (manager ou practice leader) idéalement sur site.",
     "the posting calls the role senior", False),                                                          # the interviewer
    ("Lead client engineering engagements at senior level and drive alignment.", "the posting calls the role senior", False),
    # Stability AI on Himalayas, 2026-10-14 (a senior posting a sweep let through)
    ("Qualifications\nStrong academic research track record in computer vision, computer graphics, machine learning, "
     "generative modeling, or a related field.", "research track record required", True),
    ("Requirements\nA strong publication record at top venues.", "research track record required", True),
    ("Highly Desirable\nA research track record in generative modeling.", "research track record required", False),
    ("Proven track record of shipping ML models to production.", "research track record required", False),
    ("A publication record is a plus.", "research track record required", False),
    ("Strong publication record or demonstrated research potential", "research track record required", False),  # Cohere
    # Cohere's boards, 2026-10-18 (a research-record requirement)
    ("- Track record of work on generalization, robustness, or distribution shift, through publications or shipped models.",
     "research track record required", True),                                                     # Post-Training RL
    ("- One or more papers at top-tier venues (such as NeurIPS, ICML, ICLR, AIStats, MLSys, JMLR).",
     "research track record required", True),                                                     # Data Analysis and Evaluation
    ("- Hands-on experience training large language models (LLMs) on distributed training infrastructures.",
     "large-scale model training required", True),
    ("- Experience using large-scale distributed training strategies.", "large-scale model training required", True),  # Modeling
    ("- Hands on experience on training large model at scale and having contributed to the tooling.",
     "large-scale model training required", True),                                                # Training Infra
    ("- Design, run, and analyse large-scale RL post-training experiments (e.g., RLHF, RL from verifiable rewards).",
     "large-scale model training required", True),
    ("Experience fine-tuning LLMs with LoRA or similar methods.", "large-scale model training required", False),
    ("Deploy and monitor models at scale in production.", "large-scale model training required", False),
    ("- Experience with distributed training (e.g., JAX, PyTorch, FSDP, Megatron) is a plus.",
     "large-scale model training required", False),                                               # Cohere RL, a plus
    ("Nice to have\nExperience with large-scale distributed training.", "large-scale model training required", False),
    ("You will serve our models to millions of users at scale.", "large-scale model training required", False),
    ("Responsibilities: User support and training: technical guidance for deploying large-scale AI workflows on HPC systems; "
     "develop training materials and workshops.", "large-scale model training required", False),  # census 2026-10-18
    ("Own training end to end: Build and maintain the training pipelines for large-scale model training, from data to "
     "distributed runs.", "large-scale model training required", True),                            # Gradium research scientist
]
REVIEW_REMOTE = [  # (text, location field, report field, expected)
    ("You must be comfortable working with clients based in the US.", "Remote - Europe", "france_eligible", "yes"),
    ("You will need to collaborate daily with engineers located in India.", "Remote - Europe", "france_eligible", "yes"),
    ("Occasional travel required to our office located in the UK.", "Remote - Europe", "france_eligible", "yes"),
    ("This role requires overlap with our team based in the US, so you should be available until 18:00 CET.", "Remote - Europe", "france_eligible", "yes"),
    ("Hybrid role, 3 days a week in our Paris office.\nFor US-based roles, candidates must be authorized to work in the United States.", "Paris, France", "france_eligible", "n/a"),
    ("Onsite in Paris.\nSales roles: must be based in the UK.", "Paris", "france_eligible", "n/a"),
    ("Fully remote role.", "Europe (excl. France)", "france_eligible", "no"),
    ("Fully remote role.", "Remote (Europe, except France)", "france_eligible", "no"),
    ("Fully remote role.\nWe hire anywhere in Europe except France.", "", "france_eligible", "no"),
    ("Fully remote role.\nLocation: Remote (U.S. only)", "", "france_eligible", "no"),
    ("Fully remote role.\nLocation: St. Louis, MO", "", "france_eligible", "no"),  # rule D12 (2026-10-22): a city with a US state code is outside France; no rule caught it before
    ("Fully remote role. Our office locations: Austin, Paris and Tokyo.", "", "france_eligible", "unclear"),
    ("Acme | ML Engineer | REMOTE | Full-time", "", "mode", "remote"),
    ("We build models for insurers.", "New York, NY", "france_eligible", "no"),  # rule D12 (2026-10-22): a city with a US state code is outside France; no rule caught it before
    # Indeed 2026-10-04: a French town with its département number names a place in France (no work-mode words,
    # so the mode stays unknown and only the place is settled); "basé sur", "basé en partie" and a town abroad do not
    ("Poste en CDI, basé à Vélizy Villacoublay (78).", "", "france_eligible", "yes"),
    ("Poste à pourvoir en CDI, basé à Vélizy-Villacoublay (78).", "", "france_eligible", "yes"),
    ("Poste à pourvoir en CDI, basé à Vélizy-Villacoublay (78).", "", "mode", "unknown"),
    ("We build models for insurers.", "Vélizy-Villacoublay (78)", "france_eligible", "yes"),
    ("Notre produit est basé sur des modèles de langage.", "", "france_eligible", "unclear"),
    ("Le poste est basé en partie sur des projets clients.", "", "france_eligible", "unclear"),
    ("Poste en CDI, basé à Londres.", "", "france_eligible", "unclear"),
    ("Une équipe de (12) personnes.", "", "france_eligible", "unclear"),
]
REVIEW_SALARY = [  # (text, expected (min, max, vs_floor)) against an example floor of 42,000
    ("Visa sponsorship and relocation support up to €10,000", (None, None, "not_stated")),
    ("€1,000 annual L&D budget", (None, None, "not_stated")),
    ("We are scaling toward a gigawatt of compute across Europe by 2030.", (None, None, "not_stated")),
    ("We are ISO 27001 certified.", (None, None, "not_stated")),
    ("Relocation package up to €5,000.\nSalary: €55,000 - €70,000 gross per year", (55000, 70000, "above")),
    ("What we offer:\n- €1,000 annual learning budget\n- Salary: €55,000 - €65,000 gross per year", (55000, 65000, "above")),
    ("Prime de cooptation de 2 000 €\nSalaire : 50 000 - 60 000 € brut annuel", (50000, 60000, "above")),
    ("Salary: 45 000 - 55 000 € + 2 000 € signing bonus", (45000, 55000, "above")),
    ("Salaire : 35-45k€ selon profil", (35000, 45000, "straddles")),
    ("CDI, 75008 Paris, 45 000 € brut annuel", (45000, 45000, "above")),
    ("Salary: 45 000 € gross per year, starting March 2027.", (45000, 45000, "above")),
    ("TJM : 1 000 € / jour", (None, None, "daily_rate")),
    ("Rémunération : 3 500 € bruts mensuels", (42000, 42000, "above")),
    ("Rémunération mensuelle brute de 3 500 €", (42000, 42000, "above")),
    ("Salary: 3 500 €/mo gross", (42000, 42000, "above")),
    ("Salary: €4,000 p.m. gross", (48000, 48000, "above")),
    ("Fully remote. Salary: $40,000 per year.", (None, None, "other_currency")),
]
REVIEW_BLOCKERS = [  # text that names clearance or nationality without requiring it
    "No security clearance required.", "Security clearance is not needed for this role.", "Aucune habilitation requise.",
    "We handle customs clearance data for logistics clients.", "You will partner with a famous national research lab.",
    "US citizenship is not required.", "Must be an EU citizen or hold a valid work permit.",
]
REVIEW_YEARS = [
    ("Vous avez plus de 5 ans d'expérience en machine learning.", 5),
    ("5+ years of experience; a PhD is a plus.", 5),
    ("Minimum 5 years of experience in ML, bonus if you know Rust.", 5),
    ("German is a plus. 5+ years of experience required.", 5),
    ("Fort de 20 ans d'expérience, notre cabinet accompagne les grands comptes.\nVous avez 2 ans d'expérience en data science.", 2),
    ("With 15 years of experience in fintech, we serve 3M users.", None),
    # spelled numbers (Synthesia Roleplay Sessions, 2026-10-14: read as 2 years; a senior posting)
    ("- At least seven (7) years of experience as a software engineer, with at least 2 years at senior level.", 7),
    ("Five years of experience in machine learning is required.", 5),
    ("Our seven offices across Europe are hiring.", None),
    # job-search audit 2026-10-17 (CHK-2): preferences, graduate alternatives and senior levels are not the ask
    ("Preferably 5+ years of experience in computer vision.", None),                                        # DocuSketch
    ("Expérience idéalement supérieure à 3 ans en simulation numérique.", None),                           # Dassault
    ("4–6 years of experience (or a strong recent graduate with exceptional projects).", None),           # Aline
    ("5-6 years for senior levels; we also hire juniors.", None),                                          # Harmattan
    ("Bonus points\n5+ years with Rust.", None),
    ("3+ years of experience in Python, preferably in ML.", 3),            # the preference is about ML, the 3 years stay
]


# Second review of the rewrite (2026-09-27): misreads that let a closed posting reach tier A, or
# sent an open one to C. Real lines are from Mistral's board; the rest are the reviewer's probes.
REVIEW3_FRENCH = [
    ("Fluent in English; knowledge of German or French is advantageous.", P),
    ("Fluency in English (additional languages, e.g., Arabic or French, are advantageous)", P),   # Mistral, RFP & Bid Manager
    ("Good French skills required.", R),
    ("Strong French skills.", R),
    ("Our team is a mix of French-speaking and English-speaking engineers.", U),
    ("You will support our French clients in English.", N),
    ("The team speaks French and English.", U),
    ("Good knowledge of the French AI regulatory landscape (AI Act, CNIL).", M),
    ("Anglais courant, le français est un plus", P),
    ("Vous parlez français et anglais", R),
    # job-search audit 2026-10-17 (CHK-8): English joined to French is required French
    ("Excellent written communication in English and French (C1 minimum)", R),                           # Sorare
    ("Strong communication skills in English and French.", R),
]
REVIEW3_TITLES = [("Machine Learning Intern", "internship"), ("Data Scientist Intern (6 months)", "internship"),
                  ("Data Scientist - Stage", "internship")]
REVIEW3_PLACE = [  # (text, location field, expected france_eligible, expected tier or None)
    ("Hybrid role, 3 days per week in the office. Permanent position.", "London", "unclear", "B"),
    ("On-site role in our office. Permanent contract.", "Palo Alto, CA", "no", "C"),        # Mistral "AI Deployment Strategist - USA"; D12 (2026-10-22)
    ("Hybrid, 2 days a week in our Paris office. Permanent contract.", "", "n/a", "A"),
    ("Remote role. Our European team is growing; you must be based in the United States.", "Remote", "no", "C"),
    ("Remote (EMEA). Permanent contract.", "", "yes", "A"),
    ("Fully remote in France. Permanent contract.", "", "yes", "A"),
    ("Paris or remote in Europe. Permanent contract.", "", "yes", "A"),
]
REVIEW3_BLOCKERS = [  # these do block
    "French nationality required (not negotiable).", "EU citizenship is required for this role (no sponsorship).",
    "This role requires a security clearance; no visa sponsorship available.", "Candidates must be EU citizens; no exceptions.",
]
REVIEW3_YEARS = [
    ("With over 15 years of experience delivering AI solutions, we help banks transform.", None),
    ("Depuis plus de 20 ans, notre expérience au service des grands comptes.", None),
    ("Our clients have trusted us for 10 years; you will bring your experience in NLP.", None),
    ("Diplôme Bac+5 (5 ans d'études supérieures) et une première expérience en data science.", None),
]
# Lines from the 2026-09-29 sweep (Mistral, NVIDIA, Alan) that asked for years with no "experience" word, with
# "we" in a requirement header, or with a word between the number and "years"; all read as no years before.
REVIEW4_YEARS = [
    ("5+ years in SRE, Production Engineering, or DevOps, with a record of shipping tooling.", 5),
    ("6+ years in a client-facing strategic and technical role within the cybersecurity or defense sector "
     "(e.g., cybersecurity consulting, security engineering, SOC operations, or technical sales in cyber).", 6),
    ("4 + years working on large-scale ML codebases.", 4),
    ("WHAT WE'RE LOOKING FOR - At least 3 years building and deploying production AI software.", 3),
    ("15+ overall years in enterprise software, ecosystem GTM, cloud, AI platform, ISV alliances, technical sales, "
     "or platform leadership, including 10+ or more years leading teams.", 15),
    ("EXPERIENCE WE VALUE - 3+ years in full-stack software engineering roles - Experience building products.", 3),
    # and what must still read as no years asked
    ("Nice to have - 3+ years of Rust.", None),
    ("We have been building AI for 12+ years.", None),
]
# Review 5 (2026-09-29): a curly apostrophe (10 postings of the sweep), plural and stand-alone nice-to-have headers,
# a company intro that also says "we are looking for", and years that are not asked of the candidate.
# Review 2026-09-29: a ceiling on years is not an ask, and a company's age is not either; the asks still count
REVIEW6_YEARS = [
    ("You have less than 3 years of experience in machine learning.", None),
    ("Junior profile: up to 3 years of experience.", None),
    ("Vous avez moins de 3 ans d'expérience en data science.", None),
    ("Jeune diplômé ou première expérience (moins de 3 ans).", None),
    ("No more than 3 years of professional experience.", None),
    ("Experience: 2 years maximum.", None),
    ("10+ years later, a French start-up is a real game changer in healthcare.", None),
    ("You have 3+ years of experience in machine learning.", 3),
    ("At least 4 years of experience with Python.", 4),
    ("Vous justifiez d'au moins 5 ans d'expérience.", 5),
]
REVIEW5_YEARS = [
    ("Typical indicators we’re looking for include: - 8 to 10+ years of professional experience in strategy "
     "consulting, digital transformation, AI, enterprise technology, or a similarly demanding environment.", 8),
    ("Nice-to-Haves: - 3+ years of Rust.", None),
    ("Nice to have\n3+ years of Rust.", None),
    ("Requirements\n3+ years of Python.\nNice to have\n7+ years of Rust.", 3),
    ("With 15 years of experience in fintech, we are looking for a Senior Data Scientist.", None),
    ("Avec plus de 20 ans d'expérience dans le conseil, nous recherchons un Data Scientist.", None),
    ("Trusted by 700+ customers for 10+ years.", None),
    ("Access 10+ years of historical claims data.", None),
    ("120+ years of innovation in mobility.", None),
    ("Join a team with 115+ years of combined experience in ML.", None),   # only the digit boundary stops "15+ years"
    # the "years of data" rule read these as not asked (Mistral, 2026-09-29): data here is the field of work
    ("Have 4+ years of experience in Data Infrastructure, MLOps, or Infrastructure Engineering.", 4),
    ("5+ years of data engineering in production.", 5),
    ("You have 3+ years of experience in data.", 3),   # only the experience-word condition keeps this one
    ("You must be at least 18 years old.", None),
    ("At least 3 years of residence in the EU.", None),
]
REVIEW3_SALARY = [  # against an example floor of 42,000
    ("Salaire : entre 30 et 45K€ selon profil", (30000, 45000, "straddles")),
    ("Salaire : entre 50 et 60K€", (50000, 60000, "above")),
    ("Salary: up to €45k", (None, 45000, "straddles")),
    ("Salary: EUR 50,000 - 60,000", (50000, 60000, "above")),
    ("Compensation: 60 000 - 75 000 USD", (None, None, "other_currency")),
    ("Relocation package of €5,000 and a salary of €48,000", (48000, 48000, "above")),
]


# The work mode: "hybrid" in the name of a model, an architecture or a search is machine learning, not a work
# arrangement (Featherless AI on Himalayas, 2026-09-30: "recurrent / hybrid models", "hybrid architectures" made
# three remote roles read as hybrid, and a hybrid role gets no France-eligibility check). "a hybrid model" of work stays.
WORK_MODE = [  # (text, mode)
    ("Attention mechanisms, RNNs, state-space models, or hybrid architectures.", "unknown"),
    ("Research alternatives or extensions to Transformers (recurrent / hybrid models, long-context systems).", "unknown"),
    ("Build hybrid search (BM25 plus dense retrieval) for our RAG platform.", "unknown"),
    ("This is a hybrid role based in Paris, 2 days a week in the office.", "hybrid"),
    ("Location: Paris - Hybrid", "hybrid"),
    ("Hybrid working model: three days in the office.", "hybrid"),
    ("We work in a hybrid model, three days in the office.", "hybrid"),
    ("Poste en mode hybride avec 2 jours de télétravail.", "hybrid"),
    ("Fully remote role, work from anywhere.", "remote"),
]


# A posting written in French : only a stated French
# requirement is tier C; every other French-written posting is B, with the English signal named in its reason.
_FR = ("Nous recherchons un ingénieur IA pour rejoindre notre équipe à Paris. Vous concevrez des agents et des pipelines "
       "de données avec les équipes métiers, en CDI, avec deux jours de télétravail par semaine. ")
FRENCH_POSTING_TIER = [
    (_FR + "Vous avez le sens du travail en équipe.", "B"),                                        # Illuin, Modjo
    (_FR + "Vous parlez anglais couramment.", "B"),                                                # English as a skill
    (_FR + "Construire une équipe internationale composée de collaborateurs issus de 25 pays différents.", "B"),  # Pennylane
    (_FR + "Former et accompagner les équipes multisites (Brésil, Tunisie, Portugal, Allemagne, Mexique, USA).", "B"),  # Sagemcom
    (_FR + "La langue de travail de l'équipe est l'anglais.", "B"),
    (_FR + "Rejoindre un groupe de dimension internationale.", "B"),
    (_FR + "Français courant exigé.", "C"),                                                         # a stated requirement
    (_FR + "Maîtrise du français et de l'anglais indispensable.", "C"),
]


def run() -> int:
    bad = total = 0

    def expect(label, got, want):
        nonlocal bad, total
        total += 1
        if got != want:
            bad += 1
            print(f"FAIL {label}: want {want!r}, got {got!r}")

    for group, cases in (("old filter", OLD_FILTER), ("campaign", CAMPAIGN), ("review", REVIEW)):
        for text, want in cases:
            expect(f"french/{group}: {text}", pc.check(text)["french"]["verdict"], want)
    for text, title, want in CONTRACT:
        expect(f"contract: {text}", pc.check(text, title=title)["contract"]["type"], want)
    for text, want in YEARS:
        expect(f"years: {text[:50]}", pc.check(text)["seniority"]["min_years"], want)
    # A remote-only board's posting counts as remote even when its text never says so.
    board = pc.check("Machine Learning Engineer. Build models.", location="Worldwide", remote_board=True)["remote"]
    expect("remote board: worldwide", (board["mode"], board["france_eligible"]), ("remote", "yes"))
    # For someone in Paris a Paris location is eligible, and "Paris or London" is not a London-only rule.
    for loc in ("Paris", "Remote (Paris or London)", "Île-de-France"):
        expect(f"paris location: {loc}", pc.check("Fully remote role.", location=loc)["remote"]["france_eligible"], "yes")
    for text, floor, want in SALARY:
        s = pc.check(text, salary_floor=floor)["salary"]
        expect(f"salary: {text}", (s["min_eur_year"], s["max_eur_year"], s["vs_floor"]), want)
    for text, want, kind, url in REMOTE:
        body = "Fully remote role." + ("\n" + text if kind == "text" else "")
        r = pc.check(body, location="" if kind == "text" else text)
        expect(f"remote/{kind}: {text} ({url})", r["remote"]["france_eligible_if_remote"], want)
    for text, want in WORK_MODE:
        expect(f"work mode: {text[:60]}", pc.check(text + " Machine learning engineer role, building models.")["remote"]["mode"], want)

    for text, want in REVIEW_FRENCH:
        expect(f"french/review-2: {text[:60]}", pc.check(text)["french"]["verdict"], want)
    for text, want in REVIEW_CONTRACT:
        expect(f"contract/review-2: {text[:60]}", pc.check(text)["contract"]["type"], want)
    for text, kind, months in REVIEW4_MONTHS:
        c = pc.check(text)["contract"]
        expect(f"contract length/review-4: {text[:60]}", (c["type"], c["months"]), (kind, months))
    for text, want in INDEED_FRENCH:
        expect(f"french/indeed-2026-09-29: {text[:60]}", pc.check(text)["french"]["verdict"], want)
    for text, want in INDEED_YEARS:
        expect(f"years/indeed-2026-09-29: {text[:60]}", pc.check(text)["seniority"]["min_years"], want)
    for text, want in INDEED_SALARY:
        expect(f"salary/indeed-2026-09-29: {text[:60]}", pc.check(text, salary_floor=42000)["salary"]["vs_floor"], want)
    for text, flag, present in INDEED_FLAGS:
        expect(f"flag/indeed-2026-09-29: {flag}: {text[:50]}", flag in pc.check(text)["seniority"]["flags"], present)
    for text, loc, field, want in REVIEW_REMOTE:
        expect(f"remote/review-2: {text[:50]} @ {loc}", pc.check(text, location=loc)["remote"][field], want)
    for text, want in REVIEW_SALARY:
        sal = pc.check(text, salary_floor=42000)["salary"]
        expect(f"salary/review-2: {text[:50]}", (sal["min_eur_year"], sal["max_eur_year"], sal["vs_floor"]), want)
    for text in REVIEW_BLOCKERS:
        expect(f"blocker/review-2: {text}", pc.check(text)["seniority"]["blockers"], [])
    for text, want in REVIEW_YEARS:
        expect(f"years/review-2: {text[:50]}", pc.check(text)["seniority"]["min_years"], want)
    for text, want in REVIEW3_FRENCH:
        expect(f"french/review-3: {text[:60]}", pc.check(text)["french"]["verdict"], want)
    for title, want in REVIEW3_TITLES:
        expect(f"contract/review-3 title: {title}", pc.check("Join our team and build models.", title=title)["contract"]["type"], want)
    for text, loc, want, tier in REVIEW3_PLACE:
        r = pc.check(text, location=loc)
        expect(f"place/review-3: {text[:50]} @ {loc}", (r["remote"]["france_eligible"], r["suggested"]["tier"]), (want, tier))
    for text in REVIEW3_BLOCKERS:
        expect(f"blocker/review-3: {text}", pc.check(text)["suggested"]["tier"], "C")
    for text, want in REVIEW3_YEARS:
        expect(f"years/review-3: {text[:50]}", pc.check(text)["seniority"]["min_years"], want)
    for text, want in REVIEW3_SALARY:
        sal = pc.check(text, salary_floor=42000)["salary"]
        expect(f"salary/review-3: {text[:50]}", (sal["min_eur_year"], sal["max_eur_year"], sal["vs_floor"]), want)
    for text, want in REVIEW4_YEARS:
        expect(f"years/review-4: {text[:50]}", pc.check(text)["seniority"]["min_years"], want)
    for text, want in REVIEW6_YEARS:
        expect(f"years/review-6: {text[:50]}", pc.check(text)["seniority"]["min_years"], want)
    for text, want in REVIEW5_YEARS:
        expect(f"years/review-5: {text[:50]}", pc.check(text)["seniority"]["min_years"], want)
    expect("tier: salary in dollars is a question", pc.check("Fully remote. Salary: $40,000 per year.", location="Worldwide")["suggested"]["tier"], "B")
    # 2026-10-22: a city with a US state code is outside France (rule D12); it read "unclear" (B) before
    expect("tier: US city, no work mode", pc.check("We build models for insurers.", location="New York, NY")["suggested"]["tier"], "C")
    for loc, want in (("New York, NY", "no"), ("San Francisco, CA", "no"), ("Austin, TX 78701", "no"), ("Seattle, WA, USA", "no"),
                      ("Paris, France", "yes"), ("Lyon", "unclear"), ("Remote", "unclear")):
        expect(f"state code field: {loc}", pc.check("Fully remote role.", location=loc)["remote"]["france_eligible"], want)
    expect("state code field, EMEA in the text stays a question",
           pc.check("Fully remote role. Candidates located anywhere in EMEA are welcome.", location="New York, NY")["remote"]["france_eligible"], "unclear")
    # 2026-10-14: a French posting that asks English as a skill works in French (Dassault, Banque de France, Renault)
    fr_base = ("Nous recherchons un ingénieur pour rejoindre notre équipe à Paris. Vous travaillerez avec les équipes produit et "
               "les data scientists sur des projets de machine learning. Le poste est en CDI, basé à Paris, avec deux jours de "
               "télétravail par semaine. ")
    expect("tier: French posting, English as a skill", pc.check(fr_base + "Maîtrise de l'anglais, à l'écrit comme à l'oral, "
           "indispensable.", location="Paris, France")["suggested"]["tier"], "B")   # rule withdrawn 2026-10-14
    expect("tier: French posting, international team, no English line", pc.check(fr_base + "Vous rejoindrez une équipe "
           "internationale composée de collaborateurs issus de 25 pays.", location="Paris, France")["suggested"]["tier"], "B")
    expect("tier: French posting, team works in English", pc.check(fr_base + "Vous maîtrisez l'anglais. La langue de travail "
           "de l'équipe est l'anglais.", location="Paris, France")["suggested"]["tier"], "B")
    expect("tier: English posting asking English stays A", pc.check("We are hiring an ML engineer in Paris, France. CDI, "
           "hybrid. You will build and evaluate models with our product team. Fluent English is required.", location="Paris, France")["suggested"]["tier"], "A")
    # An empty or near-empty posting (a failed fetch) must not come back as tier A.
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("Apply now")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = pc.main([f.name])
    expect("empty posting exits non-zero", (rc, json.loads(buf.getvalue())["suggested"]["tier"]), (2, "unknown"))

    # The floor is an input: with a salary in the text and no floor given, nothing is compared.
    expect("salary floor stays an input", pc.check("Salary 45 000 € brut annuel")["salary"]["vs_floor"], "no_floor_given")
    # A posting that says both "not required" and "required" is a question for the recruiter, not a verdict.
    expect("conflicting lines", pc.check("French is not required.\nFluent French mandatory for client calls.")["french"]["verdict"], U)
    # A nationality wish is a flag; a nationality rule is a blocker (tier C).
    wish = pc.check("Nationalité française souhaitée")
    expect("nationality wish is a flag", (wish["seniority"]["blockers"], wish["seniority"]["flags"]), ([], ["nationality requirement (preferred)"]))
    rule = pc.check("Candidates must hold EU citizenship.")
    expect("nationality rule is a blocker", rule["suggested"]["tier"], "C")
    # Another required language is a gate like French (Recordly, 2026-10-08); a wish is not
    finnish = pc.check("In addition to English, we require fluent written and spoken Finnish skills, as a significant "
                       "part of our customer work is conducted in Finnish.")
    expect("required Finnish is a blocker", finnish["suggested"]["tier"], "C")
    expect("German is a plus is not a blocker", pc.check("Fluent English required. German is a plus.")["seniority"]["blockers"], [])
    expect("Spanish nice to have is not a blocker", pc.check("Spanish would be nice to have.")["seniority"]["blockers"], [])
    expect("required Korean is a blocker", pc.check("Fluent Korean is required for our Seoul clients.")["suggested"]["tier"], "C")
    # The years cap is an input: no cap, no flag; a cap flags only a larger ask
    five = "You have 5+ years of experience in machine learning."
    expect("years cap stays an input", any("years asked" in w for w in pc.check(five)["suggested"]["why"]), False)
    expect("years over the cap are flagged", "5+ years asked (cap 2)" in pc.check(five, years_cap=2)["suggested"]["why"], True)
    expect("years at the cap are not", any("years asked" in w for w in pc.check(five, years_cap=5)["suggested"]["why"]), False)
    # A rule for one of several offices is a flag, not a gate (Helsing, 2026-10-08 re-read of stored postings)
    helsing = pc.check("If applying for Germany- it is a requirement that you are able to speak business level or fluent German.")
    expect("a language rule for one office is a flag", (helsing["seniority"]["blockers"],
           "another language required (one location)" in helsing["seniority"]["flags"]), ([], True))
    # Titles from the 2026-09-29 sweep: the AI labs' engineer title is not a senior one; English "confirmed" is
    for title, flagged in [("Member of Technical Staff, Post-Training", False), ("Staff ML Engineer", True),
                           ("Confirmed Machine Learning Engineer (Productivity)", True), ("Experienced Data Scientist", True),
                           ("AI/ML TECHNICAL LEADER (H/F)", True), ("Data Scientist, Leadership Analytics", False)]:
        expect(f"senior title: {title}", "senior title" in pc.check("We build models.", title=title)["seniority"]["flags"], flagged)
    # A careers page that answers 200 with "the job is filled" is a closed posting (BCG X, Allianz, 2026-10-21)
    for text in ("We're sorry… the job you are trying to apply for has been filled. Explore our teams.",
                 "Nous sommes désolés... Le poste pour lequel vous essayez de postuler a été pourvu."):
        expect(f"closed page: {text[:40]}", pc.check(text + " We build ML systems in Paris.")["suggested"]["tier"], "C")
    expect("an open posting is not closed", pc.check("We are hiring an ML Engineer in Paris on a permanent contract.")["suggested"]["tier"], "A")
    # A careers-site footer is not the offer (Dassault Systèmes, 2026-10-21)
    expect("footer internship line", pc.check("Be part of the future of our workforce – check out internship and job opportunities.")["contract"]["type"], "unknown")
    expect("an internship offer is still one", pc.check("This is a 6-month internship in our Paris lab.")["contract"]["type"], "internship")
    # A required PhD, research record or large-scale training is a blocker, not a flag to check (tier C)
    expect("tier: PhD required", pc.check("Requirements\n- PhD in computer science, machine learning or a related field.\nCDI in Paris.", location="Paris, France")["suggested"]["tier"], "C")
    expect("tier: MSc or PhD is not a blocker", pc.check("Requirements\n- An MSc or PhD in machine learning or a related field.\nCDI in Paris.", location="Paris, France")["suggested"]["tier"], "A")
    # A case list defined twice runs only its last copy: 2026-09-29 a new REVIEW5_YEARS (then REVIEW6_YEARS) above
    for text, want in FRENCH_POSTING_TIER:
        expect(f"french posting tier: {text[:50]}", pc.check(text)["suggested"]["tier"], want)

    # an older one of the same name never ran, and its cases read as passing
    import ast
    names = [t.id for node in ast.parse(Path(__file__).read_text(encoding="utf-8")).body if isinstance(node, ast.Assign)
             for t in node.targets if isinstance(t, ast.Name)]
    expect("no case list defined twice", sorted({n for n in names if names.count(n) > 1}), [])

    print(f"{total - bad}/{total} cases pass")
    return 1 if bad else 0


def test_corpus():
    """pytest entry point; the file also runs on its own."""
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())
