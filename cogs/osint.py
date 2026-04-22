import asyncio
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from config import BOT_PREFIX, COLORS
from utils.permissions import is_admin_member, require_admin


TARGET_RE = re.compile(r"^[a-zA-Z0-9._:-]+$")
PORTS_RE = re.compile(r"^[0-9,-]{1,80}$")
NMAP_SCAN_TYPES = {
    "basic": {
        "description": "Scan basique des ports",
        "args": ["-T3", "-F"],
        "timeout": 90,
    },
    "stealth": {
        "description": "Scan SYN stealth",
        "args": ["-sS", "-T3", "-F"],
        "timeout": 120,
    },
    "version": {
        "description": "Détection de version",
        "args": ["-sV", "-T3", "-F"],
        "timeout": 120,
    },
    "os": {
        "description": "Détection d'OS",
        "args": ["-O", "-T3", "-F"],
        "timeout": 120,
    },
    "vuln": {
        "description": "Scan de vulnérabilités",
        "args": ["-sV", "--script", "vuln", "-T3"],
        "timeout": 180,
    },
}


def clean_target(target: str) -> str:
    target = target.strip()
    if not target or len(target) > 253 or not TARGET_RE.match(target):
        raise ValueError("Target invalide.")
    return target


def clean_ports(ports: str | None) -> str | None:
    if not ports:
        return None

    ports = ports.strip()
    if not PORTS_RE.match(ports):
        raise ValueError("Ports invalides. Exemple valide: `22,80,443` ou `1-1000`.")
    return ports


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL invalide.")
    return url


def truncate_text(text: str, limit: int = 3800) -> str:
    text = text.strip() or "Aucune sortie."
    if len(text) <= limit:
        return text
    return text[:limit - 80].rstrip() + "\n\n... sortie tronquée ..."


def code_block(text: str, language: str = "") -> str:
    safe_text = truncate_text(text).replace("```", "'''")
    return f"```{language}\n{safe_text}\n```"


def run_command(command: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = "\n".join(part for part in [result.stdout, result.stderr] if part)
        return result.returncode, output.strip()
    except subprocess.TimeoutExpired:
        return 124, f"Timeout après {timeout}s."
    except FileNotFoundError:
        return 127, f"Outil introuvable: {command[0]}"
    except Exception as e:
        return 1, f"Erreur: {e}"


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


async def slash_require_admin(interaction: discord.Interaction) -> bool:
    if await interaction.client.is_owner(interaction.user):
        return True
    return is_admin_member(interaction.user)


def resolve_target(target: str) -> list[str]:
    target = clean_target(target)
    try:
        infos = socket.getaddrinfo(target, None)
    except socket.gaierror as e:
        raise RuntimeError(f"Résolution impossible: {e}") from e

    ips = sorted({info[4][0] for info in infos})
    return ips


def fetch_geoip(target: str) -> dict:
    target = clean_target(target)
    fields = ",".join([
        "status",
        "message",
        "query",
        "country",
        "regionName",
        "city",
        "isp",
        "org",
        "as",
        "lat",
        "lon",
        "timezone",
        "proxy",
        "hosting",
        "mobile",
    ])
    url = f"http://ip-api.com/json/{urllib.parse.quote(target)}?fields={fields}&lang=fr"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            payload = response.read().decode(charset)
            data = json.loads(payload)
    except urllib.error.URLError as e:
        raise RuntimeError(f"GeoIP indisponible: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError("Réponse GeoIP invalide.") from e

    if data.get("status") != "success":
        raise RuntimeError(data.get("message") or "GeoIP impossible.")
    return data


class Osint(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def build_output_embed(self, title: str, output: str, color: int | None = None) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=code_block(output),
            color=color or COLORS.get("info", 0x0099ff),
            timestamp=datetime.now(),
        )
        embed.set_footer(text="Raspberry Pi OSINT")
        return embed

    def build_geoip_embed(self, target: str, data: dict) -> discord.Embed:
        flags = []
        if data.get("proxy"):
            flags.append("Proxy/VPN")
        if data.get("hosting"):
            flags.append("Hosting")
        if data.get("mobile"):
            flags.append("Mobile")

        embed = discord.Embed(
            title="🌍 Geo IP",
            color=COLORS.get("info", 0x0099ff),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Target", value=f"`{target}`", inline=True)
        embed.add_field(name="IP", value=f"`{data.get('query', 'N/A')}`", inline=True)
        embed.add_field(
            name="Localisation",
            value=f"{data.get('city', 'N/A')}, {data.get('regionName', 'N/A')}, {data.get('country', 'N/A')}",
            inline=False,
        )
        embed.add_field(name="ISP", value=data.get("isp") or "N/A", inline=False)
        embed.add_field(name="Org", value=data.get("org") or "N/A", inline=False)
        embed.add_field(name="AS", value=data.get("as") or "N/A", inline=False)
        embed.add_field(name="Timezone", value=data.get("timezone") or "N/A", inline=True)
        embed.add_field(name="Coordonnées", value=f"`{data.get('lat')}, {data.get('lon')}`", inline=True)
        embed.add_field(name="Flags", value=", ".join(flags) if flags else "Aucun", inline=False)
        embed.set_footer(text="Raspberry Pi OSINT")
        return embed

    def build_nmap_help_embed(self) -> discord.Embed:
        scan_lines = [
            f"**{name}**: {config['description']}"
            for name, config in NMAP_SCAN_TYPES.items()
        ]

        embed = discord.Embed(
            title=f"📖 Aide — {BOT_PREFIX}nmap",
            color=COLORS.get("info", 0x0099ff),
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="Syntaxe",
            value=f"`{BOT_PREFIX}nmap <target> [scan_type=basic]`",
            inline=False,
        )
        embed.add_field(
            name="Description",
            value=(
                f"Usage: `{BOT_PREFIX}nmap <target> [scan_type]`\n\n"
                "Scan types disponibles:\n"
                + "\n".join(scan_lines)
            ),
            inline=False,
        )
        embed.set_footer(text="Raspberry Pi OSINT")
        return embed

    async def send_error(self, destination, message: str):
        await destination.send(f"❌ {message}")

    async def run_nmap(self, target: str, scan_type: str = "basic") -> tuple[int, str]:
        if not command_exists("nmap"):
            return 127, "nmap n'est pas installé. Installe-le avec: sudo apt install nmap"

        target = clean_target(target)
        scan_type = (scan_type or "basic").lower().strip()
        scan_config = NMAP_SCAN_TYPES.get(scan_type)
        if not scan_config:
            valid_types = ", ".join(NMAP_SCAN_TYPES)
            raise ValueError(f"Type de scan invalide. Types disponibles: {valid_types}")

        needs_sudo = scan_type in {"stealth", "os", "vuln"}
        command = ["sudo", "nmap", *scan_config["args"], target] if needs_sudo else ["nmap", *scan_config["args"], target]

        return await asyncio.to_thread(run_command, command, scan_config["timeout"])

    async def run_nslookup(self, target: str) -> tuple[int, str]:
        target = clean_target(target)
        if command_exists("nslookup"):
            return await asyncio.to_thread(run_command, ["nslookup", target], 20)

        try:
            ips = await asyncio.to_thread(resolve_target, target)
            return 0, "\n".join(ips)
        except RuntimeError as e:
            return 1, str(e)

    async def run_whois(self, target: str) -> tuple[int, str]:
        target = clean_target(target)
        if not command_exists("whois"):
            return 127, "whois n'est pas installé. Installe-le avec: sudo apt install whois"
        return await asyncio.to_thread(run_command, ["whois", target], 30)

    async def run_wpscan(self, url: str) -> tuple[int, str]:
        if not command_exists("wpscan"):
            return 127, "wpscan n'est pas installé. Installe-le avec: sudo gem install wpscan"

        url = normalize_url(url)
        command = [
            "wpscan",
            "--url",
            url,
            "--no-update",
            "--random-user-agent",
            "--format",
            "cli-no-color",
        ]
        return await asyncio.to_thread(run_command, command, 120)

    @commands.command(name="nmap")
    @require_admin()
    async def nmap_prefix(self, ctx, target: str = None, scan_type: str = "basic"):
        if not target:
            await ctx.send(embed=self.build_nmap_help_embed())
            return

        try:
            async with ctx.typing():
                _, output = await self.run_nmap(target, scan_type)
            await ctx.send(embed=self.build_output_embed(f"🛰️ Nmap — {scan_type}", output))
        except ValueError as e:
            await self.send_error(ctx, str(e))

    @commands.command(name="nslookup")
    @require_admin()
    async def nslookup_prefix(self, ctx, target: str = None):
        if not target:
            await ctx.send(f"❌ Syntaxe : `{BOT_PREFIX}nslookup <domaine/ip>`")
            return

        try:
            async with ctx.typing():
                _, output = await self.run_nslookup(target)
            await ctx.send(embed=self.build_output_embed("🔎 NSLookup", output))
        except ValueError as e:
            await self.send_error(ctx, str(e))

    @commands.command(name="whois")
    @require_admin()
    async def whois_prefix(self, ctx, target: str = None):
        if not target:
            await ctx.send(f"❌ Syntaxe : `{BOT_PREFIX}whois <domaine/ip>`")
            return

        try:
            async with ctx.typing():
                _, output = await self.run_whois(target)
            await ctx.send(embed=self.build_output_embed("📇 Whois", output))
        except ValueError as e:
            await self.send_error(ctx, str(e))

    @commands.command(name="geoip")
    @require_admin()
    async def geoip_prefix(self, ctx, target: str = None):
        if not target:
            await ctx.send(f"❌ Syntaxe : `{BOT_PREFIX}geoip <ip/domaine>`")
            return

        try:
            async with ctx.typing():
                data = await asyncio.to_thread(fetch_geoip, target)
            await ctx.send(embed=self.build_geoip_embed(target, data))
        except (ValueError, RuntimeError) as e:
            await self.send_error(ctx, str(e))

    @commands.command(name="wpscan")
    @require_admin()
    async def wpscan_prefix(self, ctx, url: str = None):
        if not url:
            await ctx.send(f"❌ Syntaxe : `{BOT_PREFIX}wpscan <url>`")
            return

        try:
            async with ctx.typing():
                _, output = await self.run_wpscan(url)
            await ctx.send(embed=self.build_output_embed("🧱 WPScan", output))
        except ValueError as e:
            await self.send_error(ctx, str(e))

    @app_commands.command(name="nmap", description="Scan nmap rapide sur un host autorisé")
    @app_commands.check(slash_require_admin)
    @app_commands.describe(target="Host ou IP à scanner", scan_type="Type de scan à lancer")
    @app_commands.choices(scan_type=[
        app_commands.Choice(name="basic - Scan basique des ports", value="basic"),
        app_commands.Choice(name="stealth - Scan SYN stealth", value="stealth"),
        app_commands.Choice(name="version - Détection de version", value="version"),
        app_commands.Choice(name="os - Détection d'OS", value="os"),
        app_commands.Choice(name="vuln - Scan de vulnérabilités", value="vuln"),
    ])
    async def nmap_slash(
        self,
        interaction: discord.Interaction,
        target: str,
        scan_type: app_commands.Choice[str] | None = None
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            selected_scan = scan_type.value if scan_type else "basic"
            _, output = await self.run_nmap(target, selected_scan)
            await interaction.followup.send(
                embed=self.build_output_embed(f"🛰️ Nmap — {selected_scan}", output),
                ephemeral=True
            )
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @app_commands.command(name="nslookup", description="Résolution DNS d'un domaine ou d'une IP")
    @app_commands.check(slash_require_admin)
    async def nslookup_slash(self, interaction: discord.Interaction, target: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            _, output = await self.run_nslookup(target)
            await interaction.followup.send(embed=self.build_output_embed("🔎 NSLookup", output), ephemeral=True)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @app_commands.command(name="whois", description="Whois d'un domaine ou d'une IP")
    @app_commands.check(slash_require_admin)
    async def whois_slash(self, interaction: discord.Interaction, target: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            _, output = await self.run_whois(target)
            await interaction.followup.send(embed=self.build_output_embed("📇 Whois", output), ephemeral=True)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @app_commands.command(name="geoip", description="Géolocalisation IP ou domaine")
    @app_commands.check(slash_require_admin)
    async def geoip_slash(self, interaction: discord.Interaction, target: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            data = await asyncio.to_thread(fetch_geoip, target)
            await interaction.followup.send(embed=self.build_geoip_embed(target, data), ephemeral=True)
        except (ValueError, RuntimeError) as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @app_commands.command(name="wpscan", description="Scan WordPress basique sur une URL autorisée")
    @app_commands.check(slash_require_admin)
    async def wpscan_slash(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            _, output = await self.run_wpscan(url)
            await interaction.followup.send(embed=self.build_output_embed("🧱 WPScan", output), ephemeral=True)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send("❌ Commande réservée aux administrateurs.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Commande réservée aux administrateurs.", ephemeral=True)
            return

        if interaction.response.is_done():
            await interaction.followup.send(f"❌ Erreur OSINT: {error}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Erreur OSINT: {error}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Osint(bot))
