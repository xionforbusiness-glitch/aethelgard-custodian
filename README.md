# 🏛️ Aethelgard 24/7 Autonomous Vault Custodian

[![Google Antigravity Pro Plan](https://img.shields.io/badge/Brain-Google_Antigravity_Pro_Plan-4285F4?style=for-the-badge&logo=google)](https://antigravity.google)
[![Model](https://img.shields.io/badge/Model-Gemini_Flash_High_(1M_Context)-34A853?style=for-the-badge)](https://deepmind.google)
[![Telegram](https://img.shields.io/badge/Interface-Telegram_(@aethelgard__vault__bot)-24A1DE?style=for-the-badge&logo=telegram)](https://t.me/aethelgard_vault_bot)
[![Obsidian](https://img.shields.io/badge/Vault-Automated_2--Way_Git_Sync-7C3AED?style=for-the-badge&logo=obsidian)](https://obsidian.md)

**Aethelgard Custodian** is an autonomous, 24/7 always-on knowledge manager and personal assistant designed specifically for Omar Elnemr's Obsidian Vault. It runs continuously on a cloud virtual machine (Google Cloud Free Tier / Daytona / Linux VPS) without ever shutting down.

---

## ⚡ Core Capabilities

* 🧠 **Google Antigravity Pro Plan Engine:** Powered by **Gemini 3.7 / 3.8 Flash High** via local OmniRoute proxy. Delivers **150+ to 250+ tokens per second** with a **1,000,000-token context window** (zero bottlenecks, zero KV cache quantization degradation).
* 🎙️ **Native Multimodal Hearing & Voice:** Send voice notes directly on Telegram in Arabic or English—the agent transcribes, translates, and synthesizes responses immediately.
* 🎥 **YouTube & Web Ingestion:** Paste any YouTube, Shorts, or article link to download transcripts, summarize takeaways, and save formatted notes directly into the vault.
* 📸 **Vision & Document Analysis:** Ingests architecture diagrams, cubing patterns, hardware wiring diagrams, and handwritten notes.
* 💾 **Automated 2-Way Git Sync:** Automatically pulls updates from and pushes new notes back to [`aethelgard-vault`](https://github.com/xionforbusiness-glitch/aethelgard-vault) on GitHub every 3 minutes.
* 🔄 **24/7 Always-On Autonomy:** Runs as a supervised `systemd` background service that automatically restarts if the server ever reboots.

---

## 🚀 1-Click Deployment (Google Cloud Free VM / Daytona / VPS)

### Option 1: Google Cloud Platform (GCP) Always Free VM (Recommended)
Google Cloud provides a **100% Free Forever `e2-micro` instance** (1 vCPU, 1 GB RAM, 30 GB SSD) in US regions (`us-central1`, `us-east1`, `us-west1`).

1. **SSH into your Google Cloud VM:**
   Open your VM terminal on Google Cloud Console or via SSH.

2. **Clone this private repository:**
   ```bash
   git clone https://xionforbusiness-glitch:GITHUB_TOKEN_REDACTED@github.com/xionforbusiness-glitch/aethelgard-custodian.git
   cd aethelgard-custodian
   ```

3. **Run the 1-Click Deployer:**
   ```bash
   chmod +x deploy.sh
   ./deploy.sh
   ```

*(The deployer automatically installs Node 22 LTS, OmniRoute, Python dependencies, restores the Antigravity Pro SQLite database, configures the `llm-wiki` Hermes profile, registers the `aethelgard.service` systemd daemon, and sends a ping to your Telegram!)*

---

### Option 2: Daytona Sandbox / Cloud Workspace
If you are deploying in a [Daytona](https://daytona.io) workspace or sandbox:

1. In the Daytona terminal:
   ```bash
   git clone https://xionforbusiness-glitch:GITHUB_TOKEN_REDACTED@github.com/xionforbusiness-glitch/aethelgard-custodian.git
   cd aethelgard-custodian
   chmod +x deploy.sh
   ./deploy.sh
   ```
2. Or use Docker Compose directly:
   ```bash
   docker compose up -d
   ```

---

## 🛠️ Service Management Commands

| Action | Command |
| :--- | :--- |
| **View Live Telegram Logs** | `sudo journalctl -u aethelgard -f` |
| **Check Daemon Status** | `sudo systemctl status aethelgard` |
| **Restart Bot Daemon** | `sudo systemctl restart aethelgard` |
| **Stop Bot Daemon** | `sudo systemctl stop aethelgard` |
| **View OmniRoute Proxy Logs** | `tail -f /tmp/omniroute.log` |

---

## 🧩 Architecture

```mermaid
flowchart TD
    User([📱 Omar on Telegram]) <--> TG[Telegram Bot Gateway\n@aethelgard_vault_bot]
    TG <--> Hermes[Hermes Agent Supervisor\nProfile: llm-wiki]
    
    subgraph Antigravity ["🧠 Google Antigravity Pro Plan"]
        Hermes <--> OmniRoute[OmniRoute Router\n127.0.0.1:20128]
        OmniRoute <--> CloudLLM[Gemini 3.7 Flash High\n1M Context | 200+ t/s]
    end
    
    subgraph Storage ["💾 Persistent Obsidian Vault"]
        Hermes <--> LocalVault[Local Markdown Vault\n~/vault]
        LocalVault <--> GitWorker[Git Auto-Sync Worker\n3-minute interval]
        GitWorker <--> GitHub[GitHub Repo\naethelgard-vault:origin/main]
    end
```

---

## 🔐 Configuration Overview (`.env`)

All verified credentials are pre-configured in `.env` (private repository):
* **Telegram Bot Token:** `8992784967:AAH...` (@aethelgard_vault_bot)
* **Allowed User ID:** `1021125594` (Omar Elnemr)
* **GitHub Access:** Full sync permissions for `aethelgard-vault`
* **Antigravity Pro Key:** Pre-loaded via encrypted SQLite state database
