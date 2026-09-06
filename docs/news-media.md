# News video players

Video blocks retain their original `url`. Recognized YouTube videos and Twitch
clips also include `player` with `provider`, `media_id`, `external_url`, and a
root-relative `player_url`, for example `/media/youtube/vbBd_Hu6o2M`. Resolve the
path against the API response origin. Unrecognized media has
`player: null`; clients can continue opening the original URL externally.

`GET /media/{provider}/{media_id}` serves a small HTML page containing the official
provider iframe. It is public even when the JSON API requires an API key, so
clients do not send API credentials to play a video. The route validates the
provider and media ID and does not fetch or proxy arbitrary URLs.

Provider playback requires HTTPS. Twitch receives the request hostname as
`parent`. The page and provider iframe use
`Referrer-Policy: strict-origin-when-cross-origin` so the browser sends only the
page origin in the `Referer` header on the cross-origin iframe request.
Clients must provide a viewport of at least 200 by 200 pixels for YouTube and
400 by 300 pixels for Twitch clips. No autoplay is requested; retain an
external-open fallback when embedded playback is unavailable.
