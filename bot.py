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
import uvicorn
import threading
import math
from typing import Optional

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
        if 'mine_distribution' in self.data['global_stats']:
            self.data['global_stats']['mine_distribution'] = dict(self.data['global_stats']['mine_distribution'])
        
        with open(self.data_file, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def get_historical_patterns(self, tile_amt):
        patterns = {
            'frequent_mines': defaultdict(int),
            'recent_mines': []
        }
        
        for game in self.data['games'].values():
            if game.get('tile_amt') == tile_amt and game.get('actual_mines'):
                for mine in game['actual_mines']:
                    patterns['frequent_mines'][mine] += 1
                
                if len(patterns['recent_mines']) < 20:
                    patterns['recent_mines'].append(game['actual_mines'])
        
        return patterns
    
    def predict_mines(self, tile_amt, user_id=None):
        mine_count = tile_amt // 2
        patterns = self.get_historical_patterns(tile_amt)
        
        # Calculate risk for each tile
        tile_risks = {}
        total_games = len([g for g in self.data['games'].values() 
                          if g.get('tile_amt') == tile_amt and g.get('actual_mines')])
        
        for tile in range(1, tile_amt + 1):
            risk = 0.5
            
            if total_games > 0:
                freq = patterns['frequent_mines'].get(tile, 0)
                historical_risk = freq / total_games
                risk = (risk + historical_risk) / 2
            
            if patterns['recent_mines']:
                recent_games = patterns['recent_mines'][-10:]
                recent_count = sum(1 for mines in recent_games if tile in mines)
                recent_risk = recent_count / len(recent_games)
                risk = (risk + recent_risk) / 2
            
            tile_risks[tile] = min(0.95, max(0.05, risk))
        
        sorted_tiles = sorted(tile_risks.items(), key=lambda x: x[1], reverse=True)
        predicted_mines = [tile for tile, risk in sorted_tiles[:mine_count]]
        
        confidence = min(0.95, 0.5 + (total_games / 200))
        
        return {
            'safe_tiles': [t for t in range(1, tile_amt + 1) if t not in predicted_mines],
            'mine_tiles': predicted_mines,
            'confidence': round(confidence, 2),
            'risk_scores': {tile: round(risk, 2) for tile, risk in sorted_tiles[:10]}
        }
    
    def submit_results(self, round_id, actual_mines, user_id):
        if round_id not in self.data['games']:
            return False, "No prediction found for this round"
        
        game = self.data['games'][round_id]
        game['actual_mines'] = actual_mines
        game['completed_at'] = datetime.now().isoformat()
        
        predicted_mines_set = set(game['predicted_mines'])
        actual_mines_set = set(actual_mines)
        
        correct_predictions = len(predicted_mines_set & actual_mines_set)
        accuracy = (correct_predictions / len(actual_mines_set)) * 100
        
        game['accuracy'] = accuracy
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
                'accuracy_history': []
            }
        
        self.data['user_stats'][user_id_str]['total_predictions'] += 1
        self.data['user_stats'][user_id_str]['total_correct'] += correct_predictions
        self.data['user_stats'][user_id_str]['accuracy_history'].append(accuracy)
        
        self.save_data()
        
        return True, {
            'accuracy': accuracy,
            'correct': correct_predictions,
            'total': len(actual_mines)
        }

# ============ FASTAPI WEB SERVER ============
app = FastAPI(title="Bloxflip Mines Predictor API")
predictor_instance = None

# HTML Template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bloxflip Mines Predictor</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            color: #fff;
        }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .header { text-align: center; padding: 40px 0; }
        .header h1 { font-size: 3em; margin-bottom: 10px; }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin: 40px 0;
        }
        .stat-card {
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            border-radius: 15px;
            padding: 20px;
            text-align: center;
        }
        .stat-value { font-size: 2.5em; font-weight: bold; margin: 10px 0; }
        .predictions-section {
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            border-radius: 15px;
            padding: 30px;
            margin: 40px 0;
        }
        .prediction-item {
            background: rgba(255,255,255,0.05);
            margin: 10px 0;
            padding: 15px;
            border-radius: 10px;
        }
        .footer { text-align: center; padding: 40px 0; opacity: 0.7; }
        @media (max-width: 768px) {
            .header h1 { font-size: 2em; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎲 Bloxflip Mines Predictor</h1>
            <p>AI-powered mine prediction system</p>
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
        </div>
        
        <div class="predictions-section">
            <h2>📊 Recent Predictions</h2>
            <div id="predictionsList">Loading...</div>
        </div>
        
        <div class="footer">
            <p>Use /predict in Discord to start!</p>
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
                
                if (data.recent_predictions && data.recent_predictions.length > 0) {
                    document.getElementById('predictionsList').innerHTML = data.recent_predictions.map(pred => `
                        <div class="prediction-item">
                            <strong>Round #${pred.round_id}</strong><br>
                            Safe: ${pred.safe_count} tiles | Mines: ${pred.mine_count} tiles<br>
                            Confidence: ${pred.confidence}%
                        </div>
                    `).join('');
                }
            } catch (error) {
                console.error('Error:', error);
            }
        }
        fetchData();
        setInterval(fetchData, 5000);
    </script>
</body>
</html>
"""

@app.get("/")
async def root():
    return HTMLResponse(content=HTML_TEMPLATE)

@app.get("/api/stats")
async def get_stats():
    if not predictor_instance:
        return JSONResponse(content={'error': 'Not ready'}, status_code=503)
    
    total_predictions = len(predictor_instance.data['games'])
    completed_games = len([g for g in predictor_instance.data['games'].values() if g.get('actual_mines')])
    
    accuracies = []
    for game in predictor_instance.data['games'].values():
        if game.get('accuracy'):
            accuracies.append(game['accuracy'])
    
    accuracy_rate = sum(accuracies) / len(accuracies) if accuracies else 0
    
    recent = []
    for round_id, game in list(predictor_instance.data['games'].items())[-10:]:
        recent.append({
            'round_id': round_id,
            'timestamp': game['timestamp'],
            'safe_count': len(game['predicted_safe']),
            'mine_count': len(game['predicted_mines']),
            'confidence': round(game.get('confidence', 0) * 100, 1)
        })
    
    return {
        'total_predictions': total_predictions,
        'completed_games': completed_games,
        'accuracy_rate': round(accuracy_rate, 1),
        'active_users': len(predictor_instance.data['user_stats']),
        'recent_predictions': recent[::-1]
    }

# ============ DISCORD BOT ============
class DiscordBot(commands.Bot):
    def __init__(self, predictor):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='/', intents=intents)
        self.predictor = predictor
        
    async def setup_hook(self):
        await self.tree.sync()
        print(f"✅ Synced slash commands")

# Create bot instance after predictor is initialized
bot = None

# ============ SLASH COMMANDS ============
# These will be registered after bot is created

async def setup_commands():
    global bot
    
    @bot.tree.command(name="predict", description="Generate mine predictions for Bloxflip")
    @app_commands.describe(tile_amount="Number of tiles (3-25)", round_id="Optional round ID")
    async def slash_predict(interaction: discord.Interaction, tile_amount: int, round_id: str = None):
        await interaction.response.defer()
        
        if tile_amount < 3 or tile_amount > 25:
            await interaction.followup.send("❌ Tile amount must be between 3 and 25!", ephemeral=True)
            return
        
        if round_id is None:
            round_id = str(random.randint(100000, 999999))
        
        prediction = bot.predictor.predict_mines(tile_amount, interaction.user.id)
        
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
        bot.predictor.data['games'][round_id] = game_data
        bot.predictor.data['global_stats']['total_predictions'] += 1
        bot.predictor.save_data()
        
        confidence_percent = prediction['confidence'] * 100
        confidence_emoji = "🟢" if confidence_percent > 70 else "🟡" if confidence_percent > 50 else "🔴"
        
        embed = discord.Embed(
            title="🎲 Bloxflip Mines Predictor",
            description=f"**Round ID:** `{round_id}`\n**Tiles:** {tile_amount} | **Mines:** {tile_amount // 2}",
            color=discord.Color.purple(),
            timestamp=datetime.now()
        )
        
        embed.add_field(
            name="📊 Confidence",
            value=f"{confidence_emoji} **{confidence_percent:.1f}%**",
            inline=False
        )
        
        safe_display = ", ".join(map(str, prediction['safe_tiles'][:15]))
        if len(prediction['safe_tiles']) > 15:
            safe_display += f" (+{len(prediction['safe_tiles'])-15} more)"
        
        embed.add_field(name="✅ SAFE TILES", value=f"```{safe_display}```", inline=False)
        
        mine_display = ", ".join(map(str, prediction['mine_tiles'][:15]))
        if len(prediction['mine_tiles']) > 15:
            mine_display += f" (+{len(prediction['mine_tiles'])-15} more)"
        
        embed.add_field(name="💣 MINE TILES", value=f"```{mine_display}```", inline=False)
        
        embed.set_footer(text="Submit results with /submit to improve accuracy!")
        
        await interaction.followup.send(embed=embed)
    
    @bot.tree.command(name="submit", description="Submit actual mine positions")
    @app_commands.describe(round_id="Round ID from prediction", mines="Mine positions (e.g., 3 7 12)")
    async def slash_submit(interaction: discord.Interaction, round_id: str, mines: str):
        await interaction.response.defer()
        
        try:
            mine_list = [int(x.strip()) for x in mines.split()]
            
            if not mine_list:
                await interaction.followup.send("❌ Please provide mine positions!", ephemeral=True)
                return
            
            success, result = bot.predictor.submit_results(round_id, mine_list, interaction.user.id)
            
            if not success:
                await interaction.followup.send(f"❌ {result}", ephemeral=True)
                return
            
            embed = discord.Embed(
                title="✅ Results Submitted!",
                description=f"Round ID: **{round_id}**",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="📊 Accuracy",
                value=f"**{result['correct']}/{result['total']}** correct ({result['accuracy']:.1f}%)",
                inline=False
            )
            
            await interaction.followup.send(embed=embed)
            
        except ValueError:
            await interaction.followup.send("❌ Invalid format! Use: `/submit round_id:12345 mines:3 7 12`", ephemeral=True)
    
    @bot.tree.command(name="stats", description="View your statistics")
    async def slash_stats(interaction: discord.Interaction):
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
            title=f"📊 {interaction.user.name}'s Stats",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="Total Predictions", value=str(stats['total_predictions']), inline=True)
        embed.add_field(name="Total Correct", value=str(stats['total_correct']), inline=True)
        embed.add_field(name="Avg Accuracy", value=f"{avg_accuracy:.1f}%", inline=True)
        
        await interaction.followup.send(embed=embed)
    
    @bot.tree.command(name="help", description="Show all commands")
    async def slash_help(interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎲 Bloxflip Mines Predictor",
            description="AI-powered mine prediction bot",
            color=discord.Color.purple()
        )
        
        embed.add_field(name="/predict <tiles> [round_id]", value="Get mine predictions", inline=False)
        embed.add_field(name="/submit <round_id> <mines>", value="Submit results to improve AI", inline=False)
        embed.add_field(name="/stats", value="View your statistics", inline=False)
        embed.add_field(name="/help", value="Show this help", inline=False)
        
        await interaction.response.send_message(embed=embed)

# ============ RUN SERVER ============
def run_fastapi():
    """Run FastAPI server"""
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")

def run_discord():
    """Run Discord bot"""
    global bot, predictor_instance
    
    # Initialize predictor
    predictor_instance = BloxflipPredictor()
    
    # Initialize bot
    bot = DiscordBot(predictor_instance)
    
    # Setup commands
    asyncio.run(setup_commands())
    
    # Run bot
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    # Start FastAPI in background thread
    fastapi_thread = threading.Thread(target=run_fastapi, daemon=True)
    fastapi_thread.start()
    
    # Run Discord bot
    run_discord()
