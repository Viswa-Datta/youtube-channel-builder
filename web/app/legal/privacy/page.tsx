export const metadata = { title: "Privacy policy" };
const OPERATOR = process.env.NEXT_PUBLIC_OPERATOR_NAME ?? "[Operator name]";
const CONTACT = process.env.NEXT_PUBLIC_CONTACT_EMAIL ?? "[contact email]";

export default function Privacy() {
  return (
    <>
      <h1>Privacy policy</h1>
      <p>{OPERATOR} operates YouTube Channel Builder. Contact: {CONTACT}.</p>
      <h2>What we collect</h2>
      <ul>
        <li>Account: your email and, if you sign in with Google, your name and Google account ID (via Supabase Auth).</li>
        <li>Content you create: channel details, ideas, scripts, research notes, media we generate for you, and footage you add.</li>
        <li>Connected YouTube data: OAuth tokens (encrypted at rest, server-side only), your channel ID and title, and the IDs of videos you upload through the app.</li>
        <li>Usage and cost records: which providers ran for your projects and what they cost.</li>
        <li>Error and analytics data: Sentry error reports and Vercel Analytics page views.</li>
      </ul>
      <h2>Google user data</h2>
      <p>We request only the youtube.upload and youtube.readonly scopes. We use them to upload the videos you approve and to show your channel and upload status. We do not sell Google user data, use it for advertising, or transfer it to others except the infrastructure providers needed to run the service. Our use of information received from Google APIs adheres to the Google API Services User Data Policy, including the Limited Use requirements. You can revoke access in Settings or at myaccount.google.com/permissions.</p>
      <h2>Processors</h2>
      <p>Supabase (database, auth, storage), Vercel (web hosting), our render host, and the AI and voice providers you choose for a project (for example OpenAI, Anthropic, Google, ElevenLabs). Text you send to these providers is processed under their terms.</p>
      <h2>Retention and deletion</h2>
      <p>Projects are kept until you delete them. You can delete media and connected Google data, or your whole account, in Settings; see the data deletion page.</p>
      <h2>Your rights</h2>
      <p>You can access, export, correct or delete your data by contacting {CONTACT}.</p>
    </>
  );
}
