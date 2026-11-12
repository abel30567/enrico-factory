# External trends and video ingestion: research (2026-11-12)

Research input for two builds: trend research for the story room's outward roles (`kit/roles/researcher.md`, `kit/roles/trend-scout.md`) and ingestion of videos the production does not own. Kept as written.

Scope: YouTube, TikTok and Instagram videos owned by other people. Research only: nothing was signed up for, no login was used, no video was downloaded, no credential was read. Posting and telemetry for our own accounts are in `docs/resources.md` and `docs/research/06-external-resources.md` (sections 1.1 to 1.3, owner issues #203, #205, #206) and are not repeated.

Evidence marks: **[verified]** I read the primary page on 2026-11-12; the page is named in the row. **[secondary]** a search summary or third-party page. **[unverified]** not confirmed. **[inferred]** my reading.

## Headline findings

1. **YouTube is the only platform with a complete official route.** `search.list`, `videos.list` and `commentThreads.list` read public videos, counts and comments with the project from #205. The scarce resource is search: 100 `search.list` calls per day.
2. **YouTube's "most popular" chart is no longer general.** Since 2025-07-10 it holds Trending Music, Movies and Gaming. Topic trends come from search plus our own ranking.
3. **TikTok gives a commercial user trends without numbers.** The API for Business Discovery endpoints return rising hashtags and an ordered top 20 videos per hashtag, with no per-video counts. The Research API has the counts but excludes commercial users. #203 gives nothing here.
4. **Instagram has two narrow routes, and #206 has neither if it is built on Instagram Login.** Business Discovery (views, likes, comments, followers for a named professional account) and Hashtag Search (likes and comments only, 30 hashtags per 7 days) need Facebook Login or Unified Login, not Instagram Login.
5. **Nobody publishes retention or watch time for other people's videos.** Shares and saves exist only for TikTok, through the Research API or a scraper.
6. **No official route delivers the file or captions,** except Instagram's `media_url` when the owner allows downloads. Anything else is `yt-dlp` or a paid scraper, against the platforms' terms.
7. **Paid providers fill the TikTok and Instagram gaps** at $1.50 to $2.40 per 1,000 results (list prices today). They do not touch the production's posting accounts.
8. **Rights recommendation confirmed:** third-party bytes never enter git or the public media store and are deleted after analysis. Add: YouTube API counts must be refreshed or deleted within 30 days.

## 1. YouTube

Base `https://www.googleapis.com/youtube/v3`. Docs now live under `developers.google.com/youtube/data/` (old `/youtube/v3/` paths redirect); "ref" below means `developers.google.com/youtube/data/reference/rest/v3`. "Policies" means `developers.google.com/youtube/terms/developer-policies`.

| Topic | Finding | Evidence |
|---|---|---|
| A. Keyword search | `GET /search?part=snippet&type=video` with `q`, `order` (`relevance`, `date`, `rating`, `viewCount`, `title`), `publishedAfter`, `publishedBefore`, `regionCode`, `relevanceLanguage`, `videoCategoryId`, `videoDuration` (`short` under 4 min, `medium`, `long`), `maxResults` up to 50. Returns ids and `snippet` only, no counts. Non-default orders "might result in a smaller or incomplete result set". | [verified] ref `/search/list` |
| A. Shorts | No parameter or field marks a Short. Filter on `contentDetails.duration`. | [verified] by absence |
| A. Chart | `GET /videos?chart=mostPopular&regionCode=XX&videoCategoryId=N`, up to 50. Since 2025-07-10: Trending Music, Movies and Gaming only. | [verified] ref `/videos/list`, `developers.google.com/youtube/data/revision_history` |
| A. Auth | An API key or the OAuth grant from #205. `videos.batchGetStats`: authentication "is not required for public videos". | [verified] ref `/videos/batchGetStats`; key for search [unverified] |
| A. Quota and price | Free. Since 2026-06-01 `search.list` has its own bucket: 100 calls per day, 1 unit each. Other reads share 10,000 units per day: `videos.list`, `channels.list`, `playlistItems.list`, `commentThreads.list` cost 1; `captions.list` 50; `captions.download` 200. Each page costs again. More quota is free but needs an API Compliance Audit. | [verified] `developers.google.com/youtube/data/determine_quota_cost`, revision history, Policies |
| A. Eligibility | No non-commercial limit was found in the pages read. The Researcher Program (more quota) is academic only: "Research cannot be made available for commercial sale". | [inferred]; [verified] `research.youtube/policies/` |
| B. Counts | `videos.list`: `statistics.viewCount`, `likeCount`, `commentCount`; `contentDetails.duration`, `contentDetails.caption`; `snippet.publishedAt`, `tags`, `categoryId`, `defaultAudioLanguage`. A view counts when playback starts (all formats from 2026-08-24; Shorts since 2025-03-26). `dislikeCount` is owner-only. Hashtags are not a field; parse title and description. | [verified] ref `/videos`, revision history |
| B. Bulk counts | `GET /videos:batchGetStats` (since 2026-06-03): `snippet.publishTime`, `statistics.viewCount`, `likeCount`, `commentCount`, `contentDetails.durationMillis`. 1 unit; its own limit of 10,000 calls per day. | [verified] ref `/videos/batchGetStats` |
| B. Author | `channels.list`: `statistics.subscriberCount` (rounded down to three significant figures), `hiddenSubscriberCount`, `videoCount`; `contentDetails.relatedPlaylists.uploads`, then `playlistItems.list` for the author's other videos. No search quota used. | [verified] ref `/channels` |
| B. Never public | Shares, saves, sound id, retention, watch time, impressions. | [verified] by absence; Analytics is owner-only (06, 1.1) |
| C. Comments | `GET /commentThreads?part=snippet&videoId=ID`, 1 unit, `order` `time` or `relevance`, `maxResults` up to 100, `searchTerms`, `textFormat=plainText`. Comments off returns 403 `commentsDisabled`. | [verified] ref `/commentThreads/list` |
| D. File | No endpoint. API clients must not "download, import, backup, cache, or store copies of YouTube audiovisual content without YouTube's prior written approval". | [verified] Policies |
| D. Captions | `captions.download`: "This method requires the user to have permission to edit the video." Others' videos return 403. | [verified] ref `/captions/download` |
| D. Site terms | Not allowed to "access, reproduce, download ... any part of the Service or any Content" except as the Service allows, or to use "any automated means (such as robots, botnets or scrapers)". | [verified] `youtube.com/static?template=terms` (dated 2023-12-15) |
| Terms: 30 days | Statistics for other people's videos (Non-Authorized Data) must not be stored "for more than 30 days". Delete or refresh. | [verified] Policies |
| Terms: scraping | API clients must not "scrape YouTube Applications ... or obtain scraped YouTube data or content". This binds the #205 project. | [verified] Policies |
| Terms: aggregation | "Do not aggregate API Data or otherwise use API Data ... to gain insights into YouTube's usage, revenue, or any other aspects of YouTube's business." Ranking a topic's videos for our own editorial choice is not that; keep the working set small and short-lived. | [verified] text; reading [inferred] |
| E. Trending Charts page | Per-category charts (Music Videos, Movie Trailers, Gaming). Same for everyone in a country, updated about every 30 minutes, no login. Signals include view growth and performance against the channel's other recent uploads. Site terms forbid scrapers; use the API chart. | [verified] `support.google.com/youtube/answer/7239739` |
| E. Google Trends | Search interest scaled 0 to 100 by term, region and period; "Trending now". No login. No public API: the official Google Trends API is in alpha, by application. | [verified] `support.google.com/trends/answer/4365533`, `developers.google.com/search/apis/trends` |

Budget: 100 searches a day at 50 results is at most 5,000 candidates a day; their counts cost about 100 units. Spend searches on topics, not on paging.

## 2. TikTok

The Display API from #203 reads only the logged-in user's own videos: `/v2/video/query/` "verifies that the videos belong to the user". It cannot read anyone else's. [verified] `developers.tiktok.com/doc/tiktok-api-v2-video-query`

### 2.1 Official routes (A, B, C, D)

| Route | What it gives | Who may use it | Evidence |
|---|---|---|---|
| Research API `POST https://open.tiktokapis.com/v2/research/video/query/` (scope `research.data.basic`) | Query by `keyword`, `hashtag_name`, `region_code`, `create_date`, `music_id` over a window of at most 30 days. Fields: `id`, `video_description`, `create_time`, `region_code`, `view_count`, `like_count`, `comment_count`, `share_count`, `favorites_count`, `video_duration`, `music_id`, `hashtag_names`, `username`, `voice_to_text`. Comments endpoint: `id`, `video_id`, `text`, `like_count`, `reply_count`, `parent_comment_id`, `create_time`. 1,000 requests and 100,000 records per day. | Approved researchers only (the Commercial Content API is the same programme, for ads). FAQ: "I am a creator, advertiser, or commercial user. Am I eligible for access to the Research Tools? No." | [verified] `developers.tiktok.com/doc/research-api-specs-query-videos`, `.../research-api-specs-query-video-comments`, `.../research-api-faq` |
| API for Business, Discovery: `GET https://business-api.tiktok.com/open_api/v1.3/discovery/trending_list/` | Top 200 popular hashtags by `country_code`, `category_name` (industry) and `date_range` (`1DAY`, `7DAY`, `30DAY`, `120DAY`). Per hashtag: `hashtag_id`, `hashtag_name`, `rank_position`, `rank_change`, `views`, `posts`, `views_global_lifetime`, `posts_global_lifetime`, `top_country_list`, `trending_history`. | A TikTok For Business account, a developer app on the Business API portal with the matching permission scope, and an advertiser access token (`advertiser_id`). Free. | [verified] `business-api.tiktok.com/portal/docs?id=1825119032526849`, overview `?id=1825127469285442` |
| Discovery: `/discovery/detail/` | One hashtag: daily `views_daily` and `rank_position_daily`, `audience_insights`, `hashtag_status`. | Same. | [verified] `?id=1825119047070721` |
| Discovery: `/discovery/video_list/` | Top 20 videos per hashtag (up to 10 `hashtag_ids` per call) for a country and period, "ranked by a score that combines views, comments, likes, and shares". Returns `video_id`, `embed_url`, `share_url` only. No counts. | Same. | [verified] `?id=1825119056527362` |
| Discovery: `/discovery/trending/search/` and `/discovery/trending/search/keyword/` | Trending and recommended search keywords. | A TikTok account token with `discovery.search.words`, and the account must have Advanced Access or be a Verified Business Account (`has_advanced_access`). | [verified] `?id=1832798345014338` |
| Discovery rate limit | 10 queries per second per endpoint per developer app. | | [verified] `?id=1825127824570434` |
| oEmbed `GET https://www.tiktok.com/oembed?url=<video url>` | Embed markup, title, author name, thumbnail. No key. No counts. | Anyone. | [verified] `developers.tiktok.com/doc/embed-videos` |
| Comments, captions, the file | Only in the Research API (`voice_to_text`, comments). No other official route. No official download of another user's video. | | [verified] by absence in the pages above |

So the one official TikTok route open to a commercial production is the Discovery API: which hashtags are rising, and an ordered top 20 per hashtag. The order is TikTok's own ranking. The numbers behind it are not returned. This needs the same Business portal account and app that `platform.tiktok.insights` already needs (06, section 1.2), plus an ad account. Review time for that app and whether an ad account with no spend is accepted are [unverified].

### 2.2 Creative Center (E)

`ads.tiktok.com/business/creativecenter/...` now redirects to `ads.tiktok.com/creative/creativeCenter/trends` (TikTok One Creative Suite). The help article (updated July 2026) says: "Trends displays trending hashtags by industry", filtered by industry and time frame, with "See Analytics" per hashtag (trendline, related videos, audience insights, regional popularity, related hashtags). Its first step is "Log in to Creative Center"; the page shell loads without a login but I saw no data rows. It is the same data family as the Discovery API. `robots.txt` on `ads.tiktok.com` allows all paths, but the Terms below forbid automated extraction, and a logged-in scrape would use an owner account. Do not automate it. Song, creator and video tabs were not seen. [verified] `ads.tiktok.com/resources/help/article/how-to-use-trends`; other tabs [unverified]

### 2.3 Terms

TikTok U.S. Terms of Service (updated 2026-07-15): users may not "scrape, crawl, export or otherwise extract any data or content in any form, for any purpose, from the Platform using any automated system or software, including automated 'bots,' except as approved in writing", nor use "another user's content ... for commercial purposes unless permitted by TikTok ... or the user". [verified] `tiktok.com/legal/page/us/terms-of-service/en`

## 3. Instagram

Docs moved to `https://developers.facebook.com/documentation/instagram-platform/...` (old `/docs/` paths redirect). All rows below read there. If #206 is built on Instagram API with Instagram Login (`graph.instagram.com`, scopes `instagram_business_*`, the no-Page variant in 06, 1.3), it has neither of the two read routes. Both are "available with both the Instagram API with Facebook Login and the Instagram API with Unified Login". An app "can either use Facebook Login or Instagram Login but not both". [verified] `.../instagram-api-with-facebook-login/hashtag-search`, `.../business-discovery`, `.../app-review`

### 3.1 Official routes (A, B, C, D)

| Route | What it gives | Requirements and limits | Evidence |
|---|---|---|---|
| Hashtag Search: `GET /ig_hashtag_search?user_id=<IG_ID>&q=<tag>`, then `GET /<IG_HASHTAG_ID>/top_media` or `/recent_media` | Public media carrying one hashtag. Fields: `id`, `caption`, `media_type`, `media_url`, `permalink`, `timestamp`, `like_count` (omitted when the owner hides likes), `comments_count`, `children`. `username` cannot be requested. No views, shares, saves, audio or follower count. | Feature `Instagram Public Content Access`, permission `instagram_basic`, a Facebook User token for a Page connected to the account. 30 unique hashtags per rolling 7 days per account. 50 results per page. No keyword search, no region or period filter. | [verified] `.../instagram-graph-api/reference/ig-hashtag/top-media`, `.../hashtag-search` |
| Business Discovery: `GET /<IG_ID>?fields=business_discovery.username(<name>){followers_count,media_count,media{...}}` | For a named professional account: `followers_count`, `media_count`, and per media `like_count`, `comments_count`, `view_count` (added 2025-06-16, "only available using the Business Discovery API") and the other public IG Media fields. | Permissions `instagram_basic`, `instagram_manage_insights`, `pages_read_engagement`. You must already know the username. Age-gated accounts return nothing. Personal accounts are not covered. | [verified] `.../instagram-graph-api/reference/ig-user/business_discovery`, `.../changelog` |
| `media_url` on other people's media | A direct file URL. Since 2026-07-30 it is omitted when the video has copyrighted or licensed audio, or when the owner "has turned off reel downloads". | Fall back to `permalink`. | [verified] `.../changelog` |
| Instagram Audio API: `GET /ig_audio?audio_type=...&user_id=...` | With no search query it returns trending original sounds or music: `audio_id`, `title`, `display_artist`, `duration_in_ms`. It does not list the Reels using a sound. | Facebook Login or Unified Login; `instagram_basic`, `instagram_content_publish`; a connected Page. | [verified] `.../content-publishing/audio-api` |
| Comments on other people's media | `GET /<IG_MEDIA_ID>/comments` needs `instagram_basic` "for the Instagram professional account that owns the IG Media object". Not readable for media we do not own. | Only `comments_count` is available. | [verified] `.../instagram-graph-api/reference/ig-media/comments` |
| Captions, transcript | No field. | | [verified] absent |
| Shares, saves, retention | Owner-only insights (06, section 1.3). Never public. | | [verified] |
| App Review | "My app is only for a business I own or manage": Standard Access, App Review "Not required". `Instagram Public Content Access` is listed among features that take Advanced Access. Whether Hashtag Search works at Standard Access for our own account was not confirmed. | The feature's reference page did not load. | [verified] `.../app-review`; Standard Access for the feature [unverified] |
| Meta Content Library and API | Research access through an application. Commercial use is not offered. | | [secondary] search summary; `transparency.meta.com` did not render |

No "trending by topic" list exists. A hashtag's `top_media` is Instagram's own selection and has no views or usernames. Views come only through Business Discovery, account by account, so the workable loop is a watch list of named accounts.

### 3.2 Trend surfaces that are pages (E)

There is no public trending page. The professional dashboard's "Trending audio" list (up to 50 tracks, creator and business accounts, in the app) is known only from third-party guides; `help.instagram.com/637936641677566` did not render. [secondary] Reels and Explore are personalised and need a login. [unverified]

### 3.3 Terms

| Rule | Text | Evidence |
|---|---|---|
| Meta Platform Terms (effective 2026-02-03) | "You will not ... sell, license, or purchase Platform Data." Delete Platform Data when "no longer necessary for a legitimate business purpose". No use for surveillance or to "build or augment user profiles" without consent. | [verified] `developers.facebook.com/terms/` (read through a fetch summary) |
| Instagram Terms of Use | The clause against collecting information "in automated ways without our express permission" could not be re-read today: the page serves script only. | [unverified] `help.instagram.com/581066165581870` |
| Court position | *Meta v. Bright Data* (N.D. Cal., 2024-01-23): Meta lost its contract claim over logged-off scraping of public pages. The ruling is narrow. It does not cover scraping behind a login, and Meta may reword its terms. | [secondary] `quinnemanuel.com` client alert, `courthousenews.com` |

## 4. Unofficial routes (F)

### 4.1 `yt-dlp`

Read from the project's `README.md` and `supportedsites.md` on `github.com/yt-dlp/yt-dlp` (master, 2026-11-12). Not installed on this machine.

| Item | Finding | Evidence |
|---|---|---|
| YouTube | Single videos, `ytsearch:` keyword search, search URLs "with sorting and filter support", channel tabs, Shorts by audio. Needs a JavaScript runtime (`deno` recommended) and `yt-dlp-ejs`. | [verified] |
| TikTok | Single video URLs and `tiktok:user`. `tiktok:tag`, `tiktok:sound` and `tiktok:effect` are marked "Currently broken", so no discovery by hashtag or sound. | [verified] |
| Instagram | Single posts and Reels, `instagram:tag`. `instagram:user` is "Currently broken". Whether anonymous access still works without cookies was not tested. | [verified] list; anonymous access [unverified] |
| Metadata only | `--skip-download` with `-j` (`--dump-json`) prints the metadata and fetches no media. `--write-auto-subs --skip-download` writes YouTube's automatic captions without the video. `--write-comments` fetches comments. | [verified] |
| Fields | `view_count`, `like_count`, `comment_count`, `repost_count`, `save_count`, `channel_follower_count`, `timestamp`, `upload_date`, `duration`, `tags`, `track`, `artist`, `subtitles`, `automatic_captions`. Which ones each extractor fills is not documented. | [verified] names; per-platform fill [unverified] |
| Terms position | Against all three sites' terms (sections 1, 2.3, 3.3). For YouTube it also breaks the Developer Policies that bind the #205 project ("must not ... scrape YouTube Applications ... or obtain scraped YouTube data"). | [verified] |
| Legal position | The 2020 takedown of `youtube-dl` claimed circumvention under 17 U.S.C. 1201 ("No person shall circumvent a technological measure that effectively controls access to a work"). GitHub reinstated the project after more information. The tool is lawful to hold; a given download may still breach terms or copyright. | [verified] `github.blog` post (2020-11-16), `law.cornell.edu/uscode/text/17/1201` |
| Account and IP risk | Anonymous runs expose the machine's IP to rate limits and blocks. Never pass cookies or a login of a production account (`--cookies`, `--cookies-from-browser`): that ties scraping to the posting account and risks its suspension. Run it only anonymously, from a machine and IP that do not hold the posting sessions. | [inferred] from the terms above |

### 4.2 Paid data providers

List prices read on each provider's page today. All of them scrape; none is licensed by the platforms.

| Provider | Covers | List price | Fields seen in sample output | Evidence |
|---|---|---|---|---|
| Apify, TikTok Scraper (Clockworks) | Hashtag, keyword search, profile, video URL | From $1.70 per 1,000 results | `playCount`, `diggCount` (likes), `shareCount`, `commentCount`, `collectCount` (saves), `videoMeta.duration`, `createTimeISO`, `authorMeta.fans`, `musicMeta.musicName`, `musicMeta.musicOriginal`, `hashtags`, `webVideoUrl`; optional subtitles and video download | [verified] `apify.com/clockworks/tiktok-scraper` |
| Apify, Instagram Scraper (Apify) | Profile, hashtag, post and Reel URLs | From $1.50 per 1,000 results | `likesCount`, `commentsCount`, `videoPlayCount`, `videoViewCount`, `timestamp`, `ownerUsername`, `musicInfo`, latest comments | [verified] `apify.com/apify/instagram-scraper` |
| Apify, YouTube Scraper (Streamers) | Search, channel, video | From $2.40 per 1,000 videos | `viewCount`, `likes`, `commentsCount`, `duration`, `numberOfSubscribers`; subtitles as an add-on | [verified] `apify.com/streamers/youtube-scraper` |
| Bright Data, Web Scraper API | TikTok, Instagram, YouTube datasets | $1.50 per 1,000 records pay as you go; $499 per month for 384,000 records; free tier 5,000 records per month | Field lists not read | [verified] `brightdata.com/pricing/web-scraper` |
| EnsembleData | TikTok, Instagram, YouTube | Free trial 50 units per day; $100 per month for 1,500 units per day; $200 for 5,000. One unit is, for example, one hashtag search returning 20 posts. | Not read | [verified] `ensembledata.com/pricing` |

Risk: the provider takes the scraping risk on its own IPs and accounts. The production's posting accounts are not exposed, because no production login is given to the provider. The remaining risks are that the data is "scraped ... data" under YouTube's Developer Policies (so do not buy YouTube data; the API is enough), that Meta's Platform Terms forbid an app to "purchase Platform Data" (keep the provider key away from the Meta app's data), and that a provider can be cut off without notice. At 500 results a week per platform the cost is under $10 per month.

## 5. Ranking without a model (G)

Notation: `V` views, `L` likes, `C` comments, `S` shares, `F` saves, `T` hours since publish, `N` author followers or subscribers, `M` median views of the author's last 10 to 20 videos.

| Ranking | Formula | YouTube (API) | TikTok official (Discovery) | TikTok (provider) | Instagram (API) |
|---|---|---|---|---|---|
| Engagement rate | `(L + C [+ S + F]) / V` | `(likeCount + commentCount) / viewCount`. No `S`, `F`. | Not possible: no counts returned. | `(diggCount + commentCount + shareCount + collectCount) / playCount` | Business Discovery: `(like_count + comments_count) / view_count`. Hashtag Search: not possible, no views. |
| Views per hour | `V / T` | `viewCount`, `snippet.publishedAt` | Not possible. | `playCount`, `createTimeISO` | Business Discovery only: `view_count`, `timestamp` |
| Outlier score | `V / M` | Yes. `M` from the channel's uploads playlist: about 3 units per channel. | Not possible. | Yes, at the price of one profile scrape per author. | Yes: Business Discovery returns the account's recent `media` with `view_count` in the same call. |
| Comment rate | `C / V` | Yes | Not possible. | Yes | Business Discovery only |
| Reach beyond audience | `V / N` | `subscriberCount` (3 significant figures; may be hidden) | Not possible. | `authorMeta.fans` | `followers_count` |
| Platform's own order | rank position | `chart=mostPopular` position (Music, Movies, Gaming only) | Position 1 to 20 in `top_video_list`; hashtag `rank_position`, `rank_change`, `trending_history` | n/a | Order of `top_media` |
| Velocity between two reads | `(V2 - V1) / hours` | Cheap: `videos.batchGetStats` twice | Hashtag level only: `views_daily` | Costs a second scrape | Second Business Discovery call |

Rules: rank inside one platform only, because a "view" differs. Compare videos of similar length. Apply a view floor so small counts do not produce large ratios. When a field is missing (hidden likes or subscribers), skip that ranking and say so. Store inputs with a read time, for the 30-day YouTube rule.

## 6. Analysis from a temporary copy (H)

Checked read-only on this machine on 2026-11-12:

| Tool | Present | Version |
|---|---|---|
| `ffmpeg`, `ffprobe` | Yes, `/usr/local/bin` | 7.1.1 |
| `whisper-cli` (whisper.cpp) | Yes, `/usr/local/bin/whisper-cli` | 1.9.2. Only the test model `for-tests-ggml-tiny.bin` is present; a real model file must be fetched. |
| `tesseract` | Yes | 5.5.0 |
| `yt-dlp` | No | n/a |
| `whisper` (Python), `faster_whisper`, `mlx_whisper` | No | n/a |
| `scenedetect` (PySceneDetect), `cv2` | No | Latest release v0.7.3 (2026-11-08), `scenedetect.com` |
| `python3` | Yes | 3.13.3 |

| Derived item | How | Kept |
|---|---|---|
| Container facts | `ffprobe`: duration, resolution, frame rate, audio present | Numbers |
| Transcript | Extract 16 kHz mono audio with `ffmpeg`, run `whisper-cli` with timestamps. Platform captions are not available officially for other people's videos (sections 1, 2.1, 3.1). | Text with timestamps, used for short quotations and notes |
| Shot boundaries and cut rate | `ffmpeg` scene score filter (`select='gt(scene,0.3)',showinfo`) is enough and already installed. PySceneDetect is the alternative and needs OpenCV. Cut rate is cuts divided by duration. | Cut times, cuts per minute, mean shot length |
| First-three-seconds hook | Frames from seconds 0 to 3 with `ffmpeg`, the transcript words before 3.0 s, and whether a cut or on-screen text appears in that window. A model may describe the frames; the frames are then deleted. | A written description |
| On-screen text | `tesseract` on frames sampled once a second. | Text with times |

The copy lives in a temporary directory outside the repository, is never uploaded, and is deleted in the same job that made the notes. The job should record the source URL, the read time and the fact of deletion.

## 7. Rights (I)

| Question | Position | Evidence |
|---|---|---|
| (i) Storing third-party video bytes | A copy is a reproduction, and all three sites' terms forbid downloading outside their own features (sections 1, 2.3, 3.3). YouTube's Developer Policies add "download, import, backup, cache, or store copies of YouTube audiovisual content" for API clients. A short-lived internal copy for analysis has a fair use argument (17 U.S.C. 107: purpose, nature, amount, market effect), but fair use is a defence decided case by case, not a permission. The terms breach remains either way. | [verified] terms; `copyright.gov/fair-use/`, `law.cornell.edu/uscode/text/17/107`; application to us [inferred] |
| (ii) Derived notes, metrics, short quotations | Counts and dates are facts. Notes in our own words and brief quotations fit the third fair use factor ("if the use employs only a small amount of copyrighted material, fair use is more likely"). Contract limits still apply to API data: YouTube statistics for other people's videos at most 30 days without refresh; Meta Platform Data deleted when "no longer necessary for a legitimate business purpose". | [verified] `copyright.gov/fair-use/`, developer-policies, Meta Platform Terms |
| (iii) Re-hosting | Not allowed without a licence. It is public distribution of someone else's work and breaks every platform's terms. The allowed form is a link or the platform's embed: YouTube's "embeddable YouTube player", TikTok `embed_url` or oEmbed, Instagram `permalink`. | [verified] YouTube Terms, TikTok docs |

**Recommendation confirmed, with two additions.** Third-party bytes never enter git or the public media store, and are deleted after analysis. Addition 1: the same rule covers frames, audio extracts and thumbnails. Addition 2: metrics need an expiry. Records built from the YouTube API carry a read time and are refreshed or deleted within 30 days. What stays in the repository is the URL, the id, our notes, short quotations and the ranking at read time.

One correction to consider: no official route delivers bytes at all, except Instagram `media_url` when the owner allows downloads. So "ingestion" for YouTube and TikTok means either metadata only, or an unofficial fetch that breaks terms. The owner should rule on whether the factory fetches bytes at all.

## 8. Recommended smallest route per platform (J)

| Platform | With what the owner is already setting up | Extra the owner would provide | Not available |
|---|---|---|---|
| YouTube | #205 is enough. `search.list` (100 calls per day), `videos.list` or `videos.batchGetStats`, `channels.list`, `playlistItems.list`, `commentThreads.list`. Every ranking in section 5. Comments. Free. | Optional: an API key in the same project, so reads do not depend on the upload grant. Free. A quota increase needs a compliance audit. | Shares, saves, retention, the file, captions. General trending chart. |
| TikTok | #203 gives nothing for other people's videos. | Free: a TikTok For Business account, a Business API developer app and an ad account, for the Discovery API (rising hashtags, ordered top 20 videos per hashtag, no counts). The account and app are the same ones `platform.tiktok.insights` needs. Paid: a provider key for counts, about $1.70 per 1,000 results (Apify free plan includes $5 of use per month). | Research API (academic only). Any official source of per-video counts, comments, captions or the file. |
| Instagram | #206 gives nothing if it is built on Instagram Login (the no-Page variant in 06, 1.3) for other people's media. | Free: build the Meta app on Facebook Login or Unified Login instead, with a Facebook Page linked, and add `instagram_manage_insights` and `pages_read_engagement`. That gives Business Discovery for a watch list of named accounts: views, likes, comments, followers, outlier score. Hashtag Search also needs `Instagram Public Content Access`, possibly App Review. Paid: a provider key for hashtag-level counts, about $1.50 per 1,000 results. | Trending by topic or region. Shares, saves, comment text, captions. Views on hashtag results. |

Order of work: YouTube first (no new owner input). Then decide #206's login type before the owner creates the Meta app, because an app "can either use Facebook Login or Instagram Login but not both". TikTok last, and only by Discovery or a provider.

## 9. Not verified

- Instagram Terms of Use text on automated collection: `help.instagram.com/581066165581870` serves script only.
- `Instagram Public Content Access`: allowed uses, and whether it works at Standard Access for an app serving only our own account. The feature reference page did not load.
- Whether Unified Login tokens work with Business Discovery's listed "Facebook User access token" requirement in practice. The guide says yes; the reference page lists the older token.
- Meta Content Library eligibility; Instagram's in-app "Trending audio" (help page did not render); Instagram oEmbed requirements.
- TikTok: review time for a Business API app; whether an ad account with no spend can call Discovery; which permission scope name covers the hashtag endpoints; Creative Center tabs other than hashtags; Research API eligibility wording beyond the FAQ line quoted.
- YouTube: whether an API key alone is accepted by `search.list` and `commentThreads.list` (stated in a fetch summary, not quoted from the page); comment resource field names; whether `likeCount` is omitted when hidden; maximum ids per `videos.list` and `videos.batchGetStats` call; whether Google Trends still has a "YouTube Search" filter; the official Google Trends API (alpha, by application).
- `yt-dlp`: which metadata fields each of the three extractors fills; whether Instagram and TikTok work without cookies today. Nothing was run.
- Providers: field lists for Bright Data and EnsembleData; Apify paid plan prices beyond the per-result rates; no account was opened.
- Case law: *Meta v. Bright Data* was read in secondary summaries only. No legal advice was taken.
