# Systemd Service Configuration

This directory contains systemd service and timer files for scheduling the Orchestrator Improvement Engine to run automatically.

## Files

- `orchestrator-improvement.service` - Systemd service that runs the improvement engine
- `orchestrator-improvement.timer` - Systemd timer that schedules daily execution

## Installation

The systemd files are installed in `/etc/systemd/system/`:

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo cp systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable orchestrator-improvement.timer
sudo systemctl start orchestrator-improvement.timer
```

## Schedule

The improvement engine runs daily at:
- 00:00:00 (midnight) JST
- 03:00:00 (3 AM) JST

## Management Commands

### Check timer status
```bash
sudo systemctl status orchestrator-improvement.timer
```

### Check service status
```bash
sudo systemctl status orchestrator-improvement.service
```

### View logs
```bash
tail -f ~/orchestrator/logs/improvement_engine.log
```

### Manually trigger improvement check
```bash
sudo systemctl start orchestrator-improvement.service
```

### Disable automatic execution
```bash
sudo systemctl stop orchestrator-improvement.timer
sudo systemctl disable orchestrator-improvement.timer
```

## How It Works

1. The timer triggers daily at the scheduled times
2. The service runs `improvement_engine.py` which:
   - Checks all projects for improvement triggers
   - Detects consecutive failures or low average scores
   - Aggregates improvement suggestions from evaluations
   - Applies improvements to separate Git branches
   - Records improvement history

3. All output is logged to `~/orchestrator/logs/improvement_engine.log`

## Logs

Logs are appended to: `/home/sorakun_fukuoka/orchestrator/logs/improvement_engine.log`

View recent activity:
```bash
tail -100 ~/orchestrator/logs/improvement_engine.log
```

## Troubleshooting

If the service fails, check:
1. Supabase connection (environment variables in `.env`)
2. Python environment and dependencies
3. File permissions on the orchestrator directory
4. Service logs: `journalctl -u orchestrator-improvement.service -n 50`
