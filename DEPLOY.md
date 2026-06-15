# Deploy Bill Collect to Vercel

## What This Deployment Includes
- Mobile PWA frontend
- Installable iPhone app shell
- AT&T PDF text extraction through `/api/extract_pdf`
- Local browser storage for members, months, payments, history, backups, and WhatsApp import

## Important Limitation
This Vercel version does not yet include a shared cloud database.

That means:
- The app works from any HTTPS URL.
- Each device keeps its own local data.
- Use Export backup / Restore backup to move data between devices.
- To sync automatically across devices, add Supabase next.

## Deploy From Vercel Dashboard
1. Put this folder in a GitHub repository:
   `outputs/routine-app`
2. Open your Vercel dashboard:
   `https://vercel.com/momentsbysunnys-projects`
3. Click `Add New...` then `Project`.
4. Import the GitHub repository.
5. Set the project root to the folder that contains:
   - `index.html`
   - `app.js`
   - `styles.css`
   - `vercel.json`
   - `api/extract_pdf.py`
6. Framework preset: `Other`.
7. Build command: `npm run build`
8. Output directory: leave empty.
9. Deploy.

## Deploy From CLI
Install and log in:

```bash
npm i -g vercel
vercel login
```

Deploy preview:

```bash
vercel outputs/routine-app
```

Deploy production:

```bash
vercel outputs/routine-app --prod
```

## Install On iPhone
1. Open the Vercel URL in Safari.
2. Tap Share.
3. Tap Add to Home Screen.
4. Open Bill Collect from the Home Screen icon.

## Next Professional Step
Add Supabase Auth + Supabase Postgres so the same members, bills, and payment history sync everywhere after login.
