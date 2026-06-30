# Therapist Scraper — Cursor Project Spec

## Context

This is a personal project for Charles, who is searching for a couples therapist in NYC with his wife Angelina. They are an intercultural couple (Japanese husband, North Vietnamese wife). The goal is to scrape Psychology Today profiles matching their filters, extract structured data, evaluate each profile against a scoring rubric via the Anthropic API, and output a ranked CSV.

## Environment

- Python 3.12+
- Use `uv` for environment management (not pip, pipenv, or poetry)
- Assume the project directory and git repo already exist
- Personal Anthropic API key (not company key)

```bash
uv init
uv add httpx beautifulsoup4 anthropic tenacity python-dotenv
```

Create `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
```

Add `.env` to `.gitignore`.

---

## Step 0 — Validate the Evaluator (RUN THIS FIRST)

Before building the scraper, validate that the scoring prompt produces correct results against two known-answer profiles. This is the calibration step. If these two don't score correctly, the prompt needs tuning before running against 275 profiles.

### Test Case 1: Travis Atkinson (expected: CALL, score 8-10)

Why he should score high: Certified EFT Therapist AND Supervisor (since 2010), Certified Gottman Method Couples Therapist (since 2006), Advanced Schema Therapy, 30 years specializing in couples, co-developed Schema Therapy for Couples, trained directly by Sue Johnson (EFT founder) and Jeffrey Young (Schema founder), NYU 1995, endorsed by other therapists, $375 individual / $450 couples, Manhattan. Fails the Asian hard filter but should still score 8+ due to supervisor/trainer tier + extraordinary style match.

**NOTE: His current Psychology Today profile says "Available online only" — this may cause a FAIL on the NYC in-person filter. The evaluator should flag this but not let it override the clinical quality signal. He previously practiced in-person in Midtown Manhattan and may still offer it — this is a "ask on the call" question, not a disqualifier.**

Raw profile text to use as test input:

```
Travis Atkinson, Clinical Social Work/Therapist, LCSW, LICSW (he, him)
New York, NY 10003
Available online only
(646) 462-4329
Loving at Your Best
$375 Individual / $450 Couples / Out of Network

Most couples who find me have already tried therapy. One of you saw the trouble years ago: read the books, named the patterns, felt unheard. The other has carried weight everywhere else and only recently noticed how quiet home had become. You are not failing. You have been using tools that were never built for what you are facing. For high-functioning couples, the gap between knowing and changing is where the real work lives, and where most therapy never reaches.

I'm Travis Atkinson. Thirty years, three couples models, and co-developer of a fourth built for chronic distress that standard methods miss. Certified Gottman therapist since 2006. Trained directly by Sue Johnson, founder of EFT, and Jeffrey Young, founder of Schema Therapy.

We start with a full assessment, then map what is driving the cycle and build a plan that targets the source, not the symptoms. Nothing off a shelf. Most couples tell me by session three or six that the pattern finally makes sense. Sessions are online, with private intensives for couples who want the work concentrated. Reach out when you are ready.

Licensed in New York (R052973), Vermont, and Florida. Practicing since 1995, specializing in couples. Certified in Gottman Method, Emotionally Focused Therapy, and Advanced Schema Therapy, and co-developer of Schema Therapy for Couples for chronic distress.

As a Certified Gottman Method Couples Therapist, Emotionally Focused Therapist and Supervisor, and Advanced Certified Schema Therapist, Supervisor, and Trainer, my expertise spans deep into the heart of relationships and individual challenges. Our journey begins with understanding your unique story, using these powerful approaches to uncover, explore, and transform the patterns that hold you back.

With expertise in EFT and Schema Therapy, mentored by founders Sue Johnson and Jeffrey Young, I bring a unique perspective to couples therapy. 20 plus years of global training and multiple professional publications reflect my deep understanding of relational dynamics and individual growth. I specialize in transforming complex emotional challenges into opportunities for deep, lasting connections.

Attended New York University, Graduated 1995
Certificate: Certified Gottman Method Couples Therapist 2006
Certificate: Certified Emotionally Focused Couples Therapist and Supervisor 2010
Endorsed by 2 therapists

Top Specialties: Relationship Issues, Marital and Premarital, ADHD
Types of Therapy: Attachment-based, Clinical Supervision and Licensed Supervisors, CBT, EMDR, Emotionally Focused, Family/Marital, Family Systems, Gottman Method, Mindfulness-Based (MBCT), Relational, Schema Therapy
Ethnicity served: Asian, Pacific Islander
Participants: Individuals, Couples, Group
```

**Expected output:** verdict=CALL, composite_score=9 or 10, tier_1.couples_certification=supervisor_trainer, tier_2.couples_focus_pct=85+, tier_3.style=active, tier_5.top_school=true (NYU).

### Test Case 2: Brithny Zhang (expected: SKIP or low READ_MORE, score 3-5)

Why she should score low: LMSW (provisional license, not LCSW), bio is entirely individual-focused ("you" singular throughout), lists 15 modalities without clear primary anchor, no couples-specific training or certification, no mention of couples work in narrative, "couples" appears only in a sidebar checkbox. Strong cultural fit (Asian American, Duke BA 2012, Columbia MSW, Mandarin) but individual therapist moonlighting as couples.

Raw profile text to use as test input:

```
Brithny Zhang, Clinical Social Work/Therapist, LMSW (she, her)
New York, NY 10010
Available both in-person and online
(332) 378-5726
$200 Individual / $300 Couples / Accepts insurance + Out of Network
Sliding scale available

People tend to find me warm, thoughtful, and kind. My academic training at Duke and Columbia and my own LGBTQ and BIPOC identities have all influenced the therapist I am today. As an Asian American woman who grew up internationally and has experienced "otherness"—I know what it feels like.

I'm here to help guide and support you in overcoming the negative self-talk, the harmful patterns, the anxious or avoidant behaviors that are inhibiting you from deepening relationships. We'll work together to heal past traumas, and undo the ways they are still causing hurt or holding you back from being your most brilliant self.

I practice from a psychodynamic framework, exploring the formative experiences that shaped you and recognizing how they manifest today. Our work will aim to uncover meaning, increase self-awareness, and build a stronger, kinder understanding of who you are. I integrate practical tools and blend methodologies, tailoring the process to fit you and your needs.

Therapy with me is collaborative, empowering, and insight-driven. I believe that meaningful change comes from not only addressing symptoms, but from deepening your understanding of self. I strive to offer a space that feels both supportive and gently challenging—a relationship grounded in trust and honesty, and healing that happens in empathy and connection.

I am psychodynamically oriented, while influenced by relational approaches and narrative styles. We'll explore your past and present in trying to understand who you are today and what you want to work on in therapy.

Attended Columbia University, M.S. in Social Work, Advanced Clinical Practice
Degree from Duke University B.A. in Psychology & English / 2012

Top Specialties: Stress and Anxiety, Relationship Issues, Life Transitions
I see individuals, couples and families
Ethnicity: Asian
Languages: English, Mandarin

Types of Therapy: AEDP, Attachment-based, CBT, Culturally Sensitive, Eclectic, Emotionally Focused, Humanistic, IFS, Narrative, Person-Centered, Psychoanalytic, Psychodynamic, Relational, Strength-Based, Trauma Focused

Clinical interests: identity, relationships, trauma, self-esteem, ADHD, anxiety, depression, and life adjustments. Serves marginalized communities including BIPOC, LGBTQ, women, expats, immigrants.
```

**Expected output:** verdict=SKIP or READ_MORE (score 4-5 max), tier_1.couples_certification=none, tier_2.couples_focus_pct=10-15, tier_4.asian_background=true, tier_5.top_school=true (Duke + Columbia), tier_5.independent_license=false. Red flags should include: "Bio is entirely individual-focused despite listing couples" and "Lists 15 modalities without clear couples-specific anchor."

### Validation Script: `validate.py`

Build this first. It runs both test cases through the scoring prompt and checks the outputs against expected ranges. If both pass, print "✓ Evaluator calibrated — ready to run." If either fails, print the actual vs expected values and stop.

```python
# Pseudocode for validation logic:
# 1. Load ANTHROPIC_API_KEY from .env
# 2. Run Travis Atkinson profile through scoring prompt
# 3. Assert: verdict == "CALL", composite_score >= 8, tier_1.couples_certification in ["certified", "supervisor_trainer"]
# 4. Run Brithny Zhang profile through scoring prompt
# 5. Assert: verdict in ["SKIP", "READ_MORE"], composite_score <= 6, tier_1.couples_certification == "none", tier_2.couples_focus_pct <= 20
# 6. If both pass: print success, exit 0
# 7. If either fails: print actual values vs expected, exit 1
```

**Do not proceed to the scraper until validate.py passes.**

---

## Step 1 — `scrape.py` — Discover Profile URLs

**Input:** Psychology Today filtered listing pages.

**Target filters:**
- Location: New York, NY
- Types of therapy: Couples Therapy (may be `spec=couples-counseling` or `therapy_type=...`)
- Treatment orientation: Emotionally Focused OR Gottman Method (run as two separate filtered searches)
- Client focus / Age: Adults
- Ethnicity: Asian
- Format: In-Person

**IMPORTANT:** Psychology Today filter parameter names change. Do NOT hardcode URLs. Instead:
1. Fetch the base page: `https://www.psychologytoday.com/us/therapists/ny/new-york`
2. Parse the HTML to find the actual form/filter field names and values
3. Build filtered URLs from the discovered parameters
4. If you can't determine parameters from HTML, fall back to these known working URLs and iterate from there:
   - `https://www.psychologytoday.com/us/therapists/ny/new-york?category=in-person&filters=2437,2681,3460,5942` (Marriage + EFT)
   - `https://www.psychologytoday.com/us/therapists/ny/new-york?category=in-person&filters=2437,2681,5942,3810` (Marriage + Gottman)
   - Add Asian ethnicity and Adult age filters to these

**Pagination:** Walk all pages. Look for "Next" links or page number patterns.

**Deduplication:** A therapist listing both EFT and Gottman appears in both searches. Deduplicate by profile ID (the numeric ID in the URL path, e.g., `120401` from `.../travis-atkinson-new-york-ny/120401`).

**Output:** `data/profile_urls.json` — deduplicated list of `{"url": "...", "profile_id": "...", "name_slug": "..."}` objects.

**Rate limiting:**
- Random delay: `random.uniform(5, 15)` seconds between requests
- Rotate User-Agent from a list of 5+ common browser strings
- On 429/403: exponential backoff (30s, 60s, 120s), max 3 retries via tenacity
- Log every request with timestamp to `data/scrape.log`

---

## Step 2 — `extract.py` — Fetch and Parse Each Profile

**Input:** `data/profile_urls.json`

**For each profile URL, fetch the page and extract:**

```python
{
    "url": str,
    "profile_id": str,
    "name": str,
    "credentials": str,  # "LCSW", "PhD", etc.
    "bio_narrative": str,  # The main bio paragraphs — the long-form text
    "specialties_top": list[str],  # "Top Specialties" section
    "specialties_all": list[str],  # Full "Expertise" section
    "therapy_types": list[str],  # "Types of Therapy" section
    "issues": list[str],  # Issues listed
    "client_focus_age": list[str],  # "Adults", "Teens", etc.
    "client_focus_participants": list[str],  # "Individuals", "Couples", "Family", "Group"
    "ethnicity": list[str],  # Ethnicity field
    "communities": list[str],  # Communities served
    "languages": list[str],
    "location": str,  # Office area
    "in_person": bool,
    "online": bool,
    "fee_individual": str | None,
    "fee_couples": str | None,
    "sliding_scale": bool,
    "insurance": list[str],
    "years_in_practice": str | None,  # Look for graduation dates, "X years" mentions
    "school": str | None,  # Education section
    "additional_credentials": list[str],  # Certificates, additional degrees
    "license_type": str | None,
    "license_number": str | None,
    "website_url": str | None,  # Personal website if listed
    "endorsements": list[dict] | None,  # Name + quote from endorsement section
    "qualifications_text": str,  # Raw text from qualifications section
    "treatment_approach_text": str,  # Raw text from treatment approach section
}
```

**Checkpoint/resume:** Save progress after each profile. If the script crashes at profile 150, it should resume from 151, not restart. Use a simple `data/extract_progress.json` tracking completed profile IDs.

**Output:** `data/profiles.json`

**Rate limiting:** Same as scrape.py.

---

## Step 2b — `enrich.py` (optional) — Fetch Personal Websites for Sparse Profiles

**Input:** Profiles from `data/profiles.json` where BOTH:
- `website_url` is not null
- `bio_narrative` is under 300 characters

**Action:** Fetch the personal website, extract readable text content (strip nav, footer, sidebars, boilerplate), store as `website_bio` field on the profile.

**Output:** Updates `data/profiles.json` in place.

**Rate limiting:** Conservative — `random.uniform(10, 20)` second delays. These are small practice websites.

---

## Step 3 — `evaluate.py` — Score Each Profile via Anthropic API

**Input:** `data/profiles.json`

**For each profile:**
1. Construct a text block from the profile fields (name, credentials, bio, specialties, therapy types, location, fees, education, etc.)
2. Send to Claude Sonnet as a user message with the scoring system prompt
3. Parse the JSON response
4. Store the full evaluation result alongside the profile

**Scoring system prompt:**

```
You are triaging couples therapist profiles for Charles and Angelina — an intercultural couple (Japanese husband, North Vietnamese wife) in NYC seeking experienced, direct, intellectually rigorous couples therapy.

For each profile, extract signals and score. Be ruthlessly honest. Most therapists are mediocre at couples work — your job is to find the exceptional ones.

SIGNAL HIERARCHY (what actually predicts extraordinary couples therapy, in order):

TIER 1 — CERTIFICATION (strongest signal):
- "Certified" in a couples modality (Certified EFT Therapist, Certified Gottman Therapist, AASECT Certified Sex Therapist) = requires 1000+ supervised clinical hours + peer review. This is the single strongest quality signal.
- "Trained in" or "Level 2/3" WITHOUT certification = they took workshops but haven't been evaluated. Much weaker.
- Supervisory role: do they train/supervise OTHER therapists? = top-tier signal. Someone clinicians pay to learn from.
- SPECIFICITY IS A SIGNAL: precise modality naming ("Certified Gottman Method 2006 + aspects of EFT", names the certificate/year) shows the therapist takes their craft seriously — upweight it. Vague "culturally sensitive / eclectic" with a 12+ modality laundry list is the opposite (generalist dilution), even if it technically lists couples modalities.

TIER 2 — PRACTICE COMPOSITION:
- What % of practice is couples? If bio only discusses individual work but checks "couples" box = individual therapist moonlighting.
- Does the bio describe HOW they work with couples (process, stages, what a session looks like)? = real couples therapist.
- Do they mention specific couples issues (resentment, infidelity, cultural dynamics, intimacy, trust repair)? = experienced.
- INDIVIDUAL-TAILORED LANGUAGE IS A DOWNGRADE even when a couples % is checked: bios written in singular "you," focused on self-esteem, "your past," "your authenticity," healing the individual = penalize. Couples-primary therapists write about the relationship, the cycle, and the two of you. (e.g. a profile reading individual-focused should not clear READ_MORE on couples-% alone.)

TIER 3 — STYLE MATCH:
- Active/directive vs passive/exploratory? Charles and Angelina both want active. Previous therapy failed because therapist just validated.
- Evidence of challenge ("I will push you," "expect to be uncomfortable," structured homework) = good match.
- Evidence of framework/plan (assessment phase, structured stages, exit criteria) = Charles's trust signal.

TIER 4 — CULTURAL FIT:
- Qualifying signal = Asian background OR lived intercultural experience. Charles and Angelina are themselves an intercultural couple; EITHER one passes the cultural filter. Neither alone guarantees a high score, and neither alone is disqualifying.
- Lived intercultural experience (immigrant, interracial marriage, raised across cultures — Travis Atkinson's "lived intercultural" profile is the anchor here) OUTRANKS an Asian-ethnicity checkbox with no couples depth. A non-Asian Certified EFT therapist with 20 years of intercultural couples work outranks an Asian individual-anxiety therapist.
- Do NOT auto-SKIP a profile for cultural reasons alone if it shows lived intercultural experience. East Asian fluency is ideal; South Asian / other intercultural backgrounds are "directionally there."
- Speaks Mandarin/Japanese/Vietnamese = bonus signal for depth, not just checkbox.

TIER 5 — CREDENTIAL SIGNALS:
- License level: LCSW/LCSW-R/LMFT/PhD/PsyD = independent. LMSW/MHC-LP = provisional (earlier career).
- School quality: top program = grit/intellect signal, but weaker predictor than certification for therapy quality.
- Years in practice: 7+ preferred, but a 7-year Certified Gottman therapist > a 20-year generalist.

TIER 6 — SOCIAL PROOF & RELEVANCE:
- Endorsements by OTHER therapists = positive signal (peers vouching). More than ~3 is notable; weight it. Discount endorsements that read generic or solicited.
- ADHD named as a TOP specialty alongside genuine couples work = small bonus — directly topical for Charles, who has lifelong severe ADHD. This is a tie-breaker bonus, not a substitute for couples depth.
- OFF-TARGET EMPHASIS is a NEGATIVE, not neutral: bios centered on unrelated populations/issues (heavy LGBTQ-affirming focus, neurodivergence, trauma/identity laundry lists, 12+ modalities) that crowd out couples-work signal = dilution. Penalize, do not reward as "breadth."

Respond ONLY in this JSON format (no markdown, no backticks, no preamble):
{
  "name": "therapist name",
  "tier_1": {
    "couples_certification": "none" | "trained_not_certified" | "certified" | "supervisor_trainer",
    "modality": "specific modality name or null",
    "certification_detail": "e.g. Certified EFT Therapist since 2019, or Level 2 Gottman trained"
  },
  "tier_2": {
    "couples_focus_pct": number 0-100 (estimate from bio emphasis),
    "describes_couples_process": true/false,
    "specific_couples_issues_named": ["list of specific issues mentioned"]
  },
  "tier_3": {
    "style": "active" | "passive" | "mixed" | "unclear",
    "evidence_of_challenge": true/false,
    "framework_plan_visible": true/false,
    "style_note": "brief evidence"
  },
  "tier_4": {
    "asian_background": true/false,
    "intercultural_experience": "none" | "checkbox" | "lived" | "specialty",
    "languages": ["list"],
    "cultural_note": "brief evidence"
  },
  "tier_5": {
    "license": "LCSW/LMFT/PhD/etc",
    "independent_license": true/false,
    "school": "school name(s) or unknown",
    "top_school": true/false,
    "years_est": "number or range",
    "sees_couples_and_families": true/false
  },
  "tier_6": {
    "endorsements_count": number or null,
    "endorsements_note": "brief note; flag if generic/solicited",
    "adhd_specialty": true/false,
    "off_target_emphasis": true/false
  },
  "composite_score": number 1-10,
  "verdict": "CALL" | "READ_MORE" | "SKIP",
  "one_line": "one sentence: why call or why skip",
  "red_flags": ["list any red flags, empty array if none"],
  "ask_on_call": "one targeted question based on profile gaps"
}

SCORING GUIDE:
8-10 = CALL (book consultation immediately)
5-7 = READ_MORE (check their website/reviews for more signal)
1-4 = SKIP

A score of 8+ requires EITHER:
- Couples certification (Tier 1) + cultural fit (Tier 4: Asian OR lived intercultural)
- OR supervisor/trainer level (Tier 1) + strong style match (Tier 3) even if cultural fit is weaker

A SKIP means: no couples certification AND no evidence of couples-primary practice, OR bio is entirely individual-focused despite listing couples. Do NOT SKIP solely for lacking Asian ethnicity if lived intercultural experience is present.

CALIBRATION NOTES (from Charles's 20-profile review — apply these corrections):
- The prior prompt UNDER-scored Asian/intercultural therapists who name specific couples modalities and show couples focus (these belong at 6-7 READ_MORE, not 5). Specific modality naming + couples focus + NYC + peer endorsements should pull up.
- The prior prompt OVER-scored therapists with individual-tailored bio language despite a couples-% checkbox (these belong at ~5, not 7).
- "Directionally there" non-East-Asian intercultural profiles should not be floored to SKIP on culture alone.
```

**Deterministic guardrail (post-processing in `evaluate.py::apply_guardrails`):**
After the LLM returns its JSON, a deterministic rule promotes gold-standard
"Travis-type" profiles to CALL so the noisy 1-10 middle band can never bury them
(promotes only, never demotes; records `ev["guardrail"]`):
- Pattern A: `tier_1.couples_certification == "supervisor_trainer"` AND couples_focus_pct >= 40 -> score = max(score, 8), verdict = CALL.
- Pattern B: `certified` + `tier_3.style == "active"` + cultural fit (Asian OR lived/specialty intercultural) + couples_focus_pct >= 50 -> CALL.
Validated selective: only 2/474 profiles promoted on the existing run.

**API configuration (LOCKED via consistency + calibration experiments, 2026-06-30):**
- Model: `claude-sonnet-4-5-20250929` (Sonnet 4.5)
- Extended thinking: OFF (thinking forces temperature=1 and reintroduces score variance; validated)
- Max tokens: 1200
- Temperature: 0  (deterministic; 18/20 within Charles's +/-1 calibration grace)
- Rate limit: max 5 requests per minute (stay well under API limits)
- Retry: 3 attempts with exponential backoff via tenacity
- Checkpoint/resume: track completed profile IDs, resume on restart
- Model comparison: Haiku 4.5 no_thinking was a near-tie (17/20, ~2.5x cheaper) but had one more verdict-band error; Opus 4.8 did NOT match Charles's higher scores any better (and forbids temperature=0, so it is less consistent).

**Output:**
- `data/evaluated.json` — full results with all tier scores
- `data/results.csv` — ranked summary sorted by: CALL first (descending score), then READ_MORE (descending), then SKIP:

```csv
Name,Verdict,Score,Certification,CouplesPercent,Style,Asian,Languages,School,License,Years,FeeCouples,Website,PT_URL,OneLine,AskOnCall
```

---

## Step 4 — `run.py` — Orchestrator

Runs all steps in sequence. If any step fails, stop and report which step failed.

```python
import subprocess, sys

steps = [
    ("Validating evaluator...", "validate.py"),
    ("Scraping listing pages...", "scrape.py"),
    ("Extracting profiles...", "extract.py"),
    ("Enriching sparse profiles...", "enrich.py"),
    ("Evaluating profiles...", "evaluate.py"),
]

for label, script in steps:
    print(f"\n{'='*60}\n{label}\n{'='*60}")
    result = subprocess.run([sys.executable, script])
    if result.returncode != 0:
        print(f"\n✗ FAILED at {script}. Check logs in data/")
        sys.exit(1)

print("\n✓ Done. Results in data/results.csv")
```

---

## File Structure

```
therapist-search/
├── .env                    # ANTHROPIC_API_KEY (gitignored)
├── .gitignore
├── pyproject.toml          # uv project config
├── run.py                  # orchestrator
├── validate.py             # evaluator calibration (run first)
├── scrape.py               # listing pages → profile URLs
├── extract.py              # profile pages → structured JSON
├── enrich.py               # personal websites for sparse bios
├── evaluate.py             # Anthropic API scoring → CSV
└── data/
    ├── scrape.log           # request log with timestamps
    ├── extract_progress.json # checkpoint for resume
    ├── profile_urls.json    # deduplicated URLs
    ├── profiles.json        # extracted profile data
    ├── evaluated.json       # full evaluation results
    └── results.csv          # final ranked output
```

---

## Execution Notes

- **Run overnight.** Start around midnight to minimize rate limiting risk.
- **Psychology Today may block scraping.** If you get persistent 403s after backoff, the manual fallback is: open the listing pages in your browser, Cmd+S to save as HTML files into `data/saved_pages/`, and modify scrape.py to parse saved HTML instead of live-fetching. The evaluation layer (the actual value) runs the same either way.
- **Total API cost:** ~275 profiles × ~1200 output tokens × Sonnet pricing ≈ $0.50–$1.00 total. Trivial.
- **The enrich.py step is optional.** If you want to skip it for the first run, that's fine — the evaluator will assign READ_MORE verdicts to sparse profiles and you can manually check those websites.
- **After the run:** Open `data/results.csv` in Google Sheets. Filter to CALL verdicts. Those are your consultation candidates. READ_MORE verdicts with high tier_4 (cultural fit) scores are worth manually checking.
