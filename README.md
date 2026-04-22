# Discord OSINT Bot

Petit bot Discord Python pour monitorer un Raspberry Pi / homelab et lancer quelques outils réseau/OSINT depuis Discord.

## Fonctionnalités

- Monitoring Raspberry Pi: CPU, RAM, température, disques, uptime, services, Docker
- Stats DNS: AdGuard Home et Unbound
- Dashboard homelab: services online/offline
- Outils OSINT/réseau: nmap, nslookup, whois, geoip, wpscan
- Commandes admin: reload des cogs, restart/shutdown, journalctl, updates APT
- Support commandes préfixées et slash commands

## Installation

```bash
git clone https://github.com/elfant0me/discordosintbot.git
cd discordosintbot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Remplis ensuite `.env` avec ton token Discord et tes paramètres locaux.

```bash
python bot.py
```

## Configuration

Les secrets et infos personnelles doivent rester dans `.env`.

Variables principales:

- `DISCORD_TOKEN`
- `MONITOR_ALERT_CHANNEL_ID`
- `MONITOR_GUILD_ID`
- `ADGUARD_BASE_URL`
- `ADGUARD_USERNAME`
- `ADGUARD_PASSWORD`
- `MONITOR_SERVICES`
- `MONITOR_DISK_PATHS`
- `MONITOR_HEALTH_HOSTS`
- `HOMELAB_SERVICES`

Voir [.env.example](.env.example) pour les exemples de format.

## Outils système optionnels

Certaines commandes nécessitent des outils installés sur le Raspberry:

```bash
sudo apt install nmap whois dnsutils
sudo gem install wpscan
```

Pour Unbound et certains scans nmap, il peut être nécessaire d'ajouter des règles `sudoers` limitées.

## Sécurité

Ne commit jamais `.env`. Il est ignoré par `.gitignore`, mais vérifie quand même avant de push:

```bash
git status
```

Si `.env` a déjà été ajouté par erreur:

```bash
git rm --cached .env
```

Utilise les commandes OSINT uniquement sur des machines, domaines ou services que tu possèdes ou pour lesquels tu as une autorisation.
