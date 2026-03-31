# yt-mute-bot

Automates YouTube Studio copyright claim muting for live stream VODs.

## Problem

After streaming drag racing, the track's PA music triggers 10-20 copyright claims per VOD. YouTube's mute tool processes one claim at a time, each taking hours. You have to manually log in, click mute, wait, check back, and repeat across multiple claims and videos.

## Solution

This bot uses Playwright to automate the exact workflow in YouTube Studio:

1. Scans Content > Live for videos with copyright restrictions
2. Navigates to each video's Copyright page
3. Clicks Take action > Mute song on the first available claim
4. Polls the page every 5 minutes until processing completes
5. Moves to the next claim, repeats until all are done
6. Moves to the next video

A systemd timer runs the bot every 30 minutes. If nothing is pending, it exits immediately.

## Files

    yt_mute_bot.py   Main automation script
    config.yaml      Configuration (channel ID, timing, paths)
    setup.sh         Deployment script for Ubuntu LXC/VM

## Deployment (Proxmox LXC)

### 1. Create the LXC

On Proxmox host (Ubuntu 24.04 unprivileged container):

    pct create 150 local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
      --hostname yt-mute-bot \
      --memory 2048 \
      --cores 2 \
      --rootfs local-lvm:8 \
      --net0 name=eth0,bridge=vmbr0,ip=dhcp \
      --unprivileged 1 \
      --features nesting=1 \
      --start 1

    pct enter 150

### 2. Copy files and run setup

From your workstation, SCP the files in:

    scp -r yt-mute-bot/ root@<lxc-ip>:/tmp/

Inside the LXC:

    cd /tmp/yt-mute-bot
    bash setup.sh

### 3. Initial login (one-time)

The bot needs a saved Google session. Run it once interactively in headed mode.

Option A - X11 forwarding (from your workstation):

    ssh -X root@<lxc-ip>
    sudo -u ytmute /opt/yt-mute-bot/venv/bin/python \
      /opt/yt-mute-bot/yt_mute_bot.py --login

Option B - Xvfb + VNC (headless LXC):

    apt install -y tigervnc-standalone-server
    sudo -u ytmute bash -c 'vncserver :1 -geometry 1920x1080 -depth 24'
    # Connect VNC client to <lxc-ip>:5901
    sudo -u ytmute DISPLAY=:1 /opt/yt-mute-bot/venv/bin/python \
      /opt/yt-mute-bot/yt_mute_bot.py --login

Option C - Run login from a desktop, copy profile to LXC:

    # On your desktop (with Python + Playwright installed):
    python yt_mute_bot.py -c config.yaml --login

    # Copy the browser profile to the LXC:
    scp -r /opt/yt-mute-bot/browser-profile/ root@<lxc-ip>:/opt/yt-mute-bot/browser-profile/
    chown -R ytmute:ytmute /opt/yt-mute-bot/browser-profile/

### 4. Test

    # Single manual run:
    sudo -u ytmute /opt/yt-mute-bot/venv/bin/python /opt/yt-mute-bot/yt_mute_bot.py

    # Or target one video:
    sudo -u ytmute /opt/yt-mute-bot/venv/bin/python /opt/yt-mute-bot/yt_mute_bot.py \
      --video W2nb2QlH3E

### 5. Enable automatic runs

    systemctl enable --now yt-mute-bot.timer

## Monitoring

    # Timer status
    systemctl status yt-mute-bot.timer
    systemctl list-timers yt-mute-bot.timer

    # Last run output
    journalctl -u yt-mute-bot.service --no-pager -n 50

    # Bot log
    tail -f /var/log/yt-mute-bot/yt-mute-bot.log

    # Error screenshots
    ls -la /var/log/yt-mute-bot/screenshots/

## Configuration

Edit /opt/yt-mute-bot/config.yaml:

    channel_id                  Your YouTube channel ID
    poll_interval_seconds       How often to check if muting finished (default: 300 = 5 min)
    max_wait_per_claim_seconds  Max wait per claim before giving up (default: 14400 = 4 hr)
    max_claims_per_run          Limit claims per run (default: 0 = unlimited)
    headless                    Run browser without GUI (default: true)

## Session expiry

Google sessions typically last weeks to months. If the bot logs "Session expired",
re-run with --login. The bot exits non-zero on auth failure.

## Stopping / Disabling

    systemctl disable --now yt-mute-bot.timer
    systemctl stop yt-mute-bot.service

Full removal:

    systemctl disable --now yt-mute-bot.timer
    rm /etc/systemd/system/yt-mute-bot.*
    systemctl daemon-reload
    userdel -r ytmute
    rm -rf /opt/yt-mute-bot /var/log/yt-mute-bot

## Known limitations

- YouTube UI changes: YouTube Studio's frontend changes periodically. If selectors
  break, the bot fails gracefully (screenshots saved, logs written). The selectors
  use text-based matching which is more resilient than CSS class selectors.

- One claim at a time: This is a YouTube limitation. The bot handles it by polling.

- Google may flag automation: The persistent browser profile with stable fingerprinting
  reduces this risk. If Google serves a CAPTCHA, do a manual --login session.

- No YouTube Data API: The mute functionality is not exposed via any public API.
  Browser automation is the only path.
