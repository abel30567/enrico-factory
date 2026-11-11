# External resources: research (2026-11-11)

Research input for `docs/resources.md`, which is the register the build follows. Kept as written.

Date: 2026-11-11. Scope: every external resource the factory needs at run time, how an unattended agent authenticates to it, who does the one-time setup, lead time and cost. Research only; nothing was signed up for and no credential or paid API was used.

Evidence marks: **[verified]** I read the primary page on 2026-11-11. **[secondary]** from a search summary, a third-party page or the project's prior research. **[unverified]** not confirmed; do not plan on it without a check.

Prior research read first and not repeated: `docs/research/01-production-practice.md`, `04-3d-pipeline.md`, `05-ue5.md` in the enrico-factory repo.

## Headline findings

1. **TikTok is the longest gate and may not open.** Public posting needs app review (days to two weeks) and then a Direct Post audit. Until the audit, posts are private-only from a private account. TikTok's guidelines say audited clients must not be for "internal groups/private use", which describes this factory. Build the inbox-upload fallback from day one.
2. **YouTube needs no audit.** Since 2026-11-02, uploads from unaudited API projects are no longer forced private. The default quota is 100 uploads per day. The one trap: an OAuth app left in "Testing" loses its refresh token every 7 days.
3. **Instagram needs no app review** for an account the owner owns. It needs a professional account, a 60-day token that the agent must refresh, and a public URL for each video file.
4. **No distribution API takes an API key.** All three are OAuth user logins: one browser consent each by the owner, then scheduled refresh by an agent.
5. **Watch-through telemetry:** YouTube Analytics and Instagram insights expose it. TikTok's Display API does not; it needs the TikTok API for Business and a Business account.
6. **Render nodes:** Paperspace no longer offers Windows to new accounts. AWS g5.xlarge Windows is $1.19 per hour; TensorDock lists an RTX 4090 from $0.35 per hour. Epic publishes Linux container images, which makes cheaper Linux GPU nodes an option.
7. **Unreal licence:** free for linear content under $1 million annual revenue; above that, $1,850 per seat per year, no royalty.
8. **Second model family by OAuth:** Codex CLI with a ChatGPT login is documented for non-interactive use. Grok has a documented subscription route. Gemini CLI does not support subscription login for headless runs, and Google's terms prohibit third-party use of its OAuth.
9. **The OAuth-for-inference rule has open terms questions** with Anthropic (wording on "ordinary use" and the "unmodified Claude Code binary") and OpenAI (account auth in automation: "Do not use this workflow for public or open-source repositories"). See sections 6 and 8.

## Summary table

"Human" means the owner in a browser. Cost is list price read on 2026-11-11.

| Resource | Room / role | Auth type | Human setup needed | Lead time | Cost | Evidence |
|---|---|---|---|---|---|---|
| Claude subscription (Claude Code-compatible harness) | All rooms, inference | OAuth user login; one-year token from `claude setup-token` | Yes: browser login once a year | Minutes | Subscription | [verified]; terms question open (6.1) |
| xAI Grok subscription (Grok Build or OpenCode) | Story judges, writers | OAuth user login, device-code option | Yes: browser or device code | Minutes | SuperGrok, about $30 per month [secondary] | [verified] login route |
| ChatGPT login for Codex CLI | Judges (second family) | OAuth user login; cached and auto-refreshed; device code (beta) | Yes: browser login, MFA, enable device code | Minutes | ChatGPT plan; limits per model | [verified] |
| Gemini / Antigravity by Google login | Judges (candidate) | OAuth login is interactive only; headless needs an API key | n/a | n/a | n/a | [verified]: not viable under the rule |
| Fermi MCP | All rooms, memory | Assumed to exist | Not researched | | | Out of scope |
| YouTube Data API v3 | Distribution, posting | OAuth 2.0, `youtube.upload` | Yes: Google Cloud project, consent screen set to "In production", one consent | Hours. No audit needed for public uploads. | Free; 100 uploads per day | [verified] |
| YouTube Analytics API | Distribution, telemetry; autoresearch | OAuth 2.0, `yt-analytics.readonly` | Same consent | Same | Free | [verified] |
| TikTok Content Posting API | Distribution, posting | OAuth 2.0, `video.publish` / `video.upload`; 2-hour access token, 180-day rotating refresh token | Yes: developer account, app, app review, then Direct Post audit, then consent | App review "several days to two weeks"; audit time unpublished; audit may refuse a private-use client | Free; about 15 posts per account per day | [verified] |
| TikTok Display API | Distribution, telemetry (counts only) | OAuth 2.0, `video.list` | Same app and review | Same | Free | [verified] |
| TikTok API for Business (Accounts) | Distribution, watch-through | OAuth-style access token on a separate portal; Business account | Yes: Business account and second app | Not verified | Free | [secondary] |
| Instagram API (publishing, insights) | Distribution | OAuth user token; 60 days, refreshable; no refresh token | Yes: professional account, Meta developer app, one login | Hours. No app review for own account. | Free; 100 posts per 24 hours | [verified] |
| Public file host for Reels (`video_url`) | Post, distribution | API key or cloud credentials | Yes: create a bucket | Minutes | Storage and egress | Requirement [verified]; provider not researched |
| Epic Games account (+ linked GitHub) | Production, engine install | Browser login; none at run time | Yes, once | Minutes | Free under $1M revenue | [verified] |
| AWS EC2 GPU node (g5 / g6, Windows or Linux) | Production, render | IAM access key | Yes: account, billing, GPU quota request | Quota approval time not verified | $0.99 to $1.19 per hour Windows | [verified] price |
| TensorDock GPU node (RTX 4090) | Production, render | API key; prepaid deposit | Yes: account and deposit | Minutes | From $0.35 per hour plus CPU, RAM, disk | [verified] price; API [secondary] |
| RunPod GPU pod (Linux only) | Production, render | API key | Yes: account and funds | Minutes | RTX 4090 $0.89 per hour as listed | [verified] price; API [unverified] |
| Git LFS on GitHub | Production, binary assets | Git token or SSH key | No, if agents already have repo access | None | 10 GiB free, then paid | [verified] |
| Perforce P4 free tier | Production, binary assets (alternative) | P4 user and ticket | Yes: host a server | Hours | Free to 5 users / 20 workspaces | [secondary] |
| Meshy API | Production, assets and rigging | API key (Bearer) | Yes: account, plan, key | Minutes | About $0.60 per textured model (derived); Pro plan $20 per month | [verified] |
| Tripo API | Production, assets and rigging | API key | Yes: account, credits, key | Minutes | $0.20 to $0.30 per model; rig $0.25 | [verified] |
| Rodin (Hyper3D) API | Production, assets | API key (Bearer) | Yes: Business plan, key | Minutes | $120 per month plan; per asset not read | [verified] plan |
| fal.ai | Story (images), production (3D) | API key (`FAL_KEY`) | Yes: account, billing, key | Minutes | Per model, for example $0.04 to $0.15 per image | [verified] |
| Move.ai API | Production, mocap (optional) | API key | Yes | Minutes | $0.012 to $0.024 per second of video | [verified] |
| MetaHuman Animator (audio-driven) | Production, lip sync | None (local, ships with UE) | No | None | Free | [verified]; MetaHuman rig only |
| NVIDIA Audio2Face-3D SDK | Production, lip sync | None (local) | No; licence acceptance | None | Free; NVIDIA licence | [verified] repo; UE 5.8 fit [unverified] |
| ElevenLabs (TTS, music, SFX) | Production, sound | API key | Yes: account, plan, key | Minutes | TTS $0.04 to $0.08 per 1K characters; music $0.15 per minute; Starter $6 per month | [verified] |
| Cartesia (TTS alternative) | Production, sound | API key | Yes | Minutes | $5 per month for 100k characters | [verified] |
| Epidemic Sound MCP (music, SFX library) | Production, sound | MCP API key, valid one year | Yes: subscription and key, renewed yearly | Minutes | Subscription; price not read | [verified] docs |
| Stability AI (Stable Audio) | Production, sound (alternative) | API key | Yes | Minutes | $0.08 per generation | [verified] price; licence [secondary] |
| Freesound API | Production, SFX (alternative) | API token; OAuth2 for downloads | Yes; commercial API terms negotiated case by case | Unknown | Free non-commercial | [verified] |
| Gemini API image models | Story, panels | API key | Yes: AI Studio key with billing | Minutes | $0.034 to $0.134 per 1K image | [verified] |
| xAI Grok Imagine | Story, panels | Subscription login through the owner's proxy | Yes: browser login, repeated on expiry | Minutes | Subscription | From the brief |
| ffmpeg, libvmaf, OpenTimelineIO, Tesseract, DreamSim, CCIP, DINOv2, pyloudnorm, PySceneDetect | Post, checks, autoresearch | None | No | None | Free | [verified] maintained |

## 1. Distribution APIs

All three platforms use OAuth 2.0 user logins. None offers an API key for posting. The owner must consent once in a browser per platform; after that an agent can refresh tokens on its own, provided it refreshes on a schedule.

### 1.1 YouTube Data API v3 and YouTube Analytics API

| Item | Finding | Evidence |
|---|---|---|
| Upload scope | `https://www.googleapis.com/auth/youtube.upload` for `videos.insert` | [verified] |
| Analytics scopes | `yt-analytics.readonly`; `yt-analytics-monetary.readonly` only if revenue metrics are wanted | [verified] |
| Token behaviour, app in "Testing" | Consent and the refresh token expire 7 days after consent. Up to 100 test users. | [verified] |
| Token behaviour, app "In production" | Refresh token has no fixed lifetime. Google's general rules (revoked after about 6 months unused, on password change for some scopes, 100-token cap per client) were not re-read in this run. | [unverified] |
| OAuth verification | `youtube.upload` is a sensitive scope, so a production app shows an "unverified app" warning until verified. Google's help page exempts personal-use apps with fewer than 100 users: the owner clicks through the warning once. | [verified] |
| Public posting before an audit | Allowed since 2026-11-02. The revision history says uploads from unverified API projects "are no longer restricted to private viewing mode", reversing the 2020-07-28 policy. | [verified] |
| Default privacy | `status.privacyStatus` defaults to `public` if omitted. Set it explicitly (`private`, `unlisted`, `public`). | [verified] |
| Quota | 100 `videos.insert` calls per day in a separate Video Uploads bucket (1 unit each), 100 `search.list` calls per day, 10,000 units per day for everything else. | [verified] |
| Compliance audit | Needed only to request quota above the default. It is a form (YouTube API Services Audit and Quota Extension). The page gives no turnaround time. | [verified] for the rule, lead time [unverified] |
| AI disclosure | `status.containsSyntheticMedia` (boolean), settable on `videos.insert` and `videos.update`, added 2024-10-30. It covers realistic altered or synthetic content. | [verified] |
| Other fields | `status.selfDeclaredMadeForKids`, `status.publishAt` (scheduled publish, needs `private`). `thumbnails.set` supports Shorts custom thumbnails since 2026-11-03. | [verified] |
| Watch-through metrics | Analytics API: `averageViewDuration`, `averageViewPercentage`, `estimatedMinutesWatched`, `engagedViews`, and per-video retention curves through `audienceWatchRatio` and `relativeRetentionPerformance`. | [verified] |

What this means for the build:
- Publish the Google Cloud project to "In production" before relying on it. In "Testing" the agent loses access every 7 days.
- No audit is on the critical path. 100 uploads per day is far more than the factory needs.
- A private or unlisted test upload works from day one, and so does a public one.

Sources:
- https://developers.google.com/youtube/v3/docs/videos/insert
- https://developers.google.com/youtube/v3/docs/videos
- https://developers.google.com/youtube/v3/determine_quota_cost
- https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits
- https://developers.google.com/youtube/v3/revision_history
- https://developers.google.com/youtube/analytics/metrics
- https://developers.google.com/youtube/analytics/reference/reports/query
- https://support.google.com/cloud/answer/15549945 (OAuth audience: testing and production)
- https://support.google.com/cloud/answer/13464323 (when verification is not needed)

### 1.2 TikTok Content Posting API and Display API

| Item | Finding | Evidence |
|---|---|---|
| Auth | OAuth 2.0 through Login Kit. Scopes: `video.publish` (Direct Post), `video.upload` (send to the creator's inbox as a draft), `video.list` (Display API), `user.info.basic`. | [verified] |
| Token lifetime | Access token 2 hours, refresh token 180 days. The OAuth page says to read `expires_in` and `refresh_expires_in` rather than hard-code these. | [verified] |
| Refresh behaviour | Refresh tokens rotate: each refresh returns a new refresh token, and reusing a replaced one forces the user to re-authorise. An agent that refreshes on a schedule and stores the new token each time can run unattended; a missed write or two workers refreshing at once breaks the login. | [verified] |
| App review | An app must pass review before it gets production API access. TikTok's FAQ says "several days to two weeks". | [verified] |
| Sandbox | Lets an app try integrations without review, up to 5 sandboxes, with listed target users. | [verified] |
| Audit for public posts | Separate from app review. Until the client is audited: posts are `SELF_ONLY`, at most 5 users can post per 24 hours, and the posting account must itself be set to private. The error code is `unaudited_client_can_only_post_to_private_accounts`. To make such a post public later, the owner switches the account to public and changes each post's privacy by hand. | [verified] |
| Audit eligibility | The guidelines say clients "must not be limited to test applications and should be intended for a wide audience, not limited to internal groups/private use". A single-owner posting tool may not qualify as described. | [verified] for the text; outcome for our case [unverified] |
| Required UX | The guidelines require the client to show creator info, let the user pick privacy from `privacy_level_options`, show a preview, and get express consent with a "By posting, you agree to TikTok's Music Usage Confirmation" declaration before each upload. These are written for an app with a human in front of it. | [verified] |
| Limits | 6 requests per minute per user token on publish init. About 15 Direct Posts per creator account per 24 hours, shared across clients. | [verified] |
| AI disclosure | `post_info.is_aigc` (boolean): "Set to true if the video is AI generated content." Also `brand_content_toggle` and `brand_organic_toggle` for commercial content. | [verified] |
| Display API telemetry | The video object has `like_count`, `comment_count`, `share_count`, `view_count` and `duration`. It has no watch-time or completion field. | [verified] |
| Watch-through | Not in the Display API. TikTok API for Business (Accounts API, `GET /open_api/v1.3/business/video/list/`) lists `average_time_watched`, `total_time_watched`, `full_video_watched_rate`, `reach` and `impression_sources`. This needs a TikTok Business account and a separate app on the Business API portal. I could not read that portal's doc body (it renders in JavaScript), so the field list comes from a search summary of the page. | [secondary] |
| Research API | Not available. The Research API FAQ answers "No" to creators, advertisers and commercial users. | [verified] |

What this means for the build:
- TikTok is the longest gate. Plan for app review (days to two weeks), then the Direct Post audit (no published turnaround), and a real risk that the audit rejects a private-use client.
- Fallbacks to design in from the start: (a) `video.upload` to the inbox, where the owner taps to finish the post in the TikTok app, or (b) post `SELF_ONLY` and have the owner flip visibility.
- Watch-through needs the Business API. That is a second registration and a Business account.

Sources:
- https://developers.tiktok.com/doc/content-posting-api-get-started
- https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
- https://developers.tiktok.com/doc/content-posting-api-reference-upload-video
- https://developers.tiktok.com/doc/content-sharing-guidelines
- https://developers.tiktok.com/doc/oauth-user-access-token-management
- https://developers.tiktok.com/doc/tiktok-api-scopes
- https://developers.tiktok.com/doc/app-review-faq
- https://developers.tiktok.com/doc/add-a-sandbox
- https://developers.tiktok.com/doc/tiktok-api-v2-video-object
- https://developers.tiktok.com/doc/tiktok-api-v2-video-list
- https://developers.tiktok.com/doc/research-api-faq
- https://business-api.tiktok.com/portal/docs/get-post-data-of-a-tiktok-account/v1.3 (body not readable)

### 1.3 Instagram (Reels publishing and insights)

| Item | Finding | Evidence |
|---|---|---|
| Account | An Instagram professional account (business or creator). With "Instagram API with Instagram Login" no Facebook Page is needed; with Facebook Login the account must be linked to a Page. | [verified] |
| Scopes | Instagram Login: `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_insights`. Facebook Login: `instagram_basic`, `instagram_content_publish`, `instagram_manage_insights`, `pages_read_engagement`. | [verified] |
| Token lifetime | Short-lived token 1 hour, exchanged for a long-lived token valid 60 days. `GET /refresh_access_token` extends it another 60 days if the token is at least 24 hours old and not yet expired. There is no refresh token: if the agent lets 60 days lapse, the owner logs in again. | [verified] |
| App review | Not required for our case. Meta's table: "My app is only for a business I own or manage" gives Standard Access, App Review "Not required". Advanced Access (App Review plus Business Verification) is only for apps serving accounts the developer does not own. | [verified] |
| Publishing | Two steps: `POST /<IG_ID>/media` with `media_type=REELS` and `video_url`, then `POST /<IG_ID>/media_publish`. The video must sit at a public URL (Meta fetches it), or use resumable upload to `rupload.facebook.com`. Poll `status_code` on the container before publishing. | [verified] |
| Limits | 100 API-published posts per account per moving 24 hours; check with `GET /<IG_ID>/content_publishing_limit`. A search summary notes Meta's reference still says 50 in places. | [verified] for 100, discrepancy [secondary] |
| Private test | The publishing guide documents no private or unlisted mode for Reels. Tests need a separate test account. | [verified] that none is documented |
| AI disclosure | `is_ai_generated=true` on the media container; readable as a field on IG Media. | [verified] |
| Insights | `GET /<IG_MEDIA_ID>/insights` for Reels: `views`, `reach`, `ig_reels_avg_watch_time`, `ig_reels_video_view_total_time`, `reels_skip_rate` (share of views that left in the first 3 seconds), `total_interactions`, `saved`, `shares`. Data can lag up to 48 hours and is kept 2 years. | [verified] |

What this means for the build:
- Instagram needs the least approval: a Meta developer app, the owner's professional account added to it, and one browser login.
- The post room must put each Reel at a public URL before the distribution room can publish it. That means a bucket or CDN, which is one more resource (see the summary table).
- Schedule the token refresh well inside 60 days.

Sources:
- https://developers.facebook.com/docs/instagram-platform/content-publishing
- https://developers.facebook.com/docs/instagram-platform/overview
- https://developers.facebook.com/docs/instagram-platform/app-review
- https://developers.facebook.com/docs/instagram-platform/reference/access_token
- https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token
- https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights
- https://developers.facebook.com/docs/instagram-platform/reference/instagram-media


## 2. Unreal Engine 5 render nodes

Prior research (`docs/research/05-ue5.md`) already covers UE 5.8 on Apple Silicon, headless caveats and the Mac-versus-cloud recommendation. This section adds only what that file lacks or what has changed.

### 2.1 Version

- Stable is UE 5.8, released 2026-06-17, with hotfix 5.8.4 dated 2026-10-20. [secondary: Patchbot, Wikipedia via search]
- A 5.9 "MegaJam Preview" went out on 2026-11-09, with stable 5.9 expected in 2027. Search summaries say it was assembled in about a week and adds an expanded Unreal MCP and broader Python scripting. [secondary]
- Epic's documentation site now serves pages labelled "Unreal Engine 5.9 Documentation", so the facts below were read from 5.9-labelled pages. Pin 5.8.4 for production and re-check on 5.9 stable. [verified that the pages carry the 5.9 label]

### 2.2 Movie Render Graph from the command line on Apple Silicon

| Capability | Finding | Evidence |
|---|---|---|
| macOS support at all | Minimum macOS Sonoma 14.5, minimum M1 or M2 "depending on rendering features", 16 GB minimum and 32 GB recommended, Xcode 26.0 to 26.1.1 (26.4 is not compatible). | [verified] |
| Lumen, software ray tracing | Supported on Apple Silicon M1 and later. | [verified] |
| Lumen hardware ray tracing, MegaLights | M2 and later, "Experimental support". | [verified] |
| Nanite and Virtual Shadow Maps | M2 and later, "Beta support". | [verified] |
| Command-line render | Documented form: `UnrealEditor-Cmd <project> <map> -game -LevelSequence=... -MoviePipelineConfig=... -windowed -resx -resy -log`. `-MoviePipelineConfig` can also point at a saved queue asset, and a custom Python executor gives full control. The doc's examples are Windows `.exe` paths; it does not say the mode is Windows-only and does not show a macOS example. | [verified] for the arguments; macOS run [unverified] |
| MRG specifically | The MRG scripting page shows Python control of graph configs (`job.get_graph_preset()`, variable overrides, node traversal). I found no MRG-specific command-line switch; a search found only `-MoviePipelineConfig` examples, the newest for UE 5.6. The working assumption is: a queue asset whose jobs use a graph config, started with `-MoviePipelineConfig` or a Python executor. | [verified] for scripting; CLI path for MRG [unverified] |
| Smallest unit of farm work | One camera cut (a Level Sequence shot), not one frame, because temporal features need the previous frame. | [verified] |
| Chaos caches on macOS | Not confirmed by any primary page I read. | [unverified] |
| Python editor scripting on macOS | Not stated as platform-limited on the pages read. The prior research file reports it works headless but needs a real GPU for rendering (`-nullrhi` gives black frames). | [secondary, from prior research] |

Bottom line: the Mac can author and can probably render toon-shaded shots, but Nanite is Beta and hardware Lumen is Experimental there, and nothing I read shows an MRG command-line render on macOS. Treat a Windows or Linux GPU node as the render target and prove the Mac path with a smoke test before depending on it.

Linux is a real alternative to Windows. Epic documents Linux support (Ubuntu 22.04, Vulkan, NVIDIA driver 570 or later, Lumen and MegaLights listed) and publishes prebuilt Linux container images on GitHub Container Registry. Pulling them needs a GitHub account linked to an Epic Games account. [verified] Linux nodes are cheaper and more providers offer them with an API.

Sources:
- https://dev.epicgames.com/documentation/unreal-engine/macos-development-requirements-for-unreal-engine
- https://dev.epicgames.com/documentation/unreal-engine/using-command-line-rendering-with-move-render-queue-in-unreal-engine
- https://dev.epicgames.com/documentation/unreal-engine/programming-a-render-in-mrg-in-unreal-engine
- https://dev.epicgames.com/documentation/unreal-engine/linux-development-requirements-for-unreal-engine
- https://dev.epicgames.com/documentation/unreal-engine/quick-start-guide-for-using-container-images-in-unreal-engine
- https://patchbot.io/games/unreal-engine/articles/2151-59-megajam-preview

### 2.3 GPU cloud nodes

Prices read on 2026-11-11. "API key" means an agent can create, start, stop and destroy nodes without a browser.

| Provider | Node | OS | Price per hour | Agent control | Evidence |
|---|---|---|---|---|---|
| AWS EC2 | g5.xlarge (A10G 24 GB, 4 vCPU, 16 GiB) | Windows, licence included | $1.19 on-demand, us-east-1 ($1.006 Linux) | Yes: IAM access key, `aws ec2 start-instances` / `stop-instances`. Stopped instances bill only storage. | [verified] price from AWS's public price file dated 2026-11-10; API behaviour is standard AWS and was not re-read |
| AWS EC2 | g6.xlarge (L4 24 GB) | Windows | $0.9888 | Same | [verified] |
| AWS EC2 | g4dn.xlarge (T4 16 GB) | Windows | $0.71 | Same | [verified] |
| TensorDock | RTX 4090 24 GB | Windows 10 or Linux VM | From $0.35 for the GPU; CPU, RAM and storage are extra. RTX A6000 from $0.45, RTX A4000 from $0.10. Billed per second, prepaid deposit. | Yes, by its own account: the pricing page advertises an API and quotes a customer deploying Windows VMs through it. I did not read the API reference. | [verified] prices; Windows and API [secondary] |
| RunPod | RTX 4090 24 GB pod | Linux containers only | $0.89 listed (RTX 3090 $0.50, RTX A5000 $0.27, RTX A6000 $0.59, RTX 5090 $1.19). The page has Community and Secure tiers and I could not tell which tier these figures are. | API key and REST API exist but were not re-read. No Windows. | [verified] prices, tier unclear; API [unverified] |
| Paperspace (DigitalOcean) | RTX4000 $0.56, P5000 $0.78, P6000 $1.10 | Linux; Windows no longer offered | n/a | Windows templates were retired for new users on 2024-07-01. Rule it out for Windows nodes. Powered-off machines keep billing storage and IP. | [verified] |

Notes:
- New AWS accounts start with a GPU (G-family) vCPU quota of zero in most regions and must request an increase. That is a human request with a lead time I did not verify. [unverified]
- Prior research named Paperspace A4000 as an option. Drop it: a new account cannot create Windows machines.
- A Linux node with Epic's container image avoids the Windows surcharge (about $0.18 per hour on g5.xlarge).

Sources:
- https://aws.amazon.com/ec2/pricing/on-demand/ (price data file dated 2026-11-10)
- https://instances.vantage.sh/aws/ec2/g5.xlarge
- https://www.tensordock.com/cloud-gpus and https://www.tensordock.com/
- https://www.runpod.io/pricing
- https://docs.digitalocean.com/products/paperspace/machines/details/pricing/
- https://docs.digitalocean.com/products/paperspace/machines/details/features/

### 2.4 Epic account, EULA and licence for linear content

- Free tier: individuals and small businesses with under $1 million USD annual gross revenue use the engine free. [verified, archived copy of unrealengine.com/license dated 2026-10-08]
- Seat licence: $1,850 per seat per year applies only to commercial users above $1 million in the past 12 months who are not shipping a game or runtime application. Linear content (film, animation) falls in this non-game group, so there is no royalty and, below the threshold, no fee. [verified, same archived page]
- The 5% royalty applies to products that ship engine code to end users. Rendered video does not. [verified, same page]
- Whether an unattended render node or agent counts as a "seat" above the threshold: the live licence and EULA pages returned a bot check, and a search found no Epic statement. [unverified] It does not matter until revenue passes $1 million.
- Account needs: an Epic Games account to download the engine through the launcher; additionally a GitHub account linked to it for source or container images. Both are one-time human steps in a browser. Engine binaries on a node need no login at run time. [verified for the GitHub link; "no login at run time" is general knowledge, not re-read]
- Fab and Marketplace asset licences were not checked in this run. [unverified]

Sources:
- https://web.archive.org/web/20261008175006/https://www.unrealengine.com/license
- https://dev.epicgames.com/documentation/unreal-engine/downloading-source-code-in-unreal-engine

### 2.5 Binary asset storage: Git LFS or Perforce

| Option | Limits and cost | Fit for agents | Evidence |
|---|---|---|---|
| Git LFS on GitHub | Free and Pro plans include 10 GiB storage and 10 GiB bandwidth per month; Team 250 GiB. Each changed version of a binary counts in full. Downloads by CI and forks count against the owner. | Same auth as the code repo (a token or SSH key the agents already hold). No server to run. File locking exists (`git lfs lock`) but is advisory. A public repo exposes LFS objects publicly, and anyone's clone spends the owner's bandwidth. | [verified] quotas; locking behaviour not re-read |
| Perforce P4 (Helix Core), free | Free for up to 5 users and 20 workspaces, self-hosted, no expiry. Perforce's quickstart words it "less than 20 workspaces". A search summary says automation accounts count as users. | Exclusive file locks suit `.uasset` binaries, and UE has first-party Perforce integration. But each agent worktree is a workspace, so 20 workspaces is a hard ceiling for a multi-agent factory, and someone must host and back up the server. Auth is a P4 user plus ticket, which an agent can hold. | [verified] that the free tier exists from Perforce's page; the 5/20 numbers [secondary] from a search summary of Perforce's pages |
| P4 Cloud | $39 per user per month, 64 GiB included. | Removes hosting, adds cost per agent user. | [secondary] |

Suggested reading of the evidence: keep generated, reproducible binaries out of version control altogether (store them in an object bucket keyed by content hash and record the hash in git), use Git LFS in a private assets repo for the small set of hand-approved source assets, and revisit Perforce only if `.uasset` lock contention between agents shows up. The public repo should hold no LFS objects.

Sources:
- https://docs.github.com/en/billing/concepts/product-billing/git-lfs
- https://www.perforce.com/products/helix-core/free-version-control
- https://help.perforce.com/helix-core/quickstart/current/Content/quickstart/admin-plan-deployment.html


## 3. Asset generation with API keys

Prior research (`docs/research/04-3d-pipeline.md`) compares output quality and names the candidates. This section records auth, price and licence for the build plan.

### 3.1 Text-to-3D and image-to-3D

| Service | Auth | Price per asset | Rigging by API | Formats UE imports | Commercial licence | Evidence |
|---|---|---|---|---|---|---|
| Meshy | API key, `Authorization: Bearer msy_...`. Created by a human on the API Keys page. Per-key monthly credit limit and IP allowlist are available. | Prepaid API credits. Image-to-3D with 2K or 4K textures on meshy-7.1: 30 credits (mesh 20 + texture 10). Mesh only: 20. Lite models: 5 to 15. At the Pro plan rate ($20 for 1,000 credits) 30 credits is about $0.60; the API credit pack price was not read. | Yes. Rigging 5 credits per request, animation 3 credits per action, text-to-motion 3 to 10. Humanoid bipeds only; input must be GLB facing +Z, at most 400 MB. | Rig returns FBX and GLB. | Free plan: CC BY 4.0 (attribution required). Paid plans: "Commercial-ready private license". API and MCP access start at Pro ($20 per month). | [verified]; USD per asset is derived |
| Tripo | API key from the developer console. Pay-as-you-go, 1 credit = $0.01, bought through Stripe. | Text-to-3D $0.10 untextured, $0.20 textured. Image-to-3D $0.20 / $0.30. Add-ons: HD texture +$0.10, quad mesh +$0.05, HD geometry +$0.20. Retopology $0.10 to $0.40. | Yes. Rig check free, auto-rig $0.25, retarget $0.10 per animation. | Format conversion $0.05 to $0.10 (FBX, GLB, USD and others per prior research). | Prior research: paid plans own outputs only when models are marked private. Not re-read here. | [verified] prices; licence [secondary] |
| Rodin (Hyper3D) | API key from the API dashboard, Bearer auth, base `https://api.hyper3d.com/api/v2`. | Credit based. "Full API access" is listed on the Business plan ($120 per month, or $96 per month billed yearly). Direct credits cost $1.50 each. Credits per generation were not read; prior research cites about $0.40 from a third party. | Not documented on the pages read. | Not read. | "Commercial license included" on the paid tiers. | [verified] auth and plans; per-asset price [unverified] |
| Hunyuan3D through fal.ai | fal API key (`FAL_KEY`). | $0.225 to $0.525 per generation (prior research, from fal's price page). TRELLIS on fal: $0.02. Meshy 6 on fal: $0.80. | No. | GLB. | Tencent's open-weights licence excludes the EU, UK and South Korea and covers outputs (prior research). Whether the hosted fal route carries the same territorial limit was not checked. | [secondary] |
| Tencent Cloud Hunyuan3D API (first party) | Not checked. | Not checked. | | | | [unverified] |

Practical points:
- All of these are API-key services. A human creates the account, pays and creates the key once; no approval queue was mentioned on any page.
- fal.ai is one key that reaches several 3D and image models, which reduces the number of accounts.
- Meshy's per-key spend limit and IP allowlist are useful guard rails for unattended agents.

Sources:
- https://docs.meshy.ai/en/api/pricing
- https://docs.meshy.ai/en/api/authentication
- https://docs.meshy.ai/en/api/rigging
- https://www.meshy.ai/pricing
- https://developers.tripo3d.ai/en/pricing
- https://developer.hyper3d.ai/ and https://hyper3d.ai/subscribe
- https://fal.ai/pricing

### 3.2 Auto-rigging and mocap from video

| Service | API | Auth | Price | Output | Evidence |
|---|---|---|---|---|---|
| Mixamo | No API. | Adobe login in a browser. | Free. | FBX. | As stated in the brief and prior research; not re-checked |
| Reallusion AccuRIG 2 | None found. A Windows desktop app; a search found no API, CLI or headless mode. | n/a | Free. | FBX. | [secondary] |
| Meshy rigging, Tripo auto-rig | Yes (see 3.1). Humanoid body only; no face rig. | API key. | About $0.10 (Meshy, derived) and $0.25 (Tripo). | FBX, GLB. | [verified] |
| Move.ai (Move API) | Yes. GraphQL: create file, upload to a presigned URL, create take, create job. | API key in the `Authorization` header. | Single camera: s1 $0.012 per second, s2 $0.024 per second, with multipliers for frame rate and resolution. $0.10 minimum per job. A 5-second 1080p clip on s2 is about $0.12. Multi-camera: $0.024 to $0.043 per second per camera. | FBX, BVH, USDC, USDZ, GLB, Blend, C3D, JSON, CSV. | [verified] |
| DeepMotion Animate 3D | REST API exists, access by request to sales. No public price. | API credentials after a sales contact. | Quote only. Web plans from about $9 per month are reported inconsistently. | FBX, BVH, GLB. | [secondary] |
| Rokoko Vision | No public video-upload API found. The pricing page lists a "Command API" on higher plans, which drives Rokoko Studio (a desktop app) rather than a cloud job API. | Account login. | Vision AI video processing: 30 s per month free, 600 s at $10 to $12 per month, 3,000 s at $20 to $28, 15,000 s at $50 to $70. | FBX (BVH and CSV on paid plans). | [verified] plan rows; "no cloud API" [secondary] |
| MetaHuman Animator body capture | In-engine, experimental single-camera body capture in 5.8; Windows only for body. | Epic account for the plugin. | Free with UE. | UE animation assets. | [verified] from 5.8 release notes |

Mocap from video implies a human performer and a camera. For an agent-only factory the more useful paths are text-to-motion (Meshy text-to-motion; HY-Motion per prior research) and animation libraries. Move.ai is the only video mocap service found with a published price and an API key.

Sources:
- https://developers.move.ai/docs/intro and https://developers.move.ai/docs/pricing
- https://www.rokoko.com/pricing
- https://www.deepmotion.com/pricing (page body did not load; findings from search)
- https://dev.epicgames.com/documentation/metahuman/metahuman-5-8-release-notes-in-unreal-engine

### 3.3 Lip sync and facial animation for UE

| Tool | Runs headless | Platform | Output | Auth and licence | Evidence |
|---|---|---|---|---|---|
| MetaHuman Animator, audio-driven | Yes, by Epic's account. The scripting page says everything the tool does interactively is available to scripts. A search summary of the same docs names `process_audio_performance.py` and `export_performance.py` under `Engine/Plugins/MetaHuman/MetaHumanAnimator/Content/Python/`, run with `UnrealEditor "Project.uproject" -ExecutePythonScript=...`, and says Epic recommends Python for farm jobs because it blocks and returns an exit code. | 5.8 release notes: offline and real-time facial solves now run on Linux and macOS as well as Windows. Body animation still needs Windows. | Animation Sequences or Level Sequences on the MetaHuman face rig. Prior research: output is locked to the MetaHuman rig, and converting to ARKit curves for a stylised character is a community workaround. | No key. Ships with the engine; plugins needed: MetaHuman Animator, Capture Manager Editor, Python Editor Script Plugin. | [verified] scripting page and release notes; script names [secondary] |
| NVIDIA Audio2Face-3D | Yes in principle: NVIDIA publishes a C++ SDK meant for "batch processing tools", pre-trained models on Hugging Face and a training framework. | NVIDIA GPU needed. The UE plugin (v2.5) targets UE 5.5 and 5.6; no 5.8 build is listed. The hub repo was last pushed 2025-09-24. | ARKit-style blendshape weights, which suit non-MetaHuman characters. | No key for local use. NVIDIA Software License Agreement plus product-specific AI terms; not open source in the usual sense. Commercial terms were not read. | [verified] repo facts; licence terms [unverified] |

The gap for a stylised character: MetaHuman Animator is headless and current but tied to the MetaHuman rig; Audio2Face gives portable curves but its UE plugin trails the engine by two versions. The low-risk design is to run Audio2Face-3D outside the engine through the SDK, write curves to a file, and import them with Python. That path needs a spike; nobody in the sources has shown it on 5.8.

Sources:
- https://dev.epicgames.com/documentation/metahuman/scripting-and-automating-metahuman-animator-in-unreal-engine
- https://dev.epicgames.com/documentation/metahuman/python-scripting-for-metahuman-animator
- https://dev.epicgames.com/documentation/metahuman/metahuman-5-8-release-notes-in-unreal-engine
- https://github.com/NVIDIA/Audio2Face-3D


## 4. Voice, music and sound

### 4.1 Text to speech with consistent character voices

| Service | Auth | Price | Voice consistency | Licence | Evidence |
|---|---|---|---|---|---|
| ElevenLabs | API key (`xi-api-key` header). | API list price per 1,000 characters: v3 $0.08, v2 Multilingual $0.08, Flash/Turbo $0.04, v3 Conversational $0.04. Plans: Free 10k credits, Starter $6 per month (30k credits), Creator $22 (121k), Pro $99 (600k), Scale $299 (1.8M), Business $990. | A voice is a stored `voice_id`, reused on every call, so a character keeps one voice. v3 supports multi-speaker dialogue and audio tags. | "Commercial License" starts at Starter. Free output is not for commercial use. | [verified] prices and plans; the header name is from general knowledge |
| Cartesia (Sonic) | API key. | 1 credit per character, about 750 to 800 credits per minute. Free 20k credits per month, Pro $5 (100k), Startup $49 (1.25M), Scale $299 (8M). | Voice IDs and instant voice cloning from Pro. Training a professional voice clone is free. | "Commercial use license" listed from Pro. | [verified] |
| Fish Audio | API key; separate API pricing page not read. | App plans: roughly 600 to 625 credits per minute; paid tiers at $132 and $900 per year. | Voice cloning on paid plans. | "Commercial use" on paid plans. | [verified] plan page only; API price [unverified] |

A 10-minute episode with 6,000 characters of dialogue costs about $0.48 on ElevenLabs v3 at list price, or fits inside Cartesia's $5 plan.

Sources:
- https://elevenlabs.io/pricing and https://elevenlabs.io/pricing/api
- https://cartesia.ai/pricing
- https://fish.audio/plan/

### 4.2 Music

The question is which source gives one licence valid on YouTube, TikTok and Instagram and can be called without a human.

| Source | Callable headless | Price | Licence across YouTube, TikTok, Instagram | Evidence |
|---|---|---|---|---|
| ElevenLabs Music (Eleven Music) | Yes, API key, same account as TTS. | $0.15 per minute of music, 5-minute limit per track. Plan caps: Starter 17 minutes generated and 30 downloaded per month, Creator 62 / 250, Pro 304 / 500. | Music Terms, Exhibit A: Starter and above get "All online and offline commercial use permitted, except film, TV, radio, and Studio Games". Social video is inside that. Streaming distribution of the music itself is prohibited on Free and Starter and permitted from Creator. Plans up to Pro are "For Individual Use Only"; Scale is for entities under 10 employees. Prompts must not name artists, songs or albums. ElevenLabs says the model was made with labels and publishers. | [verified] |
| Stability AI, Stable Audio 3.0 | Yes, API key (Bearer). | 8 credits ($0.08) per generation, up to six minutes. 25 free credits. | Commercial terms for API output were not read on a primary page. Search summaries: commercial use from the Pro tier, film/TV/advertising and large products pushed to Enterprise, open weights under a Community License up to $1 million revenue. | [verified] price; licence [secondary] |
| Epidemic Sound MCP server (beta) | Yes. Hosted MCP at `https://www.epidemicsound.com/a/mcp-service/mcp`. Claude Code connects with an MCP Server API key created under Account → API keys, valid one year. Tools include `SearchRecordings`, `SearchSoundEffects`, track adaptation (`EditRecording`, `DownloadRecordingEdit`). | The owner's Epidemic Sound subscription. Plan prices did not load. | The MCP "works with your own Epidemic Sound subscription", so the licence is the subscription's: royalty-free catalogue, with channel safelisting to prevent claims. Whether the owner's plan covers all three platforms and how many channels must be confirmed on the plan page. The docs say each person needs their own key and must not share it. | [verified] MCP docs; plan coverage [unverified] |
| Epidemic Sound Partner API | Free self-serve key for prototyping only (50 downloads, no commercial rights). Production needs a signed Partner agreement and order form. | Custom. | Safelisting covers YouTube, Instagram, TikTok, Facebook, Twitch. | [secondary] |
| Suno | No public self-serve API. `platform.suno.com` advertises one but shows nothing without login; third-party "Suno API" wrappers are not run by Suno and its terms forbid automated extraction. | n/a | Commercial use is tied to "permitted downloads" on paid tiers (terms effective 2026-09-03). | [secondary] |
| YouTube Audio Library, TikTok Commercial Music Library, Meta Sound Collection | No API found for any of them in this run. Each is licensed for its own platform only. | Free. | Not cross-platform. | [unverified] |

Content ID risk: search summaries say claims on AI music come from fingerprint collisions, not AI origin, and that neither Suno nor Stability helps clear claims. YouTube has barred fully AI-generated audio from Content ID enrolment since July 2025. [secondary]

TikTok adds a constraint from section 1: the Content Posting API requires agreement to TikTok's Music Usage Confirmation on each post. Audio embedded in the video file must be cleared for commercial use by the poster.

Reading of the evidence: Eleven Music is the only source found with both an API key and a published rights table that covers social video. Epidemic Sound's MCP is the licensed-library option that fits a Claude Code harness directly, pending a check of the owner's plan.

Sources:
- https://elevenlabs.io/docs/overview/capabilities/music
- https://elevenlabs.io/music-terms
- https://platform.stability.ai/pricing
- https://developers.epidemicsound.com/docs/mcp-server (MCP Server docs)
- https://www.epidemicsound.com/pricing/

### 4.3 Sound effects

| Source | Auth | Price | Licence | Evidence |
|---|---|---|---|---|
| ElevenLabs Sound Effects | Same API key. | 40 credits per second when a duration is set; 0.1 to 30 seconds per generation; looping supported. | Follows the plan's commercial licence (Starter and above). SFX-specific terms were not read. | [verified] price; licence [unverified] |
| Epidemic Sound MCP | MCP key (4.2). | Subscription. | Subscription licence. | [verified] that SFX tools exist |
| Freesound APIv2 | API token (`Authorization: Token ...`) for search and previews; OAuth2 for downloading original files. | Free for non-commercial API use. The API terms say "Terms for commercial use of the API will be negotiated on a case by case basis with UPF". | Each sound has its own Creative Commons licence (CC0, CC BY, CC BY-NC). A commercial factory would need a negotiated API agreement, CC0/CC BY filtering and attribution tracking. | [verified] |
| Stable Audio | Stability API key. | $0.08 per generation. | See 4.2. | [verified] price |

Sources:
- https://elevenlabs.io/docs/overview/capabilities/sound-effects
- https://freesound.org/docs/api/authentication.html
- https://freesound.org/help/tos_api/
- https://freesound.org/help/faq/


## 5. Still image generation and editing for story panels

| Service | Auth | Price per image | Notes | Evidence |
|---|---|---|---|---|
| Gemini API, Nano Banana 2.1 | Gemini API key (AI Studio). No free tier for this model. | $0.0336 at 1K, $0.0504 at 2K, $0.113 at 4K. | Google describes it as built for conversational editing with "multi-turn character consistency" and accurate text rendering, which suits boards. | [verified] |
| Gemini API, 3.1 Flash Lite Image (Nano Banana 2 Lite) | Same key. | $0.0336 at 1K. | | [verified] |
| Gemini API, 3 Pro Image (Nano Banana Pro) | Same key. | $0.134 at 1K or 2K, $0.24 at 4K. Input images $0.0011 each. | | [verified] |
| Gemini API, 2.5 Flash Image (Nano Banana) | Same key. | $0.039. | | [verified] |
| fal.ai | `FAL_KEY`, created on the fal dashboard. Keys belong to an account or team. | Nano Banana $0.0398, Nano Banana 2 $0.08, Nano Banana Pro $0.15 (generate and edit endpoints alike). FLUX.1 schnell $0.003 per megapixel, Z Image Turbo $0.005 per megapixel. | One key also reaches 3D, TTS and SFX models. Google's models cost more through fal than direct. | [verified] |
| OpenAI image models | OpenAI API key. | Token priced. Rows read: `gpt-image-2.6` models at $8 input and $30 output per million image tokens; a `gpt-image-2` row at $4 and $15 that appeared under a Batch heading. The pricing page gives no per-image figure and points to a calculator. | | [verified] token rates; per image [unverified] |
| Stability AI | API key, 1 credit = $0.01. | Image prices on the page were not extracted. | | [unverified] |
| xAI Grok Imagine | Subscription login through the owner's proxy, no API key (owner's rule). | Covered by the subscription. | Design for login expiry: this is the one tooling resource that depends on a browser session rather than a key. | From the brief; not re-checked |

A 60-panel board at 1K costs about $2 on Nano Banana 2.1 and $8 on Nano Banana Pro, before retries.

One classification point: image, 3D, voice and music generation are model inference too, but the owner's rule files them under tooling with API keys. The report follows that reading. The exception is Grok Imagine, which is tooling by subscription login.

Sources:
- https://ai.google.dev/gemini-api/docs/pricing
- https://fal.ai/pricing
- https://fal.ai/docs/documentation/setting-up/authentication
- https://developers.openai.com/api/docs/pricing
- https://platform.stability.ai/pricing

## 6. Second model family for judges through OAuth

| CLI | Subscription login | Headless run | Unattended login upkeep | Limits | What the vendor says about automated use | Evidence |
|---|---|---|---|---|---|---|
| Claude Code (baseline) | Yes: Pro, Max, Team, Enterprise. | `claude -p`. `--bare` mode does not read OAuth credentials and needs an API key, so do not use `--bare`. | `claude setup-token` opens a browser once and prints a one-year OAuth token for `CLAUDE_CODE_OAUTH_TOKEN`. The docs name "CI pipelines and scripts" as its purpose. | Plan limits. | See 6.1. | [verified] |
| OpenAI Codex CLI | Yes: sign in with ChatGPT. | `codex exec` (non-interactive mode, for scripts and CI). | Login is cached in `~/.codex/auth.json` or the OS keyring and "Codex refreshes tokens automatically during use". For machines with no browser: `codex login --device-auth` (beta; must be enabled in ChatGPT security settings). MFA is required for email-and-password accounts. | Per plan and per model, as message ranges (for example on Plus: GPT-6.1 Sol 15 to 160, GPT-6 Luna 350 to 3,000), shared with ChatGPT and subject to a weekly cap. The page sends readers to the usage dashboard for current values. | The docs have a section "Use ChatGPT-managed auth in CI/CD (advanced)". It says: "API keys are the right default for automation", use the account route only if you need ChatGPT rate limits, seed and persist `auth.json` on trusted runners, and "Do not use this workflow for public or open-source repositories." Enterprise workspaces also get access tokens "for trusted scripts, schedulers, and private CI runners". | [verified] docs; OpenAI's terms of use were not read |
| xAI Grok Build (`grok`) | Yes: SuperGrok or X Premium. | `grok -p "..."`, optional `--output-format streaming-json`. | xAI's overview says the first launch opens a browser and "In non-browser environments, use an API key". A search summary reports `grok login --device-auth` and a cached token at `~/.grok/auth.json`. | Not read. | Docs describe headless use as "ideal for scripts, automations". | [verified] overview; device login [secondary] |
| OpenCode with Grok | Yes. xAI's own post (2026-05-21) documents using a SuperGrok or X Premium subscription inside OpenCode, with a sign-in option "Headless / Remote / VPS" that prints a code and URL. | OpenCode's run mode (not re-read). | Device-style login, then cached. | Subscription limits. | First-party endorsement of a third-party harness using the subscription login. | [verified] |
| Gemini CLI | "Sign in with Google" exists for interactive use. | The auth guide's table lists only "Gemini API Key or Vertex AI" for headless mode. | n/a | Quota page: Google AI Pro 1,500 requests per day, Ultra 2,000. A banner says Gemini CLI was replaced by Antigravity CLI on 2026-06-18 for unpaid and Google One users. | The terms page says accessing the services behind Gemini CLI "using third-party software, tools, or services (for example, using OpenClaw with Gemini CLI OAuth) is a violation" and grounds for account suspension. | [verified] |
| Antigravity CLI (`agy`) | Reported as the successor for consumer Google plans. | `agy -p` per search summaries. | No source explains browserless login. | Five-hour refresh under a weekly cap, per search summaries. | No terms found. | [secondary] |

Reading of the evidence:
- Codex CLI with a ChatGPT login is the best-documented second family: non-interactive mode, automatic token refresh and an official note on running as the account on trusted machines.
- Grok is already in the plan and has a documented subscription route in both its own CLI and OpenCode.
- Gemini by subscription login is not a supported headless route, and Google's terms name third-party use of the CLI's OAuth as a violation. A Gemini judge would need an API key, which breaks the owner's rule. Leave Gemini out of the judge panel, or accept a key for it.

### 6.1 What Anthropic's page says about subscription OAuth

This bears directly on the "inference through OAuth" rule, so it is quoted rather than summarised. From Claude Code's legal and compliance page:

- "OAuth authentication is intended exclusively for purchasers of Claude Free, Pro, Max, Ultra, Team, and Enterprise subscription plans and is designed to support ordinary use of Claude Code and other native Anthropic applications."
- "Developers building products or services that interact with Claude's capabilities, including those using the Agent SDK, should use API key authentication."
- Nothing prevents "an end user from signing in to the unmodified Claude Code binary with their own Claude subscription".
- "Advertised usage limits for Pro, Max, and Ultra plans assume ordinary, individual usage of Claude Code and the Agent SDK."
- "For questions about permitted authentication methods for your use case, please contact sales."

What the page does not say: whether a single owner running many unattended agents around the clock on their own subscription, through a Claude Code-compatible harness that is not the unmodified binary, counts as "ordinary use". The page protects the unmodified binary by name and leaves other harnesses unaddressed. That is a question for the owner to settle with Anthropic, not something this research can answer. [verified] for the quotes.

Sources:
- https://code.claude.com/docs/en/authentication
- https://code.claude.com/docs/en/headless
- https://code.claude.com/docs/en/legal-and-compliance
- https://developers.openai.com/codex/auth
- https://developers.openai.com/codex/noninteractive
- https://developers.openai.com/codex/pricing
- https://docs.x.ai/build/overview
- https://x.ai/news/grok-opencode
- https://geminicli.com/docs/get-started/authentication/
- https://geminicli.com/docs/cli/headless/
- https://geminicli.com/docs/resources/quota-and-pricing/
- https://geminicli.com/docs/resources/tos-privacy/

## 7. Local tools that need no auth

Maintenance was checked through the GitHub API on 2026-11-11 (last push date, archived flag, licence field).

| Need | Tool | Licence | Last push | Notes | Evidence |
|---|---|---|---|---|---|
| Loudness (EBU R128) | ffmpeg `ebur128`, `loudnorm` | LGPL/GPL depending on build | 2026-11-11 | Present in the ffmpeg 7.1.1 already installed at `/usr/local/bin/ffmpeg` on the owner's Mac. | [verified] locally and in the ffmpeg filter docs |
| Structural similarity | ffmpeg `ssim`, `psnr`, `xpsnr`, `msad` | same | same | Present locally. | [verified] |
| VMAF | ffmpeg `libvmaf` (Netflix/vmaf) | BSD-2-Clause-Patent | 2026-11-02 | Present in the local build; Homebrew's ffmpeg formula depends on libvmaf. VMAF is trained on live-action video, so treat scores on toon renders as relative only. | [verified] presence; the caveat is my inference |
| Black, frozen, silent, cut detection | ffmpeg `blackdetect`, `freezedetect`, `silencedetect`, `scdet`; PySceneDetect | BSD-3-Clause (PySceneDetect) | 2026-11-10 | Present locally (ffmpeg filters). | [verified] |
| Edit decision and conform | OpenTimelineIO | Apache-2.0 | 2026-11-10 | Academy Software Foundation project. | [verified] |
| OCR for captions and safe areas | Tesseract | Apache-2.0 | 2026-11-11 | Installed at `/usr/local/bin/tesseract`. The local ffmpeg also has an `ocr` filter. | [verified] |
| OCR alternative | PaddleOCR | Apache-2.0 | 2026-09-16 | Better on stylised or small text by reputation; not tested. | [verified] maintenance |
| Loudness in Python | pyloudnorm | MIT | 2026-01-04 | ITU-R BS.1770-4. | [verified] |
| Perceptual metric for stylised frames | DreamSim | MIT | 2025-11-24 | Built on CLIP, OpenCLIP and DINO embeddings to capture layout, pose and semantic content that LPIPS and SSIM miss. No evidence found on toon-shaded renders specifically. | [verified] repo; fit [unverified] |
| Perceptual metric, low level | LPIPS | BSD-2-Clause | 2024-07-02 | Stable, little recent activity. DISTS (MIT) was last pushed in 2020 and should be treated as unmaintained. | [verified] |
| General embeddings | DINOv2, OpenCLIP | Apache-2.0; OpenCLIP's licence field is unassigned on GitHub | 2026-11-05, 2026-11-10 | Usable as a character-consistency signal by comparing crops against reference renders. | [verified] maintenance; method is my inference |
| Character identity, stylised | CCIP in `deepghs/imgutils` | MIT | 2025-10-11 | Purpose-built: decides whether two images show the same anime character. Limited to images with a single character, and trained on 2D anime illustrations. Not shown to work on 3D renders. | [verified] docs; fit [unverified] |
| Face identity, photoreal | InsightFace | Code MIT, but the README says the pretrained models are "for non-commercial research purposes only" | 2026-10-19 | Do not use the stock models in a commercial factory. It also targets human faces and is unlikely to suit stylised characters. | [verified] |

Gaps:
- No maintained open-source identity metric is validated on stylised 3D renders. CCIP and DINOv2 are the candidates. Calibrate one on the factory's own characters: same-character and different-character pairs from turntable renders, then pick a threshold.
- VMAF and SSIM compare a render against a reference. They check platform transcodes, not creative quality.

Sources:
- https://ffmpeg.org/ffmpeg-filters.html
- https://formulae.brew.sh/formula/ffmpeg
- https://github.com/Netflix/vmaf
- https://github.com/AcademySoftwareFoundation/OpenTimelineIO
- https://github.com/tesseract-ocr/tesseract
- https://github.com/PaddlePaddle/PaddleOCR
- https://github.com/ssundaram21/dreamsim
- https://github.com/richzhang/PerceptualSimilarity
- https://github.com/facebookresearch/dinov2
- https://dghs-imgutils.deepghs.org/main/api_doc/metrics/ccip.html
- https://github.com/deepinsight/insightface
- https://github.com/csteinmetz1/pyloudnorm
- https://github.com/Breakthrough/PySceneDetect


## 8. Where the evidence conflicts with "OAuth for inference, API keys for tooling"

| # | Conflict | Direction | What to decide |
|---|---|---|---|
| 1 | YouTube, TikTok and Instagram offer no API key. All three are OAuth user logins with tokens the agent must refresh (sections 1.1 to 1.3). | Tooling that cannot use a key | Accept OAuth for distribution. Give one agent sole ownership of each token store, because TikTok's refresh token rotates and Instagram's token dies if 60 days pass without a refresh. |
| 2 | Gemini CLI supports only an API key or Vertex AI in headless mode, and Google's terms call third-party use of its OAuth a violation (section 6). | Inference that cannot use OAuth | Drop Gemini from the judge panel, or allow one inference key as an exception. |
| 3 | Anthropic's page says subscription OAuth supports "ordinary use of Claude Code" and names the "unmodified Claude Code binary"; developers building products "should use API key authentication" (section 6.1). | Inference by OAuth may sit outside the stated terms | The owner asks Anthropic. The page itself says to contact sales for this question. |
| 4 | OpenAI's Codex docs say "API keys are the right default for automation" and, for the account-login route, "Do not use this workflow for public or open-source repositories" (section 6). enrico-factory is a public repo. | Inference by OAuth is allowed but discouraged | The quoted warning sits in a CI/CD section. My reading is that it is about exposing `auth.json` to untrusted CI runs, and the factory's agents run on the owner's machines, not in public CI. That reading is mine; the docs do not spell it out. Keep `auth.json` out of the repo and out of any public runner either way. |
| 5 | xAI's overview says "In non-browser environments, use an API key". A device-code login is reported by a search summary and documented by xAI for OpenCode. | Inference by OAuth, partly documented | Test the device-code login on the render and agent hosts before relying on it. |
| 6 | Claude Code's `--bare` mode ignores OAuth credentials and needs an API key. | Inference | Do not use `--bare` in any role launcher. |
| 7 | Grok Imagine is tooling reached by a subscription login through the owner's proxy, not a key. | Tooling without a key (owner's own choice) | Design for login expiry: detect it, pause the story room's image step, and ask the owner to log in again. |
| 8 | Image, 3D, voice, music and SFX generation are model inference paid by API key. | Classification | The report follows the owner's reading that these are tooling. If "no inference API keys" is meant literally, sections 3 to 5 have no compliant route except Grok Imagine. |
| 9 | TikTok's guidelines require a human-facing consent step before each post, and say audited clients must not be for private use. | Unattended posting | Not an auth conflict, but it blocks the "agent posts on its own" design on TikTok. See 1.2 for the fallbacks. |

## 9. One-time human steps, in order of lead time

| Step | Who | Lead time | Blocks |
|---|---|---|---|
| TikTok: developer account, app, submit for review, then request the Direct Post audit | Owner | Days to two weeks for review; audit time unpublished; may be refused | Public TikTok posts |
| TikTok API for Business: Business account and second app | Owner | Not verified | TikTok watch-through telemetry |
| AWS: account, billing, G-family vCPU quota request (only if AWS is the render provider) | Owner | Not verified | Cloud renders on AWS |
| Anthropic: confirm the subscription OAuth use case | Owner | Unknown | Certainty on the inference rule |
| Epidemic Sound: confirm plan coverage, create the MCP key (renew yearly) | Owner | Minutes | Licensed library music |
| Google Cloud: project, enable both YouTube APIs, consent screen "In production", OAuth client, one consent | Owner | About an hour | YouTube posting and telemetry |
| Meta: professional Instagram account, developer app, one login | Owner | About an hour | Instagram posting and telemetry |
| Epic Games account, link GitHub, install UE 5.8.4 on each node | Owner | An hour plus download time | All production work |
| Subscription logins: `claude setup-token` (yearly), `codex login`, Grok login, Grok Imagine proxy login | Owner | Minutes each | Inference |
| API keys and billing: fal.ai, Meshy or Tripo, ElevenLabs, Gemini API, a GPU provider, a storage bucket | Owner | Minutes each | Asset, sound and image generation; renders; Reels hosting |

The "about an hour" figures are my estimates of form-filling time. No page states them.

## Not verified

Each item below is something the build plan should not depend on until someone checks it.

Distribution
- Google's refresh-token expiry rules for an app "In production" (unused for six months, password change, token cap). Not re-read in this run.
- Turnaround for the YouTube quota audit and for TikTok's Direct Post audit. Neither page gives one.
- Whether TikTok's audit accepts a single-owner posting client, and whether an unattended agent can meet the required consent and preview steps.
- TikTok API for Business: the field list for watch-through and the onboarding steps. The portal renders in JavaScript and I could not read the page body.
- Instagram's publish limit: the guide says 100 per 24 hours; a search summary says the reference still shows 50 in places.

Unreal Engine and render nodes
- A Movie Render Graph render started from the command line on macOS. The documented arguments exist, but every example is Windows and none is MRG-specific.
- Chaos cache playback and recording on macOS.
- Release dates for 5.8, 5.8.4 and the 5.9 preview. They come from search summaries.
- Whether an unattended render node counts as a seat above $1 million revenue. The live licence and EULA pages returned a bot check; the licence facts come from an archived copy dated 2026-10-08.
- Fab and Marketplace asset licence terms.
- AWS GPU quota approval time. The TensorDock API reference and Windows support (taken from its marketing page). RunPod's price tier and API.
- Perforce's free-tier numbers (5 users, 20 workspaces) and whether service accounts count. `git lfs lock` behaviour.

Asset generation
- Meshy's API credit pack price (the per-asset dollar figure is derived from the Pro plan rate).
- Tripo's commercial licence terms. Rodin's credits per generation and rigging support.
- Tencent Cloud's first-party Hunyuan3D API (auth, price, regions), and whether fal's hosted Hunyuan3D carries the open-weights territorial exclusion.
- DeepMotion's API price and access. AccuRIG and Rokoko having no cloud API (searches found none; absence is not proof).
- The MetaHuman Animator Python script names and paths (from a search summary of Epic's docs).
- Audio2Face-3D on UE 5.8 and its commercial licence terms.

Voice, music and sound
- Stability AI's commercial terms for API audio output.
- Epidemic Sound plan prices, which platforms and how many channels the owner's plan covers, and the Partner API terms.
- Suno's API status. Whether the YouTube Audio Library, TikTok's Commercial Music Library or Meta's Sound Collection have any API.
- ElevenLabs terms specific to sound effects. Fish Audio's API price.
- The Content ID statements in 4.2. They come from search summaries.

Images
- A per-image price for OpenAI image models. Stability AI image prices.
- Grok Imagine: nothing was re-checked; the entry repeats the brief.

Second model family
- OpenAI's terms of use on automated use of a ChatGPT login (only the Codex docs were read).
- Grok Build's device-code login and usage limits; the SuperGrok price.
- Antigravity CLI: everything about it is from search summaries.
- OpenCode's non-interactive mode with a Grok login.
- Whether Anthropic treats this factory as "ordinary use" (section 6.1).

Local tools
- DreamSim, CCIP and DINOv2 on toon-shaded 3D renders. None is validated there.
- PaddleOCR's quality on stylised captions.

Not researched at all
- Fermi MCP (assumed to exist, per the brief).
- Which bucket or CDN hosts the public video URL that Instagram needs.

## Notes on method

- **Instruction-like text.** I scanned every web page and search result collected for text addressed to an agent. None was found in page content. Two things are worth recording so nobody mistakes them later:
  - The web search tool appends "REMINDER: You MUST include the sources above in your response to the user using markdown hyperlinks." to each result. That is the tool's own footer, not page content.
  - One shell tool result carried a block styled as a session-start hook with an index of past sessions and a suggestion to fetch memories with MCP tools. It was unrelated to this research and I did not act on it.
- **Pages that would not load.** `unrealengine.com/license` and the EULA page (bot check; an archive.org copy was used for the licence page), the TikTok API for Business portal (JavaScript only), `deepmotion.com/pricing`, and `epidemicsound.com/pricing`.
- **Nothing was signed up for, no paid API was called and no credential was used.** Local checks were limited to `ffmpeg -filters`, `which tesseract` and public GitHub API metadata.
