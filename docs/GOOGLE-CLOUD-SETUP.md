# Google Cloud + YouTube setup

YouTube's API **cannot create channels**. The user creates the channel on youtube.com, then connects it here.

## 1. Project and API
1. console.cloud.google.com → New project (e.g. `channel-builder`).
2. APIs & Services → Library → enable **YouTube Data API v3**.
   (Optional, for Chirp voices: also enable **Cloud Text-to-Speech API**.)

## 2. OAuth consent screen (Google Auth Platform)
1. Audience: **External**. App name, support email, logo, app home page (`NEXT_PUBLIC_APP_URL`),
   privacy policy (`/legal/privacy`), terms (`/legal/terms`), authorized domain (your domain).
2. Data access → add scopes, **only these two**:
   * `https://www.googleapis.com/auth/youtube.upload` — upload the videos the user approves
   * `https://www.googleapis.com/auth/youtube.readonly` — read channel name and upload status
   (plus `openid`, which is non-sensitive)
3. While in **Testing**, add your own Google account as a test user. Testing-mode refresh tokens expire after 7 days.

## 3. OAuth client
Credentials → Create credentials → OAuth client ID → **Web application**.
* Authorized redirect URI: `https://YOUR_APP/api/youtube/callback` (and `http://localhost:3000/api/youtube/callback` for dev)
* Copy the client ID and secret into `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` (web **and** worker; the worker refreshes tokens).

## 4. App sign-in with Google (separate from YouTube)
In Supabase → Authentication → Providers → Google, use **a second OAuth client** (or the same one with the Supabase callback
`https://YOUR-PROJECT.supabase.co/auth/v1/callback` added) with only the basic profile scopes. Signing in does not grant YouTube access.

## 5. Verification (needed before other people can connect)
`youtube.upload` and `youtube.readonly` are **sensitive** scopes. To leave Testing, submit the app for verification:
* Verified domain ownership (Search Console) for the home page and policy URLs.
* A demo video showing the consent screen and how each scope is used (connect → upload a private video → show status).
* A written justification per scope (reuse the plain-language text in Settings).
Expect days to weeks. Until then only listed test users can connect.

## 6. YouTube API compliance audit (to upload anything but private)
Videos uploaded by API projects created after 28 July 2020 that have not passed the audit are **locked to private**.
The app forces `private` and says so in the UI until you set `YOUTUBE_API_AUDITED=true` after approval.
See [YOUTUBE-AUDIT.md](YOUTUBE-AUDIT.md) for the submission checklist. Meanwhile, users download the MP4 and upload by hand,
or upload privately and switch visibility in YouTube Studio.

## 7. Quota
Default 10,000 units/day per project. Configured cost per upload: 100 units (`config/plans.json`, from the December 2025
revision history; some sources report a further cut in 2026, so check the Quota Calculator). The app caps uploads per user per
day and shows usage on the Publish page. Request more quota through the audit form.
