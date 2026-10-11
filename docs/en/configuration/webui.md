# WebUI overview

Open `http://HOST:8000/` to check Violarr and change its settings.

## What you will find

- **Dashboard** shows status and errors. Every card should be healthy.
- **General** shows identity, safety, and addresses. It requires no action.
- **Database updates** manages checks. The normal choice is every 24 hours.
- **Result processing** manages presets and rules. Start unfiltered.
- **Prowlarr** saves the connection. The expected state is **Connected**.
- **[Diagnostics](../features/diagnostics)** inspects searches and Torznab
  requests, replays against current state and exports local reports with a
  privacy preview.
- **Advanced** is read-only and normally requires no action.

## Save and verify

- Persistent settings save only when you select their **Save** button.
  Diagnostic monitoring changes immediately and remains in memory only.
- Wait for the confirmation message before leaving the page.
- A saved API key is not displayed. Use **Replace key** only to change it.

## If status is unhealthy

1. Select **Refresh** on the **Dashboard**.
2. Read the error on the red card and open the area identified by the problem.
3. If the error remains, check [troubleshooting](../troubleshooting) and the
   container logs.

The WebUI has no authentication. Expose it only on a trusted network or behind
an authenticated reverse proxy.
