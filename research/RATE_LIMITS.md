# YouTube Rate Limits & Mitigation Plan

Your errors (`HTTP 429`, `RequestBlocked`, `IpBlocked`, `403`, "Sign in to confirm you're not a bot") usually indicate YouTube's anti-abuse systems are throttling or blocking requests. This is expected when scraping unofficial endpoints and is not necessarily a bug in your code.

## 1. What the Limits Actually Are

### A. yt-dlp / InnerTube (Discovery + Metadata)

*Used by `get_playlist_data()` and `fetch_videos_full_metadata()`*

* **No official request quota.** YouTube applies dynamic IP- and behavior-based throttling.
* Common symptoms:

  * `HTTP 429 Too Many Requests`
  * `403 Forbidden`
  * CAPTCHA / "Sign in to confirm you're not a bot"
  * Partial or missing playlist entries
* Effective limits depend on:

  * IP reputation (residential vs. datacenter)
  * Request rate and concurrency
  * Cookies/authentication
  * Client behavior and endpoint
* **Official yt-dlp guidance:** if blocked, solve the CAPTCHA in a browser and reuse the same cookies/IP with yt-dlp.

---

### B. youtube-transcript-api (TimedText API)

* The library itself has **no built-in quota.**
* The upstream project documents that **many cloud-provider IPs (AWS/GCP/Azure/etc.) are blocked**, sometimes regardless of request rate.
* Common exceptions:

  * `TooManyRequests (429)`
  * `RequestBlocked`
  * `IpBlocked`
  * `NoTranscriptFound`
  * `TranscriptsDisabled`
* Recommended mitigations:

  * Residential IP or residential proxy
  * Cookies when appropriate
  * Alternate extraction path (yt-dlp fallback)

---

### C. Comment Scraping

* Uses unofficial YouTube continuation endpoints.
* Subject to the same anti-bot protections as metadata scraping.
* Failures may include:

  * 429/403
  * Expired continuations
  * Partial results
  * Disabled or unavailable comments

---

### D. YouTube Data API v3 (Official)

*Not currently used by this project.*

* Default project quota: **10,000 units/day**
* `videos.list` = 1 unit
* `commentThreads.list` = 1 unit
* `search.list` uses a separate Search Queries quota bucket (default: 100 searches/day)
* Official API provides predictable quotas but does not expose everything available through scraping.

---

# 2. Throttling Strategy

## AdaptiveThrottler (per service)

* Random delay between requests
* Exponential backoff with jitter
* Honors `Retry-After` when present
* Circuit breaker after repeated 429s
* Separate throttlers for:

  * Discovery
  * Metadata
  * Transcripts
  * Comments

Default pacing:

* Discovery: 1.2–3.0s
* Metadata: 1.5–3.5s
* Transcripts: 2.0–4.0s
* Comments: 2.5–5.0s

These are conservative defaults, **not guaranteed safe limits**.

---

## Failure Handling

Treat failures differently:

| Error                          | Action                                                 |
| ------------------------------ | ------------------------------------------------------ |
| 429                            | Retry with exponential backoff                         |
| Temporary network error        | Retry                                                  |
| `RequestBlocked` / `IpBlocked` | Stop retrying that route; switch IP/proxy if available |
| `NoTranscriptFound`            | Skip video                                             |
| `TranscriptsDisabled`          | Skip video                                             |
| 403/Auth challenge             | Retry only if cookies/authentication may resolve it    |

---

## Transcript Fallbacks

Attempt in order:

1. `youtube-transcript-api` (current API)
2. Legacy API compatibility
3. yt-dlp caption extraction

The yt-dlp fallback uses a different extraction path and may succeed when transcript retrieval fails, **but it is not guaranteed to bypass an IP-level block.**

---

## Additional Protections

* Stable browser User-Agent per session
* Optional proxy support
* Optional cookies
* Random jitter between requests
* Chunk large jobs with cooldown pauses
* Log and classify throttling events

---

# 3. If You Get Blocked

The retry logic will:

* Back off automatically
* Retry temporary failures
* Attempt transcript fallback
* Continue processing remaining videos

If blocking persists:

### Option 1 — Use Cookies

```
YTK_COOKIES_FILE=./cookies.txt
```

or

```
YTK_COOKIES_FROM_BROWSER=chrome
```

Use a dedicated account when possible and keep cookie files secure.

---

### Option 2 — Use a Residential Proxy

```
YTK_PROXY=http://user:pass@host:port
```

Residential IPs are generally more reliable than cloud-provider IPs for unofficial YouTube endpoints.

---

### Option 3 — Reduce Throughput

Increase request delays and/or reduce worker concurrency.

Example:

```bash
YTK_SLEEP_TRANS_MIN=4
YTK_SLEEP_TRANS_MAX=7
YTK_CHUNK_PAUSE=60
```

---

### Option 4 — Run from a Residential Network

If transcript requests consistently fail from AWS/GCP/Azure, running from a residential connection is often the most reliable solution.

---

# 4. Expected Behavior

Before:

* Aggressive request rate
* Immediate failures on 429
* Older transcript API only

Now:

* Conservative pacing with jitter
* Exponential backoff
* Circuit breaker
* Three transcript retrieval paths
* Automatic classification of retryable vs. non-retryable failures

Typical log output:

```
[transcript 429] retry 2/5 after 8.4s
```

or

```
[transcript blocked] switching to fallback
```

The scraper should recover from temporary throttling while avoiding repeated retries against a known blocked IP.

---

# Key References

* yt-dlp FAQ (429, cookies, CAPTCHA)
* yt-dlp PO Token Guide
* youtube-transcript-api README (cloud IP blocking)
* YouTube Data API quota documentation
