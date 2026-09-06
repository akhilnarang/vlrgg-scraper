# News video players

Video blocks retain their original `url`. Recognized YouTube videos and Twitch
clips also include `player` with `provider`, `media_id`, `external_url`, and a
relative `player_url`, for example `/media/youtube/vbBd_Hu6o2M`. Resolve that path
against the API response origin. Unrecognized media has `player: null`; clients
can continue opening the original URL externally.

`GET /media/{provider}/{media_id}` serves a small HTML page containing the official
provider iframe. It is public even when the JSON API requires an API key, so a
mobile WebView never needs to send API credentials to play a video. The route
validates provider and ID and does not fetch or proxy arbitrary URLs. There are
no additional dependencies. Load it on demand in a system WebView and release
the WebView when playback closes or the article leaves the screen.

Serve the page over HTTPS. YouTube receives the page's origin; Twitch receives
its hostname as `parent`. If using a reverse proxy, configure the server's
trusted forwarded headers so FastAPI sees the external HTTPS scheme. Preserve
`Referrer-Policy: strict-origin-when-cross-origin` and permit provider iframes;
missing referrer identification can prevent YouTube playback. No autoplay is
requested. YouTube requires a viewport of at least 200 by 200 pixels; Twitch
clips require at least 400 by 300 pixels. On narrow mobile layouts, expand Twitch
playback to a fullscreen presentation and ask the user to rotate if the available
width is still below 400 pixels. Do not scale or crop the player to evade those
minimums. Retain an external-open action if provider playback is unavailable.

The page route must be deployed alongside the JSON API before clients can use
`player_url`. Local HTTP can inspect layout and metadata, but real provider
playback must also be tested on an HTTPS origin with provider connectivity.
