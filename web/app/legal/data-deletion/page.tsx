export const metadata = { title: "Data deletion" };

export default function DataDeletion() {
  return (
    <>
      <h1>Deleting your data</h1>
      <h2>Connected Google / YouTube data</h2>
      <p>Settings → YouTube → Disconnect revokes our access token at Google and deletes the stored tokens immediately. You can also revoke access at myaccount.google.com/permissions; the next upload attempt will then fail and you will be asked to reconnect.</p>
      <h2>Media and projects</h2>
      <p>Settings → Your data → Delete media and Google data removes generated audio, video, captions and thumbnails from storage and revokes Google access.</p>
      <h2>Whole account</h2>
      <p>Settings → Your data → Delete my account removes your media, revokes Google access, and deletes your account and every row linked to it. Videos already uploaded to YouTube stay on YouTube and must be deleted there.</p>
      <h2>By email</h2>
      <p>Write to the contact address on the privacy page; we complete deletion requests within 30 days.</p>
    </>
  );
}
