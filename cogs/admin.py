# cogs/admin.py

import discord
from discord.ext import commands
from discord import app_commands
from utils.permissions import require_admin
import os
import sys
from config import BOT_PREFIX, COLORS

class Admin(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    ########################
    ##  Commande -help    ##
    ########################

    def build_help_embed(self):
        embed = discord.Embed(
            title="📖 Commandes disponibles",
            color=COLORS.get('info', discord.Color.blue())
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(
            name="⚙️ Admin",
            value=(
                f"`{BOT_PREFIX}setgame <jeu>` — Change le statut du bot\n"
                f"`{BOT_PREFIX}setnick <pseudo>` — Change le pseudo du bot\n"
                f"`{BOT_PREFIX}purge <nombre>` — Supprime des messages (max 100)\n"
                f"`{BOT_PREFIX}cogs list` — Liste les modules chargés\n"
                f"`{BOT_PREFIX}cogs load <nom>` — Charge un module\n"
                f"`{BOT_PREFIX}cogs reload <nom>` — Recharge un module\n"
                f"`{BOT_PREFIX}cogs reloadall` — Recharge tous les modules\n"
                f"`{BOT_PREFIX}cogs unload <nom>` — Décharge un module\n"
                f"`{BOT_PREFIX}restart` — Redémarre le bot *(owner)*\n"
                f"`{BOT_PREFIX}shutdown` — Éteint le bot *(owner)*"
            ),
            inline=False
        )
        embed.add_field(
            name="📊 Monitoring",
            value=(
                f"`{BOT_PREFIX}help` — Envoie l'aide en message privé\n"
                f"`{BOT_PREFIX}status` — Statut complet du Raspberry Pi\n"
                f"`{BOT_PREFIX}health` — Résumé global de santé\n"
                f"`{BOT_PREFIX}temps` — Température CPU\n"
                f"`{BOT_PREFIX}disk` — Utilisation des disques\n"
                f"`{BOT_PREFIX}services` — État des services systemd\n"
                f"`{BOT_PREFIX}docker` — Conteneurs Docker actifs\n"
                f"`{BOT_PREFIX}uptime` — Uptime système\n"
                f"`{BOT_PREFIX}system` — Infos système du Raspberry Pi\n"
                f"`{BOT_PREFIX}network` — Infos réseau locales\n"
                f"`{BOT_PREFIX}top [1-10]` — Top processus CPU/RAM\n"
                f"`{BOT_PREFIX}adguard` — Stats principales de AdGuard Home\n"
                f"`{BOT_PREFIX}unbound` — Stats principales de Unbound DNS\n"
                f"`{BOT_PREFIX}homelab` — État des services homelab\n"
                f"`{BOT_PREFIX}journal <service> [lignes]` — Logs journalctl *(admin)*\n"
                f"`{BOT_PREFIX}updates` — Paquets APT à mettre à jour *(admin)*\n"
                f"`{BOT_PREFIX}ping <host>` — Ping un host ou une IP"
            ),
            inline=False
        )
        embed.add_field(
            name="🕵️ Osint",
            value=(
                f"`{BOT_PREFIX}nmap <host/ip> [scan_type]` — Scan nmap *(admin)*\n"
                f"`{BOT_PREFIX}nslookup <domaine/ip>` — Résolution DNS *(admin)*\n"
                f"`{BOT_PREFIX}whois <domaine/ip>` — Recherche whois *(admin)*\n"
                f"`{BOT_PREFIX}geoip <ip/domaine>` — Géolocalisation IP *(admin)*\n"
                f"`{BOT_PREFIX}wpscan <url>` — Scan WordPress basique *(admin)*"
            ),
            inline=False
        )
        embed.add_field(
            name="✨ Slash Commands",
            value=(
                "`/status` `/health` `/temps` `/disk` `/services`\n"
                "`/docker` `/uptime` `/system` `/network` `/top` `/adguard` `/unbound` `/homelab`\n"
                "`/journal` `/updates` `/ping`\n"
                "`/nmap` `/nslookup` `/whois` `/geoip` `/wpscan` `/help`"
            ),
            inline=False
        )
        embed.set_footer(text=f"Préfixe : {BOT_PREFIX}")
        return embed

    async def send_help_message(self, destination):
        embed = self.build_help_embed()
        await destination.author.send(embed=embed)

    @commands.command(name='help')
    async def help_command(self, ctx):
        """Affiche la liste des commandes disponibles."""
        try:
            await self.send_help_message(ctx)
        except discord.Forbidden:
            await ctx.send("❌ Impossible d'envoyer le menu en message privé. Vérifie que tes MP sont ouverts.")

    @app_commands.command(name="help", description="Affiche l'aide du bot")
    @app_commands.describe(private="Envoyer l'aide en message privé")
    async def help_slash(self, interaction: discord.Interaction, private: bool = False):
        embed = self.build_help_embed()

        if private:
            try:
                await interaction.user.send(embed=embed)
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ Impossible d'envoyer le menu en message privé. Vérifie que tes MP sont ouverts.",
                    ephemeral=True
                )
            return

        await interaction.response.send_message(embed=embed, ephemeral=True)

    ########################
    ## shutdown, restart  ##
    ########################

    @commands.command(name="shutdown")
    @commands.is_owner()
    async def shutdown(self, ctx):
        """Éteint proprement le bot."""
        await ctx.send("🛑 Extinction en cours...")
        await self.bot.close()

    @commands.command(name="restart")
    @commands.is_owner()
    async def restart(self, ctx):
        """Redémarre le bot."""
        await ctx.send("🔁 Redémarrage en cours...")
        await self.bot.close()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    ########################
    ## Commande .setgame  ##
    ########################

    @commands.command(name='setgame')
    @require_admin()
    async def set_game(self, ctx, *, game_name: str = None):
        """Change ou supprime le statut du bot."""
        if game_name:
            game = discord.Game(game_name)
            await self.bot.change_presence(activity=game)
            embed = discord.Embed(
                title="🎮 Statut modifié",
                description=f"Nouveau statut: **{game_name}**",
                color=COLORS.get('success', discord.Color.green())
            )
        else:
            await self.bot.change_presence(activity=None)
            embed = discord.Embed(
                title="🎮 Statut retiré",
                description="Le bot n'affiche plus de jeu en cours.",
                color=COLORS.get('warning', discord.Color.orange())
            )
        await ctx.send(embed=embed)

    ########################
    ## Commande .purge    ##
    ########################

    @commands.command(name='purge')
    @require_admin()
    async def purge_messages(self, ctx, amount: int = None):
        """Supprime un nombre spécifié de messages."""
        if amount is None:
            await ctx.send(f"❌ Syntaxe : `{BOT_PREFIX}purge <nombre>`")
            return
        if amount <= 0:
            await ctx.send("❌ Le nombre doit être supérieur à 0!")
            return
        if amount > 100:
            await ctx.send("❌ Maximum 100 messages à la fois!")
            return
        try:
            await ctx.channel.purge(limit=amount + 1)
        except discord.Forbidden:
            await ctx.send("❌ Permissions insuffisantes!")
        except discord.HTTPException as e:
            await ctx.send(f"❌ Erreur HTTP: {e}")

    ########################
    ## Commande .setnick  ##
    ########################

    @commands.command(name='setnick')
    @require_admin()
    async def set_nick(self, ctx, *, nickname: str = None):
        """Change le pseudo du bot sur ce serveur."""
        try:
            bot_member = ctx.guild.get_member(self.bot.user.id)
            if nickname:
                await bot_member.edit(nick=nickname)
                embed = discord.Embed(
                    title="🏷️ Pseudo modifié",
                    description=f"Nouveau pseudo: **{nickname}**",
                    color=COLORS.get('success', discord.Color.green())
                )
            else:
                await bot_member.edit(nick=None)
                embed = discord.Embed(
                    title="🏷️ Pseudo réinitialisé",
                    description=f"Nom par défaut: **{self.bot.user.name}**",
                    color=COLORS.get('warning', discord.Color.orange())
                )
            await ctx.send(embed=embed)
        except discord.Forbidden:
            await ctx.send("❌ Permissions insuffisantes!")
        except Exception as e:
            await ctx.send(f"❌ Erreur: {e}")

    ########################
    ### GESTION DES COGS ###
    ########################

    @commands.group(name="cogs", invoke_without_command=True)
    @require_admin()
    async def cogs_group(self, ctx):
        """Gère les modules (cogs)."""
        await ctx.send(
            f"📦 Utilisation :\n"
            f"`{BOT_PREFIX}cogs list` – Voir les cogs chargés\n"
            f"`{BOT_PREFIX}cogs reload <nom>` – Recharger un cog\n"
            f"`{BOT_PREFIX}cogs load <nom>` – Charger un cog\n"
            f"`{BOT_PREFIX}cogs unload <nom>` – Décharger un cog\n"
            f"`{BOT_PREFIX}cogs reloadall` – Recharger tous les cogs"
        )

    @cogs_group.command(name="list")
    async def cogs_list(self, ctx):
        loaded = list(self.bot.extensions.keys())
        if loaded:
            msg = "\n".join(f"• `{ext}`" for ext in loaded)
            await ctx.send(f"📦 Cogs chargés :\n{msg}")
        else:
            await ctx.send("⚠️ Aucun cog chargé.")

    @cogs_group.command(name="load")
    async def cogs_load(self, ctx, extension: str = None):
        if not extension:
            await ctx.send(f"❌ Syntaxe : `{BOT_PREFIX}cogs load <nom>`")
            return
        try:
            await self.bot.load_extension(f"cogs.{extension}")
            await ctx.send(f"✅ `{extension}` chargé.")
        except Exception as e:
            await ctx.send(f"❌ Erreur : `{e}`")

    @cogs_group.command(name="reload")
    async def cogs_reload(self, ctx, extension: str = None):
        if not extension:
            await ctx.send(f"❌ Syntaxe : `{BOT_PREFIX}cogs reload <nom>`")
            return
        try:
            await self.bot.reload_extension(f"cogs.{extension}")
            await ctx.send(f"♻️ `{extension}` rechargé.")
        except Exception as e:
            await ctx.send(f"❌ Erreur : `{e}`")

    @cogs_group.command(name="reloadall")
    async def cogs_reloadall(self, ctx):
        loaded = list(self.bot.extensions.keys())
        success, failed = [], []
        for ext in loaded:
            try:
                await self.bot.reload_extension(ext)
                success.append(ext)
            except Exception as e:
                failed.append(f"{ext} : {e}")
        msg = ""
        if success:
            msg += f"✅ Rechargés ({len(success)}) :\n" + "\n".join(f"• `{c}`" for c in success)
        if failed:
            msg += f"\n❌ Erreurs :\n" + "\n".join(f"• {f}" for f in failed)
        await ctx.send(msg or "⚠️ Aucun cog chargé.")

    @cogs_group.command(name="unload")
    async def cogs_unload(self, ctx, extension: str = None):
        if not extension:
            await ctx.send(f"❌ Syntaxe : `{BOT_PREFIX}cogs unload <nom>`")
            return
        try:
            await self.bot.unload_extension(f"cogs.{extension}")
            await ctx.send(f"🗑️ `{extension}` déchargé.")
        except Exception as e:
            await ctx.send(f"❌ Erreur : `{e}`")


async def setup(bot):
    await bot.add_cog(Admin(bot))

async def teardown(bot):
    await bot.remove_cog("Admin")
