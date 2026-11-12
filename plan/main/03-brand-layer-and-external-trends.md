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

- [ ] #262 (w6-31) `docs/research/07-external-trends-and-ingestion.md` (drafter launched 2026-11-12)
- [ ] register the kit items and owner steps that follow from it (tools per platform, ranking, analysis
      of a temporary copy, role file changes, whether analyst launches move ahead of #168)
