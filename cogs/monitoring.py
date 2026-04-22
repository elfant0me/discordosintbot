import asyncio
import os
import platform
import re
import socket
import time
import shutil
import psutil
import subprocess
import json
import base64
import concurrent.futures
import urllib.request
import urllib.error
from datetime import datetime

import discord
from discord.ext import commands, tasks
from discord import app_commands

from config import (
    BOT_PREFIX,
    COLORS,
    MONITOR_ALERT_CHANNEL_ID,
    MONITOR_GUILD_ID,
    CPU_ALERT_THRESHOLD,
    TEMP_ALERT_THRESHOLD,
    DISK_ALERT_THRESHOLD,
    CHECK_INTERVAL,
    ADGUARD_BASE_URL,
    ADGUARD_USERNAME,
    ADGUARD_PASSWORD,
    HOMELAB_SERVICES,
    MONITOR_DISK_PATHS,
    MONITOR_HEALTH_HOSTS,
    MONITOR_SERVICES,
)
from utils.permissions import is_admin_member, require_admin


def run_cmd(command: list[str], timeout: int = 10) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return result.stdout.strip()
        return result.stderr.strip() or "Commande échouée"
    except Exception as e:
        return f"Erreur: {e}"


async def slash_require_admin(interaction: discord.Interaction) -> bool:
    if await interaction.client.is_owner(interaction.user):
        return True
    return is_admin_member(interaction.user)


def get_cpu_temp() -> float | None:
    thermal_paths = [
        "/sys/class/thermal/thermal_zone0/temp",
    ]

    for path in thermal_paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    raw = f.read().strip()
                return round(int(raw) / 1000, 1)
            except Exception:
                pass

    return None


def get_detected_temperature_sensors() -> int:
    sensor_count = 0

    try:
        thermal_base = "/sys/class/thermal"
        if os.path.isdir(thermal_base):
            for name in os.listdir(thermal_base):
                temp_path = os.path.join(thermal_base, name, "temp")
                if name.startswith("thermal_zone") and os.path.exists(temp_path):
                    sensor_count += 1
    except Exception:
        pass

    if sensor_count:
        return sensor_count

    try:
        sensors = psutil.sensors_temperatures()
        return sum(len(entries) for entries in sensors.values())
    except Exception:
        return 0


def get_uptime() -> str:
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    delta = datetime.now() - boot_time
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{days}j {hours}h {minutes}m"


def get_uptime_linux_style() -> str:
    uptime_output = run_cmd(["uptime"])
    if uptime_output and not uptime_output.startswith("Erreur"):
        return uptime_output
    return "Impossible de récupérer uptime"


def get_hostname() -> str:
    return socket.gethostname()


def get_local_ips() -> list[str]:
    ips = set()

    try:
        for interface_addrs in psutil.net_if_addrs().values():
            for addr in interface_addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    ips.add(addr.address)
    except Exception:
        pass

    try:
        primary_ip = socket.gethostbyname(get_hostname())
        if primary_ip and not primary_ip.startswith("127."):
            ips.add(primary_ip)
    except Exception:
        pass

    return sorted(ips)


def get_network_summary() -> list[str]:
    lines = []

    try:
        net_stats = psutil.net_if_stats()
        net_addrs = psutil.net_if_addrs()

        for interface, stats in net_stats.items():
            if interface == "lo":
                continue

            ipv4 = "N/A"
            for addr in net_addrs.get(interface, []):
                if addr.family == socket.AF_INET:
                    ipv4 = addr.address
                    break

            state = "up" if stats.isup else "down"
            speed = f"{stats.speed} Mb/s" if stats.speed and stats.speed > 0 else "N/A"
            mtu = stats.mtu or "N/A"
            lines.append(f"**{interface}** → {state} | IP: `{ipv4}` | Speed: `{speed}` | MTU: `{mtu}`")
    except Exception as e:
        lines.append(f"Impossible de lire les interfaces réseau: {e}")

    return lines


def get_top_processes(limit: int = 5) -> list[dict]:
    processes = []

    # psutil a besoin de deux relevés pour que cpu_percent soit pertinent.
    candidates = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            proc.cpu_percent(interval=None)
            candidates.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    time.sleep(0.2)

    for proc in candidates:
        try:
            processes.append({
                "pid": proc.pid,
                "name": proc.info.get("name"),
                "cpu_percent": proc.cpu_percent(interval=None),
                "memory_percent": proc.memory_percent(),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    processes.sort(
        key=lambda item: (
            item.get("cpu_percent", 0.0) or 0.0,
            item.get("memory_percent", 0.0) or 0.0,
        ),
        reverse=True
    )
    return processes[:limit]


def format_bytes(num: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def get_disk_usage(paths: list[str]) -> list[tuple[str, str]]:
    results = []
    for path in paths:
        if os.path.exists(path):
            usage = shutil.disk_usage(path)
            percent = round((usage.used / usage.total) * 100, 1)
            results.append((
                path,
                f"{percent}% used ({format_bytes(usage.used)} / {format_bytes(usage.total)})"
            ))
    return results


def get_service_status(service_name: str) -> str:
    result = run_cmd(["systemctl", "is-active", service_name])
    if result == "active":
        return "🟢 Active"
    if result == "inactive":
        return "⚪ Inactive"
    if result == "failed":
        return "🔴 Failed"
    return f"🟡 {result}"


def format_number(value: int | float) -> str:
    return f"{value:,}".replace(",", " ")


def format_seconds(value: float) -> str:
    if value < 1:
        return f"{value * 1000:.1f} ms"
    return f"{value:.3f} s"


def truncate_output(text: str, limit: int = 3800) -> str:
    text = text.strip() or "Aucune sortie."
    if len(text) <= limit:
        return text
    return text[:limit - 80].rstrip() + "\n\n... sortie tronquée ..."


def format_code_output(text: str, language: str = "") -> str:
    safe_text = truncate_output(text).replace("```", "'''")
    return f"```{language}\n{safe_text}\n```"


def format_adguard_top_list(items, limit: int = 3) -> str:
    if not items:
        return "Aucune donnée"

    lines = []
    for item in items[:limit]:
        if isinstance(item, dict) and item:
            key, value = next(iter(item.items()))
            lines.append(f"`{key}` ({format_number(value)})")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            lines.append(f"`{item[0]}` ({format_number(item[1])})")
        else:
            lines.append(f"`{item}`")

    return "\n".join(lines) if lines else "Aucune donnée"


def fetch_adguard_stats() -> dict:
    if not ADGUARD_BASE_URL:
        raise RuntimeError("ADGUARD_BASE_URL n'est pas configuré")

    url = f"{ADGUARD_BASE_URL}/control/stats"
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/json")

    if ADGUARD_USERNAME or ADGUARD_PASSWORD:
        raw_auth = f"{ADGUARD_USERNAME}:{ADGUARD_PASSWORD}".encode("utf-8")
        token = base64.b64encode(raw_auth).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")

    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            payload = response.read().decode(charset)
            return json.loads(payload)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API AdGuard indisponible ({e.code})") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connexion AdGuard impossible: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError("Réponse AdGuard invalide") from e


def parse_key_value_stats(output: str) -> dict[str, float]:
    stats = {}

    for line in output.splitlines():
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        try:
            stats[key] = float(value)
        except ValueError:
            continue

    return stats


def get_unbound_stats() -> dict[str, float]:
    command = ["sudo", "/usr/sbin/unbound-control", "stats_noreset"]
    output = run_cmd(command)

    if "No such file" in output or "not found" in output:
        output = run_cmd(["sudo", "unbound-control", "stats_noreset"])

    if output.startswith("Erreur:"):
        raise RuntimeError(output)
    if output == "Commande échouée":
        raise RuntimeError("Impossible de récupérer les stats Unbound")
    if "permission denied" in output.lower():
        raise RuntimeError("Permission refusée pour unbound-control")

    stats = parse_key_value_stats(output)
    if not stats:
        raise RuntimeError(f"Réponse Unbound invalide: {output[:200]}")

    return stats


def get_journal_output(service_name: str, lines: int = 50) -> str:
    safe_service = service_name.strip()
    if not safe_service or not re.match(r"^[a-zA-Z0-9_.@:-]+$", safe_service):
        raise RuntimeError("Nom de service invalide")

    lines = max(5, min(lines, 100))
    return run_cmd(
        ["journalctl", "-u", safe_service, "-n", str(lines), "--no-pager", "--output", "short-iso"],
        timeout=15
    )


def get_update_output() -> tuple[list[str], str]:
    update_cmd = run_cmd(["apt", "list", "--upgradable"], timeout=20)
    if update_cmd.startswith("Erreur:"):
        return [], update_cmd

    packages = []
    for line in update_cmd.splitlines():
        if not line or line.startswith("Listing..."):
            continue
        packages.append(line.strip())

    return packages, update_cmd


def get_docker_containers() -> str:
    if not shutil.which("docker"):
        return "Docker non installé"
    output = run_cmd(["docker", "ps", "--format", "{{.Names}} | {{.Status}}"])
    if not output:
        return "Aucun conteneur actif"
    return output


def get_running_docker_count() -> tuple[int, int]:
    if not shutil.which("docker"):
        return 0, 0

    running = run_cmd(["docker", "ps", "-q"])
    all_containers = run_cmd(["docker", "ps", "-aq"])

    running_count = len([x for x in running.splitlines() if x.strip()]) if running else 0
    total_count = len([x for x in all_containers.splitlines() if x.strip()]) if all_containers else 0

    return running_count, total_count


def ping_host(host: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", host],
            capture_output=True,
            text=True,
            timeout=5
        )

        output = (result.stdout or "") + (result.stderr or "")

        if result.returncode == 0:
            latency = "N/A"
            for line in output.splitlines():
                if "time=" in line:
                    latency = line.split("time=")[1].split()[0] + " ms"
                    break
            return True, latency

        return False, "Host unreachable"

    except Exception as e:
        return False, f"Erreur: {e}"


def check_tcp_endpoint(host: str, port: int, timeout: float = 3.0) -> tuple[bool, str]:
    start = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency = round((time.time() - start) * 1000, 1)
            return True, f"{latency} ms"
    except Exception as e:
        return False, str(e)


def check_http_endpoint(url: str, timeout: int = 8) -> tuple[bool, str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "RaspberryPI-MonitoringBot/1.0"}
    )

    start = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            latency = round((time.time() - start) * 1000, 1)
            return response.status < 500, f"HTTP {response.status} ({latency} ms)"
    except urllib.error.HTTPError as e:
        latency = round((time.time() - start) * 1000, 1)
        if 400 <= e.code < 500:
            return True, f"HTTP {e.code} ({latency} ms)"
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


def check_homelab_service(service: dict) -> tuple[bool, str]:
    check_type = service.get("type")

    if check_type == "http":
        return check_http_endpoint(service["target"])
    if check_type == "tcp":
        return check_tcp_endpoint(service["host"], service["port"])
    if check_type == "ping":
        return ping_host(service["target"])

    return False, "Type de check inconnu"


class Monitoring(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_alerts = {
            "cpu": 0,
            "temp": 0,
            "disk": 0,
        }
        self.temp_samples = []
        self.alert_cooldown = 900  # 15 minutes
        self.services_to_watch = MONITOR_SERVICES
        self.disk_paths = MONITOR_DISK_PATHS
        self.health_hosts = MONITOR_HEALTH_HOSTS
        self.homelab_services = HOMELAB_SERVICES

    async def cog_load(self):
        self.monitor_loop.start()

    async def cog_unload(self):
        self.monitor_loop.cancel()

    def is_on_cooldown(self, key: str) -> bool:
        return (time.time() - self.last_alerts[key]) < self.alert_cooldown

    def mark_alert(self, key: str):
        self.last_alerts[key] = time.time()

    async def send_alert(self, title: str, description: str):
        if not MONITOR_ALERT_CHANNEL_ID:
            return

        channel = self.bot.get_channel(MONITOR_ALERT_CHANNEL_ID)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(MONITOR_ALERT_CHANNEL_ID)
            except Exception:
                return

        embed = discord.Embed(
            title=title,
            description=description,
            color=COLORS.get("warning", 0xffaa00),
            timestamp=datetime.now()
        )
        embed.set_footer(text="Raspberry Pi Monitoring")
        await channel.send(embed=embed)

    def build_status_embed(self) -> discord.Embed:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        temp = get_cpu_temp()
        uptime = get_uptime()

        if cpu < 50:
            color = 0x2ecc71
        elif cpu < 80:
            color = 0xf1c40f
        else:
            color = 0xe74c3c

        embed = discord.Embed(
            title="📊 Raspberry Pi Status",
            color=color,
            timestamp=datetime.now()
        )

        embed.add_field(name="🧠 CPU", value=f"**{cpu}%**", inline=True)
        embed.add_field(
            name="💾 RAM",
            value=f"**{mem.percent}%**\n{format_bytes(mem.used)} / {format_bytes(mem.total)}",
            inline=True
        )
        embed.add_field(
            name="🌡️ Temp",
            value=f"**{temp}°C**" if temp is not None else "**N/A**",
            inline=True
        )

        embed.add_field(name="⏱️ Uptime", value=f"**{uptime}**", inline=False)

        disk_lines = [f"**{path}**\n{info}" for path, info in get_disk_usage(self.disk_paths)]
        embed.add_field(
            name="💽 Disques",
            value="\n\n".join(disk_lines[:10]) or "Aucun disque trouvé",
            inline=False
        )

        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_temp_embed(self) -> discord.Embed:
        temp = get_cpu_temp()
        sensor_count = get_detected_temperature_sensors()

        if temp is None:
            color = 0x95a5a6
            title = "🌡️ CPU Temperature"
        elif temp < 60:
            color = 0x2ecc71
            title = "🌡️ CPU Temperature"
        elif temp < 75:
            color = 0xf1c40f
            title = "🔥 CPU Temperature"
        else:
            color = 0xe74c3c
            title = "🚨 CPU Temperature"

        if temp is not None:
            self.temp_samples.append(temp)
            self.temp_samples = self.temp_samples[-1440:]

        embed = discord.Embed(
            title=title,
            color=color,
            timestamp=datetime.now()
        )
        if temp is None:
            embed.description = "Impossible de lire la température."
        else:
            max_temp = max(self.temp_samples) if self.temp_samples else temp
            avg_temp = sum(self.temp_samples) / len(self.temp_samples) if self.temp_samples else temp

            embed.add_field(name="🌡️ Actuelle", value=f"**{temp:.1f}°C**", inline=True)
            embed.add_field(name="🔥 Max", value=f"**{max_temp:.1f}°C**", inline=True)
            embed.add_field(name="📊 Moyenne", value=f"**{avg_temp:.1f}°C**", inline=True)

        embed.add_field(
            name="🛰️ Capteurs",
            value=f"**{sensor_count} détecté(s)**",
            inline=False
        )
        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_disk_embed(self) -> discord.Embed:
        lines = [f"**{path}**\n{info}" for path, info in get_disk_usage(self.disk_paths)]

        embed = discord.Embed(
            title="💽 Disk Usage",
            description="\n\n".join(lines) if lines else "Aucun disque trouvé",
            color=COLORS.get("info", 0x0099ff),
            timestamp=datetime.now()
        )
        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_services_embed(self) -> discord.Embed:
        lines = [f"**{svc}** → {get_service_status(svc)}" for svc in self.services_to_watch]

        embed = discord.Embed(
            title="🛠️ Services",
            description="\n".join(lines),
            color=COLORS.get("info", 0x0099ff),
            timestamp=datetime.now()
        )
        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_docker_embed(self) -> discord.Embed:
        containers = get_docker_containers()

        embed = discord.Embed(
            title="🐳 Docker Containers",
            color=COLORS.get("info", 0x0099ff),
            timestamp=datetime.now()
        )
        embed.description = f"```ansi\n{containers[:3500]}\n```"
        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_uptime_embed(self) -> discord.Embed:
        raw = get_uptime_linux_style()

        try:
            parts = raw.split(" up ", 1)
            time_part = parts[0].strip()
            rest = parts[1].strip()

            if " load average: " in rest:
                before_load, load_part = rest.split(" load average: ", 1)
            else:
                before_load, load_part = rest, "N/A, N/A, N/A"

            segments = [seg.strip() for seg in before_load.split(",") if seg.strip()]

            users_part = "N/A"
            uptime_segments = []

            for seg in segments:
                if "user" in seg:
                    users_part = seg
                else:
                    uptime_segments.append(seg)

            uptime_part = ", ".join(uptime_segments) if uptime_segments else "N/A"

            load_values = [x.strip() for x in load_part.split(",")]
            while len(load_values) < 3:
                load_values.append("N/A")

            load1, load5, load15 = load_values[:3]

            try:
                load1_val = float(load1)
                if load1_val < 0.50:
                    color = 0x2ecc71
                    title = "🟢 System Uptime"
                elif load1_val < 1.50:
                    color = 0xf1c40f
                    title = "🟡 System Uptime"
                else:
                    color = 0xe74c3c
                    title = "🔴 System Uptime"
            except ValueError:
                color = COLORS.get("info", 0x0099ff)
                title = "⏱️ System Uptime"

            embed = discord.Embed(
                title=title,
                color=color,
                timestamp=datetime.now()
            )

            embed.add_field(name="🕒 Heure", value=f"**{time_part}**", inline=True)
            embed.add_field(name="⏱️ Uptime", value=f"**{uptime_part}**", inline=True)
            embed.add_field(name="👥 Users", value=f"**{users_part}**", inline=True)

            embed.add_field(
                name="📊 Load Average",
                value=(
                    f"**1m:** `{load1}`\n"
                    f"**5m:** `{load5}`\n"
                    f"**15m:** `{load15}`"
                ),
                inline=False
            )

            embed.set_footer(text="Raspberry Pi Monitoring")
            return embed

        except Exception:
            embed = discord.Embed(
                title="⏱️ System Uptime",
                description=f"```{raw}```",
                color=COLORS.get("info", 0x0099ff),
                timestamp=datetime.now()
            )
            embed.set_footer(text="Raspberry Pi Monitoring")
            return embed

    def build_health_embed(self) -> discord.Embed:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        temp = get_cpu_temp()
        running_docker, total_docker = get_running_docker_count()

        # Parse uptime/users/load depuis la commande uptime
        uptime_value = "N/A"
        users_value = "N/A"
        load_value = "N/A, N/A, N/A"

        raw_uptime = get_uptime_linux_style()
        try:
            if raw_uptime and " up " in raw_uptime:
                _, rest = raw_uptime.split(" up ", 1)

                if " load average: " in rest:
                    before_load, load_part = rest.split(" load average: ", 1)
                    load_parts = [x.strip() for x in load_part.split(",")]
                    while len(load_parts) < 3:
                        load_parts.append("N/A")
                    load_value = ", ".join(load_parts[:3])
                else:
                    before_load = rest

                segments = [seg.strip() for seg in before_load.split(",") if seg.strip()]
                uptime_segments = []

                for seg in segments:
                    if "user" in seg:
                        users_value = seg
                    else:
                        uptime_segments.append(seg)

                if uptime_segments:
                    uptime_value = ", ".join(uptime_segments)

        except Exception:
            pass

        if cpu < 50 and mem.percent < 75 and (temp is None or temp < 65):
            color = 0x2ecc71
            title = "🟢 Health Check"
        elif cpu < 80 and mem.percent < 90 and (temp is None or temp < 75):
            color = 0xf1c40f
            title = "🟡 Health Check"
        else:
            color = 0xe74c3c
            title = "🔴 Health Check"

        embed = discord.Embed(
            title=title,
            color=color,
            timestamp=datetime.now()
        )

        embed.add_field(name="🧠 CPU", value=f"**{cpu}%**", inline=True)
        embed.add_field(name="💾 RAM", value=f"**{mem.percent}%**", inline=True)
        embed.add_field(
            name="🌡️ Temp",
            value=f"**{temp}°C**" if temp is not None else "**N/A**",
            inline=True
        )

        embed.add_field(name="⏱️ Uptime", value=f"**{uptime_value}**", inline=True)
        embed.add_field(name="👥 Users", value=f"**{users_value}**", inline=True)
        embed.add_field(name="📊 Load", value=f"`{load_value}`", inline=True)

        service_lines = []
        for svc in self.services_to_watch:
            service_lines.append(f"**{svc}** → {get_service_status(svc)}")

        embed.add_field(
            name="🛠️ Services",
            value="\n".join(service_lines) if service_lines else "Aucun",
            inline=False
        )

        embed.add_field(
            name="🐳 Docker",
            value=f"**{running_docker}/{total_docker}** conteneurs actifs",
            inline=False
        )

        host_lines = []
        for label, host in self.health_hosts.items():
            ok, info = ping_host(host)
            status = "🟢 Online" if ok else "🔴 Offline"
            suffix = f" ({info})" if info else ""
            host_lines.append(f"**{label}** → {status}{suffix}")

        embed.add_field(
            name="🌐 Hosts",
            value="\n".join(host_lines) if host_lines else "Aucun host configuré",
            inline=False
        )

        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_ping_embed(self, host: str) -> discord.Embed:
        ok, info = ping_host(host)

        color = 0x2ecc71 if ok else 0xe74c3c
        status = "🟢 Host reachable" if ok else "🔴 Host unreachable"

        embed = discord.Embed(
            title="🌐 Ping Host",
            color=color,
            timestamp=datetime.now()
        )
        embed.add_field(name="Host", value=f"`{host}`", inline=False)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Result", value=f"`{info}`", inline=True)
        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_system_embed(self) -> discord.Embed:
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        hostname = get_hostname()
        ips = get_local_ips()

        embed = discord.Embed(
            title="🖥️ System Info",
            color=COLORS.get("info", 0x0099ff),
            timestamp=datetime.now()
        )
        embed.add_field(name="Hostname", value=f"`{hostname}`", inline=True)
        embed.add_field(name="OS", value=f"`{platform.system()} {platform.release()}`", inline=True)
        embed.add_field(name="Machine", value=f"`{platform.machine()}`", inline=True)
        embed.add_field(name="Python", value=f"`{platform.python_version()}`", inline=True)
        embed.add_field(name="CPU Cores", value=f"`{psutil.cpu_count(logical=False) or 'N/A'}` physiques / `{psutil.cpu_count()}` logiques", inline=True)
        embed.add_field(name="Boot", value=f"<t:{int(psutil.boot_time())}:F>", inline=False)
        embed.add_field(
            name="RAM / Swap",
            value=(
                f"RAM: **{mem.percent}%** ({format_bytes(mem.used)} / {format_bytes(mem.total)})\n"
                f"Swap: **{swap.percent}%** ({format_bytes(swap.used)} / {format_bytes(swap.total)})"
            ),
            inline=False
        )
        embed.add_field(
            name="IP locales",
            value="\n".join(f"`{ip}`" for ip in ips) if ips else "Aucune IP détectée",
            inline=False
        )
        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_network_embed(self) -> discord.Embed:
        lines = get_network_summary()

        embed = discord.Embed(
            title="🌐 Network Info",
            description="\n".join(lines[:20]) if lines else "Aucune interface détectée",
            color=COLORS.get("info", 0x0099ff),
            timestamp=datetime.now()
        )
        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_top_embed(self, limit: int = 5) -> discord.Embed:
        rows = get_top_processes(limit=max(1, min(limit, 10)))

        embed = discord.Embed(
            title="📈 Top Processes",
            color=COLORS.get("info", 0x0099ff),
            timestamp=datetime.now()
        )

        if not rows:
            embed.description = "Aucun processus trouvé"
        else:
            lines = []
            for proc in rows:
                name = proc.get("name") or "unknown"
                pid = proc.get("pid", "?")
                cpu = proc.get("cpu_percent", 0.0) or 0.0
                mem = proc.get("memory_percent", 0.0) or 0.0
                lines.append(f"`{pid}` **{name[:24]}** | CPU: `{cpu:.1f}%` | RAM: `{mem:.1f}%`")
            embed.description = "\n".join(lines)

        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_adguard_embed(self) -> discord.Embed:
        stats = fetch_adguard_stats()

        queries = int(stats.get("num_dns_queries", 0) or 0)
        blocked = int(stats.get("num_blocked_filtering", 0) or 0)
        avg_processing_time = float(stats.get("avg_processing_time", 0.0) or 0.0)
        block_ratio = (blocked / queries * 100) if queries else 0.0

        if avg_processing_time < 30:
            color = 0x2ecc71
        elif avg_processing_time < 75:
            color = 0xf1c40f
        else:
            color = 0xe74c3c

        embed = discord.Embed(
            title="🛡️ AdGuard Home",
            color=color,
            timestamp=datetime.now()
        )
        embed.add_field(name="Queries", value=f"**{format_number(queries)}**", inline=True)
        embed.add_field(name="Blocked", value=f"**{format_number(blocked)}**", inline=True)
        embed.add_field(name="Latency", value=f"**{avg_processing_time:.3f} ms**", inline=True)
        embed.add_field(name="Taux de blocage", value=f"**{block_ratio:.1f}%**", inline=True)
        embed.add_field(
            name="Top domaines cherchés",
            value=format_adguard_top_list(stats.get("top_queried_domains") or [], limit=5),
            inline=False
        )
        embed.add_field(
            name="Top domaines bloqués",
            value=format_adguard_top_list(stats.get("top_blocked_domains") or [], limit=5),
            inline=False
        )

        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_unbound_embed(self) -> discord.Embed:
        stats = get_unbound_stats()

        queries = int(stats.get("total.num.queries", 0) or 0)
        cache_hits = int(stats.get("total.num.cachehits", 0) or 0)
        cache_miss = int(stats.get("total.num.cachemiss", 0) or 0)
        prefetch = int(stats.get("total.num.prefetch", 0) or 0)
        timed_out = int(stats.get("total.num.queries_timed_out", 0) or 0)
        ratelimited = int(stats.get("total.num.queries_ip_ratelimited", 0) or 0)
        recursion_avg = stats.get("total.recursion.time.avg", 0.0) or 0.0
        recursion_median = stats.get("total.recursion.time.median", 0.0) or 0.0
        requestlist_avg = stats.get("total.requestlist.avg", 0.0) or 0.0
        requestlist_max = int(stats.get("total.requestlist.max", 0) or 0)
        requestlist_overwritten = int(stats.get("total.requestlist.overwritten", 0) or 0)
        requestlist_exceeded = int(stats.get("total.requestlist.exceeded", 0) or 0)
        requestlist_current_all = int(stats.get("total.requestlist.current.all", 0) or 0)
        has_memory_stats = any(key.startswith("mem.") for key in stats)
        mem_cache_rrset = stats.get("mem.cache.rrset", 0.0) or 0.0
        mem_cache_message = stats.get("mem.cache.message", 0.0) or 0.0
        mem_mod_iterator = stats.get("mem.mod.iterator", 0.0) or 0.0
        mem_mod_validator = stats.get("mem.mod.validator", 0.0) or 0.0
        mem_total = mem_cache_rrset + mem_cache_message + mem_mod_iterator + mem_mod_validator

        hit_rate = (cache_hits / queries * 100) if queries else 0.0
        miss_rate = (cache_miss / queries * 100) if queries else 0.0

        if hit_rate >= 40 and timed_out == 0:
            color = 0x2ecc71
        elif hit_rate >= 20 and timed_out < 5:
            color = 0xf1c40f
        else:
            color = 0xe74c3c

        embed = discord.Embed(
            title="🧩 Unbound DNS",
            color=color,
            timestamp=datetime.now()
        )
        embed.add_field(name="Queries", value=f"**{format_number(queries)}**", inline=True)
        embed.add_field(name="Cache Hits", value=f"**{format_number(cache_hits)}**", inline=True)
        embed.add_field(name="Cache Miss", value=f"**{format_number(cache_miss)}**", inline=True)
        embed.add_field(name="Hit Rate", value=f"**{hit_rate:.1f}%**", inline=True)
        embed.add_field(name="Miss Rate", value=f"**{miss_rate:.1f}%**", inline=True)
        embed.add_field(name="Prefetch", value=f"**{format_number(prefetch)}**", inline=True)
        embed.add_field(name="Timed out", value=f"**{format_number(timed_out)}**", inline=True)
        embed.add_field(name="Rate limited", value=f"**{format_number(ratelimited)}**", inline=True)
        embed.add_field(
            name="Recursion Time",
            value=(
                f"Avg: **{format_seconds(recursion_avg)}**\n"
                f"Median: **{format_seconds(recursion_median)}**"
            ),
            inline=True
        )
        embed.add_field(
            name="Requestlist",
            value=(
                f"Current: **{format_number(requestlist_current_all)}**\n"
                f"Avg: **{requestlist_avg:.2f}** | Max: **{format_number(requestlist_max)}**\n"
                f"Exceeded: **{format_number(requestlist_exceeded)}** | Overwritten: **{format_number(requestlist_overwritten)}**"
            ),
            inline=False
        )
        if has_memory_stats:
            embed.add_field(
                name="Memory",
                value=(
                    f"Total tracked: **{format_bytes(mem_total)}**\n"
                    f"RRSet: **{format_bytes(mem_cache_rrset)}** | Message: **{format_bytes(mem_cache_message)}**\n"
                    f"Iterator: **{format_bytes(mem_mod_iterator)}** | Validator: **{format_bytes(mem_mod_validator)}**"
                ),
                inline=False
            )
        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_journal_embed(self, service_name: str, lines: int = 50) -> discord.Embed:
        output = get_journal_output(service_name, lines)

        embed = discord.Embed(
            title=f"🧾 Journal: {service_name}",
            description=format_code_output(output),
            color=COLORS.get("info", 0x0099ff),
            timestamp=datetime.now()
        )
        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_update_embed(self) -> discord.Embed:
        packages, raw_output = get_update_output()

        if raw_output.startswith("Erreur:"):
            raise RuntimeError(raw_output)

        color = 0x2ecc71 if not packages else 0xf1c40f
        embed = discord.Embed(
            title="📦 APT Update",
            color=color,
            timestamp=datetime.now()
        )

        if not packages:
            embed.description = "✅ Aucun paquet à mettre à jour."
        else:
            shown = packages[:20]
            extra = len(packages) - len(shown)
            description = "\n".join(f"• `{pkg.split('/')[0]}`" for pkg in shown)
            if extra > 0:
                description += f"\n\n... et **{extra}** autre(s) paquet(s)."

            embed.add_field(name="Paquets disponibles", value=f"**{len(packages)}**", inline=True)
            embed.description = description

        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    def build_homelab_embed(self) -> discord.Embed:
        if not self.homelab_services:
            embed = discord.Embed(
                title="🏠 Homelab Services",
                description="Aucun service configuré. Ajoute `HOMELAB_SERVICES` dans `.env`.",
                color=COLORS.get("warning", 0xffaa00),
                timestamp=datetime.now()
            )
            embed.set_footer(text="Raspberry Pi Monitoring")
            return embed

        online = []
        offline = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(check_homelab_service, service): service
                for service in self.homelab_services
            }

            results = []
            for future in concurrent.futures.as_completed(futures):
                service = futures[future]
                try:
                    ok, info = future.result()
                except Exception as e:
                    ok, info = False, str(e)
                results.append((service, ok, info))

        ordered_services = [service["name"] for service in self.homelab_services]
        for service, ok, info in sorted(results, key=lambda item: ordered_services.index(item[0]["name"])):
            name = service["name"]
            if ok:
                online.append((name, info))
            else:
                offline.append((name, info))

        total = len(self.homelab_services)
        online_count = len(online)
        color = 0x2ecc71 if online_count == total else 0xf1c40f if online_count >= total * 0.6 else 0xe74c3c

        embed = discord.Embed(
            title="🏠 Homelab Services",
            color=color,
            timestamp=datetime.now()
        )
        embed.add_field(name="Online", value=f"**{online_count}/{total}**", inline=True)
        embed.add_field(name="Offline", value=f"**{len(offline)}**", inline=True)

        online_lines = [
            f"🟢 **{name}** ({latency})"
            for name, latency in online
        ]
        offline_lines = [
            f"🔴 **{name}** ({info})"
            for name, info in offline
        ]

        embed.add_field(
            name="Services online",
            value="\n\n".join(online_lines) if online_lines else "Aucun",
            inline=False
        )
        embed.add_field(
            name="Services offline",
            value="\n\n".join(offline_lines) if offline_lines else "Aucun",
            inline=False
        )

        embed.set_footer(text="Raspberry Pi Monitoring")
        return embed

    @tasks.loop(seconds=CHECK_INTERVAL)
    async def monitor_loop(self):
        cpu = psutil.cpu_percent(interval=1)
        temp = get_cpu_temp()

        if temp is not None:
            self.temp_samples.append(temp)
            self.temp_samples = self.temp_samples[-1440:]

        disk_alerts = []
        for path, info in get_disk_usage(self.disk_paths):
            try:
                percent = float(info.split("%")[0])
                if percent >= DISK_ALERT_THRESHOLD:
                    disk_alerts.append(f"{path}: {info}")
            except Exception:
                continue

        if cpu >= CPU_ALERT_THRESHOLD and not self.is_on_cooldown("cpu"):
            await self.send_alert(
                "🔥 CPU élevé",
                f"CPU actuel: **{cpu}%**"
            )
            self.mark_alert("cpu")

        if temp is not None and temp >= TEMP_ALERT_THRESHOLD and not self.is_on_cooldown("temp"):
            await self.send_alert(
                "🌡️ Température élevée",
                f"Température CPU: **{temp}°C**"
            )
            self.mark_alert("temp")

        if disk_alerts and not self.is_on_cooldown("disk"):
            await self.send_alert(
                "💽 Disque presque plein",
                "\n".join(disk_alerts)
            )
            self.mark_alert("disk")

    @monitor_loop.before_loop
    async def before_monitor_loop(self):
        await self.bot.wait_until_ready()

    # Slash commands

    @app_commands.command(name="status", description="Résumé complet du Raspberry Pi")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.build_status_embed())

    @app_commands.command(name="temps", description="Voir la température du Raspberry Pi")
    async def temps(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.build_temp_embed())

    @app_commands.command(name="disk", description="Voir l'espace disque")
    async def disk(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.build_disk_embed())

    @app_commands.command(name="services", description="Voir l'état des services systemd")
    async def services(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.build_services_embed())

    @app_commands.command(name="docker", description="Voir les conteneurs Docker actifs")
    async def docker(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.build_docker_embed())

    @app_commands.command(name="uptime", description="Voir l'uptime système")
    async def uptime_slash(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.build_uptime_embed())

    @app_commands.command(name="health", description="Résumé global de santé du système")
    async def health_slash(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.build_health_embed())

    @app_commands.command(name="ping", description="Ping un host ou une IP")
    @app_commands.describe(host="Nom de domaine ou IP à tester")
    async def ping_slash(self, interaction: discord.Interaction, host: str):
        await interaction.response.send_message(embed=self.build_ping_embed(host))

    @app_commands.command(name="system", description="Informations système du Raspberry Pi")
    async def system_slash(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.build_system_embed())

    @app_commands.command(name="network", description="Informations réseau locales")
    async def network_slash(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.build_network_embed())

    @app_commands.command(name="top", description="Top des processus les plus gourmands")
    @app_commands.describe(limit="Nombre de processus à afficher (1 à 10)")
    async def top_slash(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 10] = 5):
        await interaction.response.send_message(embed=self.build_top_embed(limit))

    @app_commands.command(name="adguard", description="Statistiques principales de AdGuard Home")
    async def adguard_slash(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_message(embed=self.build_adguard_embed())
        except RuntimeError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    @app_commands.command(name="unbound", description="Statistiques principales de Unbound DNS")
    async def unbound_slash(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_message(embed=self.build_unbound_embed())
        except RuntimeError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    @app_commands.command(name="journal", description="Dernières lignes journalctl d'un service")
    @app_commands.check(slash_require_admin)
    @app_commands.describe(service="Nom du service systemd", lines="Nombre de lignes, 5 à 100")
    async def journal_slash(self, interaction: discord.Interaction, service: str, lines: app_commands.Range[int, 5, 100] = 50):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            embed = await asyncio.to_thread(self.build_journal_embed, service, lines)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except RuntimeError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @app_commands.command(name="update", description="Paquets APT à mettre à jour")
    @app_commands.check(slash_require_admin)
    async def update_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            embed = await asyncio.to_thread(self.build_update_embed)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except RuntimeError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @app_commands.command(name="homelab", description="État des appareils principaux du LAN")
    async def homelab_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        embed = await asyncio.to_thread(self.build_homelab_embed)
        await interaction.followup.send(embed=embed)

    # Prefix commands

    @commands.command(name="status")
    async def status_prefix(self, ctx):
        await ctx.send(embed=self.build_status_embed())

    @commands.command(name="temps")
    async def temps_prefix(self, ctx):
        await ctx.send(embed=self.build_temp_embed())

    @commands.command(name="disk")
    async def disk_prefix(self, ctx):
        await ctx.send(embed=self.build_disk_embed())

    @commands.command(name="services")
    async def services_prefix(self, ctx):
        await ctx.send(embed=self.build_services_embed())

    @commands.command(name="docker")
    async def docker_prefix(self, ctx):
        await ctx.send(embed=self.build_docker_embed())

    @commands.command(name="uptime")
    async def uptime_prefix(self, ctx):
        await ctx.send(embed=self.build_uptime_embed())

    @commands.command(name="health")
    async def health_prefix(self, ctx):
        await ctx.send(embed=self.build_health_embed())

    @commands.command(name="ping")
    async def ping_prefix(self, ctx, host: str = None):
        if not host:
            await ctx.send(f"❌ Syntaxe : `{BOT_PREFIX}ping <host>`")
            return

        await ctx.send(embed=self.build_ping_embed(host))

    @commands.command(name="system")
    async def system_prefix(self, ctx):
        await ctx.send(embed=self.build_system_embed())

    @commands.command(name="network")
    async def network_prefix(self, ctx):
        await ctx.send(embed=self.build_network_embed())

    @commands.command(name="top")
    async def top_prefix(self, ctx, limit: int = 5):
        limit = max(1, min(limit, 10))
        await ctx.send(embed=self.build_top_embed(limit))

    @commands.command(name="adguard")
    async def adguard_prefix(self, ctx):
        try:
            await ctx.send(embed=self.build_adguard_embed())
        except RuntimeError as e:
            await ctx.send(f"❌ {e}")

    @commands.command(name="unbound")
    async def unbound_prefix(self, ctx):
        try:
            await ctx.send(embed=self.build_unbound_embed())
        except RuntimeError as e:
            await ctx.send(f"❌ {e}")

    @commands.command(name="journal")
    @require_admin()
    async def journal_prefix(self, ctx, service: str = None, lines: int = 50):
        if not service:
            await ctx.send(f"❌ Syntaxe : `{BOT_PREFIX}journal <service> [lignes]`")
            return

        try:
            lines = max(5, min(lines, 100))
            async with ctx.typing():
                embed = await asyncio.to_thread(self.build_journal_embed, service, lines)
            await ctx.send(embed=embed)
        except RuntimeError as e:
            await ctx.send(f"❌ {e}")

    @commands.command(name="update")
    @require_admin()
    async def update_prefix(self, ctx):
        try:
            async with ctx.typing():
                embed = await asyncio.to_thread(self.build_update_embed)
            await ctx.send(embed=embed)
        except RuntimeError as e:
            await ctx.send(f"❌ {e}")

    @commands.command(name="homelab")
    async def homelab_prefix(self, ctx):
        async with ctx.typing():
            embed = await asyncio.to_thread(self.build_homelab_embed)
        await ctx.send(embed=embed)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            message = "❌ Commande réservée aux administrateurs."
        else:
            message = f"❌ Erreur monitoring: {error}"

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            if MONITOR_GUILD_ID:
                guild = discord.Object(id=MONITOR_GUILD_ID)
                self.bot.tree.copy_global_to(guild=guild)
                await self.bot.tree.sync(guild=guild)
            else:
                await self.bot.tree.sync()

            print("✅ Commandes slash synchronisées")
            print(f"✅ Commandes texte disponibles avec le préfixe: {BOT_PREFIX}")

        except Exception as e:
            print(f"❌ Erreur sync slash commands: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Monitoring(bot))
