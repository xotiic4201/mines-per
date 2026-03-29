# 🎲 Bloxflip Mines Predictor

A Discord bot + web UI for predicting mine positions in Bloxflip Mines.
Learns from submitted results to improve accuracy over time.

---

## ⚡ Quick Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set your Discord bot token
```bash
# Linux/Mac
export DISCORD_TOKEN="your_token_here"

# Windows
set DISCORD_TOKEN=your_token_here
```

### 3. Run
```bash
python main.py
```

The web UI will be available at **http://localhost:8000**
The Discord bot will connect automatically.

---

## 🌐 Web UI Features
- **Predictor** — Set tile count + mine count, get a visual grid prediction
- **Submit Results** — Enter actual mine positions to train the AI
- **Recent Predictions** — History of all predictions
- **Leaderboard** — Top users by accuracy

---

## 🤖 Discord Commands

| Command | Description |
|---------|-------------|
| `/predict <tiles> [mines] [round_id]` | Get mine predictions |
| `/submit <round_id> <mine positions>` | Submit actual results |
| `/stats` | Your personal accuracy stats |
| `/leaderboard` | Top 10 predictors |
| `/help` | Show all commands |

**Examples:**
```
/predict tile_amount:16 mine_count:8
/predict tile_amount:25 mine_count:12 round_id:ABC123
/submit round_id:123456 mines:3 7 12 15
```

---

## ☁️ Deployment (Replit / Railway / Render)

Set these environment variables:
- `DISCORD_TOKEN` — Your bot token
- `PORT` — Port for the web server (default: 8000)
- `HOST` — Host to bind (default: 0.0.0.0)

### Discord Bot Setup
1. Go to https://discord.com/developers/applications
2. Create New Application → Bot tab → Reset Token
3. Enable: `Message Content Intent`
4. Invite bot with scopes: `bot`, `applications.commands`
5. Required permissions: `Send Messages`, `Embed Links`

---

## 🧠 How the Predictor Works

1. **Base probability** — Each tile starts with `mine_count / tile_count` risk
2. **Historical weighting** — Past games for same tile count shift risk scores
3. **Recency bias** — Last 10 games weighted more heavily (40%)
4. **Confidence** — Grows with more submitted results (starts at 45%, caps at 93%)
5. **Data file** — All predictions and results stored in `bloxflip_data.json`

The more results you submit, the more accurate predictions become!
