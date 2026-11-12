# Resources: everything a run needs

Register, 2026-11-11. Companion to `docs/system-brief.md` (section 14) and `docs/harness.md`.
`kit/resources.json` is the same list in the form `factory doctor` checks. The evidence, with sources
and what was not verified, is in `docs/research/06-external-resources.md`. Prices are list prices read
on 2026-11-11.

Machine-specific values (paths, hosts, account names) are not in this repository. They are settings in
a production's gitignored `production.local.env`, and key files in the directory its `SECRETS_DIR` names.

## 1. Three ways to reach a resource

The rule is "inference by login, tools by key". The research shows a third case the rule did not name.

| Kind | Used for | Who sets it up | Who holds it at run time |
|---|---|---|---|
| **Login** (subscription OAuth) | every language model; the one generator the owner uses by subscription | the owner, in a browser | the launcher (token on a file descriptor) or the local proxy |
| **Key** (API key) | generators, voice, render nodes, storage | the owner creates the account and the key once | `factory run`, as a key file. Never a worker |
| **Grant** (OAuth consent to an app the owner registers) | YouTube, TikTok, Instagram. None of them offers a key | the owner registers the app and consents once | `factory run`. One program refreshes each token on a schedule |
| **None** | local tools, the engine at run time | an agent installs it | nobody |

Consequences:

- A grant needs upkeep that a key does not. TikTok's refresh token rotates on every refresh, so
  exactly one program may refresh it and must store the new one each time. Instagram's token ends if
  60 days pass without a refresh. `factory tick` runs the refresh; `factory doctor` reports a grant
  that is close to lapsing.
- Image, 3D, voice and music generation are model inference too. This register follows the owner's
  reading that they are tools and are paid by key.
- No role file may name an inference API key, and the launcher never starts the harness in a mode
  that needs one.

## 2. The register

"State" is the owner's setup on 2026-11-11: **have**, **partly**, or **missing**. "Wave" is the build
wave that first needs the resource (`docs/system-brief.md`, section 17).

### Runtime: all rooms

| Id | What | Kind | State | Wave | Note |
|---|---|---|---|---|---|
| `harness` | hbar, one pinned build | none | have | 2 | The headless flags in `docs/harness.md` section 3 all exist in the current build. |
| `inference.primary` | Claude subscription logins for the maker and checker slots | login | have | 2 | Several accounts hold a long-lived inference-only token. Their expiry dates are not recorded anywhere; record them at setup. Out-of-usage is the failure seen in practice, hence the account pool. |
| `inference.other` | A second model family for judges | login | have | 2 | A local proxy already holds a ChatGPT login and an xAI login and speaks the harness's API. It is shared with other systems on the same machine, so a failure there is not always the factory's. Optional: without it a launch records `family: same`. |
| `knowledge.fermi` | Fermi MCP: skills, memory, search | login | partly | 2 | The server exists. It has no reduced access: a login exposes every tool, including secrets and desktop control, and its machine tokens get skills only. The knowledge gateway (`docs/harness.md`, section 6) is the wave 2 answer. See decision D2. |
| `state.github` | Issues, pull requests, markers | login | have | 2 | Held by the programs only. The machine setting `GITHUB_LOGINS` names the logins whose comments the kit reads (`docs/harness.md` section 8). |
| sandbox | A per-launch `sandbox-exec` profile | none | have | 2 | Built and tested in the kit (`docs/harness.md`, section 7). Each machine lists its own extra login stores in `SANDBOX_DENY`. First run under a real worker turn on 2026-11-12 (#85): the pinned build starts and finishes a turn under the profile. |

### Story room

| Id | What | Kind | State | Wave | Note |
|---|---|---|---|---|---|
| `gen.image.login` | Grok Imagine for story panels and image-to-video | login | have | 3 | By subscription login through the local proxy, never a key (owner's rule). The login can end without warning; the gate must tell "login ended" from "job failed", pause the step and raise `resource:down`. |
| `gen.image.key` | A keyed image model for panels and edits | key | have (keys exist) | 3 | Optional second route. Gemini API image models cost $0.03 to $0.13 per image; fal.ai reaches several models with one key. A 60-panel board is about $2 to $8 before retries. |
| `store.media` | Bucket with public URLs for every media file | key | partly | 3 | A public bucket exists. `factory media` is the client. No key file is set aside for the factory. Instagram also needs it: a Reel is published from a public URL. |
| `local.ffmpeg` | ffmpeg and ffprobe with loudness, similarity and detection filters | none | have | 3 | Every filter the checks name is present. The installed build is not native to the machine's processor, so it runs slower than it could. |

### Story room: other people's videos

Added 2026-11-12 (owner request: trend research and external video ingestion). The evidence is in
`docs/research/07-external-trends-and-ingestion.md`. These four are not in `kit/resources.json` yet:
the item that builds each tool adds its row.

| Id | What | Kind | State | Wave | Note |
|---|---|---|---|---|---|
| `external.youtube` | Public videos by topic, with counts, the author's other videos and comments | grant | missing | 6 | The project of step 3 is enough, and an API key in it would do without the upload grant. Free. 100 searches a day; other reads share 10,000 units a day. Counts of other people's videos may be kept 30 days at most. This project must never scrape YouTube or receive scraped YouTube data. |
| `external.instagram` | Views, likes, comments and followers for a watch list of named professional accounts | grant | missing | 6 | Only with the Meta app on Facebook Login and a linked Page (R10). No trending list by topic exists. A hashtag search gives likes and comments only, for 30 hashtags in 7 days, and needs one more feature that may need app review. |
| `external.tiktok` | Rising hashtags, and the ordered top 20 videos per hashtag with no counts | grant | missing | 6 | The posting app gives nothing here. Needs a TikTok For Business account, a Business API app and an ad account (step 8), the same account and app as `platform.tiktok.insights`. Free. |
| `external.provider` | Per-video counts for TikTok and Instagram from a paid scraper | key | missing | 6 | Optional, and only after decision D6. $1.50 to $1.70 per 1,000 results. Never for YouTube data. |

### Production room

| Id | What | Kind | State | Wave | Note |
|---|---|---|---|---|---|
| `engine.unreal` | Unreal Engine 5.8.x, pinned | none | missing | 4 | Needs an Epic account, and a linked GitHub account for the Linux container images. Free for linear content below $1 million annual revenue, then $1,850 per seat per year. |
| `render.node` | A GPU node a program can start and stop | key | missing | 4 | The owner's laptop has 16 GB of memory against Epic's 32 GB recommendation, and no command-line Movie Render Graph run on macOS is documented. Plan on a cloud node; prove the laptop with a smoke test before relying on it. See decision D3. |
| `store.assets` | Engine binaries stored by content hash | key | missing | 4 | Generated binaries go to a private bucket and git records the hash. `factory assets` is the client: it stores an object under its sha256, signs every request and never overwrites. The key file is three lines: access key id, secret access key, region. A small private Git LFS repository holds the hand-approved source assets. The public repository holds no LFS objects. |
| `local.metrics` | Perceptual and character-identity metrics | none | missing | 4 | No open-source identity metric is validated on stylised 3D renders. DreamSim, CCIP and DINOv2 are the candidates; wave 4 calibrates one on the production's own characters. The usual face-identity models are licensed for research only and are excluded. |
| `gen.model3d` | Text or image to 3D | key | have (key exists) | 5 | fal.ai (one key, several models, $0.02 to $0.80 per model), Tripo ($0.20 to $0.30) or Meshy (about $0.60). |
| `gen.rig` | Automatic rigging | key | missing | 5 | Tripo ($0.25) or Meshy. Body only, humanoid only; no service rigs a face. |
| `gen.motion` | Text to motion, or motion capture from video | key | missing | 5 | Optional. Motion capture needs a human performer, so text to motion and animation libraries fit better. |
| `gen.lipsync` | Audio to facial animation | none | missing | 5 | Unsolved for a stylised character: Epic's tool runs headless but only drives its own face rig, and NVIDIA's gives portable curves but its engine plugin is two versions behind. Wave 4 needs a spike. |
| `gen.voice` | Text to speech, one stored voice per character | key | have (key exists) | 5 | ElevenLabs: $0.04 to $0.08 per 1,000 characters, about $0.50 for a 10-minute episode. Commercial use needs a paid plan. |
| `gen.music` | Music with one licence for all three platforms | key | have (same key) | 5 | ElevenLabs Music is the only source found with both a key and a published rights table that covers social video ($0.15 per minute). A licensed library by subscription is the alternative. Prompts must not name artists or songs. |
| `gen.sfx` | Sound effects | key | have (same key) | 5 | Same vendor and key. |
| `local.timeline` | OpenTimelineIO, for the conform | none | missing | 5 | A Python package. |

### Post room

| Id | What | Kind | State | Wave | Note |
|---|---|---|---|---|---|
| `local.ocr` | OCR for caption and safe-area checks | none | have | 6 | English only today. |
| `local.transcribe` | Local speech to text for caption and dialogue checks | none | partly | 6 | The program is installed; no model file is. |

### Distribution room and the lab

| Id | What | Kind | State | Wave | Note |
|---|---|---|---|---|---|
| `platform.youtube` | Upload and analytics | grant | missing | 5 | No audit is needed for public uploads since 2026-11-02. 100 uploads a day. The OAuth app must be set to "In production": in "Testing" the grant ends every 7 days. Analytics gives retention curves per video. |
| `platform.instagram` | Reels publishing and insights | grant | missing | 5 | No app review for an account the owner owns. Needs a professional account. There is no private upload: tests need a separate test account. Insights give average watch time and skip rate. |
| `platform.tiktok` | Posting and counts | grant | missing | 5 | The longest gate, and it may not open. See section 4. |
| `platform.tiktok.insights` | Watch-through | grant | missing | 6 | Not in the ordinary API. Needs a business account and a second app. Optional. |

Disclosure fields exist on all three and the upload job sets them: `containsSyntheticMedia` (YouTube),
`is_aigc` (TikTok), `is_ai_generated` (Instagram).

## 3. What the owner has to do, by lead time

Everything else an agent can do. Start the long ones in wave 4, as the build order says.

| # | Step | Lead time | Blocks |
|---|---|---|---|
| 1 | TikTok: developer account, app, submit for review, then ask for the direct-post audit | review takes days to two weeks; the audit has no published time and may be refused | public TikTok posts |
| 2 | Cloud GPU account, billing and (on AWS) a GPU quota request | not verified | cloud renders |
| 3 | Google Cloud project, both YouTube APIs, OAuth app set to "In production", one consent | about an hour | YouTube |
| 4 | Instagram professional account, Meta developer app, one login; a second test account | about an hour | Instagram |
| 5 | Epic account, linked GitHub account | an hour plus the download | all production work |
| 6 | Key files for the factory: image, 3D, voice, media store, asset store | minutes each | generation, storage |
| 7 | Logins: a yearly token per inference account; the proxy logins when they end | minutes each | inference, Grok Imagine |
| 8 | TikTok For Business account, a Business API app and an ad account | not verified | TikTok trend reads, and the optional watch-through |

## 4. TikTok

TikTok's rules are written for an app with a person in front of it, and the factory is not that.

- Until an app passes the audit, its posts are private and the posting account must itself be private.
- The audit guidelines say a client must be meant for a wide audience, "not limited to internal
  groups/private use". A one-owner posting tool may be refused.
- The guidelines also require a preview and express consent before each post.

So `TIKTOK_POSTING` in `production.env` has three values, and the kit supports all three from the start:

| Value | What the upload job does | Human step |
|---|---|---|
| `inbox` (default) | sends the video to the account's inbox as a draft | the owner taps to post |
| `private` | posts as visible to the owner only | the owner switches it to public |
| `direct` | posts publicly | none. Only after the audit passes |

## 5. Decisions

Reversible choices are taken here so the build does not wait. The owner can overrule any of them.

| # | Taken | Why | To overrule |
|---|---|---|---|
| R1 | One vendor key where one covers several slots: ElevenLabs for voice, music and effects; fal.ai for keyed images and 3D | fewer accounts, and both keys already exist | name another vendor per slot in `production.env` |
| R2 | The second model family runs through the local proxy on the same harness, not through a second harness | one harness is a rule of the design, and the logins are already there | set a different harness for a role in `production.env` |
| R3 | Gemini is not a judge | its command-line login is interactive only, and Google's terms name third-party use of that login as a violation. A Gemini judge would need an inference key | allow one inference key as an exception |
| R4 | Generated engine binaries live in a bucket by content hash, not in git | LFS on a public repository spends the owner's bandwidth on every clone, and every changed binary counts in full | use LFS or Perforce |
| R5 | TikTok starts in `inbox` mode | it works without the audit | change `TIKTOK_POSTING` |
| R6 | Fermi is reached through the local knowledge gateway | it needs no change to the live server | D2 |
| R7 | Other people's videos are read through the platforms' official APIs only, YouTube first | it is the one complete official route and needs nothing new from the owner | D5, D6 |
| R8 | Platform data that must expire never enters git. Counts live in the state directory with a read time and are refreshed or deleted within 30 days; the repository keeps the link, our notes and the rank at read time | YouTube's policy allows 30 days, and git history cannot forget | none while the policy stands |
| R9 | The factory does not fetch other people's video files, frames, audio or thumbnails | no official route delivers them and every other fetch breaks the sites' terms | D5 |
| R10 | The Meta app is built on Facebook Login with a linked Page | an app has one login type, and only this one reads other accounts | build it on Instagram Login; `external.instagram` is then not available |

These need the owner. Each holds only the item it names.

| # | Question | Why it is the owner's | Holds |
|---|---|---|---|
| D1 | **Is subscription login allowed for this use?** Anthropic's page says subscription OAuth supports "ordinary use of Claude Code", protects signing in to "the unmodified Claude Code binary", and says developers building products "should use API key authentication". hbar is not the unmodified binary, and many unattended agents may not be ordinary use. The page says to ask sales. OpenAI's Codex pages say API keys are "the right default for automation" and warn against the account route for public repositories; xAI says to use a key outside a browser. | it is the owner's accounts and the owner's agreement with each vendor | nothing yet. The design depends on the answer |
| D2 | Add a scoped token to Fermi (memory, skills and search only, with a namespace per production)? | it changes the live Fermi server | nothing. The gateway is used until then |
| D3 | Which render node: a cloud Windows node (about $1.00 to $1.20 an hour on AWS), a cheaper Linux node with Epic's container image ($0.35 to $0.90 an hour), or a machine the owner buys | money and a new account | wave 4 renders |
| D4 | Confirm the platform accounts the sample production posts from, and that each may carry AI-disclosed content | accounts and publishing | wave 5 uploads |
| D5 | May the factory fetch other people's video files to analyse a temporary copy (transcript, cut rate, opening hook, on-screen text) and delete it? | it breaks all three sites' terms, and for YouTube it breaks the developer policies that bind the project the production uploads with | only the analysis of a temporary copy. Until then: links, counts and comments |
| D6 | Pay a data provider for TikTok and Instagram counts? Under $10 a month at 500 results a week per platform | money, and the data is scraped | per-video counts on those two platforms |
| D7 | Which works may the sample production draw on, and on what basis (own, licence, permission, public domain)? (#276) | rights are the owner's to state, and only the owner writes `sources/rights.tsv` | only the first public upload of the sample. Until then the material the owner supplied counts as cleared inside the private repository |

## 6. Rough running cost at list price

Only where a price was read. Inference is the flat cost of the subscriptions.

| Item | Cost |
|---|---|
| Story panels, 60 at 1K | $2 to $8, or nothing extra by subscription login |
| Voice, 10 minutes of dialogue | about $0.50 |
| Music, 10 minutes | $1.50 |
| One 3D model with a rig | $0.45 to $0.85 |
| Render node | $0.35 to $1.20 an hour |
| YouTube, TikTok, Instagram | free |
| Other people's videos, official routes | free |
| Other people's videos, a provider (D6) | $1.50 to $1.70 per 1,000 results |

## 7. Not verified

The build must not depend on these until someone checks them. The full list is in the research file.

- A Movie Render Graph render started from the command line on macOS, and Chaos caches on macOS.
- Whether TikTok's audit accepts a one-owner client, and how long the audit takes.
- How long the subscription logins really last unattended: the inference tokens' expiry dates, and
  the proxy logins over weeks.
- Any identity metric on stylised 3D renders.
- Lip sync for a non-MetaHuman character on the pinned engine version.
- Commercial licence terms for sound effects, and for the 3D services' outputs.
- Whether the existing vendor keys are on plans that include commercial use.
