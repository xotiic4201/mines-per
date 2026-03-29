# 🚀 Deploying to Render — Step by Step

---

## What you need before starting
- A **GitHub account** (free)
- A **Render account** at https://render.com (free)
- Your **Discord bot token**
- Your **GEMINI_API_KEY** (for screenshot analysis) — get one at https://aistudio.google.com/

---

## Step 1 — Put your code on GitHub

Render deploys directly from GitHub. You need to push your bot files there first.

1. Go to https://github.com and click **New repository**
2. Name it `bloxflip-mines-bot`, set it to **Private**, click **Create**
3. On your computer, open a terminal in the folder with `main.py` and run:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/bloxflip-mines-bot.git
git push -u origin main
```

Replace `YOUR_USERNAME` with your GitHub username.

---

## Step 2 — Create a Web Service on Render

1. Go to https://render.com and sign in
2. Click **New +** → **Web Service**
3. Click **Connect a repository** → select your `bloxflip-mines-bot` repo
4. Fill in the settings:

| Setting | Value |
|---------|-------|
| **Name** | `bloxflip-mines-bot` |
| **Region** | Oregon (US West) — or closest to you |
| **Branch** | `main` |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python main.py` |
| **Instance Type** | `Free` |

5. Click **Advanced** and add a **Disk**:
   - Name: `bloxflip-data`
   - Mount Path: `/data`
   - Size: `1 GB`

   > ⚠️ This is critical — without the disk, all your prediction data gets wiped every time Render restarts your service.

6. Click **Create Web Service** — don't worry, it will fail the first deploy because we haven't set the environment variables yet.

---

## Step 3 — Set Environment Variables

Once the service is created, go to your service page on Render and click **Environment** in the left sidebar.

Add these variables by clicking **Add Environment Variable** for each:

| Key | Value |
|-----|-------|
| `DISCORD_TOKEN` | Your Discord bot token (from discord.com/developers) |
| `ANTHROPIC_API_KEY` | Your Anthropic API key (from console.anthropic.com) |
| `HOST` | `0.0.0.0` |
| `DATA_DIR` | `/data` |

> 🔒 These are secret — Render encrypts them and never shows them again after you save.

---

## Step 4 — Trigger a Redeploy

After adding the environment variables:

1. Go to the **Deploys** tab
2. Click **Deploy latest commit**
3. Watch the logs — you should see:
   ```
   🌐 Web UI running on http://0.0.0.0:XXXX
   ✅ Slash commands synced
   ✅ Logged in as YourBot#1234
   ```

---

## Step 5 — Get your Web URL

Once deployed, Render gives you a URL like:
```
https://bloxflip-mines-bot.onrender.com
```

This is your web predictor — share it with your friends!

Add this URL as an environment variable too so the keep-alive ping works:

| Key | Value |
|-----|-------|
| `RENDER_EXTERNAL_URL` | `https://bloxflip-mines-bot.onrender.com` |

---

## Step 6 — Invite your Discord Bot

If you haven't already:

1. Go to https://discord.com/developers/applications
2. Select your application → **OAuth2** → **URL Generator**
3. Check scopes: `bot` + `applications.commands`
4. Check permissions: `Send Messages`, `Embed Links`, `Attach Files`, `Read Message History`
5. Copy the generated URL and open it to invite the bot to your server

---

## ⚠️ Free Tier Limitations

Render's free tier **sleeps after 15 minutes of inactivity**. The bot has a built-in keep-alive ping every 14 minutes to prevent this, but:

- First request after sleep takes ~30 seconds (cold start)
- If the bot is sleeping, Discord commands will time out once, then work on retry
- If this is a problem, upgrade to Render's **Starter plan ($7/mo)** which never sleeps

---

## 🔄 Updating the Bot

Whenever you change `main.py`:

```bash
git add .
git commit -m "Update bot"
git push
```

Render will automatically detect the push and redeploy. Takes about 2 minutes.

---

## 🐛 Troubleshooting

**Bot goes offline after a few hours**
→ Make sure `RENDER_EXTERNAL_URL` is set correctly

**"Application did not respond" in Discord**
→ Render is waking up from sleep — wait 30 seconds and try again

**Prediction data keeps resetting**
→ Make sure the Disk is attached at `/data` and `DATA_DIR=/data` is set

**Screenshot analysis says "API key not set"**
→ Add `GEMINI_API_KEY` in Render Environment settings and redeploy

**Slash commands not showing in Discord**
→ Wait up to 1 hour for Discord to sync, or kick and reinvite the bot
