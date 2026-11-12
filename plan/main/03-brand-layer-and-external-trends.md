# Brand layer for post, external trend research, external video ingestion

Owner request, 2026-11-12: (1) a layer of brand specifics so the post room packages to the brand's
identity; (2) external research for the researcher roles: trending videos for candidate storytelling
topics, ranked by metrics and interactions, to judge whether a trend is usable; (3) an ingestion
pipeline for external video on TikTok, Instagram and YouTube, because trends differ by platform.

Success: the brief says what a brand kit is and the platform gate fails a variant that breaks it; a
research file says, with sources, what each platform lets a program read about videos the production
does not own; every part is a registered item on GitHub.

## What exists today (main at 71a1545)

- Post room (brief 3, 4, 6): takes in the locked master, the intent packet and the platform rulebook.
  `charter-judge` checks against the show's charter, `packaging-judge` the packaging. No brand anywhere.
- `researcher` (maker, stage `research`) and `trend-scout` (analyst) hold web search and page fetch
  only. Both already read `sources/**`. A web page is not a source file, so what rests on it is
  `[inferred]`. Analysts cannot be launched until #168.
- `docs/research/06-external-resources.md` and `docs/resources.md` cover the production's own posts
  and accounts (#203, #205, #206), not other people's videos.

## Brand layer (rulings, reversible)

1. One production, one brand. The kit ships none; `brand/brand.json` and `brand/voice.md` live in the
   production repository.
2. No role writes `brand/**`. The owner sets it once, like the caps and the first charter.
3. The brand governs packaging only. On a hard platform limit the rulebook wins.
4. No new role: a `brand` check in the platform gate, and `packaging-judge` scores against the voice.

- [ ] #259 (w6-28) the kit's shape in the brief and a fixture kit (orchestrator)
- [ ] #260 (w6-29) the `brand` check and `factory init` (builder; after #166 and #259)
- [ ] #261 (w6-30) the sample's brand kit from the Primal material (orchestrator; after #259, #179)
- [x] #164 carries the ruling that post-room role files read `brand/**`

## External trends and ingestion (direction, to be confirmed by the research)

1. Ingestion is a program, not an agent: keyed reads through `factory run`, results as files under
   `sources/external/<platform>/`, which the two roles already read.
2. One tool and one ranking per platform; never merged across platforms.
3. Third-party bytes never enter git or the public media store: analyse a temporary copy, delete it,
   keep metadata, metrics and derived notes.
4. A trend is a symptom that becomes a pivot proposal; the showrunner triages; `distinctiveness-judge`
   and the "chasing metrics" monitor apply.
5. Posting accounts are never used to read other people's videos.

- [x] #262 (w6-31) `docs/research/07-external-trends-and-ingestion.md` on main (48c14ff), with the register
      rows, rulings R7 to R10, decisions D5 and D6 and owner step 8 in `docs/resources.md`
- [ ] #266 (w6-32) the brief: topics, reads by a program, expiry, what git keeps (orchestrator)
- [ ] #267 (w6-33) YouTube read tool (builder; after #266, #205)
- [ ] #268 (w6-34) ranking per platform and the trend card (builder; after #266; needs no key)
- [ ] #269 (w6-35) snapshot at launch and the two role files (builder; after #268; trend-scout after #168)
- [ ] #270 (w6-36) Instagram watch list (builder; after #266, #206 on Facebook Login)
- [ ] #271 (w6-37) TikTok Discovery (builder; after #266, #265)
- [ ] #272 (w6-38) analysis of a temporary copy (held by the owner's decision #263)
- [ ] owner: #263 D5 fetch files or not, #264 D6 a paid provider, #265 TikTok For Business

What the research changed (2026-11-12):

- Direction 1 was wrong in one point. YouTube allows counts of other people's videos to be kept 30
  days, and git cannot forget. Reads live in the state directory; git keeps the link, the rank at read
  time and our notes (R8).
- No official route delivers a video file or its captions (Instagram excepted, when the video's owner
  allows it). So "ingestion" is links, counts and comments until the owner rules on #263 (R9).
- YouTube is complete and free with #205: every ranking works. TikTok gives a commercial user rising
  hashtags and an ordered top 20 with no counts. Instagram gives views only for accounts named in
  advance, and only if the Meta app is on Facebook Login (R10, noted on #206 before the app exists).
