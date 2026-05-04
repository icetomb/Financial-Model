# Deployment Guide — Financial Model

## 1. Overview

This app is deployed on a **DigitalOcean Ubuntu 24.04 LTS droplet** (Toronto region, $16/month Premium Intel — 1 vCPU, 2GB RAM, 70GB NVMe SSD).

The production stack:

- **Gunicorn** — WSGI server that runs the Flask app on `127.0.0.1:8000` (not exposed publicly)
- **Nginx** — reverse proxy that accepts public traffic on port 80, forwards it to Gunicorn, and serves static files directly
- **systemd** — keeps Gunicorn running in the background, auto-restarts it on crashes or reboots

The project lives at `/var/www/Financial-Model` on the server.

---

## 2. Connecting to the Server (SSH Access)

All server management is done over SSH from your Windows machine using PowerShell.

### 1. Direct SSH Command

Connect to the droplet using your private key:

```powershell
ssh -i $env:USERPROFILE\.ssh\id_ed25519_personal root@YOUR_SERVER_IP
```

- `id_ed25519_personal` is the **private key** file (not the `.pub` file)
- `YOUR_SERVER_IP` is the **Public IPv4** address shown in your DigitalOcean dashboard
- This command must be run from **PowerShell**

Example:

```powershell
ssh -i $env:USERPROFILE\.ssh\id_ed25519_personal root@134.122.50.10
```

On your very first connection you will see a message like:

```
The authenticity of host '134.122.50.10' can't be established.
Are you sure you want to continue connecting (yes/no)?
```

Type `yes` and press Enter. This only happens once.

---

### 2. Simplifying SSH with a Config File (Recommended)

To avoid typing the full command every time, add an alias to your SSH config file.

Open the config file in Notepad:

```powershell
notepad $env:USERPROFILE\.ssh\config
```

Add the following block (replace `YOUR_SERVER_IP`):

```
Host stock-server
    HostName YOUR_SERVER_IP
    User root
    IdentityFile ~/.ssh/id_ed25519_personal
```

Save the file. You can now connect with just:

```powershell
ssh stock-server
```

---

### 3. Troubleshooting SSH

**`Permission denied (publickey)`**
The wrong key is being used or the path to the key is incorrect. Double-check the filename and that you are pointing to the private key, not the `.pub` file.

**`Identity file ... not accessible`**
The key filename is wrong. Verify the exact filename in `$env:USERPROFILE\.ssh\` by running `ls $env:USERPROFILE\.ssh\` in PowerShell.

**First-time connection prompt about host authenticity**
This is normal. Type `yes` to trust the server and add it to your known hosts.

---

## 3. Initial Server Setup

SSH into the droplet, then update the system and install dependencies:

```bash
apt update && apt upgrade -y
apt install python3-pip python3-venv git nginx -y
```

---

## 4. Cloning the Repo

```bash
cd /var/www
git clone -b main https://github.com/icetomb/Financial-Model.git
cd Financial-Model
```

The `-b main` flag ensures the production server always clones the `main` branch, not `dev` or any feature branch.

---

## 5. Python Virtual Environment

Create and activate an isolated virtual environment, then install all dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn
```

---

## 6. Testing Flask Manually

Before wiring up Gunicorn and Nginx, you can verify the app runs correctly by starting Flask directly:

```bash
flask --app app run --host=0.0.0.0 --port=5000
```

Then visit `http://YOUR_SERVER_IP:5000` in a browser. This is **only for testing** — do not use Flask's built-in server in production.

---

## 7. Testing Gunicorn Manually

Once Flask works, test Gunicorn directly before setting up systemd:

```bash
gunicorn --bind 127.0.0.1:8000 app:app
```

`app:app` means: look in `app.py` for a Flask variable named `app`. If this starts without errors, the app is ready for production wiring.

---

## 8. Nginx Configuration

Create the Nginx config for this app:

```bash
nano /etc/nginx/sites-available/stockapp
```

Paste the following:

```nginx
server {
    listen 80;
    server_name YOUR_SERVER_IP;

    location /static/ {
        alias /var/www/Financial-Model/static/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        include proxy_params;
    }
}
```

Enable the config and disable the default Nginx page:

```bash
rm /etc/nginx/sites-enabled/default
ln -s /etc/nginx/sites-available/stockapp /etc/nginx/sites-enabled/stockapp
nginx -t
systemctl restart nginx
```

`nginx -t` validates the config before restarting — always run it first.

---

## 9. systemd Service

Create a systemd service so Gunicorn starts automatically and restarts on failure:

```bash
nano /etc/systemd/system/stockapp.service
```

Paste the following:

```ini
[Unit]
Description=Gunicorn instance for Financial Model
After=network.target

[Service]
User=root
Group=www-data
WorkingDirectory=/var/www/Financial-Model
Environment="PATH=/var/www/Financial-Model/venv/bin"
ExecStart=/var/www/Financial-Model/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:8000 app:app

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
systemctl start stockapp
systemctl enable stockapp
systemctl status stockapp
```

`active (running)` in the status output means Gunicorn is running in the background and will survive server reboots.

---

## 10. Updating the Deployed App After New Commits

This is the standard workflow every time you push new changes to `main` and want them live on the server.

SSH into the droplet, then run:

```bash
cd /var/www/Financial-Model
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
systemctl restart stockapp
systemctl status stockapp
```

Then refresh the website to confirm the update is live.

**Notes:**

- Even if only frontend files (HTML templates, CSS, JS) changed, still restart `stockapp` — it ensures Gunicorn picks up any template or config changes cleanly.
- If `requirements.txt` was updated (new or removed packages), `pip install -r requirements.txt` is required before restarting.
- If database migrations or schema changes are introduced later, run those migration commands **before** restarting `stockapp`.

---

## 11. Useful Commands

| Task | Command |
|---|---|
| Check app status | `systemctl status stockapp` |
| Restart the app | `systemctl restart stockapp` |
| Stop the app | `systemctl stop stockapp` |
| View recent app logs | `journalctl -u stockapp -n 50 --no-pager` |
| Validate Nginx config | `nginx -t` |
| Restart Nginx | `systemctl restart nginx` |

---

## 12. Troubleshooting

**Visiting `http://SERVER_IP` shows the default "Welcome to nginx!" page**
The default Nginx site is still enabled, or the `stockapp` config was not linked correctly. Make sure you ran `rm /etc/nginx/sites-enabled/default` and created the symlink in `/etc/nginx/sites-enabled/`.

**CSS or JS files are not loading (404 on static assets)**
Check that the `alias` path in the Nginx config matches exactly: `/var/www/Financial-Model/static/`. Verify the files exist there and that Nginx was restarted after any config change.

**App works on port 5000 but not through Nginx**
Gunicorn is probably not running, or it is not binding to `127.0.0.1:8000`. Run `systemctl status stockapp` to check, and verify the `proxy_pass` address in the Nginx config matches.

**SSH returns `Permission denied (publickey)`**
You need to specify the correct key explicitly:

```bash
ssh -i ~/.ssh/id_ed25519_personal root@SERVER_IP
```

**First SSH connection asks "authenticity of host can't be established"**
This is normal on the very first connection. Type `yes` to add the server to your known hosts.

**`systemctl status stockapp` shows `failed`**
Check the logs for the actual error:

```bash
journalctl -u stockapp -n 50 --no-pager
```

Common causes: wrong `WorkingDirectory` path, missing virtual environment, syntax error in `app.py`, or a missing environment variable.
