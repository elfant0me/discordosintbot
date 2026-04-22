import discord
from discord.ext import commands
import asyncio
import os
import sys
from datetime import datetime
from config import BOT_TOKEN, BOT_PREFIX, BOT_DESCRIPTION, MESSAGES, COLORS
import logging
logging.basicConfig(level=logging.INFO)

# Configuration des intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Désactiver la commande help par défaut
bot = commands.Bot(
    command_prefix=BOT_PREFIX,
    help_command=None,    # ⚠️ Ligne cruciale
    intents=discord.Intents.all()
)

@bot.event
async def on_ready():
    """Événement déclenché quand le bot est prêt"""
    bot.start_time = datetime.now()
    print(f'{bot.user} est connecté!')
    print(f'Démarrage à: {bot.start_time.strftime("%d/%m/%Y à %H:%M:%S")}')
    
    # Définir le jeu personnalisé
    game = discord.Game("RaspberryPI | -help")
    await bot.change_presence(activity=game)
    print("🎮 Statut défini: RaspberryPI")

@bot.event
async def on_disconnect():
    """Événement de déconnexion"""
    print("🔌 Déconnecté de Discord.")

@bot.event
async def on_close():
    """Événement de fermeture"""
    print("🛑 Fermeture du bot...")

async def load_extensions():
    """Charge toutes les extensions du bot"""
    extensions = [
        'cogs.admin',             # Fichier: cogs/admin.py
        'cogs.monitoring',        # Fichier: cogs/monitoring.py
        'cogs.osint',             # Fichier: cogs/osint.py
    ]
    
    print("📦 Chargement des extensions...")
    for extension in extensions:
        try:
            await bot.load_extension(extension)
            print(f'✅ Extension {extension} chargée')
        except Exception as e:
            print(f'❌ Erreur lors du chargement de {extension}: {e}')
    
    print(f"📦 {len(bot.cogs)} extensions chargées au total")

async def main():
    """Fonction principale avec gestion des erreurs"""
    try:
        print("🚀 Démarrage du bot...")
        
        # Charger les extensions avant de démarrer
        await load_extensions()
        
        # Démarrer le bot avec gestion automatique des ressources
        async with bot:
            await bot.start(BOT_TOKEN)
            
    except KeyboardInterrupt:
        print("\n⏹️ Ctrl+C détecté, arrêt du bot...")
    except discord.LoginFailure:
        print("❌ Erreur de connexion: Token invalide")
    except discord.HTTPException as e:
        print(f"❌ Erreur HTTP Discord: {e}")
    except Exception as e:
        print(f"❌ Erreur inattendue : {e}")
    finally:
        # S'assurer que le bot est fermé
        if not bot.is_closed():
            await bot.close()
        
        # Attendre un peu pour que toutes les tâches se terminent
        await asyncio.sleep(1)
        
        # Nettoyer les tâches restantes
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        if tasks:
            print(f"🧹 Nettoyage de {len(tasks)} tâches restantes...")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == '__main__':
    try:
        # Utiliser la politique d'événements appropriée pour Windows
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
        asyncio.run(main())
    except KeyboardInterrupt:
        pass  # Ignore l'interruption clavier
    except Exception as e:
        print(f"💥 Erreur fatale : {e}")
    finally:
        print("👋 Programme terminé")
