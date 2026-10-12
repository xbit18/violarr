# Database updates

Open **Database updates** in the WebUI to keep the data used for searches up to
date.

1. Keep **Automatic snapshot updates** enabled for most installations.
2. Enter a numeric interval and select minutes, hours, or days. The default is
   `1` day; accepted values range from 1 minute to 7 days, inclusive. Decimals
   must yield whole seconds (for example, `1.5` minutes); invalid input is not
   rounded.
3. Select **Save update settings** and wait for confirmation.

Select **Check and update now** for an asynchronous check, even with automatic
updates disabled. Acceptance means the check has started, not that an update has
finished. Progress distinguishes checking, downloading, validating, restoring,
switching, already up to date, updated, and failure. An active check disables
the button; another request receives a busy response. Temporary maintenance
responses are retried by status polling without changing unsaved settings.

Changing the interval while idle schedules the next check from save time;
enabling checks permits a prompt check, and disabling clears the deadline.
Changes during an active operation do not cancel it or queue another one.
Completion, including manual completion, schedules the next check from
completion time using the latest effective interval, or none when disabled.
Environment overrides remain authoritative.

The **Database snapshot** card on the **Dashboard** shows the installed and
latest detected versions and the last and next checks. **Updating** or active
**Maintenance** are temporary states; **Error** means you should read the
message and container logs.

For technical details, see [safe updates](../how-it-works/safe-updates).
