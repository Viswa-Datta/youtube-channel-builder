# YouTube API Services audit — submission checklist

Use the "YouTube API Services – Audit and Quota Extension Form" (support.google.com/youtube/contact/yt_api_form).

## Before you submit
- [ ] OAuth app verified (sensitive scopes `youtube.upload`, `youtube.readonly`)
- [ ] Public privacy policy at `/legal/privacy` that covers Google user data, Limited Use, retention and deletion
- [ ] Terms at `/legal/terms` that state users are responsible for compliance with YouTube ToS and Community Guidelines
- [ ] Your app links to the YouTube Terms of Service and the Google Privacy Policy where users connect (Settings page)
- [ ] Revocation: Settings → Disconnect revokes the token at Google and deletes stored tokens
- [ ] Data deletion path documented at `/legal/data-deletion`; deletion completes within 30 days
- [ ] Tokens encrypted at rest (AES-256-GCM), server-side only; never exposed to the browser (RLS + view)
- [ ] No API data is used for advertising or sold; cached reads are refreshed or deleted per the developer policies
- [ ] Upload flow requires explicit human approval (checklist gate) — no auto-publish

## What to include
- [ ] Google Cloud project number and OAuth client ID
- [ ] Screencast: sign in → create kit → approve video → pre-publish checklist → upload (private) → status shown
- [ ] Description of each API method used: `videos.insert` (resumable), `thumbnails.set`, `channels.list?mine=true`
- [ ] Expected daily uploads and quota need (uploads x cost per upload plus reads)
- [ ] Contact and organisation details

## After approval
Set `YOUTUBE_API_AUDITED=true` in the web env. Existing connections keep `audited_project=false` until the user reconnects
(or run: `update youtube_connections set audited_project = true;`).
