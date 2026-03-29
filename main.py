import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
from datetime import datetime
from collections import defaultdict
import random
import asyncio
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import threading
import math
from typing import Optional, List
import aiohttp
import base64
import re

# ============ CONFIGURATION ============
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
PORT = int(os.environ.get('PORT', 8000))
HOST = os.environ.get('HOST', '0.0.0.0')
# On Render use persistent disk at /data; locally use current dir
DATA_DIR = os.environ.get('DATA_DIR')
os.makedirs(DATA_DIR, exist_ok=True)

# ============ PREDICTOR CLASS ============
class BloxflipPredictor:
    def __init__(self):
        self.data_file = os.path.join(DATA_DIR, 'bloxflip_data.json')
        self.load_data()

    def load_data(self):
        try:
            with open(self.data_file, 'r') as f:
                raw = json.load(f)
                self.data = raw
                # Fix mine_distribution if stored as plain dict
                if 'mine_distribution' in self.data['global_stats']:
                    dist = self.data['global_stats']['mine_distribution']
                    self.data['global_stats']['mine_distribution'] = defaultdict(int, {int(k): v for k, v in dist.items()})
                else:
                    self.data['global_stats']['mine_distribution'] = defaultdict(int)
        except Exception:
            self.data = {
                'games': {},
                'user_stats': {},
                'global_stats': {
                    'total_predictions': 0,
                    'total_correct': 0,
                    'accuracy_history': [],
                    'mine_distribution': defaultdict(int)
                }
            }

    def save_data(self):
        save_data = {
            'games': self.data['games'],
            'user_stats': self.data['user_stats'],
            'global_stats': {
                'total_predictions': self.data['global_stats']['total_predictions'],
                'total_correct': self.data['global_stats']['total_correct'],
                'accuracy_history': self.data['global_stats']['accuracy_history'],
                'mine_distribution': dict(self.data['global_stats']['mine_distribution'])
            }
        }
        with open(self.data_file, 'w') as f:
            json.dump(save_data, f, indent=2)

    def get_historical_patterns(self, tile_amt):
        patterns = {
            'frequent_mines': defaultdict(int),
            'recent_mines': [],
            'total_games': 0
        }

        for game in self.data['games'].values():
            if game.get('tile_amt') == tile_amt and game.get('actual_mines'):
                for mine in game['actual_mines']:
                    patterns['frequent_mines'][mine] += 1
                patterns['total_games'] += 1
                patterns['recent_mines'].append(game['actual_mines'])

        # Keep only the most recent 20
        patterns['recent_mines'] = patterns['recent_mines'][-20:]
        return patterns

    def predict_mines(self, tile_amt: int, mine_count: Optional[int] = None, user_id=None):
        if mine_count is None:
            mine_count = tile_amt // 2

        mine_count = max(1, min(mine_count, tile_amt - 1))

        patterns = self.get_historical_patterns(tile_amt)
        total_games = patterns['total_games']

        base_risk = mine_count / tile_amt

        tile_risks = {}
        for tile in range(1, tile_amt + 1):
            risk = base_risk

            if total_games > 0:
                freq = patterns['frequent_mines'].get(tile, 0)
                historical_risk = freq / total_games
                weight = min(1.0, total_games / 100)
                risk = (1 - weight) * base_risk + weight * historical_risk

            if patterns['recent_mines']:
                recent_games = patterns['recent_mines'][-10:]
                recent_count = sum(1 for mines in recent_games if tile in mines)
                recent_risk = recent_count / len(recent_games)
                risk = 0.6 * risk + 0.4 * recent_risk

            tile_risks[tile] = round(min(0.97, max(0.03, risk)), 3)

        sorted_tiles = sorted(tile_risks.items(), key=lambda x: x[1], reverse=True)
        predicted_mines = [tile for tile, _ in sorted_tiles[:mine_count]]
        safe_tiles = [tile for tile in range(1, tile_amt + 1) if tile not in predicted_mines]

        confidence = round(min(0.93, 0.45 + (total_games / 300)), 2)

        return {
            'safe_tiles': safe_tiles,
            'mine_tiles': predicted_mines,
            'confidence': confidence,
            'risk_scores': {str(tile): risk for tile, risk in sorted_tiles},
            'mine_count': mine_count,
            'tile_amt': tile_amt
        }

    def submit_results(self, round_id: str, actual_mines: List[int], user_id):
        if round_id not in self.data['games']:
            return False, "No prediction found for this round ID."

        game = self.data['games'][round_id]
        game['actual_mines'] = actual_mines
        game['completed_at'] = datetime.now().isoformat()

        predicted_mines_set = set(game['predicted_mines'])
        actual_mines_set = set(actual_mines)

        correct_predictions = len(predicted_mines_set & actual_mines_set)
        total = len(actual_mines_set)
        accuracy = (correct_predictions / total * 100) if total > 0 else 0

        game['accuracy'] = round(accuracy, 1)
        game['correct_count'] = correct_predictions

        self.data['global_stats']['total_correct'] += correct_predictions
        self.data['global_stats']['accuracy_history'].append(accuracy)

        for mine in actual_mines:
            self.data['global_stats']['mine_distribution'][mine] += 1

        user_id_str = str(user_id)
        if user_id_str not in self.data['user_stats']:
            self.data['user_stats'][user_id_str] = {
                'total_predictions': 0,
                'total_correct': 0,
                'accuracy_history': [],
                'name': str(user_id)
            }

        self.data['user_stats'][user_id_str]['total_predictions'] += 1
        self.data['user_stats'][user_id_str]['total_correct'] += correct_predictions
        self.data['user_stats'][user_id_str]['accuracy_history'].append(accuracy)

        self.save_data()

        return True, {
            'accuracy': accuracy,
            'correct': correct_predictions,
            'total': total
        }

    def record_bet(self, user_id, bet_amount: float, tile_amt: int, mine_count: int,
                   won: bool, payout: float = 0.0):
        user_id_str = str(user_id)
        if user_id_str not in self.data['user_stats']:
            self.data['user_stats'][user_id_str] = {
                'total_predictions': 0,
                'total_correct': 0,
                'accuracy_history': [],
                'name': user_id_str,
                'bets': []
            }
        stats = self.data['user_stats'][user_id_str]
        if 'bets' not in stats:
            stats['bets'] = []
        stats['bets'].append({
            'ts': datetime.now().isoformat(),
            'bet': bet_amount,
            'tiles': tile_amt,
            'mines': mine_count,
            'won': won,
            'payout': payout
        })
        stats['bets'] = stats['bets'][-200:]
        self.save_data()

    def get_bet_insights(self, user_id) -> dict:
        stats = self.data['user_stats'].get(str(user_id), {})
        bets = stats.get('bets', [])
        if not bets:
            return {}

        total = len(bets)
        wins = [b for b in bets if b['won']]
        losses = [b for b in bets if not b['won']]
        win_rate = round(len(wins) / total * 100, 1)
        total_wagered = sum(b['bet'] for b in bets)
        total_returned = sum(b.get('payout', 0) for b in wins)
        net = round(total_returned - total_wagered, 2)
        avg_bet = round(total_wagered / total, 2)

        by_mines = defaultdict(lambda: {'w': 0, 'l': 0})
        for b in bets:
            k = b.get('mines', '?')
            if b['won']:
                by_mines[k]['w'] += 1
            else:
                by_mines[k]['l'] += 1
        best_mines = max(by_mines, key=lambda k: by_mines[k]['w'] / max(1, by_mines[k]['w'] + by_mines[k]['l']), default=None)

        return {
            'total_bets': total,
            'win_rate': win_rate,
            'net_robux': net,
            'avg_bet': avg_bet,
            'best_mine_count': best_mines,
            'wins': len(wins),
            'losses': len(losses)
        }

    def get_leaderboard(self, limit=10):
        lb = []
        for uid, stats in self.data['user_stats'].items():
            if stats['total_predictions'] > 0:
                avg_acc = sum(stats['accuracy_history']) / len(stats['accuracy_history']) if stats['accuracy_history'] else 0
                lb.append({
                    'user': stats.get('name', uid),
                    'predictions': stats['total_predictions'],
                    'correct': stats['total_correct'],
                    'avg_accuracy': round(avg_acc, 1)
                })
        lb.sort(key=lambda x: x['avg_accuracy'], reverse=True)
        return lb[:limit]


# ============ SCREENSHOT ANALYZER ============
class ScreenshotAnalyzer:
    """Uses Google Gemini (free tier) to read a Bloxflip Mines screenshot."""

    SYSTEM_PROMPT = """You are an expert at reading Bloxflip Mines game screenshots.
Bloxflip Mines is a grid-based game where some tiles hide bombs/mines.

Analyze the screenshot and return ONLY valid JSON with these exact keys:
{
  "tile_count": <total number of tiles in the grid, integer, e.g. 25>,
  "mine_count": <number of mines set for this game, integer>,
  "bet_amount": <bet in Robux as a number, e.g. 100.0, or null if not visible>,
  "revealed_safe": [<tile numbers already clicked and confirmed safe, 1-indexed left-to-right top-to-bottom>],
  "revealed_mines": [<tile numbers already revealed as mines>],
  "grid_rows": <number of rows, integer>,
  "grid_cols": <number of columns, integer>,
  "game_state": "<active|won|lost|unknown>",
  "notes": "<any extra observations, e.g. multiplier shown>"
}

Tile numbering: top-left = 1, goes left to right, then next row. So a 5x5 grid: row1=1-5, row2=6-10, etc.
If you cannot determine a value with confidence, use null.
Return ONLY the JSON object, no markdown, no explanation."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def analyze(self, image_bytes: bytes, mime_type: str = "image/png") -> dict:
        if not self.api_key:
            return {'error': 'GEMINI_API_KEY not set. Add it to your environment variables. Get a free key at aistudio.google.com'}

        b64 = base64.standard_b64encode(image_bytes).decode('utf-8')

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": b64
                            }
                        },
                        {
                            "text": self.SYSTEM_PROMPT + "\n\nAnalyze this Bloxflip Mines screenshot and return the JSON."
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 1024
            }
        }

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return {'error': f'Gemini API error {resp.status}: {text[:200]}'}
                data = await resp.json()

        try:
            raw = data['candidates'][0]['content']['parts'][0]['text'].strip()
        except (KeyError, IndexError):
            return {'error': 'Unexpected response structure from Gemini API'}

        # Strip markdown fences if present
        raw = re.sub(r'^```[a-z]*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {'error': f'Could not parse Gemini response: {raw[:300]}'}


# ============ FASTAPI WEB SERVER ============
app = FastAPI(title="Bloxflip Mines Predictor")
predictor_instance: Optional[BloxflipPredictor] = None


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bloxflip Mines Predictor</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@300;400;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #080c14;
    --surface: #0d1421;
    --surface2: #111b2e;
    --border: rgba(0,255,180,0.15);
    --accent: #00ffb4;
    --accent2: #ff3d71;
    --accent3: #00b4ff;
    --text: #c8d8e8;
    --text-dim: #5a7090;
    --mine: #ff3d71;
    --safe: #00ffb4;
    --glow: 0 0 20px rgba(0,255,180,0.3);
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: 'Rajdhani', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    overflow-x: hidden;
  }
  body::before {
    content:'';
    position:fixed;
    inset:0;
    background:
      radial-gradient(ellipse 60% 40% at 20% 10%, rgba(0,255,180,0.04) 0%, transparent 60%),
      radial-gradient(ellipse 50% 50% at 80% 90%, rgba(0,180,255,0.04) 0%, transparent 60%);
    pointer-events:none;
    z-index:0;
  }
  .grid-bg {
    position:fixed;
    inset:0;
    background-image:
      linear-gradient(rgba(0,255,180,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,255,180,0.03) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events:none;
    z-index:0;
  }

  nav {
    position: sticky;
    top: 0;
    z-index: 100;
    background: rgba(8,12,20,0.9);
    backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--border);
    padding: 0 40px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 64px;
  }
  .logo {
    font-family: 'Orbitron', sans-serif;
    font-size: 1.1rem;
    font-weight: 900;
    color: var(--accent);
    letter-spacing: 0.05em;
    text-shadow: var(--glow);
  }
  .logo span { color: var(--text-dim); }
  .nav-links { display:flex; gap:30px; }
  .nav-link {
    font-size: 0.85rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-dim);
    cursor:pointer;
    transition: color 0.2s;
  }
  .nav-link:hover, .nav-link.active { color: var(--accent); }

  main { position: relative; z-index: 1; max-width: 1300px; margin: 0 auto; padding: 40px 20px 80px; }

  .hero {
    text-align: center;
    padding: 60px 0 50px;
  }
  .hero-tag {
    display: inline-block;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--accent);
    border: 1px solid var(--accent);
    padding: 4px 14px;
    border-radius: 2px;
    margin-bottom: 20px;
    box-shadow: var(--glow);
  }
  .hero h1 {
    font-family: 'Orbitron', sans-serif;
    font-size: clamp(2rem, 5vw, 3.8rem);
    font-weight: 900;
    line-height: 1.1;
    margin-bottom: 16px;
  }
  .hero h1 em {
    font-style: normal;
    color: var(--accent);
    text-shadow: var(--glow);
  }
  .hero p {
    color: var(--text-dim);
    font-size: 1.1rem;
    max-width: 520px;
    margin: 0 auto;
    line-height: 1.6;
  }

  .stats-row {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin-bottom: 50px;
  }
  @media(max-width:800px) { .stats-row { grid-template-columns: repeat(2,1fr); } }
  .stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    text-align: center;
    position:relative;
    overflow:hidden;
  }
  .stat-card::before {
    content:'';
    position:absolute;
    top:0; left:0; right:0;
    height:2px;
    background: linear-gradient(90deg, transparent, var(--accent), transparent);
    opacity: 0.6;
  }
  .stat-val {
    font-family: 'Orbitron', sans-serif;
    font-size: 2rem;
    font-weight: 700;
    color: var(--accent);
    line-height: 1;
    margin-bottom: 6px;
  }
  .stat-label { font-size: 0.78rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--text-dim); }

  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 24px; }
  @media(max-width:900px){ .two-col { grid-template-columns: 1fr; } }

  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 28px;
  }
  .panel-title {
    font-family: 'Orbitron', sans-serif;
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 22px;
    display:flex;
    align-items:center;
    gap: 10px;
  }
  .panel-title::after {
    content:'';
    flex:1;
    height:1px;
    background: var(--border);
  }

  label { font-size: 0.82rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-dim); display: block; margin-bottom: 6px; }
  input[type=number], select {
    width: 100%;
    background: var(--bg);
    border: 1px solid rgba(0,255,180,0.2);
    border-radius: 6px;
    color: var(--text);
    font-family: 'Rajdhani', sans-serif;
    font-size: 1rem;
    padding: 10px 14px;
    outline: none;
    transition: border 0.2s, box-shadow 0.2s;
    margin-bottom: 16px;
    appearance: none;
  }
  input[type=number]:focus, select:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(0,255,180,0.1);
  }

  .range-wrap { margin-bottom: 20px; }
  .range-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
  .range-val {
    font-family: 'Orbitron', sans-serif;
    font-size: 1.4rem;
    font-weight: 700;
    color: var(--accent);
  }
  input[type=range] {
    width: 100%;
    appearance: none;
    height: 4px;
    background: var(--surface2);
    border-radius: 4px;
    outline: none;
    cursor: pointer;
  }
  input[type=range]::-webkit-slider-thumb {
    appearance: none;
    width: 18px;
    height: 18px;
    background: var(--accent);
    border-radius: 50%;
    box-shadow: 0 0 10px rgba(0,255,180,0.5);
    cursor: pointer;
    transition: transform 0.15s;
  }
  input[type=range]::-webkit-slider-thumb:hover { transform: scale(1.2); }

  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    font-family: 'Orbitron', sans-serif;
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    padding: 12px 28px;
    border-radius: 6px;
    border: none;
    cursor: pointer;
    transition: all 0.2s;
    width: 100%;
  }
  .btn-primary {
    background: var(--accent);
    color: #000;
    box-shadow: 0 0 20px rgba(0,255,180,0.3);
  }
  .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 4px 30px rgba(0,255,180,0.45); }
  .btn-primary:active { transform: translateY(0); }
  .btn-primary:disabled { opacity:0.4; cursor:not-allowed; transform:none; }

  .btn-secondary {
    background: transparent;
    color: var(--accent);
    border: 1px solid var(--accent);
    box-shadow: inset 0 0 0 0 var(--accent);
    transition: all 0.25s;
    margin-top: 10px;
  }
  .btn-secondary:hover { background: rgba(0,255,180,0.08); }

  #grid-container { margin-top: 20px; }
  .mine-grid {
    display: grid;
    gap: 6px;
    margin-bottom: 20px;
  }
  .tile {
    aspect-ratio: 1;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: 'Orbitron', sans-serif;
    font-size: 0.75rem;
    font-weight: 700;
    cursor: default;
    border: 1px solid transparent;
    transition: transform 0.15s, box-shadow 0.2s;
    animation: tileIn 0.4s ease both;
    position: relative;
    overflow: hidden;
  }
  .tile::before {
    content:'';
    position:absolute;
    inset:0;
    background: linear-gradient(135deg, rgba(255,255,255,0.06) 0%, transparent 60%);
    pointer-events:none;
  }
  @keyframes tileIn {
    from { opacity:0; transform: scale(0.7); }
    to   { opacity:1; transform: scale(1); }
  }
  .tile-safe {
    background: rgba(0,255,180,0.1);
    border-color: rgba(0,255,180,0.35);
    color: var(--safe);
    box-shadow: 0 0 10px rgba(0,255,180,0.1);
  }
  .tile-mine {
    background: rgba(255,61,113,0.12);
    border-color: rgba(255,61,113,0.35);
    color: var(--mine);
    box-shadow: 0 0 10px rgba(255,61,113,0.1);
  }
  .tile-mine .tile-icon { filter: drop-shadow(0 0 4px rgba(255,61,113,0.6)); }
  .tile-safe .tile-icon { filter: drop-shadow(0 0 4px rgba(0,255,180,0.5)); }
  .tile-icon { font-size: 1.1em; }
  .tile-num { font-size: 0.6em; opacity: 0.6; position:absolute; top:3px; left:4px; }

  .confidence-section { margin-bottom: 20px; }
  .conf-label { display:flex; justify-content:space-between; margin-bottom:6px; }
  .conf-pct {
    font-family:'Orbitron',sans-serif;
    font-size: 1.3rem;
    font-weight:700;
    color: var(--accent);
  }
  .conf-bar-bg {
    height: 8px;
    background: var(--surface2);
    border-radius: 4px;
    overflow: hidden;
  }
  .conf-bar-fill {
    height:100%;
    border-radius:4px;
    background: linear-gradient(90deg, var(--accent3), var(--accent));
    box-shadow: 0 0 10px rgba(0,255,180,0.4);
    transition: width 0.8s cubic-bezier(0.22, 1, 0.36, 1);
    width: 0%;
  }

  .legend {
    display:flex;
    gap:20px;
    margin-top: 14px;
    justify-content:center;
  }
  .legend-item { display:flex; align-items:center; gap:6px; font-size:0.82rem; color:var(--text-dim); }
  .legend-dot { width:10px; height:10px; border-radius:50%; }
  .legend-dot.safe { background:var(--safe); box-shadow:0 0 6px rgba(0,255,180,0.5); }
  .legend-dot.mine { background:var(--mine); box-shadow:0 0 6px rgba(255,61,113,0.5); }

  .pred-summary {
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:12px;
    margin-bottom:20px;
  }
  .pred-stat {
    background: var(--surface2);
    border-radius: 8px;
    padding: 12px 16px;
    text-align:center;
  }
  .pred-stat-val { font-family:'Orbitron',sans-serif; font-size:1.6rem; font-weight:700; }
  .pred-stat-val.safe-color { color: var(--safe); }
  .pred-stat-val.mine-color { color: var(--mine); }
  .pred-stat-label { font-size:0.75rem; letter-spacing:0.1em; text-transform:uppercase; color:var(--text-dim); margin-top:2px; }

  .recent-list { display:flex; flex-direction:column; gap:10px; }
  .recent-item {
    background: var(--surface2);
    border-radius:8px;
    padding:14px 18px;
    display:flex;
    justify-content:space-between;
    align-items:center;
    border-left: 3px solid var(--accent);
    animation: slideIn 0.3s ease both;
  }
  @keyframes slideIn { from{opacity:0;transform:translateX(-10px)} to{opacity:1;transform:translateX(0)} }
  .recent-item-info { font-size:0.92rem; }
  .recent-item-id { font-family:'Orbitron',sans-serif; font-size:0.72rem; color:var(--accent); margin-bottom:3px; }
  .recent-item-detail { color:var(--text-dim); font-size:0.82rem; }
  .recent-item-conf {
    font-family:'Orbitron',sans-serif;
    font-size:1.1rem;
    font-weight:700;
    color:var(--accent);
  }

  .lb-table { width:100%; border-collapse:collapse; }
  .lb-table th {
    font-size:0.72rem;
    letter-spacing:0.1em;
    text-transform:uppercase;
    color:var(--text-dim);
    padding:8px 12px;
    text-align:left;
    border-bottom: 1px solid var(--border);
  }
  .lb-table td {
    padding: 12px;
    font-size:0.92rem;
    border-bottom: 1px solid rgba(255,255,255,0.03);
  }
  .lb-rank { font-family:'Orbitron',sans-serif; font-weight:700; color:var(--text-dim); }
  .lb-rank.gold { color:#ffd700; }
  .lb-rank.silver { color:#c0c0c0; }
  .lb-rank.bronze { color:#cd7f32; }
  .lb-acc { font-family:'Orbitron',sans-serif; font-weight:700; color:var(--accent); }

  .section { margin-bottom:28px; }
  .empty { text-align:center; padding:30px; color:var(--text-dim); font-size:0.9rem; }

  #toast {
    position:fixed;
    bottom:30px;
    left:50%;
    transform:translateX(-50%) translateY(80px);
    background:var(--surface);
    border:1px solid var(--accent);
    color:var(--text);
    padding:12px 24px;
    border-radius:8px;
    font-size:0.9rem;
    box-shadow:0 4px 30px rgba(0,0,0,0.4);
    z-index:999;
    transition:transform 0.3s cubic-bezier(0.22,1,0.36,1);
    white-space:nowrap;
  }
  #toast.show { transform:translateX(-50%) translateY(0); }

  .spinner {
    display:inline-block;
    width:16px;
    height:16px;
    border:2px solid rgba(0,0,0,0.3);
    border-top-color:#000;
    border-radius:50%;
    animation:spin 0.6s linear infinite;
  }
  @keyframes spin { to { transform:rotate(360deg); } }

  .tabs { display:flex; gap:0; margin-bottom:28px; border:1px solid var(--border); border-radius:8px; overflow:hidden; }
  .tab {
    flex:1;
    padding:12px;
    text-align:center;
    font-family:'Orbitron',sans-serif;
    font-size:0.72rem;
    font-weight:700;
    letter-spacing:0.1em;
    text-transform:uppercase;
    cursor:pointer;
    color:var(--text-dim);
    background:var(--surface);
    transition:all 0.2s;
    border:none;
  }
  .tab.active { background:rgba(0,255,180,0.1); color:var(--accent); }
  .tab-panel { display:none; }
  .tab-panel.active { display:block; }
</style>
</head>
<body>
<div class="grid-bg"></div>

<nav>
  <div class="logo">BLOX<span>FLIP</span> · PREDICTOR</div>
  <div class="nav-links">
    <span class="nav-link active" onclick="scrollTo(0,0)">HOME</span>
    <span class="nav-link" onclick="document.getElementById('predictor-section').scrollIntoView({behavior:'smooth'})">PREDICT</span>
    <span class="nav-link" onclick="document.getElementById('stats-section').scrollIntoView({behavior:'smooth'})">STATS</span>
  </div>
</nav>

<main>
  <section class="hero">
    <div class="hero-tag">⚡ AI-Powered</div>
    <h1>Mine <em>Predictor</em><br>Intelligence</h1>
    <p>Advanced pattern recognition trained on game history. Identify safe tiles with data-driven confidence.</p>
  </section>

  <div class="stats-row" id="stats-section">
    <div class="stat-card">
      <div class="stat-val" id="s-total">—</div>
      <div class="stat-label">Total Predictions</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" id="s-acc">—</div>
      <div class="stat-label">Avg Accuracy</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" id="s-users">—</div>
      <div class="stat-label">Active Users</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" id="s-completed">—</div>
      <div class="stat-label">Completed Games</div>
    </div>
  </div>

  <div class="tabs">
    <button class="tab active" onclick="switchTab('predictor')">🎲 Predictor</button>
    <button class="tab" onclick="switchTab('recent')">📋 Recent</button>
    <button class="tab" onclick="switchTab('leaderboard')">🏆 Leaderboard</button>
  </div>

  <div class="tab-panel active" id="tab-predictor" id="predictor-section">
    <div class="two-col" id="predictor-section">
      <div class="panel">
        <div class="panel-title">Configure Prediction</div>

        <div class="range-wrap">
          <div class="range-header">
            <label style="margin:0">Total Tiles</label>
            <div class="range-val" id="tiles-val">16</div>
          </div>
          <input type="range" id="tiles-slider" min="4" max="25" value="16" oninput="updateSliders()">
          <div style="display:flex;justify-content:space-between;font-size:0.72rem;color:var(--text-dim);margin-top:4px">
            <span>4</span><span>25</span>
          </div>
        </div>

        <div class="range-wrap">
          <div class="range-header">
            <label style="margin:0">Mine Count</label>
            <div class="range-val" id="mines-val">8</div>
          </div>
          <input type="range" id="mines-slider" min="1" max="24" value="8" oninput="updateSliders()">
          <div style="display:flex;justify-content:space-between;font-size:0.72rem;color:var(--text-dim);margin-top:4px">
            <span>1</span><span id="mines-max-label">24</span>
          </div>
        </div>

        <button class="btn btn-primary" id="predict-btn" onclick="runPredict()">
          <span id="btn-text">ANALYZE &amp; PREDICT</span>
        </button>

        <div id="submit-section" style="display:none;margin-top:24px;padding-top:20px;border-top:1px solid var(--border)">
          <div class="panel-title">Submit Results</div>
          <label>Round ID</label>
          <input type="text" id="submit-round-id" placeholder="auto-filled" style="background:var(--bg);border:1px solid rgba(0,255,180,0.2);border-radius:6px;color:var(--text);font-family:'Rajdhani',sans-serif;font-size:1rem;padding:10px 14px;width:100%;margin-bottom:12px;outline:none">
          <label>Actual Mine Positions (space-separated)</label>
          <input type="text" id="submit-mines" placeholder="e.g. 3 7 12 15" style="background:var(--bg);border:1px solid rgba(0,255,180,0.2);border-radius:6px;color:var(--text);font-family:'Rajdhani',sans-serif;font-size:1rem;padding:10px 14px;width:100%;margin-bottom:12px;outline:none">
          <button class="btn btn-secondary" onclick="submitResults()">SUBMIT RESULTS</button>
        </div>
      </div>

      <div class="panel">
        <div class="panel-title">Prediction Output</div>
        <div id="prediction-output">
          <div class="empty">Configure settings and click ANALYZE to see predictions</div>
        </div>
      </div>
    </div>
  </div>

  <div class="tab-panel" id="tab-recent">
    <div class="panel">
      <div class="panel-title">Recent Predictions</div>
      <div id="recent-list" class="recent-list">
        <div class="empty">No predictions yet</div>
      </div>
    </div>
  </div>

  <div class="tab-panel" id="tab-leaderboard">
    <div class="panel">
      <div class="panel-title">Leaderboard</div>
      <div id="lb-content">
        <div class="empty">No submissions yet</div>
      </div>
    </div>
  </div>
</main>

<div id="toast"></div>

<script>
let currentRoundId = null;
let currentPrediction = null;

function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
  if (name === 'recent') loadRecent();
  if (name === 'leaderboard') loadLeaderboard();
}

function updateSliders() {
  const tiles = parseInt(document.getElementById('tiles-slider').value);
  const minesSlider = document.getElementById('mines-slider');
  const maxMines = tiles - 1;
  minesSlider.max = maxMines;
  if (parseInt(minesSlider.value) > maxMines) minesSlider.value = maxMines;
  document.getElementById('tiles-val').textContent = tiles;
  document.getElementById('mines-val').textContent = minesSlider.value;
  document.getElementById('mines-max-label').textContent = maxMines;
}

async function runPredict() {
  const tileAmt = parseInt(document.getElementById('tiles-slider').value);
  const mineCount = parseInt(document.getElementById('mines-slider').value);
  const btn = document.getElementById('predict-btn');
  const btnText = document.getElementById('btn-text');

  btn.disabled = true;
  btnText.innerHTML = '<span class="spinner"></span> ANALYZING...';

  try {
    const res = await fetch(`/api/predict?tiles=${tileAmt}&mines=${mineCount}`);
    if (!res.ok) throw new Error('API error');
    const data = await res.json();
    currentRoundId = data.round_id;
    currentPrediction = data;
    renderPrediction(data, tileAmt);
    document.getElementById('submit-section').style.display = 'block';
    document.getElementById('submit-round-id').value = data.round_id;
    showToast('✅ Prediction generated!');
  } catch(e) {
    showToast('❌ Error generating prediction');
  } finally {
    btn.disabled = false;
    btnText.textContent = 'ANALYZE & PREDICT';
  }
}

function renderPrediction(data, tileAmt) {
  const cols = Math.ceil(Math.sqrt(tileAmt));
  const conf = Math.round(data.confidence * 100);

  const html = `
    <div class="confidence-section">
      <div class="conf-label">
        <span style="font-size:0.82rem;letter-spacing:0.08em;text-transform:uppercase;color:var(--text-dim)">Confidence Score</span>
        <span class="conf-pct">${conf}%</span>
      </div>
      <div class="conf-bar-bg">
        <div class="conf-bar-fill" id="conf-fill" style="width:0%"></div>
      </div>
    </div>

    <div class="pred-summary">
      <div class="pred-stat">
        <div class="pred-stat-val safe-color">${data.safe_tiles.length}</div>
        <div class="pred-stat-label">Safe Tiles</div>
      </div>
      <div class="pred-stat">
        <div class="pred-stat-val mine-color">${data.mine_tiles.length}</div>
        <div class="pred-stat-label">Mine Tiles</div>
      </div>
    </div>

    <div id="grid-container">
      <div class="mine-grid" style="grid-template-columns:repeat(${cols},1fr)">
        ${Array.from({length:tileAmt},(_,i)=>{
          const n = i+1;
          const isMine = data.mine_tiles.includes(n);
          const risk = data.risk_scores[String(n)] || 0;
          const delay = (i * 20) + 'ms';
          return `<div class="tile ${isMine?'tile-mine':'tile-safe'}" style="animation-delay:${delay}" title="Tile ${n} | Risk: ${Math.round(risk*100)}%">
            <span class="tile-num">${n}</span>
            <span class="tile-icon">${isMine?'💣':'✅'}</span>
          </div>`;
        }).join('')}
      </div>
    </div>

    <div class="legend">
      <div class="legend-item"><div class="legend-dot safe"></div> Safe</div>
      <div class="legend-item"><div class="legend-dot mine"></div> Mine</div>
    </div>
    <div style="margin-top:12px;font-size:0.8rem;color:var(--text-dim);text-align:center">Round ID: <span style="color:var(--accent);font-family:'Orbitron',sans-serif">${data.round_id}</span></div>
  `;

  document.getElementById('prediction-output').innerHTML = html;
  setTimeout(() => {
    const fill = document.getElementById('conf-fill');
    if (fill) fill.style.width = conf + '%';
  }, 100);
}

async function submitResults() {
  const roundId = document.getElementById('submit-round-id').value.trim();
  const minesStr = document.getElementById('submit-mines').value.trim();
  if (!roundId || !minesStr) { showToast('❌ Fill in both fields'); return; }

  const mines = minesStr.split(/[\s,]+/).map(Number).filter(n => n > 0);
  if (!mines.length) { showToast('❌ Invalid mine positions'); return; }

  try {
    const res = await fetch('/api/submit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({round_id: roundId, mines, user_id: 'web_user'})
    });
    const data = await res.json();
    if (data.error) { showToast('❌ ' + data.error); return; }
    showToast(`✅ ${data.correct}/${data.total} correct — ${data.accuracy.toFixed(1)}% accuracy!`);
    loadStats();
  } catch(e) {
    showToast('❌ Submission failed');
  }
}

async function loadStats() {
  try {
    const res = await fetch('/api/stats');
    const data = await res.json();
    document.getElementById('s-total').textContent = data.total_predictions;
    document.getElementById('s-acc').textContent = data.accuracy_rate + '%';
    document.getElementById('s-users').textContent = data.active_users;
    document.getElementById('s-completed').textContent = data.completed_games;
  } catch(e) {}
}

async function loadRecent() {
  try {
    const res = await fetch('/api/stats');
    const data = await res.json();
    const el = document.getElementById('recent-list');
    if (!data.recent_predictions || !data.recent_predictions.length) {
      el.innerHTML = '<div class="empty">No predictions yet — use the Predictor tab!</div>';
      return;
    }
    el.innerHTML = data.recent_predictions.map((p,i) => `
      <div class="recent-item" style="animation-delay:${i*50}ms">
        <div class="recent-item-info">
          <div class="recent-item-id">ROUND #${p.round_id}</div>
          <div class="recent-item-detail">${p.tile_amt || '?'} tiles · ${p.mine_count} mines · ${p.safe_count} safe</div>
          <div class="recent-item-detail" style="font-size:0.75rem;margin-top:2px;color:var(--text-dim)">${p.timestamp ? new Date(p.timestamp).toLocaleString() : ''}</div>
        </div>
        <div class="recent-item-conf">${p.confidence}%</div>
      </div>
    `).join('');
  } catch(e) {}
}

async function loadLeaderboard() {
  try {
    const res = await fetch('/api/leaderboard');
    const data = await res.json();
    const el = document.getElementById('lb-content');
    if (!data.leaderboard || !data.leaderboard.length) {
      el.innerHTML = '<div class="empty">No submissions yet! Submit round results to appear here.</div>';
      return;
    }
    const rankClass = i => i===0?'gold':i===1?'silver':i===2?'bronze':'';
    el.innerHTML = `<table class="lb-table">
      <thead><tr><th>#</th><th>User</th><th>Predictions</th><th>Correct</th><th>Avg Accuracy</th></tr></thead>
      <tbody>${data.leaderboard.map((u,i) => `
        <tr>
          <td><span class="lb-rank ${rankClass(i)}">${i+1}</span></td>
          <td>${u.user}</td>
          <td>${u.predictions}</td>
          <td>${u.correct}</td>
          <td><span class="lb-acc">${u.avg_accuracy}%</span></td>
        </tr>`).join('')}
      </tbody>
    </table>`;
  } catch(e) {}
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

updateSliders();
loadStats();
setInterval(loadStats, 10000);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML_PAGE


@app.get("/health")
async def health():
    return {"status": "ok", "predictions": len(predictor_instance.data['games']) if predictor_instance else 0}


@app.get("/api/stats")
async def get_stats():
    if not predictor_instance:
        return JSONResponse(content={'error': 'Not ready'}, status_code=503)

    games = predictor_instance.data['games']
    total_predictions = len(games)
    completed = [g for g in games.values() if g.get('actual_mines')]
    accuracies = [g['accuracy'] for g in completed if 'accuracy' in g]
    accuracy_rate = round(sum(accuracies) / len(accuracies), 1) if accuracies else 0.0

    recent = []
    for round_id, game in list(games.items())[-20:]:
        recent.append({
            'round_id': round_id,
            'timestamp': game.get('timestamp', ''),
            'tile_amt': game.get('tile_amt'),
            'safe_count': len(game.get('predicted_safe', [])),
            'mine_count': len(game.get('predicted_mines', [])),
            'confidence': round(game.get('confidence', 0) * 100, 1),
            'accuracy': game.get('accuracy')
        })
    recent.reverse()

    return {
        'total_predictions': total_predictions,
        'completed_games': len(completed),
        'accuracy_rate': accuracy_rate,
        'active_users': len(predictor_instance.data['user_stats']),
        'recent_predictions': recent
    }


@app.get("/api/predict")
async def api_predict(tiles: int = 16, mines: int = None):
    if not predictor_instance:
        return JSONResponse(content={'error': 'Not ready'}, status_code=503)

    if tiles < 4 or tiles > 25:
        return JSONResponse(content={'error': 'tiles must be 4–25'}, status_code=400)

    if mines is not None and (mines < 1 or mines >= tiles):
        return JSONResponse(content={'error': f'mines must be 1–{tiles-1}'}, status_code=400)

    prediction = predictor_instance.predict_mines(tiles, mines, user_id='web_user')
    round_id = str(random.randint(100000, 999999))

    game_data = {
        'timestamp': datetime.now().isoformat(),
        'user_id': 'web_user',
        'user_name': 'Web User',
        'tile_amt': tiles,
        'round_id': round_id,
        'predicted_safe': prediction['safe_tiles'],
        'predicted_mines': prediction['mine_tiles'],
        'confidence': prediction['confidence']
    }
    predictor_instance.data['games'][round_id] = game_data
    predictor_instance.data['global_stats']['total_predictions'] += 1
    predictor_instance.save_data()

    return {
        'round_id': round_id,
        **prediction
    }


@app.post("/api/submit")
async def api_submit(body: dict):
    if not predictor_instance:
        return JSONResponse(content={'error': 'Not ready'}, status_code=503)

    round_id = body.get('round_id', '').strip()
    mines = body.get('mines', [])
    user_id = body.get('user_id', 'web_user')

    if not round_id or not mines:
        return JSONResponse(content={'error': 'Missing round_id or mines'}, status_code=400)

    try:
        mines = [int(m) for m in mines]
    except Exception:
        return JSONResponse(content={'error': 'Invalid mine positions'}, status_code=400)

    success, result = predictor_instance.submit_results(round_id, mines, user_id)
    if not success:
        return JSONResponse(content={'error': result}, status_code=404)

    return result


@app.get("/api/leaderboard")
async def api_leaderboard():
    if not predictor_instance:
        return JSONResponse(content={'error': 'Not ready'}, status_code=503)
    return {'leaderboard': predictor_instance.get_leaderboard()}


# ============ DISCORD BOT ============
class DiscordBot(commands.Bot):
    def __init__(self, predictor: BloxflipPredictor):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!', intents=intents)
        self.predictor = predictor

    async def setup_hook(self):
        await self.add_cog(MinesCog(self))
        await self.tree.sync()
        print("✅ Slash commands synced")

    async def on_ready(self):
        print(f"✅ Logged in as {self.user}")
        await self.change_presence(activity=discord.Game(name="Bloxflip Mines 🎲"))
        self.keep_alive.start()

    @tasks.loop(minutes=14)
    async def keep_alive(self):
        render_url = os.environ.get('RENDER_EXTERNAL_URL', '')
        if not render_url:
            return
        try:
            async with aiohttp.ClientSession() as session:
                await session.get(f"{render_url}/health", timeout=aiohttp.ClientTimeout(total=10))
        except Exception:
            pass


class MinesCog(commands.Cog):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        self.analyzer = ScreenshotAnalyzer(GEMINI_API_KEY)  # ← uses Gemini now

    @app_commands.command(name="analyze", description="Upload a Bloxflip screenshot to auto-detect grid and get a prediction")
    @app_commands.describe(screenshot="Your Bloxflip Mines screenshot")
    async def slash_analyze(self, interaction: discord.Interaction, screenshot: discord.Attachment):
        await interaction.response.defer()

        if not screenshot.content_type or not screenshot.content_type.startswith('image/'):
            await interaction.followup.send("❌ Please attach an **image** file (PNG, JPG, etc.)", ephemeral=True)
            return

        if screenshot.size > 8_000_000:
            await interaction.followup.send("❌ Image too large (max 8MB)", ephemeral=True)
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(screenshot.url) as resp:
                    image_bytes = await resp.read()
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to download image: {e}", ephemeral=True)
            return

        thinking_embed = discord.Embed(
            title="🔍 Analyzing Screenshot...",
            description="Reading your Bloxflip grid with Gemini AI. This takes a few seconds.",
            color=discord.Color.orange()
        )
        msg = await interaction.followup.send(embed=thinking_embed)

        mime = screenshot.content_type.split(';')[0]
        result = await self.analyzer.analyze(image_bytes, mime)

        if 'error' in result:
            err_embed = discord.Embed(
                title="❌ Analysis Failed",
                description=result['error'],
                color=discord.Color.red()
            )
            if 'GEMINI_API_KEY' in result['error']:
                err_embed.add_field(
                    name="Setup Required",
                    value="Set `GEMINI_API_KEY` environment variable. Get a free key at https://aistudio.google.com",
                    inline=False
                )
            await msg.edit(embed=err_embed)
            return

        tile_count = result.get('tile_count')
        mine_count = result.get('mine_count')
        bet_amount = result.get('bet_amount')
        revealed_safe = result.get('revealed_safe', []) or []
        revealed_mines = result.get('revealed_mines', []) or []
        game_state = result.get('game_state', 'unknown')
        notes = result.get('notes', '')

        if not tile_count or not mine_count:
            err_embed = discord.Embed(
                title="⚠️ Could Not Read Grid",
                description=(
                    "Gemini couldn't confidently detect the tile/mine count from this screenshot.\n\n"
                    "**Try:**\n"
                    "• Make sure the full Bloxflip Mines grid is visible\n"
                    "• Use a higher quality screenshot\n"
                    "• Use `/predict` manually instead"
                ),
                color=discord.Color.yellow()
            )
            if notes:
                err_embed.add_field(name="AI Notes", value=notes[:500], inline=False)
            await msg.edit(embed=err_embed)
            return

        tile_count = int(tile_count)
        mine_count = int(mine_count)

        if bet_amount is not None:
            try:
                bet_float = float(bet_amount)
                won = game_state == 'won'
                self.bot.predictor.record_bet(
                    interaction.user.id, bet_float, tile_count, mine_count, won
                )
                uid_str = str(interaction.user.id)
                if uid_str in self.bot.predictor.data['user_stats']:
                    self.bot.predictor.data['user_stats'][uid_str]['name'] = interaction.user.display_name
                    self.bot.predictor.save_data()
            except Exception:
                pass

        if game_state in ('won', 'lost') and revealed_mines:
            round_id = str(random.randint(100000, 999999))
            game_data = {
                'timestamp': datetime.now().isoformat(),
                'user_id': interaction.user.id,
                'user_name': interaction.user.display_name,
                'tile_amt': tile_count,
                'round_id': round_id,
                'predicted_safe': [],
                'predicted_mines': revealed_mines,
                'confidence': 1.0,
                'from_screenshot': True
            }
            self.bot.predictor.data['games'][round_id] = game_data
            self.bot.predictor.submit_results(round_id, revealed_mines, interaction.user.id)

            result_embed = discord.Embed(
                title=f"{'🏆 Game Won!' if game_state == 'won' else '💥 Game Lost'}",
                description=f"Screenshot analysed. Mine positions recorded for AI training.",
                color=discord.Color.green() if game_state == 'won' else discord.Color.red()
            )
            result_embed.add_field(name="Grid", value=f"{tile_count} tiles · {mine_count} mines", inline=True)
            if bet_amount:
                result_embed.add_field(name="Bet", value=f"R${bet_amount:,.0f}", inline=True)
            result_embed.add_field(name="💣 Mine Positions", value=f"```{' '.join(map(str, revealed_mines))}```", inline=False)
            result_embed.set_footer(text="Mine data saved — improves future predictions!")
            await msg.edit(embed=result_embed)
            return

        prediction = self.bot.predictor.predict_mines(tile_count, mine_count, interaction.user.id)

        safe_tiles = [t for t in prediction['safe_tiles'] if t not in revealed_safe and t not in revealed_mines]
        mine_tiles = [t for t in prediction['mine_tiles'] if t not in revealed_safe and t not in revealed_mines]

        round_id = str(random.randint(100000, 999999))
        game_data = {
            'timestamp': datetime.now().isoformat(),
            'user_id': interaction.user.id,
            'user_name': interaction.user.display_name,
            'tile_amt': tile_count,
            'round_id': round_id,
            'predicted_safe': safe_tiles,
            'predicted_mines': mine_tiles,
            'confidence': prediction['confidence'],
            'bet_amount': bet_amount,
            'from_screenshot': True
        }
        self.bot.predictor.data['games'][round_id] = game_data
        self.bot.predictor.data['global_stats']['total_predictions'] += 1
        self.bot.predictor.save_data()

        conf_pct = prediction['confidence'] * 100
        conf_emoji = "🟢" if conf_pct > 70 else "🟡" if conf_pct > 50 else "🔴"

        embed = discord.Embed(
            title="🎲 Screenshot Prediction",
            description=(
                f"**Round ID:** `{round_id}`\n"
                f"**Grid:** {tile_count} tiles · **Mines:** {mine_count}"
            ),
            color=discord.Color.from_rgb(0, 255, 180),
            timestamp=datetime.now()
        )

        if bet_amount is not None:
            embed.add_field(name="💰 Bet Detected", value=f"R${bet_amount:,.0f}", inline=True)

        if revealed_safe:
            embed.add_field(
                name=f"✔️ Already Revealed Safe ({len(revealed_safe)})",
                value=f"```{' '.join(map(str, revealed_safe[:20]))}```",
                inline=False
            )

        embed.add_field(name="📊 Confidence", value=f"{conf_emoji} **{conf_pct:.1f}%**", inline=False)

        safe_display = " ".join(map(str, safe_tiles[:20]))
        if len(safe_tiles) > 20:
            safe_display += f" (+{len(safe_tiles)-20} more)"
        embed.add_field(name=f"✅ CLICK THESE ({len(safe_tiles)} safe)", value=f"```{safe_display}```", inline=False)

        mine_display = " ".join(map(str, mine_tiles[:20]))
        if len(mine_tiles) > 20:
            mine_display += f" (+{len(mine_tiles)-20} more)"
        embed.add_field(name=f"💣 AVOID THESE ({len(mine_tiles)} mines)", value=f"```{mine_display}```", inline=False)

        if notes:
            embed.add_field(name="📝 AI Notes", value=notes[:300], inline=False)

        embed.set_footer(text=f"Use /submit {round_id} <mine positions> after the game to train the AI")
        await msg.edit(embed=embed)

    @app_commands.command(name="betlog", description="View your bet history and performance insights")
    async def slash_betlog(self, interaction: discord.Interaction):
        await interaction.response.defer()
        insights = self.bot.predictor.get_bet_insights(interaction.user.id)

        if not insights:
            embed = discord.Embed(
                title="💰 Your Bet History",
                description="No bets recorded yet!\nUse `/analyze` with your Bloxflip screenshots to auto-track bets.",
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed)
            return

        net = insights['net_robux']
        net_str = f"+R${net:,.2f}" if net >= 0 else f"-R${abs(net):,.2f}"
        net_color = discord.Color.green() if net >= 0 else discord.Color.red()

        embed = discord.Embed(
            title=f"💰 {interaction.user.display_name}'s Bet Insights",
            color=net_color
        )
        embed.add_field(name="Total Bets", value=str(insights['total_bets']), inline=True)
        embed.add_field(name="Win Rate", value=f"{insights['win_rate']}%", inline=True)
        embed.add_field(name="Net P&L", value=net_str, inline=True)
        embed.add_field(name="Avg Bet", value=f"R${insights['avg_bet']:,.2f}", inline=True)
        embed.add_field(name="W / L", value=f"{insights['wins']} / {insights['losses']}", inline=True)
        if insights.get('best_mine_count'):
            embed.add_field(name="Best Mine Count", value=f"{insights['best_mine_count']} mines", inline=True)

        embed.set_footer(text="Bets are tracked automatically via /analyze screenshots")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="predict", description="Generate mine predictions for Bloxflip")
    @app_commands.describe(
        tile_amount="Number of tiles (4–25)",
        mine_count="Number of mines in the game (default: half of tiles)",
        round_id="Optional custom round ID"
    )
    async def slash_predict(
        self,
        interaction: discord.Interaction,
        tile_amount: int,
        mine_count: Optional[int] = None,
        round_id: Optional[str] = None
    ):
        await interaction.response.defer()

        if tile_amount < 4 or tile_amount > 25:
            await interaction.followup.send("❌ Tile amount must be between **4 and 25**!", ephemeral=True)
            return

        if mine_count is not None and (mine_count < 1 or mine_count >= tile_amount):
            await interaction.followup.send(f"❌ Mine count must be between **1 and {tile_amount-1}**!", ephemeral=True)
            return

        if round_id is None:
            round_id = str(random.randint(100000, 999999))

        prediction = self.bot.predictor.predict_mines(tile_amount, mine_count, interaction.user.id)

        game_data = {
            'timestamp': datetime.now().isoformat(),
            'user_id': interaction.user.id,
            'user_name': interaction.user.name,
            'tile_amt': tile_amount,
            'round_id': round_id,
            'predicted_safe': prediction['safe_tiles'],
            'predicted_mines': prediction['mine_tiles'],
            'confidence': prediction['confidence']
        }
        self.bot.predictor.data['games'][round_id] = game_data
        self.bot.predictor.data['global_stats']['total_predictions'] += 1
        self.bot.predictor.save_data()

        conf_pct = prediction['confidence'] * 100
        conf_emoji = "🟢" if conf_pct > 70 else "🟡" if conf_pct > 50 else "🔴"

        embed = discord.Embed(
            title="🎲 Bloxflip Mines Predictor",
            description=(
                f"**Round ID:** `{round_id}`\n"
                f"**Tiles:** {tile_amount} · **Mines:** {prediction['mine_count']}"
            ),
            color=discord.Color.from_rgb(0, 255, 180),
            timestamp=datetime.now()
        )

        embed.add_field(
            name="📊 Confidence",
            value=f"{conf_emoji} **{conf_pct:.1f}%**",
            inline=False
        )

        safe = prediction['safe_tiles']
        safe_display = " ".join(map(str, safe[:20]))
        if len(safe) > 20:
            safe_display += f" (+{len(safe)-20} more)"
        embed.add_field(name=f"✅ SAFE TILES ({len(safe)})", value=f"```{safe_display}```", inline=False)

        mines = prediction['mine_tiles']
        mine_display = " ".join(map(str, mines[:20]))
        if len(mines) > 20:
            mine_display += f" (+{len(mines)-20} more)"
        embed.add_field(name=f"💣 MINE TILES ({len(mines)})", value=f"```{mine_display}```", inline=False)

        embed.set_footer(text=f"Submit actual mines with /submit {round_id} <positions>")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="submit", description="Submit actual mine positions to improve accuracy")
    @app_commands.describe(
        round_id="Round ID from /predict",
        mines="Mine positions space-separated (e.g. 3 7 12)"
    )
    async def slash_submit(self, interaction: discord.Interaction, round_id: str, mines: str):
        await interaction.response.defer()

        try:
            mine_list = [int(x) for x in mines.split()]
        except ValueError:
            await interaction.followup.send("❌ Invalid format! Example: `/submit 123456 3 7 12`", ephemeral=True)
            return

        if not mine_list:
            await interaction.followup.send("❌ Please provide at least one mine position!", ephemeral=True)
            return

        success, result = self.bot.predictor.submit_results(round_id, mine_list, interaction.user.id)
        if not success:
            await interaction.followup.send(f"❌ {result}", ephemeral=True)
            return

        acc = result['accuracy']
        acc_emoji = "🟢" if acc >= 70 else "🟡" if acc >= 50 else "🔴"

        embed = discord.Embed(
            title="✅ Results Submitted!",
            description=f"Round `{round_id}` results recorded.",
            color=discord.Color.green()
        )
        embed.add_field(
            name="📊 Accuracy",
            value=f"**{result['correct']}/{result['total']}** mines correct\n{acc_emoji} **{acc:.1f}%** accuracy",
            inline=False
        )
        embed.set_footer(text="Thanks! This improves future predictions.")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="stats", description="View your prediction statistics")
    async def slash_stats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user_id = str(interaction.user.id)
        stats = self.bot.predictor.data['user_stats'].get(user_id)

        if not stats or stats['total_predictions'] == 0:
            embed = discord.Embed(
                title="📊 Your Statistics",
                description="You haven't submitted any results yet!\nUse `/predict` then `/submit` to get started.",
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed)
            return

        avg_acc = sum(stats['accuracy_history']) / len(stats['accuracy_history']) if stats['accuracy_history'] else 0

        embed = discord.Embed(
            title=f"📊 {interaction.user.display_name}'s Stats",
            color=discord.Color.from_rgb(0, 180, 255)
        )
        embed.add_field(name="Total Predictions", value=str(stats['total_predictions']), inline=True)
        embed.add_field(name="Total Correct", value=str(stats['total_correct']), inline=True)
        embed.add_field(name="Avg Accuracy", value=f"{avg_acc:.1f}%", inline=True)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="leaderboard", description="View top predictors")
    async def slash_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        lb = self.bot.predictor.get_leaderboard(10)

        if not lb:
            await interaction.followup.send("No submissions yet! Use `/submit` after a prediction.", ephemeral=True)
            return

        medals = ["🥇", "🥈", "🥉"]
        embed = discord.Embed(title="🏆 Leaderboard", color=discord.Color.gold())
        desc = ""
        for i, entry in enumerate(lb):
            medal = medals[i] if i < 3 else f"`{i+1}.`"
            desc += f"{medal} **{entry['user']}** — {entry['avg_accuracy']}% avg · {entry['predictions']} games\n"
        embed.description = desc
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="help", description="Show all commands")
    async def slash_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎲 Bloxflip Mines Predictor",
            description="AI-powered mine prediction. Upload screenshots for auto-detection!",
            color=discord.Color.from_rgb(0, 255, 180)
        )
        embed.add_field(name="📸 /analyze <screenshot>", value="**Best command!** Upload a Bloxflip screenshot — Gemini AI reads grid size, mine count, bet, and predicts", inline=False)
        embed.add_field(name="🎲 /predict <tiles> [mines]", value="Manual prediction if you know the grid settings", inline=False)
        embed.add_field(name="✅ /submit <round_id> <mines>", value="Submit actual mine positions after a game to train the AI", inline=False)
        embed.add_field(name="💰 /betlog", value="View your bet history, win rate, and net profit/loss", inline=False)
        embed.add_field(name="📊 /stats", value="Your prediction accuracy statistics", inline=False)
        embed.add_field(name="🏆 /leaderboard", value="Top predictors by accuracy", inline=False)
        embed.set_footer(text="Tip: /analyze is the easiest — just screenshot and upload!")
        await interaction.response.send_message(embed=embed)


# ============ STARTUP ============
def run_fastapi():
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


async def run_discord(predictor: BloxflipPredictor):
    bot = DiscordBot(predictor)
    async with bot:
        await bot.start(DISCORD_TOKEN)


def main():
    global predictor_instance
    predictor_instance = BloxflipPredictor()

    fastapi_thread = threading.Thread(target=run_fastapi, daemon=True)
    fastapi_thread.start()
    print(f"🌐 Web UI running on http://{HOST}:{PORT}")

    asyncio.run(run_discord(predictor_instance))


if __name__ == "__main__":
    main()
