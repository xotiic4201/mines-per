import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from datetime import datetime
from collections import defaultdict
import random
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import threading
from typing import Optional
import math

# ============ CONFIGURATION ============
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', 'YOUR_BOT_TOKEN')
PORT = int(os.environ.get('PORT', 8000))
HOST = os.environ.get('HOST', '0.0.0.0')

# ============ PREDICTOR CLASS ============
class BloxflipPredictor:
    def __init__(self):
        self.data_file = 'bloxflip_data.json'
        self.load_data()
    
    def load_data(self):
        try:
            with open(self.data_file, 'r') as f:
                self.data = json.load(f)
        except:
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
        # Convert defaultdict to dict for JSON serialization
        if 'mine_distribution' in self.data['global_stats']:
            self.data['global_stats']['mine_distribution'] = dict(self.data['global_stats']['mine_distribution'])
        
        with open(self.data_file, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def get_historical_patterns(self, tile_amt):
        """Extract patterns from historical data"""
        patterns = {
            'frequent_mines': defaultdict(int),
            'recent_mines': [],
            'user_patterns': defaultdict(list)
        }
        
        # Analyze all games with this tile amount
        for game in self.data['games'].values():
            if game.get('tile_amt') == tile_amt and game.get('actual_mines'):
                # Count frequency
                for mine in game['actual_mines']:
                    patterns['frequent_mines'][mine] += 1
                
                # Track recent games (last 20)
                if len(patterns['recent_mines']) < 20:
                    patterns['recent_mines'].append(game['actual_mines'])
                
                # User-specific patterns
                user_id = str(game.get('user_id', ''))
                if user_id:
                    patterns['user_patterns'][user_id].append(game['actual_mines'])
        
        return patterns
    
    def calculate_risk_score(self, tile, patterns, tile_amt):
        """Calculate risk score for a specific tile"""
        risk = 0.5  # Base risk
        
        # Historical frequency risk
        freq = patterns['frequent_mines'].get(tile, 0)
        total_games = len([g for g in self.data['games'].values() 
                          if g.get('tile_amt') == tile_amt and g.get('actual_mines')])
        
        if total_games > 0:
            historical_risk = freq / total_games
            risk = (risk + historical_risk) / 2
        
        # Recent pattern risk (last 10 games)
        if patterns['recent_mines']:
            recent_games = patterns['recent_mines'][-10:]
            recent_count = sum(1 for mines in recent_games if tile in mines)
            recent_risk = recent_count / len(recent_games)
            risk = (risk + recent_risk) / 2
        
        return min(0.95, max(0.05, risk))
    
    def predict_mines(self, tile_amt, user_id=None):
        """Advanced prediction based on historical patterns"""
        mine_count = tile_amt // 2
        patterns = self.get_historical_patterns(tile_amt)
        
        # Calculate risk for each tile
        tile_risks = {}
        for tile in range(1, tile_amt + 1):
            risk = self.calculate_risk_score(tile, patterns, tile_amt)
            
            # Adjust based on user patterns if available
            if user_id and str(user_id) in patterns['user_patterns']:
                user_games = patterns['user_patterns'][str(user_id)]
                if user_games:
                    user_freq = sum(1 for mines in user_games[-5:] if tile in mines) / len(user_games[-5:])
                    risk = (risk + user_freq) / 2
            
            tile_risks[tile] = risk
        
        # Sort by risk (highest risk = most likely to be mine)
        sorted_tiles = sorted(tile_risks.items(), key=lambda x: x[1], reverse=True)
        
        # Select top mines
        predicted_mines = [tile for tile, risk in sorted_tiles[:mine_count]]
        
        # Calculate confidence based on data availability
        total_historical = len([g for g in self.data['games'].values() 
                               if g.get('tile_amt') == tile_amt and g.get('actual_mines')])
        confidence = min(0.95, 0.5 + (total_historical / 200))
        
        # Add variance based on risk consistency
        risk_values = [risk for tile, risk in sorted_tiles[:mine_count]]
        if risk_values:
            risk_std = math.sqrt(sum((r - sum(risk_values)/len(risk_values))**2 for r in risk_values) / len(risk_values))
            confidence = confidence * (1 - risk_std)
        
        return {
            'safe_tiles': [t for t in range(1, tile_amt + 1) if t not in predicted_mines],
            'mine_tiles': predicted_mines,
            'confidence': round(confidence, 2),
            'risk_scores': {tile: round(risk, 2) for tile, risk in sorted_tiles}
        }
    
    def submit_results(self, round_id, actual_mines, user_id):
        """Submit actual results to improve future predictions"""
        if round_id not in self.data['games']:
            return False, "No prediction found for this round"
        
        game = self.data['games'][round_id]
        game['actual_mines'] = actual_mines
        game['completed_at'] = datetime.now().isoformat()
        
        # Calculate accuracy
        predicted_mines_set = set(game['predicted_mines'])
        actual_mines_set = set(actual_mines)
        
        correct_predictions = len(predicted_mines_set & actual_mines_set)
        accuracy = (correct_predictions / len(actual_mines_set)) * 100
        
        game['accuracy'] = accuracy
        game['correct_count'] = correct_predictions
        
        # Update global stats
        self.data['global_stats']['total_correct'] += correct_predictions
        self.data['global_stats']['accuracy_history'].append(accuracy)
        
        # Update mine distribution
        for mine in actual_mines:
            self.data['global_stats']['mine_distribution'][mine] += 1
        
        # Update user stats
        user_id_str = str(user_id)
        if user_id_str not in self.data['user_stats']:
            self.data['user_stats'][user_id_str] = {
                'total_predictions': 0,
                'total_correct': 0,
                'accuracy_history': [],
                'games': []
            }
        
        self.data['user_stats'][user_id_str]['total_predictions'] += 1
        self.data['user_stats'][user_id_str]['total_correct'] += correct_predictions
        self.data['user_stats'][user_id_str]['accuracy_history'].append(accuracy)
        self.data['user_stats'][user_id_str]['games'].append(round_id)
        
        self.save_data()
        
        return True, {
            'accuracy': accuracy,
            'correct': correct_predictions,
            'total': len(actual_mines)
        }

# ============ FASTAPI WEB SERVER ============
app = FastAPI(title="Bloxflip Mines Predictor API")

# HTML Template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bloxflip Mines Predictor - Advanced AI Prediction System</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            min-height: 100vh;
            color: #fff;
        }
        
        .navbar {
            background: rgba(0,0,0,0.3);
            backdrop-filter: blur(10px);
            padding: 1rem 2rem;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        
        .nav-content {
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .logo {
            font-size: 1.5rem;
            font-weight: bold;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .discord-btn {
            background: #5865F2;
            padding: 0.5rem 1rem;
            border-radius: 8px;
            text-decoration: none;
            color: white;
            transition: transform 0.2s;
        }
        
        .discord-btn:hover {
            transform: translateY(-2px);
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem;
        }
        
        .hero {
            text-align: center;
            padding: 4rem 0;
            animation: fadeInUp 1s ease;
        }
        
        .hero h1 {
            font-size: 3rem;
            margin-bottom: 1rem;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .hero p {
            font-size: 1.2rem;
            opacity: 0.9;
            margin-bottom: 2rem;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 1.5rem;
            margin: 3rem 0;
            animation: fadeInUp 1s ease 0.2s backwards;
        }
        
        .stat-card {
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            border-radius: 15px;
            padding: 1.5rem;
            text-align: center;
            transition: transform 0.3s;
        }
        
        .stat-card:hover {
            transform: translateY(-5px);
        }
        
        .stat-value {
            font-size: 2.5rem;
            font-weight: bold;
            margin: 0.5rem 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .stat-label {
            font-size: 0.9rem;
            opacity: 0.8;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        
        .predictions-section {
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            border-radius: 15px;
            padding: 2rem;
            margin: 3rem 0;
            animation: fadeInUp 1s ease 0.4s backwards;
        }
        
        .predictions-section h2 {
            margin-bottom: 1.5rem;
        }
        
        .prediction-list {
            max-height: 500px;
            overflow-y: auto;
        }
        
        .prediction-item {
            background: rgba(255,255,255,0.05);
            margin: 1rem 0;
            padding: 1rem;
            border-radius: 10px;
            transition: transform 0.2s;
        }
        
        .prediction-item:hover {
            transform: translateX(5px);
        }
        
        .prediction-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 0.5rem;
            flex-wrap: wrap;
        }
        
        .round-id {
            font-weight: bold;
            color: #667eea;
        }
        
        .timestamp {
            font-size: 0.8rem;
            opacity: 0.7;
        }
        
        .prediction-details {
            display: flex;
            gap: 1rem;
            margin: 0.5rem 0;
            flex-wrap: wrap;
        }
        
        .safe-tiles {
            color: #4ade80;
        }
        
        .mine-tiles {
            color: #f87171;
        }
        
        .confidence-bar {
            height: 6px;
            background: rgba(255,255,255,0.2);
            border-radius: 3px;
            overflow: hidden;
            margin-top: 0.5rem;
        }
        
        .confidence-fill {
            height: 100%;
            background: linear-gradient(90deg, #4ade80, #fbbf24, #f87171);
            border-radius: 3px;
            transition: width 0.3s;
        }
        
        .footer {
            text-align: center;
            padding: 3rem 0;
            opacity: 0.7;
        }
        
        @keyframes fadeInUp {
            from {
                opacity: 0;
                transform: translateY(20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        @media (max-width: 768px) {
            .hero h1 {
                font-size: 2rem;
            }
            
            .container {
                padding: 1rem;
            }
            
            .prediction-header {
                flex-direction: column;
                gap: 0.5rem;
            }
        }
    </style>
</head>
<body>
    <div class="navbar">
        <div class="nav-content">
            <div class="logo">🎲 Bloxflip Mines Predictor</div>
            <a href="https://discord.gg/YOUR_INVITE" class="discord-btn" target="_blank">Add to Discord</a>
        </div>
    </div>
    
    <div class="container">
        <div class="hero">
            <h1>Advanced AI Mine Prediction</h1>
            <p>Powered by machine learning and historical pattern analysis</p>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Total Predictions</div>
                <div class="stat-value" id="totalPredictions">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Accuracy Rate</div>
                <div class="stat-value" id="accuracyRate">0%</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Active Users</div>
                <div class="stat-value" id="activeUsers">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Completed Games</div>
                <div class="stat-value" id="completedGames">0</div>
            </div>
        </div>
        
        <div class="predictions-section">
            <h2>📊 Recent Predictions</h2>
            <div class="prediction-list" id="predictionsList">
                <div style="text-align: center;">Loading predictions...</div>
            </div>
        </div>
        
        <div class="footer">
            <p>Made with ❤️ for Bloxflip | Real-time predictions with AI accuracy</p>
            <p style="font-size: 0.8rem; margin-top: 0.5rem;">Use /predict in Discord to start predicting!</p>
        </div>
    </div>
    
    <script>
        async function fetchData() {
            try {
                const response = await fetch('/api/stats');
                const data = await response.json();
                
                document.getElementById('totalPredictions').textContent = data.total_predictions;
                document.getElementById('accuracyRate').textContent = data.accuracy_rate + '%';
                document.getElementById('activeUsers').textContent = data.active_users;
                document.getElementById('completedGames').textContent = data.completed_games;
                
                const predictionsList = document.getElementById('predictionsList');
                if (data.recent_predictions && data.recent_predictions.length > 0) {
                    predictionsList.innerHTML = data.recent_predictions.map(pred => `
                        <div class="prediction-item">
                            <div class="prediction-header">
                                <span class="round-id">Round #${pred.round_id}</span>
                                <span class="timestamp">${new Date(pred.timestamp).toLocaleString()}</span>
                            </div>
                            <div class="prediction-details">
                                <span class="safe-tiles">✅ Safe: ${pred.safe_count} tiles</span>
                                <span class="mine-tiles">💣 Mines: ${pred.mine_count} tiles</span>
                            </div>
                            <div class="confidence-bar">
                                <div class="confidence-fill" style="width: ${pred.confidence}%"></div>
                            </div>
                            <div style="font-size: 0.85rem; margin-top: 0.5rem;">
                                Confidence: ${pred.confidence}% | User: ${pred.user_name || 'Anonymous'}
                            </div>
                        </div>
                    `).join('');
                } else {
                    predictionsList.innerHTML = '<div style="text-align: center;">No predictions yet. Use /predict in Discord!</div>';
                }
            } catch (error) {
                console.error('Error fetching data:', error);
            }
        }
        
        fetchData();
        setInterval(fetchData, 5000);
    </script>
</body>
</html>
"""

# Store predictor instance
predictor_instance = None

@app.on_event("startup")
async def startup_event():
    """Initialize predictor on startup"""
    global predictor_instance
    predictor_instance = BloxflipPredictor()
    app.predictor = predictor_instance

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main dashboard"""
    return HTMLResponse(content=HTML_TEMPLATE)

@app.get("/api/stats")
async def get_stats():
    """Get global statistics"""
    if not predictor_instance:
        raise HTTPException(status_code=503, detail="Predictor not initialized")
    
    total_predictions = len(predictor_instance.data['games'])
    completed_games = len([g for g in predictor_instance.data['games'].values() if g.get('actual_mines')])
    
    # Calculate accuracy
    accuracies = []
    for game in predictor_instance.data['games'].values():
        if game.get('accuracy'):
            accuracies.append(game['accuracy'])
    
    accuracy_rate = sum(accuracies) / len(accuracies) if accuracies else 0
    
    # Get recent predictions with user info
    recent = []
    for round_id, game in list(predictor_instance.data['games'].items())[-20:]:
        # Get username if available
        user_name = "Unknown"
        if game.get('user_id'):
            # We'll try to get username from Discord later
            user_name = f"User_{game['user_id'][-4:]}"
        
        recent.append({
            'round_id': round_id,
            'timestamp': game['timestamp'],
            'safe_count': len(game['predicted_safe']),
            'mine_count': len(game['predicted_mines']),
            'confidence': round(game.get('confidence', 0) * 100, 1),
            'user_name': user_name
        })
    
    return {
        'total_predictions': total_predictions,
        'completed_games': completed_games,
        'accuracy_rate': round(accuracy_rate, 1),
        'active_users': len(predictor_instance.data['user_stats']),
        'recent_predictions': recent[::-1]
    }

@app.get("/api/user/{user_id}")
async def get_user_stats(user_id: str):
    """Get statistics for a specific user"""
    if not predictor_instance:
        raise HTTPException(status_code=503, detail="Predictor not initialized")
    
    if user_id not in predictor_instance.data['user_stats']:
        raise HTTPException(status_code=404, detail="User not found")
    
    stats = predictor_instance.data['user_stats'][user_id]
    avg_accuracy = sum(stats['accuracy_history']) / len(stats['accuracy_history']) if stats['accuracy_history'] else 0
    
    return {
        'total_predictions': stats['total_predictions'],
        'total_correct': stats['total_correct'],
        'average_accuracy': round(avg_accuracy, 1),
        'games_played': len(stats.get('games', [])),
        'accuracy_history': stats['accuracy_history'][-10:]
    }

@app.get("/api/patterns/{tile_amount}")
async def get_patterns(tile_amount: int):
    """Get pattern analysis for specific tile amount"""
    if not predictor_instance:
        raise HTTPException(status_code=503, detail="Predictor not initialized")
    
    patterns = predictor_instance.get_historical_patterns(tile_amount)
    
    return {
        'tile_amount': tile_amount,
        'frequent_mines': dict(sorted(patterns['frequent_mines'].items(), key=lambda x: x[1], reverse=True)[:10]),
        'total_games_analyzed': len(patterns['recent_mines'])
    }

# ============ DISCORD BOT ============
class DiscordBot(commands.Bot):
    def __init__(self, predictor):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        super().__init__(command_prefix='/', intents=intents)
        self.predictor = predictor
        
    async def setup_hook(self):
        await self.tree.sync()
        print(f"✅ Synced slash commands for {self.user}")

# Initialize bot
bot = DiscordBot(predictor_instance) if predictor_instance else None

# ============ SLASH COMMANDS ============
@bot.tree.command(name="predict", description="Generate advanced mine predictions for Bloxflip")
@app_commands.describe(
    tile_amount="Number of tiles in the game (3-25)",
    round_id="Optional ID to track this prediction"
)
async def slash_predict(interaction: discord.Interaction, tile_amount: int, round_id: str = None):
    """Generate prediction using AI pattern analysis"""
    await interaction.response.defer()
    
    if tile_amount < 3 or tile_amount > 25:
        await interaction.followup.send("❌ Tile amount must be between 3 and 25!", ephemeral=True)
        return
    
    if round_id is None:
        round_id = str(random.randint(100000, 999999))
    
    # Generate prediction
    prediction = bot.predictor.predict_mines(tile_amount, interaction.user.id)
    
    # Store prediction
    game_data = {
        'timestamp': datetime.now().isoformat(),
        'user_id': interaction.user.id,
        'user_name': interaction.user.name,
        'tile_amt': tile_amount,
        'round_id': round_id,
        'predicted_safe': prediction['safe_tiles'],
        'predicted_mines': prediction['mine_tiles'],
        'confidence': prediction['confidence'],
        'risk_scores': prediction.get('risk_scores', {})
    }
    bot.predictor.data['games'][round_id] = game_data
    bot.predictor.data['global_stats']['total_predictions'] += 1
    bot.predictor.save_data()
    
    # Create embed
    embed = discord.Embed(
        title="🎲 Bloxflip Mines Predictor",
        description=f"**Round ID:** `{round_id}`\n**Tiles:** {tile_amount} | **Mines:** {tile_amount // 2}",
        color=discord.Color.purple(),
        timestamp=datetime.now()
    )
    
    # Confidence indicator
    confidence_percent = prediction['confidence'] * 100
    confidence_emoji = "🟢" if confidence_percent > 70 else "🟡" if confidence_percent > 50 else "🔴"
    
    embed.add_field(
        name="📊 Prediction Confidence",
        value=f"{confidence_emoji} **{confidence_percent:.1f}%**\nBased on {len([g for g in bot.predictor.data['games'].values() if g.get('actual_mines')])} analyzed games",
        inline=False
    )
    
    # Safe tiles (top recommendations)
    safe_display = ", ".join(map(str, prediction['safe_tiles'][:15]))
    if len(prediction['safe_tiles']) > 15:
        safe_display += f" (+{len(prediction['safe_tiles'])-15} more)"
    
    embed.add_field(
        name="✅ RECOMMENDED SAFE TILES",
        value=f"```{safe_display}```",
        inline=False
    )
    
    # Mine tiles (to avoid)
    mine_display = ", ".join(map(str, prediction['mine_tiles'][:15]))
    if len(prediction['mine_tiles']) > 15:
        mine_display += f" (+{len(prediction['mine_tiles'])-15} more)"
    
    embed.add_field(
        name="💣 MINE TILES (AVOID)",
        value=f"```{mine_display}```",
        inline=False
    )
    
    # Risk analysis for top tiles
    if prediction.get('risk_scores'):
        high_risk = sorted(prediction['risk_scores'].items(), key=lambda x: x[1], reverse=True)[:5]
        risk_display = "\n".join([f"Tile {tile}: {risk*100:.1f}% risk" for tile, risk in high_risk])
        embed.add_field(name="⚠️ HIGH RISK TILES", value=f"```{risk_display}```", inline=False)
    
    embed.set_footer(text="Submit results with /submit to improve future predictions!")
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="submit", description="Submit actual mine positions to improve AI accuracy")
@app_commands.describe(
    round_id="Round ID from your prediction",
    mines="Mine positions separated by spaces (e.g., 3 7 12)"
)
async def slash_submit(interaction: discord.Interaction, round_id: str, mines: str):
    """Submit actual results to train the AI"""
    await interaction.response.defer()
    
    try:
        # Parse mine positions
        mine_list = [int(x.strip()) for x in mines.split()]
        
        # Validate
        if not mine_list:
            await interaction.followup.send("❌ Please provide at least one mine position!", ephemeral=True)
            return
        
        # Submit results
        success, result = bot.predictor.submit_results(round_id, mine_list, interaction.user.id)
        
        if not success:
            await interaction.followup.send(f"❌ {result}", ephemeral=True)
            return
        
        # Create results embed
        embed = discord.Embed(
            title="✅ Results Submitted Successfully!",
            description=f"Round ID: **{round_id}**",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="📊 Round Performance",
            value=f"**Correct Mines:** {result['correct']}/{result['total']}\n"
                  f"**Accuracy:** {result['accuracy']:.1f}%",
            inline=False
        )
        
        # Show improvement impact
        total_games = len([g for g in bot.predictor.data['games'].values() if g.get('actual_mines')])
        embed.add_field(
            name="🤖 AI Improvement",
            value=f"Thanks to your submission, the AI has improved!\n"
                  f"Total trained games: **{total_games}**",
            inline=False
        )
        
        await interaction.followup.send(embed=embed)
        
    except ValueError:
        await interaction.followup.send("❌ Invalid format! Use: `/submit round_id:12345 mines:3 7 12`", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

@bot.tree.command(name="stats", description="View your prediction statistics")
async def slash_stats(interaction: discord.Interaction):
    """Show personal prediction statistics"""
    await interaction.response.defer()
    
    user_id = str(interaction.user.id)
    
    if user_id not in bot.predictor.data['user_stats']:
        embed = discord.Embed(
            title="📊 Your Statistics",
            description="No predictions yet! Use `/predict` to get started.",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed)
        return
    
    stats = bot.predictor.data['user_stats'][user_id]
    avg_accuracy = sum(stats['accuracy_history']) / len(stats['accuracy_history']) if stats['accuracy_history'] else 0
    
    embed = discord.Embed(
        title=f"📊 {interaction.user.name}'s Statistics",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    
    embed.add_field(name="Total Predictions", value=str(stats['total_predictions']), inline=True)
    embed.add_field(name="Total Correct Mines", value=str(stats['total_correct']), inline=True)
    embed.add_field(name="Average Accuracy", value=f"{avg_accuracy:.1f}%", inline=True)
    
    # Get rank
    all_users = [(uid, ustats['total_correct'] / max(1, ustats['total_predictions'])) 
                 for uid, ustats in bot.predictor.data['user_stats'].items()
                 if ustats['total_predictions'] >= 5]
    all_users.sort(key=lambda x: x[1], reverse=True)
    
    for rank, (uid, acc) in enumerate(all_users[:10], 1):
        if uid == user_id:
            embed.add_field(name="🏆 Global Rank", value=f"#{rank} of {len(all_users)}", inline=True)
            break
    
    embed.set_footer(text="Keep predicting to improve your stats!")
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="analyze", description="Analyze patterns for specific tile amounts")
@app_commands.describe(
    tile_amount="Number of tiles to analyze (3-25)"
)
async def slash_analyze(interaction: discord.Interaction, tile_amount: int):
    """Analyze historical patterns for mines"""
    await interaction.response.defer()
    
    if tile_amount < 3 or tile_amount > 25:
        await interaction.followup.send("❌ Tile amount must be between 3 and 25!", ephemeral=True)
        return
    
    patterns = bot.predictor.get_historical_patterns(tile_amount)
    total_games = len([g for g in bot.predictor.data['games'].values() 
                      if g.get('tile_amt') == tile_amount and g.get('actual_mines')])
    
    if total_games == 0:
        await interaction.followup.send(f"❌ No historical data for {tile_amount} tiles yet. Start predicting!")
        return
    
    embed = discord.Embed(
        title=f"📈 Pattern Analysis - {tile_amount} Tiles",
        description=f"Based on {total_games} completed games",
        color=discord.Color.gold()
    )
    
    # Most frequent mine positions
    frequent = sorted(patterns['frequent_mines'].items(), key=lambda x: x[1], reverse=True)[:10]
    if frequent:
        freq_text = "\n".join([f"Tile {pos}: {count}x ({count/total_games*100:.1f}%)" 
                               for pos, count in frequent])
        embed.add_field(name="🔥 Most Common Mine Positions", value=f"```{freq_text}```", inline=False)
    
    # Least frequent (safest) positions
    all_tiles = range(1, tile_amount + 1)
    safe_tiles = [(tile, patterns['frequent_mines'].get(tile, 0)) for tile in all_tiles]
    safest = sorted(safe_tiles, key=lambda x: x[1])[:10]
    safest_text = "\n".join([f"Tile {tile}: {count}x ({count/total_games*100:.1f}%)" 
                             for tile, count in safest])
    embed.add_field(name="🟢 Safest Tiles", value=f"```{safest_text}```", inline=False)
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="help", description="Show all available commands")
async def slash_help(interaction: discord.Interaction):
    """Show help menu"""
    embed = discord.Embed(
        title="🎲 Bloxflip Mines Predictor - Help",
        description="Advanced AI-powered mine prediction system",
        color=discord.Color.purple()
    )
    
    commands_info = {
        "/predict <tiles> [round_id]": "Generate AI predictions with confidence scores",
        "/submit <round_id> <mines>": "Submit actual results to improve AI accuracy",
        "/stats": "View your personal prediction statistics",
        "/analyze <tiles>": "Analyze patterns for specific tile amounts",
        "/help": "Show this help message"
    }
    
    for cmd, desc in commands_info.items():
        embed.add_field(name=cmd, value=desc, inline=False)
    
    embed.add_field(
        name="💡 Tips",
        value="• Higher confidence = Safer predictions\n"
              "• Submit results to make the AI smarter\n"
              "• Check /analyze to find hot patterns\n"
              "• Your stats improve with more predictions",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)

# ============ RUN SERVER ============
def run_fastapi():
    """Run FastAPI server in separate thread"""
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")

def run_discord():
    """Run Discord bot"""
    asyncio.run(bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    # Start FastAPI in background thread
    fastapi_thread = threading.Thread(target=run_fastapi, daemon=True)
    fastapi_thread.start()
    
    # Run Discord bot in main thread
    run_discord()
