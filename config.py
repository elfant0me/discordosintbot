import os
from dotenv import load_dotenv

load_dotenv()


def parse_csv_env(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_mapping_env(name: str, default: str = "") -> dict[str, str]:
    raw = os.getenv(name, default)
    items = {}

    for entry in raw.split(";"):
        if "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            items[key] = value

    return items


def parse_homelab_services_env(name: str) -> list[dict]:
    raw = os.getenv(name, "")
    services = []

    for entry in raw.split(";"):
        parts = [part.strip() for part in entry.split("|")]
        if len(parts) < 3:
            continue

        service_type, name_value = parts[0].lower(), parts[1]
        if service_type in {"http", "ping"}:
            services.append({
                "type": service_type,
                "name": name_value,
                "target": parts[2],
            })
        elif service_type == "tcp" and len(parts) >= 4:
            try:
                port = int(parts[3])
            except ValueError:
                continue
            services.append({
                "type": "tcp",
                "name": name_value,
                "host": parts[2],
                "port": port,
                "target": f"{parts[2]}:{port}",
            })

    return services

# Configuration du bot
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
BOT_PREFIX = '-'
BOT_DESCRIPTION = "eLFantome RaspberryPI Bot"

# Messages
MESSAGES = {
    'bot_ready': 'Bot démarré avec succès ! 🚀',
    'error_generic': 'Une erreur est survenue. 😞',
    'no_permission': 'Vous n\'avez pas la permission d\'utiliser cette commande. 🚫'
}

# Couleurs
COLORS = {
    'success': 0x00ff00,
    'error': 0xff0000,
    'info': 0x0099ff,
    'warning': 0xffaa00
}

MONITOR_ALERT_CHANNEL_ID = int(os.getenv("MONITOR_ALERT_CHANNEL_ID", "0"))
MONITOR_GUILD_ID = int(os.getenv("MONITOR_GUILD_ID", "0"))

CPU_ALERT_THRESHOLD = int(os.getenv("CPU_ALERT_THRESHOLD", "85"))
TEMP_ALERT_THRESHOLD = float(os.getenv("TEMP_ALERT_THRESHOLD", "75"))
DISK_ALERT_THRESHOLD = int(os.getenv("DISK_ALERT_THRESHOLD", "90"))

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))

ADGUARD_BASE_URL = os.getenv("ADGUARD_BASE_URL", "").rstrip("/")
ADGUARD_USERNAME = os.getenv("ADGUARD_USERNAME", "")
ADGUARD_PASSWORD = os.getenv("ADGUARD_PASSWORD", "")

MONITOR_SERVICES = parse_csv_env("MONITOR_SERVICES", "AdGuardHome,tailscaled,docker")
MONITOR_DISK_PATHS = parse_csv_env("MONITOR_DISK_PATHS", "/,/home")
MONITOR_HEALTH_HOSTS = parse_mapping_env("MONITOR_HEALTH_HOSTS")
HOMELAB_SERVICES = parse_homelab_services_env("HOMELAB_SERVICES")
