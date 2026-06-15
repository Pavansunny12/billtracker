# Deploying & Configuring Bill Collect (Production Ready)

Your Bill Collect app is now deployed on Vercel. However, it displays **"Local only"** because it needs a GitHub Access Token to connect to your repository and sync your data securely across PC and mobile.

Once configured, the status will change to **"Synced"** and your history will be safely saved in your GitHub repository as `state.json`.

---

## 🛠️ Step 1: Create a GitHub Access Token

1. Go to your **GitHub Settings** (click your profile picture on the top right > Settings).
2. Scroll to the bottom of the left sidebar and click **Developer settings**.
3. Under **Personal Access Tokens**, click **Tokens (classic)**.
4. Click **Generate new token** > select **Generate new token (classic)**.
5. **Note**: Give it a name like `Bill Tracker Sync`.
6. **Expiration**: Select `No expiration` (or set a date of your choice).
7. **Select scopes**: Check the **`repo`** checkbox (this allows the app to read/write `state.json` inside your repository).
8. Scroll to the bottom and click **Generate token**.
9. **Copy the token** (it starts with `ghp_...`) and save it somewhere temporary. *You won't be able to see it again!*

---

## ⚙️ Step 2: Add the Token to Vercel

1. Open your Vercel Dashboard: `https://vercel.com`
2. Click on your project: **`billtracker-tsyw-rose`** (or your active project name).
3. Go to the **Settings** tab at the top of the project page.
4. Click on **Environment Variables** in the left sidebar.
5. Add the following environment variable:
   * **Key**: `GITHUB_TOKEN`
   * **Value**: `[Paste your ghp_... token here]`
6. Leave the checkmarks checked for *Production*, *Preview*, and *Development*.
7. Click **Save**.

*(Optional)* If you want to use a different repository or state file name, you can also add:
* `GITHUB_REPO`: `owner/repository-name` (defaults to `Pavansunny12/billtracker`)
* `GITHUB_FILE_PATH`: `file-name.json` (defaults to `state.json`)

---

## 🚀 Step 3: Redeploy to Apply Changes

Since Vercel encrypts and locks environment variables on build:
1. Go to the **Deployments** tab in your Vercel project dashboard.
2. Click the three dots (`...`) next to your latest deployment.
3. Click **Redeploy**.
4. Confirm by clicking **Redeploy** again in the popup.

Once the build is complete (takes about 30 seconds), reload your app. The status at the top will change from **"Local only"** to **"Synced"**, and a new file named `state.json` will automatically appear in your GitHub repository, containing your bill history!

---

## 📱 How to Install on iPhone / Android
1. Open the production URL in Safari (iPhone) or Chrome (Android).
2. Tap the **Share** button (iOS) or the menu icon (Android).
3. Tap **Add to Home Screen**.
4. Open the app from your home screen. It will load instantly in full-screen native standalone mode!
